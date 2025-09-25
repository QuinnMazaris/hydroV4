import asyncio
import json
import queue
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import paho.mqtt.client as mqtt
from loguru import logger
from .config import settings
from .events import event_broker
from .metrics import build_metric_meta
from .models import RelayControl
from .services.persistence import (
    get_metric_by_key,
    insert_reading,
    mark_devices_inactive,
    sync_device_metrics,
    upsert_device,
)

PLACEHOLDER_DEVICE_ID = 'esp32_main'

class MQTTClient:
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self.is_connected = False
        self.device_registry: Dict[str, Dict[str, Any]] = {}
        self.last_seen: Dict[str, datetime] = {}
        self.message_handlers: Dict[str, Callable] = {}
        self.message_queue = queue.Queue()
        self.processing_task = None
        base_topic = settings.mqtt_base_topic.strip('/')
        self.base_topic_parts = base_topic.split('/') if base_topic else []
        self._setup_handlers()
        self.device_db_ids: Dict[str, int] = {}
        self.metric_cache: Dict[str, Dict[str, int]] = {}

    async def _ensure_device_record(
        self,
        device_key: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        metadata: Optional[Any] = None,
        last_seen: Optional[datetime] = None,
    ):
        metadata_str = None
        if metadata is not None:
            if isinstance(metadata, str):
                metadata_str = metadata
            else:
                metadata_str = json.dumps(metadata, default=str)
        device = await upsert_device(
            device_key=device_key,
            name=name,
            description=description,
            metadata=metadata_str,
            last_seen=last_seen,
        )
        self.device_db_ids[device_key] = device.id
        return device

    def _cache_metrics(self, device_key: str, metrics: Dict[str, Any]) -> None:
        cache = self.metric_cache.setdefault(device_key, {})
        for metric_key, metric in metrics.items():
            metric_id = getattr(metric, 'id', None)
            if metric_id is not None:
                cache[metric_key] = metric_id

    async def _sync_metric_definitions(
        self,
        device_key: str,
        definitions: List[Dict[str, Optional[str]]],
    ) -> Dict[str, int]:
        if not definitions:
            return self.metric_cache.get(device_key, {})
        device = await self._ensure_device_record(device_key)
        metrics = await sync_device_metrics(device.id, definitions)
        self._cache_metrics(device_key, metrics)
        return self.metric_cache.get(device_key, {})

    async def _ensure_metric_id(
        self,
        device_key: str,
        metric_key: str,
        *,
        label: Optional[str] = None,
        unit: Optional[str] = None,
    ) -> Optional[int]:
        cache = self.metric_cache.setdefault(device_key, {})
        if metric_key in cache:
            return cache[metric_key]

        metric = await get_metric_by_key(device_key, metric_key)
        if metric:
            cache[metric_key] = metric.id
            self.device_db_ids.setdefault(device_key, metric.device_id)
            return metric.id

        definitions = [
            {
                'metric_key': metric_key,
                'display_name': label or metric_key,
                'unit': unit or None,
            }
        ]
        metrics = await self._sync_metric_definitions(device_key, definitions)
        return metrics.get(metric_key)

    def _flatten_sensor_payload(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten incoming sensor payload to simple key/value pairs."""
        sensors: Dict[str, Any] = {}
        try:
            if not isinstance(data, dict):
                return sensors
            for key, value in data.items():
                if key == 'device_id':
                    continue
                if isinstance(value, (int, float, bool, str)):
                    sensors[key] = value
                elif isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        if isinstance(sub_val, (int, float, bool, str)):
                            if key in ['bme680']:
                                sensors[sub_key] = sub_val
                            else:
                                sensors[f"{key}_{sub_key}"] = sub_val
        except Exception:
            pass
        return sensors

    def _setup_handlers(self):
        """Setup message handlers for different topics"""
        self.message_handlers = {
            settings.sensor_data_topic: self._handle_sensor_data,
            settings.relay_status_topic: self._handle_relay_status,
            "esp32/critical_relays": self._handle_critical_relays,
            "esp32/status": self._handle_device_status_msg,
        }

    def _sensor_descriptor(self, sensor_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        overrides = overrides or {}
        meta = build_metric_meta(sensor_id, overrides)
        return {
            'id': meta.id,
            'label': meta.label,
            'unit': meta.unit,
            'color': meta.color,
        }

    def _normalize_sensor_payload(self, payload: Any) -> Dict[str, Dict[str, Any]]:
        definitions: Dict[str, Dict[str, Any]] = {}
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                sensor_id = item.get('id')
                if not sensor_id:
                    continue
                overrides = {k: item.get(k) for k in ('label', 'unit', 'color') if item.get(k)}
                definitions[sensor_id] = self._sensor_descriptor(sensor_id, overrides)
        elif isinstance(payload, dict):
            for sensor_id, info in payload.items():
                overrides = {}
                if isinstance(info, dict):
                    overrides = {k: info.get(k) for k in ('label', 'unit', 'color') if info.get(k)}
                definitions[sensor_id] = self._sensor_descriptor(sensor_id, overrides)
        return definitions

    def _normalize_actuator_payload(self, payload: Any) -> List[Dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            normalized: List[Dict[str, Any]] = []
            for key, info in payload.items():
                if isinstance(info, dict):
                    normalized.append({'id': key, **info})
            return normalized
        return []

    def request_discovery_broadcast(self) -> None:
        if not self.client:
            return
        try:
            payload = json.dumps({'request': 'ping'})
            self.client.publish(settings.discovery_request_topic, payload, qos=settings.mqtt_qos)
        except Exception as exc:
            logger.error(f"Failed to request discovery broadcast: {exc}")


    def _device_id_from_topic(self, topic: str) -> Optional[str]:
        parts = topic.split('/')
        if not parts:
            return None
        base_parts = self.base_topic_parts
        base_len = len(base_parts)
        if base_len == 0:
            return None
        if parts[:base_len] != base_parts:
            return None
        if len(parts) <= base_len:
            return None
        candidate = parts[base_len]
        if candidate in {'data', 'status', 'relay', 'discovery'}:
            return None
        return candidate

    def _canonical_device_id(self, device_id: Optional[str]) -> str:
        candidate = device_id or PLACEHOLDER_DEVICE_ID
        if candidate != PLACEHOLDER_DEVICE_ID:
            return candidate
        known_ids = [key for key in self.device_registry.keys() if key != PLACEHOLDER_DEVICE_ID]
        if len(known_ids) == 1:
            return known_ids[0]
        return candidate

    async def _publish_error(self, code: str, message: str, context: Optional[Dict[str, Any]] = None) -> None:
        event = {
            'type': 'error',
            'code': code,
            'message': message,
            'context': context or {},
            'ts': int(datetime.utcnow().timestamp() * 1000),
        }
        try:
            await event_broker.publish(event)
        except Exception:
            logger.error(f"Failed to publish error event {code}: {message}")

    def _absorb_placeholder(self, device_id: str) -> None:
        if device_id == PLACEHOLDER_DEVICE_ID:
            return
        placeholder = self.device_registry.get(PLACEHOLDER_DEVICE_ID)
        if not placeholder:
            return
        entry = self.device_registry.get(device_id)
        if not entry:
            return
        entry['sensors'] = {**placeholder.get('sensors', {}), **entry.get('sensors', {})}
        if not entry.get('actuators') and placeholder.get('actuators'):
            entry['actuators'] = placeholder['actuators']
        if not entry.get('data') and placeholder.get('data'):
            entry['data'] = placeholder.get('data')
        if not entry.get('last_seen') and placeholder.get('last_seen'):
            entry['last_seen'] = placeholder.get('last_seen')
        self.device_registry.pop(PLACEHOLDER_DEVICE_ID, None)
        self.last_seen.pop(PLACEHOLDER_DEVICE_ID, None)

    async def connect(self):
        """Connect to MQTT broker with reconnection handling"""
        try:
            self.client = mqtt.Client()

            # Set up authentication if provided
            if settings.mqtt_username and settings.mqtt_password:
                self.client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

            # Set up callbacks
            self.client.on_connect = self._on_connect
            self.client.on_disconnect = self._on_disconnect
            self.client.on_message = self._on_message

            # Connect to broker
            self.client.connect(settings.mqtt_broker, settings.mqtt_port, settings.mqtt_keepalive)
            self.client.loop_start()

            logger.info(f"Connecting to MQTT broker at {settings.mqtt_broker}:{settings.mqtt_port}")

        except Exception as e:
            logger.error(f"Failed to connect to MQTT broker: {e}")
            raise

    def _on_connect(self, client, userdata, flags, rc):
        """Callback for successful MQTT connection"""
        if rc == 0:
            self.is_connected = True
            logger.info("Connected to MQTT broker successfully")

            # Subscribe to all relevant topics
            topics = [
                (settings.sensor_data_topic, settings.mqtt_qos),
                (settings.relay_status_topic, settings.mqtt_qos),
                ("esp32/critical_relays", settings.mqtt_qos),               # Critical relay updates
                ("esp32/status", settings.mqtt_qos),                        # Device status
                (f"{settings.mqtt_base_topic}/+/status", settings.mqtt_qos),  # Device status
                (f"{settings.mqtt_base_topic}/+/data", settings.mqtt_qos),    # Device data
                (f"{settings.mqtt_base_topic}/+/discovery", settings.mqtt_qos),  # Device capabilities (boot)
                (f"{settings.mqtt_base_topic}/+/heartbeat", settings.mqtt_qos),  # Device heartbeat responses
                (f"{settings.mqtt_base_topic}/+/relay/status", settings.mqtt_qos),  # Device-specific relay status
            ]

            for topic, qos in topics:
                client.subscribe(topic, qos)
                logger.info(f"Subscribed to topic: {topic}")

        else:
            logger.error(f"Failed to connect to MQTT broker. Return code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        """Callback for MQTT disconnection"""
        self.is_connected = False
        if rc != 0:
            logger.warning("Unexpected MQTT disconnection. Will auto-reconnect.")

    def _on_message(self, client, userdata, msg):
        """Handle incoming MQTT messages"""
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            logger.debug(f"Received message on topic {topic}: {payload}")

            # Add message to queue for async processing
            self.message_queue.put((topic, payload))

        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    async def start_message_processor(self):
        """Start the async message processor"""
        if self.processing_task is None:
            self.processing_task = asyncio.create_task(self._message_processor())

    async def _message_processor(self):
        """Process messages from the queue asynchronously"""
        while True:
            try:
                # Check for messages with a timeout
                try:
                    topic, payload = self.message_queue.get(timeout=1.0)
                    await self._process_message(topic, payload)
                    self.message_queue.task_done()
                except queue.Empty:
                    # No message available, continue
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"Error in message processor: {e}")
                await asyncio.sleep(1.0)

    async def _process_message(self, topic: str, payload: str):
        """Process MQTT message asynchronously"""
        try:
            # Try to parse JSON payload, but handle plain text for status messages
            data = payload
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                # For topics like esp32/status, keep as string
                if topic not in ["esp32/status"]:
                    logger.warning(f"Invalid JSON payload on topic {topic}: {payload}")
                    return

            # Route to appropriate handler
            handler = self.message_handlers.get(topic)
            if handler:
                await handler(topic, data)
            else:
                # Handle dynamic topics (device-specific)
                if isinstance(data, dict):
                    await self._handle_dynamic_topic(topic, data)

        except Exception as e:
            logger.error(f"Error processing message for topic {topic}: {e}")


    async def _handle_sensor_data(self, topic: str, data: Dict[str, Any]):
        """Handle sensor data messages"""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            derived_id = derived_id or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Sensor data missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Sensor data missing device_id; payload ignored.',
                    {
                        'topic': topic,
                        'payload_keys': list(data.keys()) if isinstance(data, dict) else [],
                        'device_id': derived_id,
                    },
                )
                return

            sensors = self._flatten_sensor_payload(data)
            if not sensors:
                await self._publish_error(
                    'empty_sensor_payload',
                    'Sensor data contained no usable sensors; payload ignored.',
                    {'topic': topic, 'device_id': device_id},
                )
                return

            registry_sensors = self.device_registry.get(device_id, {}).get('sensors', {}) or {}
            sensor_defs: Dict[str, Dict[str, Any]] = {}
            for sensor_id in sensors.keys():
                existing_meta = registry_sensors.get(sensor_id) if isinstance(registry_sensors, dict) else None
                if isinstance(existing_meta, dict):
                    sensor_defs[sensor_id] = existing_meta
                else:
                    sensor_defs[sensor_id] = self._sensor_descriptor(sensor_id)

            await self._update_device_discovery(device_id, metadata=data, sensors=sensor_defs)

            timestamp = datetime.utcnow()
            for sensor_name, value in sensors.items():
                meta = sensor_defs.get(sensor_name, {})
                metric_id = await self._ensure_metric_id(
                    device_id,
                    sensor_name,
                    label=meta.get('label'),
                    unit=meta.get('unit'),
                )
                if metric_id is None:
                    logger.warning(
                        'No metric registered for sensor value; skipping reading',
                        device_id=device_id,
                        sensor=sensor_name,
                    )
                    continue
                try:
                    await insert_reading(metric_id, value, timestamp=timestamp)
                except Exception as exc:
                    logger.error(f"Failed to persist sensor reading for {device_id}/{sensor_name}: {exc}")
                    await self._publish_error(
                        'sensor_persist_failed',
                        'Failed to persist sensor reading; continuing with live stream.',
                        {'device_id': device_id, 'topic': topic, 'metric_key': sensor_name},
                    )

            await event_broker.publish({
                'type': 'reading',
                'device_id': device_id,
                'timestamp': int(timestamp.timestamp() * 1000),
                'sensors': sensors,
            })

            logger.debug(f"Processed sensor data for device {device_id}")

        except Exception as exc:
            logger.error(f"Error handling sensor data: {exc}")

    async def _handle_relay_status(self, topic: str, data: Dict[str, Any]):
        """Handle relay status messages"""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            derived_id = derived_id or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Relay status missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Relay status missing device_id; payload ignored.',
                    {
                        'topic': topic,
                        'payload_keys': list(data.keys()) if isinstance(data, dict) else [],
                        'device_id': derived_id,
                    },
                )
                return

            actuator_defs: List[Dict[str, Any]] = []
            relay_values: Dict[str, Any] = {}
            existing = {
                (entry.get('id') or f"relay{entry.get('number')}"): entry
                for entry in self.device_registry.get(device_id, {}).get('actuators', []) or []
            }

            items = data.items() if isinstance(data, dict) else []
            for key, value in items:
                if not isinstance(key, str) or key == 'device_id' or not key.startswith('relay'):
                    continue
                try:
                    relay_num = int(key.replace('relay', ''))
                except ValueError:
                    relay_num = None
                prior = existing.get(key) or existing.get(f'relay{relay_num}') if relay_num is not None else existing.get(key)
                actuator_defs.append({
                    'id': key,
                    'type': 'relay',
                    'number': relay_num,
                    'label': (prior or {}).get('label') or (f"Relay {relay_num}" if relay_num is not None else key),
                    'state': value,
                    'unit': '',
                })
                relay_values[key] = value

            if not actuator_defs:
                return

            await self._update_device_discovery(device_id, metadata=data, actuators=actuator_defs)

            timestamp = datetime.utcnow()
            for relay_key, value in relay_values.items():
                meta = next((item for item in actuator_defs if item.get('id') == relay_key), {})
                metric_id = await self._ensure_metric_id(
                    device_id,
                    relay_key,
                    label=meta.get('label'),
                    unit=meta.get('unit'),
                )
                if metric_id is None:
                    logger.warning(
                        'No metric registered for relay state; skipping reading',
                        device_id=device_id,
                        relay=relay_key,
                    )
                    continue
                try:
                    await insert_reading(metric_id, value, timestamp=timestamp)
                except Exception as exc:
                    logger.error(f"Failed to persist relay state for {device_id}/{relay_key}: {exc}")
                    await self._publish_error(
                        'relay_persist_failed',
                        'Failed to persist relay state; continuing with live stream.',
                        {'device_id': device_id, 'topic': topic, 'metric_key': relay_key},
                    )

            await event_broker.publish({
                'type': 'reading',
                'device_id': device_id,
                'timestamp': int(timestamp.timestamp() * 1000),
                'sensors': relay_values,
            })

            logger.debug(f"Processed relay status for device {device_id}")

        except Exception as exc:
            logger.error(f"Error handling relay status: {exc}")

    async def _handle_critical_relays(self, topic: str, data: Dict[str, Any]):
        """Handle legacy critical relay updates."""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            derived_id = derived_id or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id in {PLACEHOLDER_DEVICE_ID, 'critical_relays'}:
                logger.warning('Critical relay update missing device identity; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Critical relay update missing device_id; payload ignored.',
                    {
                        'topic': topic,
                        'payload_keys': list(data.keys()) if isinstance(data, dict) else [],
                        'device_id': derived_id,
                    },
                )
                return

            relay_defs: List[Dict[str, Any]] = []
            relay_values: Dict[str, Any] = {}

            relay_key = data.get('relay', '') if isinstance(data, dict) else ''
            if isinstance(relay_key, str) and relay_key.startswith('relay'):
                try:
                    relay_num = int(relay_key.replace('relay', ''))
                except ValueError:
                    relay_num = None
                existing = {
                    (entry.get('id') or f"relay{entry.get('number')}"): entry
                    for entry in self.device_registry.get(device_id, {}).get('actuators', []) or []
                }
                prior = existing.get(relay_key) or existing.get(f'relay{relay_num}') if relay_num is not None else existing.get(relay_key)

                raw_state = data.get('state')
                if isinstance(raw_state, bool):
                    state_value = raw_state
                    state_label = 'on' if raw_state else 'off'
                else:
                    state_label = str(raw_state).lower() if raw_state is not None else 'unknown'
                    if isinstance(raw_state, str):
                        lowered = raw_state.strip().lower()
                        if lowered in {'on', 'off'}:
                            state_value = lowered == 'on'
                        else:
                            state_value = state_label
                    else:
                        state_value = state_label

                relay_defs.append({
                    'id': relay_key,
                    'type': 'relay',
                    'number': relay_num,
                    'label': (prior or {}).get('label') or (f"Relay {relay_num}" if relay_num is not None else relay_key),
                    'state': state_label,
                    'unit': '',
                })
                relay_values[relay_key] = state_value

            if not relay_defs:
                return

            await self._update_device_discovery(device_id, metadata=data, actuators=relay_defs)

            timestamp = datetime.utcnow()
            for relay_key, value in relay_values.items():
                meta = next((item for item in relay_defs if item.get('id') == relay_key), {})
                metric_id = await self._ensure_metric_id(
                    device_id,
                    relay_key,
                    label=meta.get('label'),
                    unit=meta.get('unit'),
                )
                if metric_id is None:
                    logger.warning(
                        'No metric registered for critical relay state; skipping reading',
                        device_id=device_id,
                        relay=relay_key,
                    )
                    continue
                try:
                    await insert_reading(metric_id, value, timestamp=timestamp)
                except Exception as exc:
                    logger.error(f"Failed to persist critical relay state for {device_id}/{relay_key}: {exc}")
                    await self._publish_error(
                        'relay_persist_failed',
                        'Failed to persist critical relay state; continuing with live stream.',
                        {'device_id': device_id, 'topic': topic, 'metric_key': relay_key},
                    )

            await event_broker.publish({
                'type': 'reading',
                'device_id': device_id,
                'timestamp': int(timestamp.timestamp() * 1000),
                'sensors': relay_values,
            })

        except Exception as exc:
            logger.error(f"Error handling critical relay update: {exc}")

    async def _handle_device_status_msg(self, topic: str, data: Dict[str, Any]):
        """Handle device status messages"""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            derived_id = derived_id or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Status update missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Status update missing device_id; payload ignored.',
                    {
                        'topic': topic,
                        'payload_keys': list(data.keys()) if isinstance(data, dict) else [],
                        'device_id': derived_id,
                    },
                )
                return

            await self._update_device_discovery(device_id, metadata={'status': data})

        except Exception as exc:
            logger.error(f"Error handling device status: {exc}")

    async def _handle_dynamic_topic(self, topic: str, data: Dict[str, Any]):
        """Handle dynamic device topics for autodiscovery and status updates"""
        try:
            parts = topic.split('/')
            base_parts = self.base_topic_parts
            base_len = len(base_parts)

            if base_len == 0 or len(parts) <= base_len + 1 or parts[:base_len] != base_parts:
                return

            device_id = parts[base_len]
            message_type = parts[base_len + 1]
            canonical_id = self._canonical_device_id(data.get('device_id') or device_id)

            if message_type == 'data':
                payload = {**data, 'device_id': canonical_id} if isinstance(data, dict) else {'device_id': canonical_id}
                await self._handle_sensor_data(topic, payload)
            elif message_type == 'status':
                payload = {**data, 'device_id': canonical_id} if isinstance(data, dict) else {'device_id': canonical_id}
                await self._handle_device_status_msg(topic, payload)
            elif message_type == 'discovery':
                await self._handle_discovery(canonical_id, data, topic=topic)
            elif message_type == 'heartbeat':
                await self._handle_heartbeat(canonical_id, data)
            elif message_type == 'relay' and len(parts) >= base_len + 3 and parts[base_len + 2] == 'status':
                payload = {**data, 'device_id': canonical_id} if isinstance(data, dict) else {'device_id': canonical_id}
                await self._handle_relay_status(topic, payload)
            else:
                await self._update_device_discovery(canonical_id, metadata=data)

        except Exception as exc:
            logger.error(f"Error handling dynamic topic {topic}: {exc}")

    async def _handle_heartbeat(self, device_id: str, data: Dict[str, Any]):
        """Handle lightweight heartbeat responses from discovery ping"""
        try:
            current_time = datetime.utcnow()
            self.last_seen[device_id] = current_time
            await self._ensure_device_record(device_id, metadata=None, last_seen=current_time)

            entry = self.device_registry.get(device_id)
            if entry:
                entry['last_seen'] = current_time
                entry['is_active'] = True
                logger.debug(f"Heartbeat received from known device {device_id}")
            else:
                logger.info(f"Heartbeat from unknown device {device_id} - waiting for boot discovery")

        except Exception as exc:
            logger.error(f"Error handling heartbeat from {device_id}: {exc}")

    async def _handle_discovery(self, device_id: str, data: Dict[str, Any], topic: Optional[str] = None):
        """Handle capability discovery payloads from devices."""
        try:
            derived_id = device_id or (data.get('device_id') if isinstance(data, dict) else None) or (self._device_id_from_topic(topic) if topic else None)
            canonical_id = self._canonical_device_id(derived_id)
            if canonical_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Discovery payload missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Discovery payload missing device_id; payload ignored.',
                    {'topic': topic, 'payload_keys': list(data.keys()) if isinstance(data, dict) else [], 'device_id': derived_id},
                )
                return

            sensors = self._normalize_sensor_payload(data.get('sensors', {}))
            actuators = self._normalize_actuator_payload(data.get('actuators', []))
            await self._update_device_discovery(
                canonical_id,
                metadata=data,
                sensors=sensors,
                actuators=actuators,
            )
        except Exception as exc:
            logger.error(f"Error handling discovery payload for {device_id}: {exc}")

    async def _update_device_discovery(
        self,
        device_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        sensors: Optional[Dict[str, Dict[str, Any]]] = None,
        actuators: Optional[Any] = None,
    ) -> None:
        """Update device discovery registry and database with optional capability metadata."""
        try:
            current_time = datetime.utcnow()
            self.last_seen[device_id] = current_time

            name = None
            description = None
            if isinstance(metadata, dict):
                name = metadata.get('name') or metadata.get('device_name')
                description = metadata.get('description')

            await self._ensure_device_record(
                device_id,
                name=name,
                description=description,
                metadata=metadata,
                last_seen=current_time,
            )

            metric_defs: List[Dict[str, Optional[str]]] = []
            seen_keys = set()
            if sensors:
                for key, meta in sensors.items():
                    metric_key = str(key)
                    if not metric_key or metric_key in seen_keys:
                        continue
                    seen_keys.add(metric_key)
                    display_name = meta.get('label') if isinstance(meta, dict) else None
                    unit = meta.get('unit') if isinstance(meta, dict) else None
                    metric_defs.append(
                        {
                            'metric_key': metric_key,
                            'display_name': display_name or metric_key,
                            'unit': unit or None,
                        }
                    )
            if isinstance(actuators, list):
                for entry in actuators:
                    if not isinstance(entry, dict):
                        continue
                    metric_key = str(entry.get('id') or entry.get('key') or '')
                    if not metric_key or metric_key in seen_keys:
                        continue
                    seen_keys.add(metric_key)
                    display_name = entry.get('label') or metric_key
                    unit = entry.get('unit') or None
                    metric_defs.append(
                        {
                            'metric_key': metric_key,
                            'display_name': display_name,
                            'unit': unit,
                        }
                    )

            if metric_defs:
                await self._sync_metric_definitions(device_id, metric_defs)

            entry = self.device_registry.get(device_id)
            if not entry:
                entry = {
                    'last_seen': current_time,
                    'is_active': True,
                    'data': metadata,
                    'sensors': {},
                    'actuators': [],
                }
                self.device_registry[device_id] = entry
                logger.info(f"Discovered new device: {device_id}")
            else:
                entry['last_seen'] = current_time
                entry['is_active'] = True
                if metadata is not None:
                    entry['data'] = metadata
                entry.setdefault('sensors', {})
                entry.setdefault('actuators', [])

            if sensors:
                sensor_store = entry.setdefault('sensors', {})
                for sensor_id, meta in sensors.items():
                    sensor_store[sensor_id] = meta
            if isinstance(actuators, list):
                entry['actuators'] = actuators

            self._absorb_placeholder(device_id)

            payload = {
                'type': 'device',
                'device_id': device_id,
                'is_active': entry.get('is_active', True),
                'last_seen': int(entry['last_seen'].timestamp() * 1000) if isinstance(entry.get('last_seen'), datetime) else None,
                'sensors': entry.get('sensors', {}),
                'actuators': entry.get('actuators', []),
            }

            try:
                await event_broker.publish(payload)
            except Exception:
                pass

        except Exception as exc:
            logger.error(f"Error updating device discovery for {device_id}: {exc}")

    async def get_device_list(self) -> Dict[str, Dict[str, Any]]:
        """Get list of discovered devices with JSON-serialisable fields"""
        snapshot: Dict[str, Dict[str, Any]] = {}
        for device_id, info in self.device_registry.items():
            last_seen = info.get('last_seen')
            snapshot[device_id] = {
                'is_active': info.get('is_active', True),
                'last_seen': int(last_seen.timestamp() * 1000) if isinstance(last_seen, datetime) else None,
                'sensors': deepcopy(info.get('sensors', {})),
                'actuators': deepcopy(info.get('actuators', [])),
            }
        return snapshot

    async def mark_inactive_devices(self):
        """Mark devices as inactive if not seen for a while"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(seconds=settings.sensor_discovery_timeout)

            await mark_devices_inactive(cutoff_time)

            inactive_devices = []
            for device_id, info in self.device_registry.items():
                last_seen = info.get('last_seen')
                if isinstance(last_seen, datetime) and last_seen < cutoff_time:
                    inactive_devices.append(device_id)

            for device_id in inactive_devices:
                entry = self.device_registry[device_id]
                entry['is_active'] = False
                try:
                    await event_broker.publish({
                        'type': 'device',
                        'device_id': device_id,
                        'is_active': False,
                        'last_seen': int(entry['last_seen'].timestamp() * 1000) if isinstance(entry.get('last_seen'), datetime) else None,
                        'sensors': entry.get('sensors', {}),
                        'actuators': entry.get('actuators', []),
                    })
                except Exception:
                    pass

        except Exception as exc:
            logger.error(f"Error marking inactive devices: {exc}")

    async def publish_relay_control(self, device_id: str, relay_control: RelayControl):
        """Publish relay control command to ESP32"""
        if not self.client or not self.is_connected:
            raise Exception("MQTT client not connected")

        try:
            topic = f"esp32/{device_id}/control"
            payload = {
                "relay": relay_control.relay,
                "state": relay_control.state,
            }

            self.client.publish(topic, json.dumps(payload), qos=settings.mqtt_qos)
            logger.info(f"Published relay control command to {topic}: {payload}")

        except Exception as exc:
            logger.error(f"Error publishing relay control: {exc}")
            raise

    async def disconnect(self):
        """Disconnect from MQTT broker"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_connected = False
            logger.info("Disconnected from MQTT broker")

# Global MQTT client instance
mqtt_client = MQTTClient()
