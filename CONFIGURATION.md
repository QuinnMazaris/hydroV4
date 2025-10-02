# Hydro System Configuration

This document describes all configurable environment variables for the Hydro system.

## Environment Variables

All environment variables have sensible defaults. You can override them by:
1. Creating a `.env` file in the project root
2. Setting them in `docker-compose.yml`
3. Passing them directly when running the application

### Application Ports

| Variable | Default | Description |
|----------|---------|-------------|
| `FRONTEND_PORT` | `3001` | Port where the Next.js frontend runs |
| `API_PORT` | `8001` | Port where the FastAPI backend runs |

### MQTT Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MQTT_BROKER` | `127.0.0.1` | MQTT broker hostname or IP |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `MQTT_USERNAME` | _(none)_ | MQTT authentication username |
| `MQTT_PASSWORD` | _(none)_ | MQTT authentication password |

### MediaMTX Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `MEDIAMTX_HOST` | `localhost` | Hostname where MediaMTX is running |
| `MEDIAMTX_API_PORT` | `9997` | MediaMTX REST API port (for path discovery) |
| `MEDIAMTX_WEBRTC_PORT` | `8889` | MediaMTX WebRTC port (for camera streaming via WHEP) |

### Database Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./hydro.db` | Database connection string |

### Sensor Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `SENSOR_DISCOVERY_TIMEOUT` | `300` | Seconds before marking a device as inactive |
| `SENSOR_HEARTBEAT_INTERVAL` | `60` | How often devices should send heartbeats (seconds) |

### Data Retention

| Variable | Default | Description |
|----------|---------|-------------|
| `DATA_RETENTION_DAYS` | `30` | Days to keep historical sensor data |

### Logging

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |

## Example Configuration

### Using docker-compose.yml

The `docker-compose.yml` file already includes all variables with their defaults. To customize:

1. Create a `.env` file in the project root:
```bash
# .env
API_PORT=8002
MEDIAMTX_WEBRTC_PORT=9000
MQTT_BROKER=192.168.1.100
```

2. Docker Compose will automatically read the `.env` file and use those values.

### Using Direct Environment Variables

```bash
export API_PORT=8002
export MEDIAMTX_WEBRTC_PORT=9000
docker-compose up -d
```

## Notes

- All ports and hostnames are now configurable via environment variables
- The system maintains backward compatibility with hardcoded defaults
- When running with Docker host networking mode, `localhost` and `127.0.0.1` refer to the host machine
- MediaMTX must be accessible at the configured host and ports for camera streaming to work

