import asyncio
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import AsyncSessionLocal, get_db, init_db
from .events import event_broker
from .models import Device, DeviceResponse, Metric, Reading, RelayControl
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


async def _metric_readings(
    db: AsyncSession,
    device_key: str,
    *,
    metric_key: Optional[str] = None,
    metric_prefix: Optional[str] = None,
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    query = (
        select(
            Metric.metric_key,
            Reading.timestamp,
            Reading.value,
        )
        .join(Reading, Reading.metric_id == Metric.id)
        .join(Device, Device.id == Metric.device_id)
        .where(Device.device_key == device_key)
    )

    if metric_key:
        query = query.where(Metric.metric_key == metric_key)
    if metric_prefix:
        query = query.where(Metric.metric_key.like(f"{metric_prefix}%"))
    if since:
        query = query.where(Reading.timestamp >= since)

    query = query.order_by(Reading.timestamp.desc())
    if limit:
        query = query.limit(limit)

    rows = (await db.execute(query)).all()
    records = [
        {
            'metric_key': row[0],
            'timestamp': row[1],
            'value': row[2],
        }
        for row in rows
    ]
    records.sort(key=lambda item: item['timestamp'])
    return records



async def build_initial_snapshot():
    """Build a snapshot of active devices, latest values, and 24h history."""
    devices: Dict[str, Any] = {}
    latest: Dict[str, Dict[str, Any]] = {}
    history: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}

    discovered = await mqtt_client.get_device_list()
    for device_key, info in discovered.items():
        devices[device_key] = {
            'is_active': info.get('is_active', True),
            'last_seen': info.get('last_seen'),
            'sensors': info.get('sensors', {}),
            'actuators': info.get('actuators', []),
        }

    try:
        async with AsyncSessionLocal() as db:
            # Ensure database-known devices are registered in snapshot
            device_rows = await db.execute(
                select(Device.device_key, Device.is_active, Device.last_seen)
            )
            for device_key, is_active, last_seen in device_rows:
                if device_key not in devices:
                    devices[device_key] = {
                        'is_active': is_active,
                        'last_seen': _ts_to_ms(last_seen) if isinstance(last_seen, datetime) else None,
                        'sensors': {},
                        'actuators': [],
                    }
                else:
                    entry = devices[device_key]
                    entry.setdefault('is_active', is_active)
                    if entry.get('last_seen') is None and isinstance(last_seen, datetime):
                        entry['last_seen'] = _ts_to_ms(last_seen)

            latest_rows = await _latest_metric_rows(db)
            for device_key, metric_key, ts, value in latest_rows:
                devices.setdefault(device_key, {
                    'is_active': True,
                    'last_seen': _ts_to_ms(ts),
                    'sensors': {},
                    'actuators': [],
                })
                entry = latest.setdefault(
                    device_key,
                    {'timestamp': None, 'metrics': {}, 'values': {}},
                )
                ts_ms = _ts_to_ms(ts)
                entry['metrics'][metric_key] = {'timestamp': ts_ms, 'value': value}
                entry['values'][metric_key] = value
                if entry['timestamp'] is None or ts_ms > entry['timestamp']:
                    entry['timestamp'] = ts_ms

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
        select(Device).where(Device.device_key == device_id)
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

@app.get("/api/sensors/{device_id}/readings")
async def get_sensor_readings(
    device_id: str,
    metric: Optional[str] = None,
    limit: int = 500,
    hours: Optional[int] = 24,
    db: AsyncSession = Depends(get_db),
):
    """Return recent readings grouped by metric for a specific device."""
    since = datetime.utcnow() - timedelta(hours=hours) if hours else None
    records = await _metric_readings(
        db,
        device_key=device_id,
        metric_key=metric,
        since=since,
        limit=limit,
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record['metric_key'], []).append({
            'timestamp': _ts_to_ms(record['timestamp']),
            'value': record['value'],
        })

    return grouped


@app.get("/api/sensors/{device_id}/latest")
async def get_latest_sensor_reading(
    device_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Return the latest reading per metric for a device."""
    rows = await _latest_metric_rows(db, device_key=device_id)
    if not rows:
        raise HTTPException(status_code=404, detail="No readings found for device")

    metrics: Dict[str, Dict[str, Any]] = {}
    values: Dict[str, Any] = {}
    latest_ts: Optional[int] = None

    for _device_key, metric_key, ts, value in rows:
        ts_ms = _ts_to_ms(ts)
        metrics[metric_key] = {'timestamp': ts_ms, 'value': value}
        values[metric_key] = value
        if latest_ts is None or ts_ms > latest_ts:
            latest_ts = ts_ms

    return {
        'device_id': device_id,
        'timestamp': latest_ts,
        'metrics': metrics,
        'values': values,
    }


@app.get("/api/sensors/summary")
async def get_sensor_summary(db: AsyncSession = Depends(get_db)):
    """Return the latest value for each metric grouped by device."""
    rows = await _latest_metric_rows(db)
    summary: Dict[str, Dict[str, Any]] = {}
    for device_key, metric_key, ts, value in rows:
        device_entry = summary.setdefault(device_key, {})
        device_entry[metric_key] = {
            'timestamp': _ts_to_ms(ts),
            'value': value,
        }
    return summary

# Actuator endpoints

@app.post("/api/actuators/{device_id}/relay/control")
async def control_relay(
    device_id: str,
    relay_control: RelayControl,
    db: AsyncSession = Depends(get_db),
):
    """Control relay state via MQTT after validating the target metric exists."""
    if not 1 <= relay_control.relay <= 16:
        raise HTTPException(status_code=400, detail="Relay number must be between 1 and 16")

    if relay_control.state not in ["on", "off"]:
        raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

    metric_key = f"relay{relay_control.relay}"
    metric_row = await db.execute(
        select(Metric)
        .join(Device, Device.id == Metric.device_id)
        .where((Device.device_key == device_id) & (Metric.metric_key == metric_key))
    )
    metric_obj = metric_row.scalar_one_or_none()
    if not metric_obj:
        raise HTTPException(status_code=404, detail=f"Relay metric {metric_key} not registered for device")

    try:
        await mqtt_client.publish_relay_control(device_id, relay_control)
        return {"message": f"Relay {relay_control.relay} control command sent", "status": "success"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send relay command: {exc}")


@app.get("/api/actuators/{device_id}/states")
async def get_actuator_states(
    device_id: str,
    limit: int = 100,
    hours: Optional[int] = 24,
    metric_prefix: str = 'relay',
    db: AsyncSession = Depends(get_db),
):
    """Return actuator readings (e.g., relays) grouped by metric."""
    since = datetime.utcnow() - timedelta(hours=hours) if hours else None
    records = await _metric_readings(
        db,
        device_key=device_id,
        metric_prefix=metric_prefix,
        since=since,
        limit=limit,
    )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record['metric_key'], []).append({
            'timestamp': _ts_to_ms(record['timestamp']),
            'value': record['value'],
        })

    return grouped


@app.get("/api/actuators/{device_id}/relays/status")
async def get_relay_status(
    device_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get the latest relay states for a device."""
    rows = await _latest_metric_rows(db, device_key=device_id)
    relays: Dict[str, Dict[str, Any]] = {}
    latest_ts: Optional[int] = None

    for _device_key, metric_key, ts, value in rows:
        if not metric_key.startswith('relay'):
            continue
        ts_ms = _ts_to_ms(ts)
        relays[metric_key] = {'value': value, 'timestamp': ts_ms}
        if latest_ts is None or ts_ms > latest_ts:
            latest_ts = ts_ms

    values = {key: payload['value'] for key, payload in relays.items()}
    return {
        'device_id': device_id,
        'timestamp': latest_ts,
        'relays': values,
        'metrics': relays,
    }

# Analytics endpoints

@app.get("/api/analytics/trends")
async def get_sensor_trends(
    device_id: str,
    metric: str,
    hours: int = 24,
    interval_minutes: int = 60,
    db: AsyncSession = Depends(get_db),
):
    """Compute rolling averages for a metric over time."""
    metric_row = await db.execute(
        select(Metric)
        .join(Device, Device.id == Metric.device_id)
        .where((Device.device_key == device_id) & (Metric.metric_key == metric))
    )
    metric_obj = metric_row.scalar_one_or_none()
    if not metric_obj:
        raise HTTPException(status_code=404, detail="Metric not found for device")

    since = datetime.utcnow() - timedelta(hours=hours)
    readings = await _metric_readings(
        db,
        device_key=device_id,
        metric_key=metric,
        since=since,
    )

    interval_delta = timedelta(minutes=interval_minutes)
    current_time = since
    now = datetime.utcnow()
    pointer = 0
    trends: List[Dict[str, Any]] = []

    while current_time < now:
        interval_end = current_time + interval_delta
        values: List[float] = []
        while pointer < len(readings) and readings[pointer]['timestamp'] < interval_end:
            raw_value = readings[pointer]['value']
            if isinstance(raw_value, (int, float)):
                values.append(float(raw_value))
            elif isinstance(raw_value, bool):
                values.append(1.0 if raw_value else 0.0)
            pointer += 1
        avg_value = sum(values) / len(values) if values else None
        trends.append({
            'timestamp': _ts_to_ms(current_time),
            'average': avg_value,
            'count': len(values),
        })
        current_time = interval_end

    return {
        'device_id': device_id,
        'metric': metric,
        'interval_minutes': interval_minutes,
        'trends': trends,
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