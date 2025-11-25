import asyncio
import os
from contextlib import suppress
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import AsyncSessionLocal, get_db, init_db
from .events import event_broker
from .metrics import build_metric_meta
from .models import (
    ActuatorBatchControl, ActuatorCommand, ActuatorControl,
    Device, DeviceResponse, Metric, Reading,
    CameraFrame, CameraFrameResponse,
    ConversationMessageCreate, ConversationMessageResponse,
    LatestMetricSnapshot, LatestReadingsResponse,
    HistoricalReading, HistoricalReadingsResponse,
    MetricStatistics,
)
from .mqtt_client import mqtt_client
from .services.history import (
    _determine_downsample_interval,
    _fetch_history_rows,
    _parse_history_filters,
    _summarize_metric_series,
)
from .services.persistence import delete_old_readings, mark_devices_inactive
from .services.agent_history import (
    get_conversation_messages,
    get_recent_automated_highlights,
    save_conversation_messages,
    to_conversation_response,
)
from .services.camera_sync import sync_cameras_to_db
from .services.frame_capture import capture_all_cameras, cleanup_old_frames, capture_frame_for_camera
from .utils.time import epoch_millis, utc_now

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
    """Background task handling device heartbeat checks, data retention, camera sync, and frame capture."""
    last_cleanup = utc_now()
    last_frame_capture = utc_now()
    cleanup_interval = timedelta(hours=24)
    frame_capture_interval = timedelta(minutes=settings.frame_capture_interval_minutes)

    while True:
        try:
            now = utc_now()

            # Sync cameras from MediaMTX to database
            await sync_cameras_to_db()

            # Capture frames from all cameras if enabled and interval has passed
            if settings.frame_capture_enabled and (now - last_frame_capture) >= frame_capture_interval:
                await capture_all_cameras()
                last_frame_capture = now

            # Mark inactive devices (both MQTT and cameras)
            cutoff_time = utc_now() - timedelta(seconds=settings.sensor_discovery_timeout)
            await mqtt_client.mark_inactive_devices()  # MQTT devices
            await mark_devices_inactive(cutoff_time, device_type='camera')  # Cameras

            # Cleanup old data once per day
            if (now - last_cleanup) >= cleanup_interval:
                if settings.data_retention_days > 0:
                    cutoff = now - timedelta(days=settings.data_retention_days)
                    await delete_old_readings(cutoff)

                # Cleanup old frames
                await cleanup_old_frames()
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
            "ts": utc_now().timestamp()
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
    return epoch_millis(ts)


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
    device_keys: Optional[List[str]] = None,
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
            Metric.display_name,
            Metric.unit,
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

    if device_keys:
        query = query.where(Device.device_key.in_(device_keys))
    if metric_keys:
        query = query.where(Metric.metric_key.in_(metric_keys))
    
    # Only show active metrics
    query = query.where(Metric.is_active == True)

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
                .where(Metric.is_active == True)
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
            current_time_ms = epoch_millis(utc_now())

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

            since = utc_now() - timedelta(hours=24)
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
@app.get("/api/readings/latest", response_model=LatestReadingsResponse)
async def get_latest_readings(
    device_keys: Optional[str] = Query(
        default=None,
        description="Comma separated device_key list to filter",
    ),
    db: AsyncSession = Depends(get_db),
):
    keys = None
    if device_keys:
        keys = [key.strip() for key in device_keys.split(",") if key.strip()]

    rows = await _latest_metric_rows(db, device_keys=keys)
    devices: Dict[str, List[LatestMetricSnapshot]] = {}

    for device_key, metric_key, display_name, unit, timestamp, value in rows:
        snapshot = LatestMetricSnapshot(
            metric_key=metric_key,
            display_name=display_name,
            unit=unit,
            timestamp=timestamp,
            value=value,
        )
        devices.setdefault(device_key, []).append(snapshot)

    return LatestReadingsResponse(devices=devices)


@app.get("/api/readings/historical", response_model=HistoricalReadingsResponse)
async def get_historical_readings(
    device_keys: Optional[str] = Query(
        default=None,
        description="Comma separated device_key list to filter",
    ),
    metric_keys: Optional[str] = Query(
        default=None,
        description="Comma separated metric_key list to filter",
    ),
    hours: int = Query(
        default=24,
        ge=1,
        le=720,
        description="Number of hours of history to retrieve (max 30 days)",
    ),
    limit: int = Query(
        default=1000,
        ge=1,
        le=1000,
        description="Maximum number of data points to return per metric",
    ),
    downsample_minutes: Optional[int] = Query(
        default=None,
        ge=1,
        le=1440,
        description="Downsample to N-minute intervals (auto if not specified)",
    ),
    include_stats: bool = Query(
        default=True,
        description="Include statistical summary for each metric",
    ),
    db: AsyncSession = Depends(get_db),
):
    """
    Get historical sensor readings with intelligent downsampling and statistics.

    Auto-downsampling based on time range:
    - <1 hour: every reading
    - 1-6 hours: 1-minute averages
    - 6-24 hours: 5-minute averages
    - 24+ hours: 15-minute averages
    """
    # Calculate time range
    end_time = utc_now()
    start_time = end_time - timedelta(hours=hours)

    # Parse filter lists and determine downsampling
    device_key_list, metric_key_list = _parse_history_filters(device_keys, metric_keys)
    downsample_minutes = _determine_downsample_interval(hours, downsample_minutes)

    by_device_metric, total_raw_points = await _fetch_history_rows(
        db,
        start_time,
        end_time,
        device_key_list=device_key_list,
        metric_key_list=metric_key_list,
    )

    devices: Dict[str, List[HistoricalReading]] = {}
    statistics: Dict[str, List[MetricStatistics]] = {}
    total_returned_points = 0

    for device_key, metrics in by_device_metric.items():
        devices[device_key] = []
        if include_stats:
            statistics[device_key] = []

        for metric_key, readings_list in metrics.items():
            serialized, metric_stats, returned = _summarize_metric_series(
                metric_key,
                readings_list,
                include_stats=include_stats,
                downsample_minutes=downsample_minutes,
                limit=limit,
            )

            devices[device_key].extend(serialized)
            total_returned_points += returned

            if include_stats and metric_stats is not None:
                statistics[device_key].append(metric_stats)

    return HistoricalReadingsResponse(
        devices=devices,
        start_time=start_time,
        end_time=end_time,
        total_points=total_raw_points,
        returned_points=total_returned_points,
        aggregated=(downsample_minutes > 0),
        statistics=statistics if include_stats else None,
    )


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
    """Batch actuator control with mode-based permissions.
    
    Mode logic:
    - AUTO mode: AI and automation can control. User blocked unless force=True.
    - MANUAL mode: User can control. AI and automation blocked.
    
    This means AUTO is the normal operating mode where the system runs itself,
    and MANUAL is the emergency override mode for human intervention.
    """
    if not batch.commands:
        return {"processed": 0, "skipped": 0, "missing": [], "blocked": []}

    source = batch.source.lower()
    if source not in {"user", "ai", "automation"}:
        source = "user"

    deduped: Dict[tuple[str, str], ActuatorCommand] = {}
    for command in batch.commands:
        device_id = command.device_id.strip()
        # Normalize key: lowercase and remove spaces (e.g. "Relay 1" -> "relay1")
        normalized_key = command.actuator_key.lower().replace(" ", "")
        
        if not device_id or not normalized_key:
            raise HTTPException(status_code=400, detail="Each command requires device_id and actuator_key")
        state = command.state.lower()
        if state not in {"on", "off"}:
            raise HTTPException(status_code=400, detail=f"Invalid state '{command.state}' for actuator '{command.actuator_key}'")
        deduped[(device_id, normalized_key)] = ActuatorCommand(
            device_id=device_id,
            actuator_key=normalized_key,
            state=state,
        )

    if not deduped:
        return {"processed": 0, "skipped": 0, "missing": [], "blocked": []}

    pairs = list(deduped.keys())

    # Check which actuators exist and their control mode
    result = await db.execute(
        select(Device.device_key, Metric.metric_key, Metric.control_mode)
        .join(Metric, Metric.device_id == Device.id)
        .where(tuple_(Device.device_key, Metric.metric_key).in_(pairs))
        .where(Metric.metric_type == 'actuator')
    )

    valid_pairs = {}
    for device_key, metric_key, control_mode in result.all():
        valid_pairs[(device_key, metric_key)] = control_mode or 'manual'

    missing = []
    blocked = []

    # Check permissions based on mode and source
    def is_allowed(mode: str, source: str, force: bool) -> tuple[bool, str]:
        """Check if source is allowed to control actuator in given mode.
        
        AUTO mode = normal operation (AI + automation control)
        MANUAL mode = emergency override (user control only)
        """
        if mode == 'auto':
            # AUTO mode: AI and automation allowed, user blocked unless force
            if source in ('ai', 'automation'):
                return True, ""
            elif source == 'user' and force:
                return True, ""  # Emergency override
            else:
                return False, "Actuator is in AUTO mode (use force=true for emergency override)"
        else:  # manual mode
            # MANUAL mode: User allowed, AI and automation blocked
            if source == 'user':
                return True, ""
            else:
                return False, "Actuator is in MANUAL mode (user emergency override active)"

    # Categorize commands
    for device_id, actuator_key in pairs:
        if (device_id, actuator_key) not in valid_pairs:
            missing.append({"device_id": device_id, "actuator_key": actuator_key})
        else:
            mode = valid_pairs[(device_id, actuator_key)]
            allowed, reason = is_allowed(mode, source, batch.force)
            if not allowed:
                blocked.append({
                    "device_id": device_id,
                    "actuator_key": actuator_key,
                    "reason": reason,
                    "mode": mode,
                    "source": source
                })

    # Process allowed commands
    device_commands: Dict[str, List[ActuatorControl]] = defaultdict(list)
    processed_details: List[Dict[str, Any]] = []

    for device_id, actuator_key in pairs:
        if (device_id, actuator_key) not in valid_pairs:
            continue
        
        mode = valid_pairs[(device_id, actuator_key)]
        allowed, _ = is_allowed(mode, source, batch.force)
        if not allowed:
            continue

        command = deduped[(device_id, actuator_key)]
        control = ActuatorControl(actuator_key=command.actuator_key, state=command.state)
        device_commands[device_id].append(control)
        processed_details.append({
            "device_id": device_id,
            "actuator_key": actuator_key,
            "state": command.state,
            "mode": mode,
        })

    # Publish to MQTT
    for device_id, controls in device_commands.items():
        await mqtt_client.publish_actuator_batch(device_id, controls)

    return {
        "processed": len(processed_details),
        "skipped": len(deduped) - len(processed_details),
        "missing": missing,
        "blocked": blocked,
        "details": processed_details,
        "source": source,
    }


@app.get("/api/actuators/modes")
async def get_actuator_modes(
    device_keys: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Get control modes for all actuators."""

    query = (
        select(Device.device_key, Metric.metric_key, Metric.control_mode)
        .join(Metric, Metric.device_id == Device.id)
        .where(Metric.metric_type == 'actuator')
    )

    if device_keys:
        keys = [key.strip() for key in device_keys.split(",")]
        query = query.where(Device.device_key.in_(keys))

    result = await db.execute(query)

    modes = {}
    for device_key, metric_key, control_mode in result.all():
        if device_key not in modes:
            modes[device_key] = {}
        modes[device_key][metric_key] = control_mode or 'manual'

    return {"modes": modes}


@app.post("/api/actuators/mode/global")
async def set_global_control_mode(
    mode: str = Query(..., regex="^(manual|auto)$"),
    db: AsyncSession = Depends(get_db),
):
    """Set all actuators to manual or auto mode globally."""

    result = await db.execute(
        select(Metric).where(Metric.metric_type == 'actuator')
    )
    actuators = result.scalars().all()

    updated_count = 0
    for actuator in actuators:
        actuator.control_mode = mode
        updated_count += 1

    await db.commit()

    # Broadcast mode change event
    await event_broker.publish({
        "type": "global_mode_changed",
        "mode": mode,
        "updated_count": updated_count,
        "timestamp": utc_now().timestamp()
    })

    print(f"Global mode changed to {mode.upper()} ({updated_count} actuators)")

    return {"mode": mode, "updated_actuators": updated_count}


@app.post("/api/actuators/{device_key}/{actuator_key}/mode")
async def set_actuator_mode(
    device_key: str,
    actuator_key: str,
    mode: str = Query(..., regex="^(manual|auto)$"),
    db: AsyncSession = Depends(get_db),
):
    """Set control mode for a specific actuator (for future use)."""

    result = await db.execute(
        select(Metric)
        .join(Device, Device.id == Metric.device_id)
        .where(Device.device_key == device_key)
        .where(Metric.metric_key == actuator_key.lower().replace(" ", ""))
        .where(Metric.metric_type == 'actuator')
    )
    metric = result.scalar_one_or_none()

    if not metric:
        raise HTTPException(status_code=404, detail="Actuator not found")

    metric.control_mode = mode
    await db.commit()

    await event_broker.publish({
        "type": "control_mode_changed",
        "device_key": device_key,
        "actuator_key": actuator_key,
        "mode": mode,
        "timestamp": utc_now().timestamp()
    })

    return {
        "device_key": device_key,
        "actuator_key": actuator_key,
        "control_mode": mode
    }


@app.patch("/api/metrics/{device_key}/{metric_key}/nickname")
async def update_metric_nickname(
    device_key: str,
    metric_key: str,
    nickname: Optional[str] = Query(None, description="Custom display name (null to clear)"),
    db: AsyncSession = Depends(get_db),
):
    """Set a custom nickname/display name for a metric (sensor or actuator)."""

    result = await db.execute(
        select(Metric)
        .join(Device, Device.id == Metric.device_id)
        .where(Device.device_key == device_key)
        .where(Metric.metric_key == metric_key)
    )
    metric = result.scalar_one_or_none()

    if not metric:
        raise HTTPException(status_code=404, detail="Metric not found")

    metric.display_name = nickname
    await db.commit()

    await event_broker.publish({
        "type": "metric_nickname_updated",
        "device_key": device_key,
        "metric_key": metric_key,
        "display_name": nickname,
        "timestamp": utc_now().timestamp()
    })

    return {
        "device_key": device_key,
        "metric_key": metric_key,
        "display_name": nickname,
        "metric_type": metric.metric_type
    }


# Health check
@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "mqtt_connected": mqtt_client.is_connected,
        "timestamp": utc_now()
    }


# ---------------- Conversation Endpoints ---------------- #


@app.post("/api/conversations", response_model=List[ConversationMessageResponse])
async def create_conversation_messages(
    messages: List[ConversationMessageCreate],
):
    """Store one or more conversation messages."""

    saved = await save_conversation_messages(messages)
    return [to_conversation_response(message) for message in saved]


@app.get("/api/conversations", response_model=List[ConversationMessageResponse])
async def list_conversation_messages(
    limit: int = Query(100, ge=1, le=500),
    since: Optional[datetime] = Query(None),
    source: Optional[str] = Query(None, pattern="^(automated|manual)$"),
):
    """Return conversation history sorted chronologically."""

    messages = await get_conversation_messages(limit=limit, since=since, source=source)
    return [to_conversation_response(message) for message in messages]


@app.get("/api/conversations/highlights", response_model=List[ConversationMessageResponse])
async def conversation_highlights(limit: int = Query(5, ge=1, le=20)):
    """Return recent automated assistant messages for dashboard highlights."""

    messages = await get_recent_automated_highlights(limit=limit)
    return [to_conversation_response(message) for message in messages]


# ---------------- Camera Frame Endpoints ---------------- #

# Simplified Camera Image Endpoints
@app.get("/api/cameras/{device_key}/image")
async def get_camera_image(
    device_key: str,
    days_ago: int = Query(0, ge=0, le=30, description="Get image from N days ago (0 = latest)"),
    db: AsyncSession = Depends(get_db),
):
    """Get camera image - latest by default, or historical by days_ago parameter."""
    # Calculate target timestamp
    target_time = utc_now() - timedelta(days=days_ago)

    # Find frame closest to target time
    query = (
        select(CameraFrame)
        .where(CameraFrame.device_key == device_key)
        .where(CameraFrame.timestamp <= target_time)
        .order_by(CameraFrame.timestamp.desc())
        .limit(1)
    )

    result = await db.execute(query)
    frame = result.scalar_one_or_none()

    if not frame:
        raise HTTPException(status_code=404, detail=f"No frame found for camera {device_key}")

    # Build full file path
    full_path = os.path.join("/app", frame.file_path)

    if not os.path.exists(full_path):
        raise HTTPException(status_code=404, detail="Frame file not found on disk")

    return FileResponse(
        full_path,
        media_type="image/webp",
        filename=f"{device_key}_{frame.timestamp.strftime('%Y%m%d_%H%M%S')}.webp"
    )


@app.post("/api/cameras/{device_key}/capture")
async def capture_camera_frame(
    device_key: str,
    db: AsyncSession = Depends(get_db),
):
    """Trigger immediate frame capture for a camera."""
    # Verify camera exists and is active
    device_result = await db.execute(
        select(Device).where(
            Device.device_key == device_key,
            Device.device_type == 'camera'
        )
    )
    device = device_result.scalar_one_or_none()

    if not device:
        raise HTTPException(
            status_code=404,
            detail=f"Camera {device_key} not found or not active"
        )

    # Capture frame
    frame = await capture_frame_for_camera(device_key)

    if not frame:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to capture frame for camera {device_key}"
        )

    return frame


@app.get("/api/automation/rules")
async def get_automation_rules():
    """Get all automation rules (proxied from gardener agent)."""
    import httpx

    gardener_url = os.getenv("GARDENER_API_URL", "http://localhost:8600")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{gardener_url}/automation/rules", timeout=10.0)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to gardener service: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch automation rules: {str(e)}"
        )


@app.post("/api/automation/rules")
async def create_automation_rule(rule: Dict[str, Any]):
    """Create a new automation rule (proxied to gardener agent)."""
    import httpx

    gardener_url = os.getenv("GARDENER_API_URL", "http://localhost:8600")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{gardener_url}/automation/rules",
                json=rule,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, 'response') else 503,
            detail=f"Gardener service error: {str(e)}"
        )


@app.patch("/api/automation/rules/{rule_id}")
async def update_automation_rule(rule_id: str, rule: Dict[str, Any]):
    """Update an automation rule (proxied to gardener agent)."""
    import httpx

    gardener_url = os.getenv("GARDENER_API_URL", "http://localhost:8600")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{gardener_url}/automation/rules/{rule_id}",
                json=rule,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, 'response') else 503,
            detail=f"Gardener service error: {str(e)}"
        )


@app.delete("/api/automation/rules/{rule_id}")
async def delete_automation_rule(rule_id: str):
    """Delete an automation rule (proxied to gardener agent)."""
    import httpx

    gardener_url = os.getenv("GARDENER_API_URL", "http://localhost:8600")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.delete(
                f"{gardener_url}/automation/rules/{rule_id}",
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, 'response') else 503,
            detail=f"Gardener service error: {str(e)}"
        )


@app.post("/api/automation/rules/{rule_id}/toggle")
async def toggle_automation_rule(rule_id: str, toggle: Dict[str, bool]):
    """Toggle an automation rule (proxied to gardener agent)."""
    import httpx

    gardener_url = os.getenv("GARDENER_API_URL", "http://localhost:8600")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{gardener_url}/automation/rules/{rule_id}/toggle",
                json=toggle,
                timeout=10.0
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=e.response.status_code if hasattr(e, 'response') else 503,
            detail=f"Gardener service error: {str(e)}"
        )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)
