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
from .metrics import build_metric_meta
from .models import ActuatorControl, Device, DeviceResponse, Metric, Reading
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
    """Background task handling device heartbeat checks, data retention, and capability refresh."""
    last_cleanup = datetime.utcnow()
    cleanup_interval = timedelta(hours=24)
    while True:
        try:
            await mqtt_client.mark_inactive_devices()

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
    db: AsyncSession = Depends(get_db)
):
    """Get all devices from the system - includes both active and inactive devices for administration purposes"""
    query = select(Device)
    if active_only:
        query = query.where(Device.is_active == True)

    result = await db.execute(query.order_by(Device.created_at))
    devices = result.scalars().all()
    return devices

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


# LLM Analytics endpoint for intelligent system analysis

@app.get("/api/analytics/llm-insights/{device_id}")
async def get_llm_insights(
    device_id: str,
    metrics: Optional[str] = None,  # Comma-separated metric names
    timerange: str = "24h",  # "1h", "24h", "7d", "30d", or "2024-01-01T00:00:00Z/2024-01-02T00:00:00Z"
    aggregation: str = "hourly",  # "raw", "hourly", "daily"
    include_stats: bool = True,
    include_anomalies: bool = True,
    include_correlations: bool = True,
    db: AsyncSession = Depends(get_db),
):
    """Comprehensive analytics endpoint optimized for LLM analysis of hydroponic system data.

    Provides statistical summaries, trend analysis, anomaly detection, and actionable insights
    for AI-powered decision making about hydroponic tower management.
    """
    # Parse timerange
    if timerange.endswith('h'):
        hours = int(timerange[:-1])
        since = datetime.utcnow() - timedelta(hours=hours)
        period_desc = f"Last {timerange}"
    elif timerange.endswith('d'):
        days = int(timerange[:-1])
        since = datetime.utcnow() - timedelta(days=days)
        period_desc = f"Last {timerange}"
    else:
        # Custom ISO range - for future implementation
        since = datetime.utcnow() - timedelta(hours=24)
        period_desc = "Last 24 hours"

    # Parse requested metrics
    metric_list = metrics.split(',') if metrics else None

    # Get device info
    device_result = await db.execute(
        select(Device).where(Device.device_key == device_id)
    )
    device = device_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get all metrics for this device
    metrics_query = (
        select(Metric)
        .where(Metric.device_id == device.id)
    )
    if metric_list:
        metrics_query = metrics_query.where(Metric.metric_key.in_(metric_list))

    device_metrics = (await db.execute(metrics_query)).scalars().all()

    if not device_metrics:
        raise HTTPException(status_code=404, detail="No metrics found for device")

    # Optimal ranges for common hydroponic metrics
    OPTIMAL_RANGES = {
        'temperature': [20, 26],
        'humidity': [60, 70],
        'ph': [5.5, 6.5],
        'tds_ppm': [800, 1200],
        'water_temp_c': [18, 22],
        'vpd_kpa': [0.8, 1.2]
    }

    result = {
        'device': {
            'id': device.device_key,
            'name': device.name or f"Device {device.device_key}",
            'analysis_period': period_desc,
            'last_seen': _ts_to_ms(device.last_seen) if device.last_seen else None
        },
        'metrics': {},
        'system_insights': {
            'overall_status': 'analyzing',
            'attention_needed': [],
            'correlations': [],
            'recommendations': []
        }
    }

    import statistics

    for metric in device_metrics:
        # Get recent readings for this metric
        readings_query = (
            select(Reading.timestamp, Reading.value)
            .where(
                (Reading.metric_id == metric.id) &
                (Reading.timestamp >= since)
            )
            .order_by(Reading.timestamp.desc())
            .limit(1000)  # Reasonable limit for analysis
        )

        readings_result = await db.execute(readings_query)
        readings = [(ts, val) for ts, val in readings_result.all()]

        if not readings:
            continue

        # Extract numeric values for analysis
        numeric_values = []
        for _, value in readings:
            if isinstance(value, (int, float)):
                numeric_values.append(float(value))
            elif isinstance(value, bool):
                numeric_values.append(1.0 if value else 0.0)

        if not numeric_values:
            continue

        current_value = numeric_values[0]  # Most recent
        optimal_range = OPTIMAL_RANGES.get(metric.metric_key)

        # Calculate statistics
        metric_stats = {
            'mean': round(statistics.mean(numeric_values), 2),
            'std': round(statistics.stdev(numeric_values) if len(numeric_values) > 1 else 0, 2),
            'min': round(min(numeric_values), 2),
            'max': round(max(numeric_values), 2),
        }

        # Determine status
        status = 'unknown'
        if optimal_range:
            if optimal_range[0] <= current_value <= optimal_range[1]:
                status = 'optimal'
            elif current_value < optimal_range[0] * 0.8 or current_value > optimal_range[1] * 1.2:
                status = 'critical'
            else:
                status = 'warning'

        # Trend analysis (simple)
        trend = 'stable'
        if len(numeric_values) >= 10:
            recent_avg = statistics.mean(numeric_values[:5])
            older_avg = statistics.mean(numeric_values[-5:])
            change_pct = (recent_avg - older_avg) / older_avg * 100
            if abs(change_pct) > 10:
                trend = 'increasing' if change_pct > 0 else 'decreasing'

        # Anomaly detection
        anomalies = []
        if include_anomalies and optimal_range:
            for ts, value in readings[:20]:  # Check recent 20 readings
                if isinstance(value, (int, float)):
                    val = float(value)
                    if val < optimal_range[0] or val > optimal_range[1]:
                        severity = 'critical' if (val < optimal_range[0] * 0.8 or val > optimal_range[1] * 1.2) else 'warning'
                        anomalies.append({
                            'timestamp': ts.isoformat(),
                            'value': val,
                            'severity': severity,
                            'reason': f"Outside optimal range [{optimal_range[0]}-{optimal_range[1]}]"
                        })

        result['metrics'][metric.metric_key] = {
            'display_name': metric.display_name or metric.metric_key,
            'unit': metric.unit or '',
            'optimal_range': optimal_range,
            'current': {
                'value': current_value,
                'status': status,
                'last_updated': readings[0][0].isoformat()
            },
            'statistics': {
                **metric_stats,
                'trend': trend,
                'data_points': len(readings)
            },
            'anomalies': anomalies[:5] if include_anomalies else []
        }

        # Add to attention list if needed
        if status in ['warning', 'critical']:
            result['system_insights']['attention_needed'].append(
                f"{metric.display_name or metric.metric_key}: {status} ({current_value} {metric.unit or ''})"
            )

    # Overall system status
    statuses = [m['current']['status'] for m in result['metrics'].values()]
    if 'critical' in statuses:
        result['system_insights']['overall_status'] = 'critical'
    elif 'warning' in statuses:
        result['system_insights']['overall_status'] = 'warning'
    elif 'optimal' in statuses:
        result['system_insights']['overall_status'] = 'healthy'

    # Simple recommendations
    recommendations = []
    for metric_key, data in result['metrics'].items():
        if data['current']['status'] == 'critical':
            recommendations.append({
                'priority': 'high',
                'action': f"Immediate attention needed for {data['display_name']} - value is {data['current']['value']} {data['unit']}",
                'metric': metric_key
            })
        elif data['current']['status'] == 'warning':
            recommendations.append({
                'priority': 'medium',
                'action': f"Monitor {data['display_name']} - trending outside optimal range",
                'metric': metric_key
            })

    result['system_insights']['recommendations'] = recommendations

    return result



@app.get("/api/devices/{device_id}/capabilities")
async def get_device_capabilities(
    device_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get complete device metadata - all sensors and actuators with their definitions.

    This endpoint provides the frontend with the structure of available metrics,
    separated by their actual type as stored in the database from discovery data.
    """
    # Get device info
    device_result = await db.execute(
        select(Device).where(Device.device_key == device_id)
    )
    device = device_result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    # Get all metrics for this device
    metrics_result = await db.execute(
        select(Metric).where(Metric.device_id == device.id)
    )
    metrics = metrics_result.scalars().all()

    # Separate sensors from actuators using database metric_type
    sensors = {}
    actuators = {}

    for metric in metrics:
        overrides = {}
        if metric.display_name:
            overrides['label'] = metric.display_name
        if metric.unit:
            overrides['unit'] = metric.unit

        meta = build_metric_meta(metric.metric_key, overrides if overrides else None)

        metric_info = {
            'display_name': meta.label,
            'unit': meta.unit,
            'color': meta.color,
            'created_at': metric.created_at.isoformat()
        }

        if metric.metric_type == 'sensor':
            sensors[metric.metric_key] = metric_info
        elif metric.metric_type == 'actuator':
            actuators[metric.metric_key] = metric_info
        else:
            # This should never happen if our validation is working
            raise ValueError(f"Invalid metric_type '{metric.metric_type}' for metric '{metric.metric_key}'")

    return {
        'device': {
            'id': device.device_key,
            'name': device.name or f"Device {device.device_key}",
            'description': device.description,
            'last_seen': _ts_to_ms(device.last_seen) if device.last_seen else None,
            'is_active': device.is_active
        },
        'sensors': sensors,
        'actuators': actuators,
        'summary': {
            'sensor_count': len(sensors),
            'actuator_count': len(actuators),
            'total_metrics': len(metrics)
        }
    }

# Device control endpoint


@app.post("/api/actuators/{device_id}/control")
async def control_actuator(
    device_id: str,
    actuator_control: ActuatorControl,
    db: AsyncSession = Depends(get_db),
):
    """Control any actuator via MQTT after validating it exists as an actuator in the database."""
    if actuator_control.state not in ["on", "off"]:
        raise HTTPException(status_code=400, detail="State must be 'on' or 'off'")

    # Validate that the actuator exists and is of type 'actuator'
    metric_row = await db.execute(
        select(Metric)
        .join(Device, Device.id == Metric.device_id)
        .where(
            (Device.device_key == device_id)
            & (Metric.metric_key == actuator_control.actuator_key)
            & (Metric.metric_type == 'actuator')
        )
    )
    metric_obj = metric_row.scalar_one_or_none()
    if not metric_obj:
        raise HTTPException(
            status_code=404,
            detail=f"Actuator '{actuator_control.actuator_key}' not found for device '{device_id}'"
        )

    try:
        await mqtt_client.publish_actuator_control(device_id, actuator_control)
        return {
            "message": f"Actuator '{actuator_control.actuator_key}' control command sent",
            "status": "success"
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to send actuator command: {exc}")


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
