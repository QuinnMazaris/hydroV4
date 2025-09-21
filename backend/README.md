# Hydroponic System MQTT Backend

A Python backend service that handles MQTT communication with ESP32 sensors and actuators for hydroponic systems.

## Features

- **Sensor Autodiscovery**: Automatically discovers new ESP32 devices when they start publishing data
- **Real-time Data Collection**: Collects sensor data (BME680, pH, TDS, water temperature, light, distance)
- **Actuator Control**: Controls relays (1-16) via MQTT commands
- **Data Persistence**: Stores all sensor readings and actuator states in SQLite database
- **REST API**: Provides HTTP API for frontend integration
- **Device Management**: Tracks device status and heartbeats
- **Data Retention**: Automatic cleanup of old sensor readings

## Quick Start

1. **Install Dependencies**:
```bash
pip install -r requirements.txt
```

2. **Configure Environment**:
```bash
cp .env.example .env
# Edit .env with your MQTT broker settings
```

3. **Run the Service**:

**Option A: Run MQTT service only**:
```bash
python main.py
```

**Option B: Run with API server**:
```bash
python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

**Option C: Run both (recommended)**:
```bash
# Terminal 1: MQTT Service
python main.py

# Terminal 2: API Server
python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

## MQTT Topics

### Published by ESP32:
- `esp32/data` - Main sensor data (every 1 second)
- `esp32/relay/status` - Relay state changes

### Subscribed by ESP32:
- `esp32/relay/control` - Relay control commands

### Example Messages:

**Sensor Data** (`esp32/data`):
```json
{
  "bme680": {
    "temperature": 24.5,
    "pressure": 1013.2,
    "humidity": 65.0,
    "gas_kohms": 150.0
  },
  "lux": 850,
  "water_temp_c": 22.0,
  "tds_ppm": 350,
  "ph": 6.5,
  "distance_mm": 120,
  "vpd_kpa": 1.2
}
```

**Relay Control** (`esp32/relay/control`):
```json
{
  "relay": 1,
  "state": "on"
}
```

## API Endpoints

### Devices
- `GET /api/devices` - List all devices
- `GET /api/devices/{device_id}` - Get specific device
- `GET /api/devices/discovered` - Get MQTT-discovered devices

### Sensors
- `GET /api/sensors/{device_id}/readings` - Get sensor readings
- `GET /api/sensors/{device_id}/latest` - Get latest reading
- `GET /api/sensors/summary` - Get summary of all sensors

### Actuators
- `POST /api/actuators/{device_id}/relay/control` - Control relay
- `GET /api/actuators/{device_id}/states` - Get actuator states
- `GET /api/actuators/{device_id}/relays/status` - Get relay status

### Analytics
- `GET /api/analytics/trends` - Get sensor trends
- `GET /api/health` - Service health check

## Configuration

Key configuration options in `.env`:

```env
# MQTT Broker
MQTT_BROKER=localhost
MQTT_PORT=1883

# Topics
MQTT_BASE_TOPIC=esp32
SENSOR_DATA_TOPIC=esp32/data

# Database
DATABASE_URL=sqlite+aiosqlite:///./hydro.db

# API
API_HOST=0.0.0.0
API_PORT=8000
```

## Adding New Sensors

The system automatically discovers new sensors when they start publishing to MQTT topics following the pattern:
- `esp32/data` for main sensor hub
- `esp32/{device_id}/data` for individual devices

New sensor types can be added by:
1. Extending the `SensorReading` model in `models.py`
2. Updating the `_handle_sensor_data` method in `mqtt_client.py`

## Adding New Actuators

To add new actuator types:
1. Extend the `ActuatorState` model if needed
2. Add new handler methods in `mqtt_client.py`
3. Create API endpoints in `api.py`

## Architecture

```
┌─────────────────┐    MQTT     ┌──────────────────┐    HTTP API    ┌─────────────┐
│   ESP32 Devices │ ←─────────→ │  MQTT Client     │ ←─────────────→ │  Frontend   │
│   (Sensors &    │             │  - Autodiscovery │                │  Dashboard  │
│    Actuators)   │             │  - Data Storage  │                │             │
└─────────────────┘             │  - Device Mgmt   │                └─────────────┘
                                └──────────────────┘
                                         │
                                         ▼
                                ┌──────────────────┐
                                │   SQLite DB      │
                                │  - Sensor Data   │
                                │  - Device Info   │
                                │  - Actuator Log  │
                                └──────────────────┘
```

## Production Deployment

For production use:

1. **Use a proper MQTT broker** (Mosquitto, HiveMQ, etc.)
2. **Configure authentication** for MQTT
3. **Use PostgreSQL** instead of SQLite
4. **Set up proper logging** and monitoring
5. **Configure CORS** appropriately
6. **Use environment variables** for secrets

Example production `.env`:
```env
MQTT_BROKER=your-mqtt-broker.com
MQTT_USERNAME=your-username
MQTT_PASSWORD=your-password
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/hydro
LOG_LEVEL=WARNING
```