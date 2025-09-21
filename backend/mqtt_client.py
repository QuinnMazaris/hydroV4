import asyncio
import json
import queue
from copy import deepcopy
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

import paho.mqtt.client as mqtt
from loguru import logger
from sqlalchemy import select

from .config import settings
from .database import AsyncSessionLocal
from .events import event_broker
from .metrics import build_metric_meta
from .models import (
    ActuatorStateCreate,
    Device,
    DeviceCreate,
    RelayControl,
    SensorReadingCreate,
)
from .services.persistence import mark_devices_inactive, save_actuator_state, save_sensor_reading

DEFAULT_DEVICE_TYPE = 'sensor'
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

    def _extract_numeric_metrics(self, data: Dict[str, Any]) -> Dict[str, float]:
        """Flatten incoming sensor payload and extract numeric metrics dynamically.
        Supports one level of nesting for common sensor groups (e.g., bme680).
        """
        metrics: Dict[str, float] = {}
        try:
            if not isinstance(data, dict):
                return metrics
            for key, value in data.items():
                if key == 'device_id':
                    continue
                if isinstance(value, (int, float)):
                    metrics[key] = float(value)
                elif isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        if isinstance(sub_val, (int, float)):
                            # compose metric name as nested_key
                            metrics[sub_key if key in ['bme680'] else f"{key}_{sub_key}"] = float(sub_val)
        except Exception:
            pass
        return metrics

    def _setup_handlers(self):
        """Setup message handlers for different topics"""
        self.message_handlers = {
            settings.sensor_data_topic: self._handle_sensor_data,
            settings.relay_status_topic: self._handle_relay_status,
            "esp32/critical_relays": self._handle_critical_relays,
            "esp32/status": self._handle_device_status_msg,
        }

    def _metric_descriptor(self, metric_id: str, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        overrides = overrides or {}
        meta = build_metric_meta(metric_id, overrides)
        return {
            'id': meta.id,
            'label': meta.label,
            'unit': meta.unit,
            'color': meta.color,
        }

    def _normalize_metric_payload(self, payload: Any) -> Dict[str, Dict[str, Any]]:
        definitions: Dict[str, Dict[str, Any]] = {}
        if isinstance(payload, list):
            for item in payload:
                if not isinstance(item, dict):
                    continue
                metric_id = item.get('id')
                if not metric_id:
                    continue
                overrides = {k: item.get(k) for k in ('label', 'unit', 'color') if item.get(k)}
                definitions[metric_id] = self._metric_descriptor(metric_id, overrides)
        elif isinstance(payload, dict):
            for metric_id, info in payload.items():
                overrides = {}
                if isinstance(info, dict):
                    overrides = {k: info.get(k) for k in ('label', 'unit', 'color') if info.get(k)}
                definitions[metric_id] = self._metric_descriptor(metric_id, overrides)
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
            payload = json.dumps({'request': 'capabilities'})
            self.client.publish(settings.discovery_request_topic, payload, qos=settings.mqtt_qos)
        except Exception as exc:
            logger.error(f"Failed to request discovery broadcast: {exc}")

    def request_device_discovery(self, device_id: str) -> None:
        if not self.client:
            return
        topic = f"{settings.mqtt_base_topic}/{device_id}/discover"
        try:
            payload = json.dumps({'request': 'capabilities'})
            self.client.publish(topic, payload, qos=settings.mqtt_qos)
        except Exception as exc:
            logger.error(f"Failed to request discovery for {device_id}: {exc}")

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
        entry['metrics'] = {**placeholder.get('metrics', {}), **entry.get('metrics', {})}
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
                (settings.discovery_response_topic, settings.mqtt_qos),         # Capability discovery
                (f"{settings.mqtt_base_topic}/+/status", settings.mqtt_qos),  # Device status
                (f"{settings.mqtt_base_topic}/+/data", settings.mqtt_qos),    # Device data
                (f"{settings.mqtt_base_topic}/+/discovery", settings.mqtt_qos),  # Device capabilities
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
            derived_id = data.get('device_id') or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Sensor data missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Sensor data missing device_id; payload ignored.',
                    {'topic': topic, 'payload_keys': list(data.keys()) if isinstance(data, dict) else [], 'device_id': derived_id},
                )
                self.request_discovery_broadcast()
                return
            data = {**data, 'device_id': device_id}

            metrics = self._extract_numeric_metrics(data)
            if not metrics:
                await self._publish_error(
                    'empty_sensor_payload',
                    'Sensor data contained no numeric metrics; payload ignored.',
                    {'topic': topic, 'device_id': device_id},
                )
                return

            registry_metrics = self.device_registry.get(device_id, {}).get('metrics', {}) or {}
            metric_defs: Dict[str, Dict[str, Any]] = {}
            for metric_id in metrics.keys():
                existing_meta = registry_metrics.get(metric_id) if isinstance(registry_metrics, dict) else None
                if isinstance(existing_meta, dict):
                    metric_defs[metric_id] = existing_meta
                else:
                    metric_defs[metric_id] = self._metric_descriptor(metric_id)

            await self._update_device_discovery(device_id, 'sensor', data, metrics=metric_defs)

            reading_data = {
                'device_id': device_id,
                'temperature': data.get('bme680', {}).get('temperature'),
                'pressure': data.get('bme680', {}).get('pressure'),
                'humidity': data.get('bme680', {}).get('humidity'),
                'gas_kohms': data.get('bme680', {}).get('gas_kohms'),
                'lux': data.get('lux'),
                'water_temp_c': data.get('water_temp_c'),
                'tds_ppm': data.get('tds_ppm'),
                'ph': data.get('ph'),
                'distance_mm': data.get('distance_mm'),
                'vpd_kpa': data.get('vpd_kpa'),
                'raw_data': json.dumps(data, default=str),
            }
            reading_data = {k: v for k, v in reading_data.items() if v is not None}
            if reading_data:
                try:
                    await save_sensor_reading(SensorReadingCreate(**reading_data))
                except Exception as exc:
                    logger.error(f"Failed to persist sensor reading for {device_id}: {exc}")
                    await self._publish_error(
                        'sensor_persist_failed',
                        'Failed to persist sensor reading; continuing with live stream.',
                        {'device_id': device_id, 'topic': topic},
                    )

            now_dt = datetime.utcnow()

            await event_broker.publish({
                'type': 'reading',
                'device_id': device_id,
                'timestamp': int(now_dt.timestamp() * 1000),
                'metrics': metrics,
            })

            logger.debug(f"Processed sensor data for device {device_id}")

        except Exception as e:
            logger.error(f"Error handling sensor data: {e}")

    async def _handle_relay_status(self, topic: str, data: Dict[str, Any]):
        """Handle relay status messages"""
        try:
            derived_id = data.get('device_id') or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Relay status missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Relay status missing device_id; payload ignored.',
                    {'topic': topic, 'payload_keys': list(data.keys()) if isinstance(data, dict) else [], 'device_id': derived_id},
                )
                self.request_discovery_broadcast()
                return
            data = {**data, 'device_id': device_id}

            actuator_defs: List[Dict[str, Any]] = []
            existing = {
                (entry.get('id') or f"relay{entry.get('number')}"): entry
                for entry in self.device_registry.get(device_id, {}).get('actuators', []) or []
            }
            for relay_key, state in data.items():
                if relay_key.startswith('relay') and relay_key != 'device_id':
                    try:
                        relay_num = int(relay_key.replace('relay', ''))
                    except ValueError:
                        continue

                    prior = existing.get(relay_key) or existing.get(f'relay{relay_num}') or {}
                    actuator_defs.append({
                        'id': relay_key,
                        'type': 'relay',
                        'number': relay_num,
                        'label': prior.get('label') or f"Relay {relay_num}",
                        'state': state,
                    })

                    actuator_data = ActuatorStateCreate(
                        device_id=device_id,
                        actuator_type='relay',
                        actuator_number=relay_num,
                        state=state,
                        command_source='device_report'
                    )

                    await save_actuator_state(actuator_data)

            await self._update_device_discovery(device_id, 'actuator', data, actuators=actuator_defs)

            logger.debug(f"Processed relay status for device {device_id}")

        except Exception as e:
            logger.error(f"Error handling relay status: {e}")

    async def _handle_critical_relays(self, topic: str, data: Dict[str, Any]):
        """Handle legacy critical relay updates."""
        try:
            derived_id = data.get('device_id') or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id in {PLACEHOLDER_DEVICE_ID, 'critical_relays'}:
                logger.warning('Critical relay update missing device identity; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Critical relay update missing device_id; payload ignored.',
                    {'topic': topic, 'payload_keys': list(data.keys()) if isinstance(data, dict) else [], 'device_id': derived_id},
                )
                self.request_discovery_broadcast()
                return

            relay_defs: List[Dict[str, Any]] = []

            relay_key = data.get('relay', '')
            if relay_key.startswith('relay'):
                try:
                    relay_num = int(relay_key.replace('relay', ''))
                except ValueError:
                    relay_num = None
                state = 'on' if data.get('state') else 'off'

                if relay_num is not None:
                    existing = {
                        (entry.get('id') or f"relay{entry.get('number')}"): entry
                        for entry in self.device_registry.get(device_id, {}).get('actuators', []) or []
                    }
                    prior = existing.get(relay_key) or existing.get(f'relay{relay_num}') or {}
                    relay_defs.append({
                        'id': relay_key,
                        'type': 'relay',
                        'number': relay_num,
                        'label': prior.get('label') or f"Relay {relay_num}",
                        'state': state,
                    })

                    actuator_data = ActuatorStateCreate(
                        device_id=device_id,
                        actuator_type='relay',
                        actuator_number=relay_num,
                        state=state,
                        command_source='critical_update'
                    )

                    await save_actuator_state(actuator_data)

            if relay_defs:
                await self._update_device_discovery(device_id, 'actuator', data, actuators=relay_defs)

        except Exception as e:
            logger.error(f"Error handling critical relay update: {e}")

    async def _handle_device_status_msg(self, topic: str, data: Dict[str, Any]):
        """Handle device status messages"""
        try:
            derived_id = data.get('device_id') or self._device_id_from_topic(topic)
            device_id = self._canonical_device_id(derived_id)
            if device_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Status update missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Status update missing device_id; payload ignored.',
                    {'topic': topic, 'payload_keys': list(data.keys()) if isinstance(data, dict) else [], 'device_id': derived_id},
                )
                self.request_discovery_broadcast()
                return

            await self._update_device_discovery(device_id, 'status', {'status': data})

        except Exception as e:
            logger.error(f"Error handling device status: {e}")

    async def _handle_dynamic_topic(self, topic: str, data: Dict[str, Any]):
        """Handle dynamic device topics for autodiscovery and status updates"""
        try:
            parts = topic.split('/')
            base_parts = self.base_topic_parts
            base_len = len(base_parts)

            if base_len == 0:
                return
            if len(parts) <= base_len + 1:
                return
            if parts[:base_len] != base_parts:
                return

            device_id = parts[base_len]
            message_type = parts[base_len + 1]
            canonical_id = self._canonical_device_id(data.get('device_id') or device_id)

            if message_type == 'data':
                await self._handle_sensor_data(topic, {**data, 'device_id': canonical_id})
            elif message_type == 'status':
                await self._handle_device_status(canonical_id, data)
            elif message_type == 'discovery':
                await self._handle_discovery(canonical_id, data, topic=topic)
            elif message_type == 'relay' and len(parts) >= base_len + 3 and parts[base_len + 2] == 'status':
                payload = {**data, 'device_id': canonical_id}
                await self._handle_relay_status(topic, payload)
            else:
                await self._update_device_discovery(canonical_id, 'unknown', data)

        except Exception as e:
            logger.error(f"Error handling dynamic topic {topic}: {e}")
    async def _handle_device_status(self, device_id: str, data: Dict[str, Any]):
        """Handle device status messages for heartbeat"""
        await self._update_device_discovery(device_id, 'status', data)

    async def _handle_discovery(self, device_id: str, data: Dict[str, Any], topic: Optional[str] = None):
        """Handle capability discovery payloads from devices."""
        try:
            derived_id = device_id or data.get('device_id') or (self._device_id_from_topic(topic) if topic else None)
            canonical_id = self._canonical_device_id(derived_id)
            if canonical_id == PLACEHOLDER_DEVICE_ID:
                logger.warning('Discovery payload missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Discovery payload missing device_id; payload ignored.',
                    {'topic': topic, 'payload_keys': list(data.keys()) if isinstance(data, dict) else [], 'device_id': derived_id},
                )
                self.request_discovery_broadcast()
                return

            metrics = self._normalize_metric_payload(data.get('metrics', {}))
            actuators = self._normalize_actuator_payload(data.get('actuators', []))
            device_type = data.get('device_type', 'sensor')
            await self._update_device_discovery(
                canonical_id,
                device_type,
                data,
                metrics=metrics,
                actuators=actuators,
            )
        except Exception as exc:
            logger.error(f"Error handling discovery payload for {device_id}: {exc}")

    async def _update_device_discovery(
        self,
        device_id: str,
        device_type: str,
        data: Dict[str, Any],
        *,
        metrics: Optional[Dict[str, Dict[str, Any]]] = None,
        actuators: Optional[Any] = None,
    ) -> None:
        """Update device discovery registry and database with optional capability metadata."""
        try:
            current_time = datetime.utcnow()
            self.last_seen[device_id] = current_time

            stored_type = device_type if device_type not in {"unknown", "status"} else DEFAULT_DEVICE_TYPE

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Device).where(Device.device_id == device_id)
                )
                device = result.scalar_one_or_none()

                if device:
                    device.last_seen = current_time
                    device.is_active = True
                    if device_type not in {"unknown", "status"}:
                        device.device_type = device_type
                    if data:
                        device.device_metadata = json.dumps(data, default=str)
                else:
                    device_data = DeviceCreate(
                        device_id=device_id,
                        device_type=stored_type,
                        name=f"Auto-discovered {device_id}",
                        description=f"Automatically discovered device: {device_id}",
                        device_metadata=json.dumps(data, default=str) if data else None,
                    )
                    session.add(Device(**device_data.model_dump()))

                await session.commit()

            entry = self.device_registry.get(device_id)
            if not entry:
                entry = {
                    'device_type': stored_type,
                    'last_seen': current_time,
                    'is_active': True,
                    'data': data,
                    'metrics': {},
                    'actuators': [],
                }
                self.device_registry[device_id] = entry
                logger.info(f"Discovered new device: {device_id} (type: {stored_type})")
                self.request_device_discovery(device_id)
            else:
                entry['last_seen'] = current_time
                entry['is_active'] = True
                entry.setdefault('metrics', {})
                entry.setdefault('actuators', [])
                if device_type not in {"unknown", "status"}:
                    entry['device_type'] = stored_type
                if data:
                    entry['data'] = data

            self._absorb_placeholder(device_id)

            if metrics:
                metric_store = entry.setdefault('metrics', {})
                for metric_id, meta in metrics.items():
                    existing = metric_store.get(metric_id)
                    if existing != meta:
                        metric_store[metric_id] = meta

            if actuators:
                entry['actuators'] = actuators

            payload = {
                'type': 'device',
                'device_id': device_id,
                'device_type': entry.get('device_type', DEFAULT_DEVICE_TYPE),
                'is_active': entry.get('is_active', True),
                'last_seen': int(entry['last_seen'].timestamp() * 1000),
                'metrics': entry.get('metrics', {}),
                'actuators': entry.get('actuators', []),
            }

            try:
                await event_broker.publish(payload)
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Error updating device discovery for {device_id}: {e}")

    async def get_device_list(self) -> Dict[str, Dict[str, Any]]:
        """Get list of discovered devices with JSON-serialisable fields"""
        snapshot: Dict[str, Dict[str, Any]] = {}
        for device_id, info in self.device_registry.items():
            last_seen = info.get('last_seen')
            snapshot[device_id] = {
                'device_type': info.get('device_type', DEFAULT_DEVICE_TYPE),
                'is_active': info.get('is_active', True),
                'last_seen': int(last_seen.timestamp() * 1000) if isinstance(last_seen, datetime) else None,
                'metrics': deepcopy(info.get('metrics', {})),
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
                if info['last_seen'] < cutoff_time:
                    inactive_devices.append(device_id)

            for device_id in inactive_devices:
                entry = self.device_registry[device_id]
                entry['is_active'] = False
                try:
                    await event_broker.publish({
                        'type': 'device',
                        'device_id': device_id,
                        'device_type': entry.get('device_type', DEFAULT_DEVICE_TYPE),
                        'is_active': False,
                        'last_seen': int(entry['last_seen'].timestamp() * 1000),
                        'metrics': entry.get('metrics', {}),
                        'actuators': entry.get('actuators', []),
                    })
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error marking inactive devices: {e}")

    async def publish_relay_control(self, device_id: str, relay_control):
        """Publish relay control command to ESP32"""
        if not self.client or not self.is_connected:
            raise Exception("MQTT client not connected")

        try:
            topic = f"esp32/{device_id}/control"
            payload = {
                "relay": relay_control.relay,
                "state": relay_control.state
            }

            self.client.publish(topic, json.dumps(payload), qos=settings.mqtt_qos)
            logger.info(f"Published relay control command to {topic}: {payload}")

        except Exception as e:
            logger.error(f"Error publishing relay control: {e}")
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
