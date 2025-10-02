import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import AsyncSessionLocal, get_db, init_db
from .events import event_broker
from .metrics import build_metric_meta
from .models import ActuatorBatchControl, ActuatorCommand, ActuatorControl, Device, DeviceResponse, Metric, Reading
from .mqtt_client import mqtt_client
from .services.persistence import delete_old_readings, mark_devices_inactive
from .services.camera_sync import sync_cameras_to_db

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
    # Populate cache from database before starting message processor
    await mqtt_client.populate_cache_from_db()
    # Start async processor for queued MQTT messages
    await mqtt_client.start_message_processor()

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
    """Background task handling device heartbeat checks, data retention, and camera sync."""
    last_cleanup = datetime.utcnow()
    cleanup_interval = timedelta(hours=24)
    while True:
        try:
            # Sync cameras from MediaMTX to database
            await sync_cameras_to_db()

            # Mark inactive devices (both MQTT and cameras)
            cutoff_time = datetime.utcnow() - timedelta(seconds=settings.sensor_discovery_timeout)
            await mqtt_client.mark_inactive_devices()  # MQTT devices
            await mark_devices_inactive(cutoff_time, device_type='camera')  # Cameras

            now = datetime.utcnow()
            if settings.data_retention_days > 0 and (now - last_cleanup) >= cleanup_interval:
                cutoff = now - timedelta(days=settings.data_retention_days)
                await delete_old_readings(cutoff)
                last_cleanup = now

            await asyncio.sleep(settings.sensor_heartbeat_interval)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            print(f"Error in maintenance loop: {exc}")
            await asyncio.sleep(settings.sensor_heartbeat_interval)




# ---------------- WebSocket live sensors ---------------- #

@app.websocket("/ws/sensors")
async def ws_sensors(websocket: WebSocket):
    await websocket.accept()

    # Subscribe to broker
    queue = await event_broker.subscribe()

    # Send initial snapshot: active devices and their latest values
    try:
        snapshot = await build_initial_snapshot()
        await websocket.send_json({
            "type": "snapshot",
            "devices": snapshot.get("devices", {}),
            "latest": snapshot.get("latest", {}),
            "history": snapshot.get("history", {}),
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


def _ts_to_ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def _downsample_points(points: List[Dict[str, Any]], target: int) -> List[Dict[str, Any]]:
    if target <= 0 or len(points) <= target:
        return points
    step = len(points) / float(target)
    indices = [int(i * step) for i in range(target)]
    if indices[-1] != len(points) - 1:
        indices[-1] = len(points) - 1
    return [points[i] for i in indices]


async def _latest_metric_rows(
    db: AsyncSession,
    device_key: Optional[str] = None,
    metric_keys: Optional[List[str]] = None,
):
    subquery = (
        select(Reading.metric_id, func.max(Reading.timestamp).label('latest_ts'))
        .group_by(Reading.metric_id)
        .subquery()
    )

    query = (
        select(
            Device.device_key,
            Metric.metric_key,
            Reading.timestamp,
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

    if device_key:
        query = query.where(Device.device_key == device_key)
    if metric_keys:
        query = query.where(Metric.metric_key.in_(metric_keys))

    result = await db.execute(query)
    return result.all()


async def build_initial_snapshot():
    """Build a snapshot of active devices, latest values, and 24h history."""
    devices: Dict[str, Any] = {}
    latest: Dict[str, Dict[str, Any]] = {}
    history: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    try:
        async with AsyncSessionLocal() as db:
            device_rows = await db.execute(
                select(Device).where(Device.is_active == True)
            )
            for device in device_rows.scalars().all():
                devices[device.device_key] = {
                    'is_active': device.is_active,
                    'last_seen': _ts_to_ms(device.last_seen) if device.last_seen else None,
                    'sensors': {},
                    'actuators': {},
                }

            metric_rows = await db.execute(
                select(
                    Device.device_key,
                    Metric.metric_key,
                    Metric.display_name,
                    Metric.unit,
                    Metric.metric_type,
                ).join(Metric, Metric.device_id == Device.id)
            )
            for device_key, metric_key, display_name, unit, metric_type in metric_rows:
                entry = devices.setdefault(
                    device_key,
                    {
                        'is_active': True,
                        'last_seen': None,
                        'sensors': {},
                        'actuators': {},
                    },
                )
                overrides: Dict[str, Any] = {}
                if display_name:
                    overrides['label'] = display_name
                if unit:
                    overrides['unit'] = unit
                meta = build_metric_meta(metric_key, overrides if overrides else None)

                metric_info = {
                    'id': meta.id,
                    'label': meta.label,
                    'unit': meta.unit,
                    'color': meta.color,
                }

                if metric_type == 'sensor':
                    entry['sensors'][metric_key] = metric_info
                elif metric_type == 'actuator':
                    entry['actuators'][metric_key] = metric_info

            # Get current values from cache instead of database
            cached_values = mqtt_client.get_cached_values()
            current_time_ms = int(datetime.utcnow().timestamp() * 1000)

            for device_key, metric_values in cached_values.items():
                devices.setdefault(device_key, {
                    'is_active': True,
                    'last_seen': current_time_ms,
                    'sensors': {},
                    'actuators': {},
                })
                if metric_values:
                    entry = latest.setdefault(
                        device_key,
                        {'timestamp': current_time_ms, 'metrics': {}, 'values': {}},
                    )
                    for metric_key, value in metric_values.items():
                        entry['metrics'][metric_key] = {'timestamp': current_time_ms, 'value': value}
                        entry['values'][metric_key] = value

            since = datetime.utcnow() - timedelta(hours=24)
            history_query = (
                select(
                    Device.device_key,
                    Metric.metric_key,
                    Reading.timestamp,
                    Reading.value,
                )
                .join(Metric, Metric.device_id == Device.id)
                .join(Reading, Reading.metric_id == Metric.id)
                .where(Reading.timestamp >= since)
                .order_by(Device.device_key, Metric.metric_key, Reading.timestamp)
            )
            history_rows = (await db.execute(history_query)).all()

            per_series: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
            for device_key, metric_key, ts, value in history_rows:
                per_series.setdefault((device_key, metric_key), []).append({
                    'timestamp': ts,
                    'value': value,
                })

            target_points = max(50, min(3000, settings.history_snapshot_target_points))
            for (device_key, metric_key), points in per_series.items():
                if not points:
                    continue
                points.sort(key=lambda item: item['timestamp'])
                serialised = [
                    {'timestamp': _ts_to_ms(item['timestamp']), 'value': item['value']}
                    for item in points
                ]
                trimmed = _downsample_points(serialised, target_points)
                history.setdefault(device_key, {})[metric_key] = trimmed
    except Exception:
        pass

    return {'devices': devices, 'latest': latest, 'history': history}




# Device endpoints
@app.get("/api/devices", response_model=List[DeviceResponse])
async def get_devices(
    active_only: bool = True,
    device_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Return devices, optionally filtered by activity status or type."""
    query = select(Device)
    if device_type:
        query = query.where(Device.device_type == device_type)
    if active_only:
        query = query.where(Device.is_active == True)

    result = await db.execute(query.order_by(Device.created_at))
    return result.scalars().all()

@app.get("/api/devices/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str, db: AsyncSession = Depends(get_db)):
    """Get a single device by its device_key - used for device configuration and detailed information"""
    result = await db.execute(
        select(Device).where(Device.device_key == device_id)
    )
    device = result.scalar_one_or_none()

    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    return device


@app.post("/api/actuators/batch-control")
async def control_actuators_batch(
    batch: ActuatorBatchControl,
    db: AsyncSession = Depends(get_db),
):
    """Batch actuator control with server-side deduplication and rate-limited MQTT publish."""
    if not batch.commands:
        return {"processed": 0, "skipped": 0, "missing": []}

    deduped: Dict[tuple[str, str], ActuatorCommand] = {}
    for command in batch.commands:
        device_id = command.device_id.strip()
        actuator_key = command.actuator_key.strip()
        if not device_id or not actuator_key:
            raise HTTPException(status_code=400, detail="Each command requires device_id and actuator_key")
        state = command.state.lower()
        if state not in {"on", "off"}:
            raise HTTPException(status_code=400, detail=f"Invalid state '{command.state}' for actuator '{actuator_key}'")
        deduped[(device_id, actuator_key)] = ActuatorCommand(
            device_id=device_id,
            actuator_key=actuator_key,
            state=state,
        )

    if not deduped:
        return {"processed": 0, "skipped": 0, "missing": []}

    pairs = list(deduped.keys())

    result = await db.execute(
        select(Device.device_key, Metric.metric_key)
        .join(Metric, Metric.device_id == Device.id)
        .where(tuple_(Device.device_key, Metric.metric_key).in_(pairs))
        .where(Metric.metric_type == 'actuator')
    )
    valid_pairs = {(device_key, metric_key) for device_key, metric_key in result.all()}

    missing = [
        {"device_id": device_id, "actuator_key": actuator_key}
        for device_id, actuator_key in pairs
        if (device_id, actuator_key) not in valid_pairs
    ]

    device_commands: Dict[str, List[ActuatorControl]] = defaultdict(list)
    processed_details: List[Dict[str, Any]] = []

    for device_id, actuator_key in pairs:
        if (device_id, actuator_key) not in valid_pairs:
            continue
        command = deduped[(device_id, actuator_key)]
        control = ActuatorControl(actuator_key=command.actuator_key, state=command.state)
        device_commands[device_id].append(control)
        processed_details.append({
            "device_id": device_id,
            "actuator_key": actuator_key,
            "state": command.state,
        })

    for device_id, controls in device_commands.items():
        await mqtt_client.publish_actuator_batch(device_id, controls)

    return {
        "processed": len(processed_details),
        "skipped": len(deduped) - len(processed_details),
        "missing": missing,
        "details": processed_details,
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
