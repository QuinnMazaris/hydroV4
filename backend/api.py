import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import AsyncSessionLocal, get_db, init_db
from .events import event_broker
from .models import (
    ActuatorState,
    ActuatorStateResponse,
    Device,
    DeviceResponse,
    RelayControl,
    RelayStatus,
    SensorReading,
    SensorReadingResponse,
)
from .mqtt_client import mqtt_client
from .services.persistence import delete_old_readings

app = FastAPI(title="Hydroponic System API", version="1.0.0")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_event():
    """Initialize database and MQTT client on startup"""
    await init_db()
    await mqtt_client.connect()
    # Start async processor for queued MQTT messages
    await mqtt_client.start_message_processor()

    mqtt_client.request_discovery_broadcast()

    app.state.maintenance_task = asyncio.create_task(maintenance_loop())


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    task = getattr(app.state, "maintenance_task", None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        app.state.maintenance_task = None
    await mqtt_client.disconnect()


async def maintenance_loop():
    """Background task handling device heartbeat checks, data retention, and capability refresh."""
    last_cleanup = datetime.utcnow()
    cleanup_interval = timedelta(hours=24)
    discovery_interval = timedelta(seconds=max(120, settings.sensor_discovery_timeout))
    last_discovery = datetime.utcnow()
    while True:
        try:
            await mqtt_client.mark_inactive_devices()

            now = datetime.utcnow()
            if settings.data_retention_days > 0 and (now - last_cleanup) >= cleanup_interval:
                cutoff = now - timedelta(days=settings.data_retention_days)
                await delete_old_readings(cutoff)
                last_cleanup = now

            if (now - last_discovery) >= discovery_interval:
                mqtt_client.request_discovery_broadcast()
                last_discovery = now

            await asyncio.sleep(settings.sensor_heartbeat_interval)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"Error in maintenance loop: {exc}")
            await asyncio.sleep(settings.sensor_heartbeat_interval)




# ---------------- WebSocket live metrics ---------------- #

@app.websocket("/ws/metrics")
async def ws_metrics(websocket: WebSocket):
    await websocket.accept()

    # Subscribe to broker
    queue = await event_broker.subscribe()

    # Send initial snapshot: active devices and their latest values
    try:
        snapshot = await build_initial_snapshot()
        await websocket.send_json({
            "type": "snapshot",
            "devices": snapshot["devices"],
            "latest": snapshot["latest"],
            "ts": datetime.utcnow().timestamp()
        })
    except Exception:
        # If snapshot fails, continue with live stream
        pass

    try:
        while True:
            event = await queue.get()
            # Forward events as-is
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        # On any error close gracefully
        try:
            await websocket.close()
        except Exception:
            pass
    finally:
        event_broker.unsubscribe(queue)


SENSOR_FIELD_EXCLUDES = {"id", "device_id", "timestamp", "raw_data"}

def reading_to_metric_payload(reading: SensorReading) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    for column in SensorReading.__table__.columns:
        if column.name in SENSOR_FIELD_EXCLUDES:
            continue
        value = getattr(reading, column.name)
        if value is not None:
            metrics[column.name] = value
    return metrics



async def build_initial_snapshot():
    """Build a snapshot of active devices and their latest reading values."""
    devices: Dict[str, Any] = {}
    latest: Dict[str, Dict[str, Any]] = {}

    # Use in-memory registry first
    discovered = await mqtt_client.get_device_list()
    for device_id, info in discovered.items():
        devices[device_id] = {
            "device_type": info.get("device_type", "sensor"),
            "is_active": info.get("is_active", True),
            "last_seen": info.get("last_seen"),
            "metrics": info.get("metrics", {}),
            "actuators": info.get("actuators", []),
        }

    # Query latest DB values per device
    try:
        async with AsyncSessionLocal() as db:
            # Subquery to get latest timestamp per device
            subquery = (
                select(
                    SensorReading.device_id,
                    func.max(SensorReading.timestamp).label('max_timestamp')
                )
                .group_by(SensorReading.device_id)
                .subquery()
            )

            query = (
                select(SensorReading)
                .join(
                    subquery,
                    (SensorReading.device_id == subquery.c.device_id) &
                    (SensorReading.timestamp == subquery.c.max_timestamp)
                )
            )

            result = await db.execute(query)
            rows = result.scalars().all()

            for r in rows:
                latest[r.device_id] = {
                    "timestamp": int(r.timestamp.timestamp() * 1000),
                    "metrics": reading_to_metric_payload(r),
                }
    except Exception:
        pass

    return {"devices": devices, "latest": latest}

# Device endpoints
@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_devices(
    active_only: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """Get list of all devices"""
    query = select(Device)
    if active_only:
        query = query.where(Device.is_active == True)

    result = await db.execute(query.order_by(Device.created_at))
    devices = result.scalars().all()
    return devices

@app.get("/api/devices/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str, db: AsyncSession = Depends(get_db)):
    """Get specific device by ID"""
    result = await db.execute(
        select(Device).where(Device.device_id == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    return device

@app.get("/api/devices/discovered")
async def get_discovered_devices():
    """Get devices discovered via MQTT"""
    return await mqtt_client.get_device_list()

# Sensor data endpoints
@app.get("/api/sensors/{device_id}/readings", response_model=List[SensorReadingResponse])
async def get_sensor_readings(
    device_id: str,
    limit: int = 100,
    hours: Optional[int] = 24,
    db: AsyncSession = Depends(get_db)
):
    """Get sensor readings for a specific device"""
    query = select(SensorReading).where(SensorReading.device_id == device_id)

    if hours:
        since = datetime.utcnow() - timedelta(hours=hours)
        query = query.where(SensorReading.timestamp >= since)

    query = query.order_by(desc(SensorReading.timestamp)).limit(limit)

    result = await db.execute(query)
    readings = result.scalars().all()
    return readings

@app.get("/api/sensors/{device_id}/latest", response_model=SensorReadingResponse)
async def get_latest_sensor_reading(
    device_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get latest sensor reading for a device"""
    result = await db.execute(
        select(SensorReading)
        .where(SensorReading.device_id == device_id)
        .order_by(desc(SensorReading.timestamp))
        .limit(1)
    )
    reading = result.scalar_one_or_none()

    if not reading:
        raise HTTPException(status_code=404, detail="No readings found for device")

    return reading

@app.get("/api/sensors/summary")
async def get_sensor_summary(db: AsyncSession = Depends(get_db)):
    """Get summary of all sensor data"""
    # Get latest reading for each active device
    subquery = (
        select(
            SensorReading.device_id,
            func.max(SensorReading.timestamp).label('max_timestamp')
        )
        .group_by(SensorReading.device_id)
        .subquery()
    )

    query = (
        select(SensorReading)
        .join(
            subquery,
            (SensorReading.device_id == subquery.c.device_id) &
            (SensorReading.timestamp == subquery.c.max_timestamp)
        )
    )

    result = await db.execute(query)
    latest_readings = result.scalars().all()

    summary: Dict[str, Any] = {}
    for reading in latest_readings:
        summary[reading.device_id] = {
            "timestamp": reading.timestamp,
            **reading_to_metric_payload(reading),
        }

    return summary

# Actuator endpoints
@app.post("/api/actuators/{device_id}/relay/control")
async def control_relay(
    device_id: str,
    relay_control: RelayControl,
    background_tasks: BackgroundTasks
):
    """Control relay state"""
    # Validate relay number
    if not 1 <= relay_control.relay <= 16:
        raise HTTPException(status_code=400, detail="Relay number must be between 1 and 16")

    # Validate state
    if relay_control.state not in ["on", "off"]:
        raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

    # Send MQTT command
    try:
        await mqtt_client.publish_relay_control(device_id, relay_control)
        return {"message": f"Relay {relay_control.relay} control command sent", "status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send relay command: {str(e)}")

@app.get("/api/actuators/{device_id}/states", response_model=List[ActuatorStateResponse])
async def get_actuator_states(
    device_id: str,
    limit: int = 50,
    hours: Optional[int] = 24,
    db: AsyncSession = Depends(get_db)
):
    """Get actuator states for a device"""
    query = select(ActuatorState).where(ActuatorState.device_id == device_id)

    if hours:
        since = datetime.utcnow() - timedelta(hours=hours)
        query = query.where(ActuatorState.timestamp >= since)

    query = query.order_by(desc(ActuatorState.timestamp)).limit(limit)

    result = await db.execute(query)
    states = result.scalars().all()
    return states

@app.get("/api/actuators/{device_id}/relays/status")
async def get_relay_status(
    device_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get current status of all relays for a device"""
    relay_states: Dict[int, str] = {}

    query = (
        select(ActuatorState)
        .where(
            (ActuatorState.device_id == device_id)
            & (ActuatorState.actuator_type == "relay")
        )
        .order_by(ActuatorState.actuator_number, desc(ActuatorState.timestamp))
    )

    result = await db.execute(query)
    for state in result.scalars():
        if state.actuator_number not in relay_states:
            relay_states[state.actuator_number] = state.state

    relays_payload = {
        f"relay{relay_num}": relay_states.get(relay_num, "unknown")
        for relay_num in range(1, 17)
    }

    return {
        "device_id": device_id,
        "relays": relays_payload,
        "timestamp": datetime.utcnow(),
    }

# Analytics endpoints
@app.get("/api/analytics/trends")
async def get_sensor_trends(
    device_id: str,
    metric: str,
    hours: int = 24,
    interval_minutes: int = 60,
    db: AsyncSession = Depends(get_db)
):
    """Get sensor trends for analytics"""
    # Validate metric
    valid_metrics = [
        column.name
        for column in SensorReading.__table__.columns
        if column.name not in SENSOR_FIELD_EXCLUDES
    ]
    if metric not in valid_metrics:
        raise HTTPException(status_code=400, detail=f"Invalid metric. Must be one of: {valid_metrics}")

    since = datetime.utcnow() - timedelta(hours=hours)

    # This is a simplified version - in production you might want to use proper time-series aggregation
    query = (
        select(SensorReading)
        .where(
            (SensorReading.device_id == device_id) &
            (SensorReading.timestamp >= since)
        )
        .order_by(SensorReading.timestamp)
    )

    result = await db.execute(query)
    readings = result.scalars().all()

    # Group by intervals
    trends = []
    current_time = since
    interval_delta = timedelta(minutes=interval_minutes)

    while current_time < datetime.utcnow():
        interval_end = current_time + interval_delta
        interval_readings = [
            r for r in readings
            if current_time <= r.timestamp < interval_end
        ]

        if interval_readings:
            values = [getattr(r, metric) for r in interval_readings if getattr(r, metric) is not None]
            if values:
                avg_value = sum(values) / len(values)
                trends.append({
                    "timestamp": current_time,
                    "value": avg_value,
                    "count": len(values)
                })

        current_time = interval_end

    return {
        "device_id": device_id,
        "metric": metric,
        "interval_minutes": interval_minutes,
        "trends": trends
    }

# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "mqtt_connected": mqtt_client.is_connected,
        "timestamp": datetime.utcnow()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)