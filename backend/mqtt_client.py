import asyncio
import json
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable
from loguru import logger
import paho.mqtt.client as mqtt
from .config import settings
from .models import DeviceCreate, SensorReadingCreate, ActuatorStateCreate, RelayControl
from .database import AsyncSessionLocal
from sqlalchemy import select, update
from .models import Device, SensorReading, ActuatorState
import queue
from .events import event_broker

class MQTTClient:
    def __init__(self):
        self.client: Optional[mqtt.Client] = None
        self.is_connected = False
        self.device_registry: Dict[str, Dict[str, Any]] = {}
        self.last_seen: Dict[str, datetime] = {}
        self.message_handlers: Dict[str, Callable] = {}
        self.message_queue = queue.Queue()
        self.processing_task = None
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
            # Extract device ID from data or topic
            device_id = data.get('device_id', 'esp32_main')


            # Update device discovery
            await self._update_device_discovery(device_id, 'sensor', data)

            # Create sensor reading
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
                'raw_data': json.dumps(data) if data else None
            }

            # Remove None values
            reading_data = {k: v for k, v in reading_data.items() if v is not None}

            # Save to database
            now_dt = datetime.utcnow()
            await self._save_sensor_reading(SensorReadingCreate(**reading_data))

            # Publish live event to broker
            metrics = self._extract_numeric_metrics(data)
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
            device_id = data.get('device_id', 'esp32_main')


            # Update device discovery
            await self._update_device_discovery(device_id, 'actuator', data)

            # Process relay states
            for relay_key, state in data.items():
                if relay_key.startswith('relay') and relay_key != 'device_id':
                    relay_num = int(relay_key.replace('relay', ''))

                    actuator_data = ActuatorStateCreate(
                        device_id=device_id,
                        actuator_type='relay',
                        actuator_number=relay_num,
                        state=state,
                        command_source='device_report'
                    )

                    await self._save_actuator_state(actuator_data)

            logger.debug(f"Processed relay status for device {device_id}")

        except Exception as e:
            logger.error(f"Error handling relay status: {e}")

    async def _handle_critical_relays(self, topic: str, data: Dict[str, Any]):
        """Handle critical relay updates"""
        try:
            device_id = "esp32_main"  # Assume main device for critical relays


            # Update device discovery
            await self._update_device_discovery(device_id, 'actuator', data)

            # Parse relay info
            relay_key = data.get('relay', '')
            if relay_key.startswith('relay'):
                relay_num = int(relay_key.replace('relay', ''))
                state = 'on' if data.get('state') else 'off'

                actuator_data = ActuatorStateCreate(
                    device_id=device_id,
                    actuator_type='relay',
                    actuator_number=relay_num,
                    state=state,
                    command_source='critical_update'
                )

                await self._save_actuator_state(actuator_data)

        except Exception as e:
            logger.error(f"Error handling critical relay update: {e}")

    async def _handle_device_status_msg(self, topic: str, data: Dict[str, Any]):
        """Handle device status messages"""
        try:
            device_id = "esp32_main"  # Assume main device


            # Update device discovery
            await self._update_device_discovery(device_id, 'status', {'status': data})

        except Exception as e:
            logger.error(f"Error handling device status: {e}")

    async def _handle_dynamic_topic(self, topic: str, data: Dict[str, Any]):
        """Handle dynamic device topics for autodiscovery"""
        try:
            parts = topic.split('/')
            if len(parts) >= 3:
                namespace = parts[0]  # esp32
                device_id = parts[1]  # device identifier
                message_type = parts[2]  # data, status, etc.

                if namespace == settings.mqtt_base_topic.split('/')[0]:
                    # This is a device in our ecosystem
                    await self._update_device_discovery(device_id, 'unknown', data)

                    if message_type == 'data':
                        await self._handle_sensor_data(topic, {**data, 'device_id': device_id})
                    elif message_type == 'status':
                        await self._handle_device_status(device_id, data)

        except Exception as e:
            logger.error(f"Error handling dynamic topic {topic}: {e}")

    async def _handle_device_status(self, device_id: str, data: Dict[str, Any]):
        """Handle device status messages for heartbeat"""
        await self._update_device_discovery(device_id, 'status', data)

    async def _update_device_discovery(self, device_id: str, device_type: str, data: Dict[str, Any]):
        """Update device discovery registry and database"""
        try:
            current_time = datetime.utcnow()
            self.last_seen[device_id] = current_time

            async with AsyncSessionLocal() as session:
                # Check if device exists
                result = await session.execute(
                    select(Device).where(Device.device_id == device_id)
                )
                device = result.scalar_one_or_none()

                if device:
                    # Update existing device
                    device.last_seen = current_time
                    device.is_active = True
                    if device_type != 'unknown' and device_type != 'status':
                        device.device_type = device_type
                else:
                    # Create new device
                    device_data = DeviceCreate(
                        device_id=device_id,
                        device_type=device_type if device_type not in ['unknown', 'status'] else 'sensor',
                        name=f"Auto-discovered {device_id}",
                        description=f"Automatically discovered device: {device_id}",
                        device_metadata=json.dumps(data) if data else None
                    )

                    new_device = Device(**device_data.dict())
                    session.add(new_device)

                await session.commit()

                is_new = device_id not in self.device_registry
                if is_new:
                    logger.info(f"Discovered new device: {device_id} (type: {device_type})")

                self.device_registry[device_id] = {
                    'device_type': device_type,
                    'last_seen': current_time,
                    'data': data,
                    'is_active': True,
                }

                # Publish device discovery/update event
                try:
                    await event_broker.publish({
                        'type': 'device',
                        'device_id': device_id,
                        'device_type': device_type,
                        'is_active': True,
                        'last_seen': int(current_time.timestamp() * 1000),
                    })
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error updating device discovery for {device_id}: {e}")

    async def _save_sensor_reading(self, reading: SensorReadingCreate):
        """Save sensor reading to database"""
        try:
            async with AsyncSessionLocal() as session:
                db_reading = SensorReading(**reading.dict())
                session.add(db_reading)
                await session.commit()

        except Exception as e:
            logger.error(f"Error saving sensor reading: {e}")

    async def _save_actuator_state(self, state: ActuatorStateCreate):
        """Save actuator state to database"""
        try:
            async with AsyncSessionLocal() as session:
                db_state = ActuatorState(**state.dict())
                session.add(db_state)
                await session.commit()

        except Exception as e:
            logger.error(f"Error saving actuator state: {e}")

    async def publish_relay_control(self, device_id: str, relay_control: RelayControl):
        """Publish relay control command"""
        try:
            if not self.is_connected:
                raise Exception("MQTT client not connected")

            topic = settings.relay_control_topic
            payload = {
                "relay": relay_control.relay,
                "state": relay_control.state
            }

            result = self.client.publish(topic, json.dumps(payload), qos=settings.mqtt_qos)

            if result.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(f"Published relay control: {payload} to topic: {topic}")

                # Save command to database
                actuator_data = ActuatorStateCreate(
                    device_id=device_id,
                    actuator_type='relay',
                    actuator_number=relay_control.relay,
                    state=relay_control.state,
                    command_source='api_command'
                )
                await self._save_actuator_state(actuator_data)

            else:
                logger.error(f"Failed to publish relay control: {result.rc}")

        except Exception as e:
            logger.error(f"Error publishing relay control: {e}")
            raise

    async def get_device_list(self) -> Dict[str, Dict[str, Any]]:
        """Get list of discovered devices"""
        return self.device_registry.copy()

    async def mark_inactive_devices(self):
        """Mark devices as inactive if not seen for a while"""
        try:
            cutoff_time = datetime.utcnow() - timedelta(seconds=settings.sensor_discovery_timeout)

            async with AsyncSessionLocal() as session:
                # Mark devices as inactive
                await session.execute(
                    update(Device)
                    .where(Device.last_seen < cutoff_time)
                    .values(is_active=False)
                )
                await session.commit()

            # Update local registry
            inactive_devices = []
            for device_id, info in self.device_registry.items():
                if info['last_seen'] < cutoff_time:
                    inactive_devices.append(device_id)

            for device_id in inactive_devices:
                self.device_registry[device_id]['is_active'] = False
                try:
                    await event_broker.publish({
                        'type': 'device',
                        'device_id': device_id,
                        'device_type': self.device_registry[device_id].get('device_type', 'sensor'),
                        'is_active': False,
                        'last_seen': int(self.device_registry[device_id]['last_seen'].timestamp() * 1000),
                    })
                except Exception:
                    pass

        except Exception as e:
            logger.error(f"Error marking inactive devices: {e}")

    async def disconnect(self):
        """Disconnect from MQTT broker"""
        if self.client:
            self.client.loop_stop()
            self.client.disconnect()
            self.is_connected = False
            logger.info("Disconnected from MQTT broker")

# Global MQTT client instance
mqtt_client = MQTTClient()