import asyncio
import json
import queue
import re
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Set

import paho.mqtt.client as mqtt
from loguru import logger
from sqlalchemy import select

from .config import settings
from .database import AsyncSessionLocal
from .events import event_broker
from .metrics import build_metric_meta
from .models import ActuatorControl, Device, Metric, Reading
from .services.persistence import (
    get_metric_map,
    get_metric_by_key,
    insert_reading,
    mark_devices_inactive,
    sync_device_metrics,
    upsert_device,
)

class MQTTClient:
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self.is_connected = False
        self.last_seen: Dict[str, datetime] = {}
        self.message_handlers: Dict[str, Callable] = {}
        self.message_queue = queue.Queue()
        self.processing_task = None
        base_topic = settings.mqtt_base_topic.strip('/')
        self.base_topic_parts = base_topic.split('/') if base_topic else []
        self._setup_handlers()
        self.device_db_ids: Dict[str, int] = {}
        self.metric_cache: Dict[str, Dict[str, int]] = {}
        # In-memory cache for latest values: device_key -> metric_key -> latest_value
        self.values_cache: Dict[str, Dict[str, Any]] = {}
        # Track which devices have completed discovery
        self.discovery_completed: Set[str] = set()
        # Actuator publish rate limiting
        self.actuator_rate_limit = settings.actuator_publish_rate_hz
        self.actuator_buckets: Dict[str, Dict[str, Any]] = {}
        self._actuator_publish_lock = asyncio.Lock()

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

    async def populate_cache_from_db(self):
        """Bootstrap the in-memory values cache from database latest readings."""
        try:
            async with AsyncSessionLocal() as db:
                latest_rows = await self._latest_metric_rows_for_cache(db)
                for device_key, metric_key, value in latest_rows:
                    device_cache = self.values_cache.setdefault(device_key, {})
                    device_cache[metric_key] = value
                logger.info(f"Populated cache with {len(latest_rows)} latest metric values")
        except Exception as exc:
            logger.error(f"Failed to populate cache from database: {exc}")

    async def _latest_metric_rows_for_cache(self, db):
        """Get latest reading for each metric across all devices."""
        from sqlalchemy import func, select
        subquery = (
            select(Reading.metric_id, func.max(Reading.timestamp).label('latest_ts'))
            .group_by(Reading.metric_id)
            .subquery()
        )

        query = (
            select(
                Device.device_key,
                Metric.metric_key,
                Reading.value,
            )
            .join(Metric, Metric.device_id == Device.id)
            .join(Reading, Reading.metric_id == Metric.id)
            .join(
                subquery,
                (Reading.metric_id == subquery.c.metric_id)
                & (Reading.timestamp == subquery.c.latest_ts),
            )
        )

        result = await db.execute(query)
        return result.all()

    def _update_cache_value(self, device_key: str, metric_key: str, value: Any):
        """Update a single value in the cache."""
        device_cache = self.values_cache.setdefault(device_key, {})
        device_cache[metric_key] = value

    def get_cached_values(self) -> Dict[str, Dict[str, Any]]:
        """Get a copy of the current values cache."""
        return {
            device_key: device_values.copy()
            for device_key, device_values in self.values_cache.items()
        }

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

    async def _touch_device(
        self,
        device_key: str,
        *,
        metadata: Optional[Any] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ) -> Device:
        """Ensure a device record exists and refresh last_seen metadata."""
        current_time = last_seen or datetime.utcnow()
        device = await self._ensure_device_record(
            device_key,
            name=name,
            description=description,
            metadata=metadata,
            last_seen=current_time,
        )
        self.last_seen[device_key] = current_time
        return device

    async def _build_metric_snapshot(self, device_key: str) -> Dict[str, Dict[str, Any]]:
        metrics = await get_metric_map(device_key)
        snapshot: Dict[str, Dict[str, Any]] = {}
        for metric in metrics.values():
            overrides: Dict[str, Any] = {}
            if metric.display_name:
                overrides['label'] = metric.display_name
            if metric.unit:
                overrides['unit'] = metric.unit
            meta = build_metric_meta(metric.metric_key, overrides)
            snapshot[metric.metric_key] = {
                'id': meta.id,
                'label': meta.label,
                'unit': meta.unit,
                'color': meta.color,
            }
        return snapshot

    async def _build_metric_snapshots(self, device_key: str) -> tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
        """Build separate snapshots for sensors and actuators."""
        metrics = await get_metric_map(device_key)
        sensors: Dict[str, Dict[str, Any]] = {}
        actuators: Dict[str, Dict[str, Any]] = {}

        for metric in metrics.values():
            overrides: Dict[str, Any] = {}
            if metric.display_name:
                overrides['label'] = metric.display_name
            if metric.unit:
                overrides['unit'] = metric.unit
            meta = build_metric_meta(metric.metric_key, overrides)

            metric_info = {
                'id': meta.id,
                'label': meta.label,
                'unit': meta.unit,
                'color': meta.color,
            }

            if metric.metric_type == 'sensor':
                sensors[metric.metric_key] = metric_info
            elif metric.metric_type == 'actuator':
                actuators[metric.metric_key] = metric_info

        return sensors, actuators

    async def _publish_device_event(self, device: Device) -> None:
        try:
            sensors, actuators = await self._build_metric_snapshots(device.device_key)
            payload = {
                'type': 'device',
                'device_id': device.device_key,
                'is_active': device.is_active,
                'last_seen': int(device.last_seen.timestamp() * 1000) if device.last_seen else None,
                'sensors': sensors,
                'actuators': actuators,
            }
            await event_broker.publish(payload)
        except Exception as exc:
            logger.debug(f"Failed to publish device event for {device.device_key}: {exc}")

    def _collect_metric_definitions(
        self,
        sensors_payload: Any = None,
        actuators_payload: Any = None,
    ) -> List[Dict[str, Optional[str]]]:
        definitions: List[Dict[str, Optional[str]]] = []
        seen: Set[str] = set()

        def add_definition(metric_key: Optional[str], label: Optional[str], unit: Optional[str], metric_type: str) -> None:
            if not metric_key:
                return
            key = str(metric_key).strip()
            if not key or key in seen:
                return
            overrides: Dict[str, Any] = {}
            if label:
                overrides['label'] = label
            if unit:
                overrides['unit'] = unit
            meta = build_metric_meta(key, overrides if overrides else None)
            definitions.append(
                {
                    'metric_key': key,
                    'display_name': meta.label,
                    'unit': meta.unit,
                    'metric_type': metric_type,
                }
            )
            seen.add(key)

        if isinstance(sensors_payload, list):
            for item in sensors_payload:
                if isinstance(item, dict):
                    add_definition(item.get('id'), item.get('label'), item.get('unit'), 'sensor')
        elif isinstance(sensors_payload, dict):
            for sensor_id, info in sensors_payload.items():
                label = info.get('label') if isinstance(info, dict) else None
                unit = info.get('unit') if isinstance(info, dict) else None
                add_definition(sensor_id, label, unit, 'sensor')

        if isinstance(actuators_payload, list):
            for entry in actuators_payload:
                if isinstance(entry, dict):
                    # Handle different actuator formats
                    actuator_id = entry.get('id') or entry.get('key')

                    # Handle ESP32 relay format: {"type": "relay", "number": 1, "label": "Relay 1"}
                    if not actuator_id and entry.get('type') == 'relay' and 'number' in entry:
                        actuator_id = f"relay{entry['number']}"

                    add_definition(actuator_id, entry.get('label'), entry.get('unit'), 'actuator')
        elif isinstance(actuators_payload, dict):
            for actuator_id, info in actuators_payload.items():
                label = info.get('label') if isinstance(info, dict) else None
                unit = info.get('unit') if isinstance(info, dict) else None
                add_definition(actuator_id, label, unit, 'actuator')

        return definitions

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
                (f"{settings.mqtt_base_topic}/+/actuators", settings.mqtt_qos),  # Device-specific actuator state
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
        """Handle sensor data messages."""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            device_id = derived_id or self._device_id_from_topic(topic)
            if not device_id:
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

            # Wait for discovery to complete before processing sensor data
            if device_id not in self.discovery_completed:
                logger.debug(f'Skipping sensor data for {device_id} - discovery not yet completed')
                return

            sensors = self._flatten_sensor_payload(data)
            if not sensors:
                await self._publish_error(
                    'empty_sensor_payload',
                    'Sensor data contained no usable sensors; payload ignored.',
                    {'topic': topic, 'device_id': device_id},
                )
                return

            timestamp = datetime.utcnow()
            device = await self._touch_device(device_id, last_seen=timestamp)

            for sensor_name, value in sensors.items():
                meta = build_metric_meta(sensor_name)
                metric_id = await self._ensure_metric_id(
                    device_id,
                    sensor_name,
                    label=meta.label,
                    unit=meta.unit,
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
                    # Update in-memory cache
                    self._update_cache_value(device_id, sensor_name, value)
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

            await self._publish_device_event(device)
            logger.debug(f"Processed sensor data for device {device_id}")

        except Exception as exc:
            logger.error(f"Error handling sensor data: {exc}")

    async def _handle_relay_status(self, topic: str, data: Dict[str, Any]):
        """Handle relay status messages."""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            device_id = derived_id or self._device_id_from_topic(topic)
            if not device_id:
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

            items = data.items() if isinstance(data, dict) else []
            relay_values: Dict[str, Any] = {}
            for key, value in items:
                if not isinstance(key, str) or key == 'device_id' or not key.startswith('relay'):
                    continue
                relay_values[key] = value

            if not relay_values:
                return

            timestamp = datetime.utcnow()
            device = await self._touch_device(device_id, last_seen=timestamp)
            metric_map = await get_metric_map(device_id)

            for relay_key, value in relay_values.items():
                relay_num: Optional[int] = None
                if relay_key.startswith('relay'):
                    try:
                        relay_num = int(relay_key.replace('relay', ''))
                    except ValueError:
                        relay_num = None

                metric = metric_map.get(relay_key)
                label = metric.display_name if metric and metric.display_name else None
                unit = metric.unit if metric and metric.unit else ''
                if not label and relay_num is not None:
                    label = f"Relay {relay_num}"
                label = label or relay_key

                metric_id = await self._ensure_metric_id(
                    device_id,
                    relay_key,
                    label=label,
                    unit=unit,
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
                    # Update in-memory cache
                    self._update_cache_value(device_id, relay_key, value)
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
                'actuators': relay_values,
            })

            await self._publish_device_event(device)
            logger.debug(f"Processed relay status for device {device_id}")

        except Exception as exc:
            logger.error(f"Error handling relay status: {exc}")

    async def _handle_critical_relays(self, topic: str, data: Dict[str, Any]):
        """Handle legacy critical relay updates."""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            device_id = derived_id or self._device_id_from_topic(topic)
            if not device_id or device_id == 'critical_relays':
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

            relay_key = data.get('relay', '') if isinstance(data, dict) else ''
            if not isinstance(relay_key, str) or not relay_key.startswith('relay'):
                return

            try:
                relay_num = int(relay_key.replace('relay', ''))
            except ValueError:
                relay_num = None

            raw_state = data.get('state') if isinstance(data, dict) else None
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

            timestamp = datetime.utcnow()
            device = await self._touch_device(device_id, last_seen=timestamp)
            metric_map = await get_metric_map(device_id)

            metric = metric_map.get(relay_key)
            label = metric.display_name if metric and metric.display_name else None
            unit = metric.unit if metric and metric.unit else ''
            if not label and relay_num is not None:
                label = f"Relay {relay_num}"
            label = label or relay_key

            metric_id = await self._ensure_metric_id(
                device_id,
                relay_key,
                label=label,
                unit=unit,
            )
            if metric_id is None:
                logger.warning(
                    'No metric registered for critical relay state; skipping reading',
                    device_id=device_id,
                    relay=relay_key,
                )
                return

            try:
                await insert_reading(metric_id, state_value, timestamp=timestamp)
                # Update in-memory cache
                self._update_cache_value(device_id, relay_key, state_value)
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
                'actuators': {relay_key: state_value},
            })

            await self._publish_device_event(device)

        except Exception as exc:
            logger.error(f"Error handling critical relay update: {exc}")

    async def _handle_device_status_msg(self, topic: str, data: Dict[str, Any]):
        """Handle device status messages."""
        try:
            derived_id = data.get('device_id') if isinstance(data, dict) else None
            device_id = derived_id or self._device_id_from_topic(topic)
            if not device_id:
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

            metadata = {'status': data} if data is not None else None
            device = await self._touch_device(device_id, metadata=metadata)
            await self._publish_device_event(device)

        except Exception as exc:
            logger.error(f"Error handling device status: {exc}")

    async def _handle_dynamic_topic(self, topic: str, data: Dict[str, Any]):
        """Handle dynamic device topics for autodiscovery and status updates."""
        try:
            parts = topic.split('/')
            base_parts = self.base_topic_parts
            base_len = len(base_parts)

            if base_len == 0 or len(parts) <= base_len + 1 or parts[:base_len] != base_parts:
                return

            device_id = parts[base_len]
            message_type = parts[base_len + 1]
            canonical_id = (data.get('device_id') if isinstance(data, dict) else None) or device_id

            if message_type == 'data':
                payload = {**data, 'device_id': canonical_id} if isinstance(data, dict) else {'device_id': canonical_id}
                await self._handle_sensor_data(topic, payload)
            elif message_type == 'status':
                payload = {**data, 'device_id': canonical_id} if isinstance(data, dict) else {'device_id': canonical_id}
                await self._handle_device_status_msg(topic, payload)
            elif message_type == 'discovery':
                await self._handle_discovery(canonical_id, data, topic=topic)
            elif message_type == 'heartbeat':
                if canonical_id:
                    await self._handle_heartbeat(canonical_id, data if isinstance(data, dict) else {})
            elif message_type == 'relay' and len(parts) >= base_len + 3 and parts[base_len + 2] == 'status':
                payload = {**data, 'device_id': canonical_id} if isinstance(data, dict) else {'device_id': canonical_id}
                await self._handle_relay_status(topic, payload)
            elif message_type == 'actuators':
                payload = {**data, 'device_id': canonical_id} if isinstance(data, dict) else {'device_id': canonical_id}
                await self._handle_relay_status(topic, payload)
            else:
                if canonical_id:
                    metadata = data if isinstance(data, dict) else None
                    device = await self._touch_device(canonical_id, metadata=metadata)
                    await self._publish_device_event(device)

        except Exception as exc:
            logger.error(f"Error handling dynamic topic {topic}: {exc}")

    async def _handle_heartbeat(self, device_id: str, data: Dict[str, Any]):
        """Handle lightweight heartbeat responses from discovery ping."""
        try:
            current_time = datetime.utcnow()
            device = await self._touch_device(device_id, last_seen=current_time)
            logger.debug(f"Heartbeat received from device {device_id}")
            await self._publish_device_event(device)

        except Exception as exc:
            logger.error(f"Error handling heartbeat from {device_id}: {exc}")

    async def _handle_discovery(self, device_id: str, data: Dict[str, Any], topic: Optional[str] = None):
        """Handle capability discovery payloads from devices."""
        try:
            derived_id = device_id or (data.get('device_id') if isinstance(data, dict) else None) or (self._device_id_from_topic(topic) if topic else None)
            canonical_id = derived_id
            if not canonical_id:
                logger.warning('Discovery payload missing device_id; emitted error and skipped payload', topic=topic)
                await self._publish_error(
                    'missing_device_id',
                    'Discovery payload missing device_id; payload ignored.',
                    {
                        'topic': topic,
                        'payload_keys': list(data.keys()) if isinstance(data, dict) else [],
                        'device_id': derived_id,
                    },
                )
                return

            metadata = data if isinstance(data, dict) else None
            name = None
            description = None
            if metadata:
                name = metadata.get('name') or metadata.get('device_name')
                description = metadata.get('description')
            definitions = self._collect_metric_definitions(
                metadata.get('sensors') if metadata else None,
                metadata.get('actuators') if metadata else None,
            )

            device = await self._touch_device(
                canonical_id,
                metadata=metadata,
                name=name,
                description=description,
            )

            if definitions:
                await self._sync_metric_definitions(canonical_id, definitions)

            await self._publish_device_event(device)

            # Mark discovery as completed for this device
            self.discovery_completed.add(canonical_id)
            logger.info(f"Discovery completed for device {canonical_id}")

        except Exception as exc:
            logger.error(f"Error handling discovery payload for {device_id}: {exc}")

    async def mark_inactive_devices(self):
        """Mark MQTT devices as inactive if not seen for a while. Cameras are managed separately."""
        try:
            cutoff_time = datetime.utcnow() - timedelta(seconds=settings.sensor_discovery_timeout)

            # Only mark MQTT sensor devices as inactive (cameras have their own heartbeat)
            await mark_devices_inactive(cutoff_time, device_type='mqtt_sensor')

            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Device).where(Device.last_seen < cutoff_time)
                )
                inactive = result.scalars().all()

            for device in inactive:
                try:
                    sensors = await self._build_metric_snapshot(device.device_key)
                    await event_broker.publish({
                        'type': 'device',
                        'device_id': device.device_key,
                        'is_active': device.is_active,
                        'last_seen': int(device.last_seen.timestamp() * 1000) if device.last_seen else None,
                        'sensors': sensors,
                        'actuators': [],
                    })
                except Exception:
                    pass

        except Exception as exc:
            logger.error(f"Error marking inactive devices: {exc}")

    async def publish_actuator_control(self, device_id: str, actuator_control: ActuatorControl):
        """Publish generic actuator control command to ESP32"""
        if not self.client or not self.is_connected:
            raise Exception("MQTT client not connected")

        try:
            topic_template = settings.relay_control_topic.strip()
            resolved_topics: Set[str] = set()

            # Normalize device identifier for topic usage (replace whitespace / disallowed chars)
            normalized_device_id = re.sub(r"[^A-Za-z0-9_\-]", "_", device_id.strip()) or device_id

            # Always include device-specific control topic expected by ESP32 firmware
            base_topic = settings.mqtt_base_topic.strip('/')
            resolved_topics.add(f"{base_topic}/{normalized_device_id}/control")

            # Honour optional template/environment override for additional topics
            if topic_template:
                formatted = topic_template
                if "{device_id}" in topic_template:
                    formatted = topic_template.format(device_id=normalized_device_id)
                resolved_topics.add(formatted)

            payload: Dict[str, Any] = {
                "device_id": device_id,
                "actuator": actuator_control.actuator_key,
                "state": actuator_control.state,
            }

            # Provide numeric relay identifier when using conventional relay keys (e.g. relay1)
            if actuator_control.actuator_key.lower().startswith("relay"):
                suffix = actuator_control.actuator_key[5:]
                if suffix.isdigit():
                    payload["relay"] = int(suffix)

            # Publish using the rate-limited topic aggregator
            for topic in sorted(resolved_topics):
                normalized_topic = topic.strip('/')
                await self._publish_rate_limited(normalized_topic, payload)

        except Exception as exc:
            logger.error(f"Error publishing actuator control: {exc}")
            raise

    async def _publish_rate_limited(self, topic: str, payload: Dict[str, Any]):
        """Aggregate actuator payloads per topic and publish at a limited rate."""
        bucket_key = topic
        now = datetime.utcnow()
        async with self._actuator_publish_lock:
            bucket = self.actuator_buckets.get(bucket_key)
            if not bucket:
                bucket = {
                    'last_sent': None,
                    'accumulator': {},
                }
                self.actuator_buckets[bucket_key] = bucket

            # Merge into accumulator (one entry per actuator key)
            actuator = payload.get('actuator')
            if actuator:
                bucket['accumulator'][actuator] = payload
            else:
                # Fallback: store as unique key
                bucket['accumulator'][json.dumps(payload, sort_keys=True)] = payload

            # Determine if we can send immediately
            min_interval = 1.0 / max(1.0, float(self.actuator_rate_limit))
            last_sent = bucket['last_sent']
            delta = (now - last_sent).total_seconds() if last_sent else None

            if last_sent is None or (delta is not None and delta >= min_interval):
                await self._flush_actuator_bucket(topic, bucket, now)
            else:
                # Schedule future flush
                delay = max(0.0, min_interval - delta)
                asyncio.create_task(self._delayed_flush(topic, delay))

    async def _delayed_flush(self, topic: str, delay: float):
        await asyncio.sleep(delay)
        async with self._actuator_publish_lock:
            bucket = self.actuator_buckets.get(topic)
            if not bucket or not bucket['accumulator']:
                return
            await self._flush_actuator_bucket(topic, bucket, datetime.utcnow())

    async def _flush_actuator_bucket(self, topic: str, bucket: Dict[str, Any], timestamp: datetime):
        accumulator = bucket['accumulator']
        if not accumulator:
            return

        payloads = list(accumulator.values())
        bucket['accumulator'] = {}
        bucket['last_sent'] = timestamp

        if len(payloads) == 1:
            payload_to_send = payloads[0]
        else:
            payload_to_send = {
                'device_id': payloads[0].get('device_id'),
                'batched': True,
                'commands': payloads,
            }

        try:
            message = json.dumps(payload_to_send)
            self.client.publish(topic, message, qos=settings.mqtt_qos)
            logger.info(f"Published actuator command to {topic}: {payload_to_send}")
        except Exception as exc:
            logger.error(f"Failed to publish actuator command on topic {topic}: {exc}")
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
