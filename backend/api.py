from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import asyncio

from .database import get_db, init_db, AsyncSessionLocal
from .models import (
    Device, SensorReading, ActuatorState,
    DeviceResponse, SensorReadingResponse, ActuatorStateResponse,
    RelayControl, RelayStatus
)
from .mqtt_client import mqtt_client
from .config import settings
from .events import event_broker

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

    # Start background tasks
    asyncio.create_task(device_heartbeat_task())

    # Warm event stream with initial snapshot periodically (optional)

@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    await mqtt_client.disconnect()

async def device_heartbeat_task():
    """Background task to check device heartbeats"""
    while True:
        try:
            await mqtt_client.mark_inactive_devices()
            await asyncio.sleep(settings.sensor_heartbeat_interval)
        except Exception as e:
            print(f"Error in heartbeat task: {e}")
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
            "last_seen": int(info.get("last_seen").timestamp() * 1000) if info.get("last_seen") else None,
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
                    "metrics": {
                        k: v for k, v in {
                            "temperature": r.temperature,
                            "pressure": r.pressure,
                            "humidity": r.humidity,
                            "gas_kohms": r.gas_kohms,
                            "lux": r.lux,
                            "water_temp_c": r.water_temp_c,
                            "tds_ppm": r.tds_ppm,
                            "ph": r.ph,
                            "distance_mm": r.distance_mm,
                            "vpd_kpa": r.vpd_kpa,
                        }.items() if v is not None
                    }
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

    summary = {}
    for reading in latest_readings:
        summary[reading.device_id] = {
            "timestamp": reading.timestamp,
            "temperature": reading.temperature,
            "humidity": reading.humidity,
            "pressure": reading.pressure,
            "ph": reading.ph,
            "tds_ppm": reading.tds_ppm,
            "water_temp_c": reading.water_temp_c,
            "lux": reading.lux,
            "vpd_kpa": reading.vpd_kpa,
            "distance_mm": reading.distance_mm
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
    # Get latest state for each relay
    relay_states = {}

    for relay_num in range(1, 17):
        result = await db.execute(
            select(ActuatorState)
            .where(
                (ActuatorState.device_id == device_id) &
                (ActuatorState.actuator_type == "relay") &
                (ActuatorState.actuator_number == relay_num)
            )
            .order_by(desc(ActuatorState.timestamp))
            .limit(1)
        )
        state = result.scalar_one_or_none()

        if state:
            relay_states[f"relay{relay_num}"] = state.state
        else:
            relay_states[f"relay{relay_num}"] = "unknown"

    return {
        "device_id": device_id,
        "relays": relay_states,
        "timestamp": datetime.utcnow()
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
    valid_metrics = ["temperature", "humidity", "pressure", "ph", "tds_ppm", "water_temp_c", "lux", "vpd_kpa"]
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