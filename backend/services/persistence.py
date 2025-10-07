from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional, Sequence

from sqlalchemy import delete, select, update
from loguru import logger

from ..database import AsyncSessionLocal
from ..models import Device, JsonValue, Metric, Reading
from ..events import event_broker
from ..utils.time import ensure_utc, epoch_millis, utc_now


async def upsert_device(
    device_key: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    metadata: Optional[str] = None,
    last_seen: Optional[datetime] = None,
    device_type: str = 'mqtt_sensor',
) -> Device:
    """Insert a new device or refresh an existing one."""
    touch_time = ensure_utc(last_seen) if last_seen else utc_now()
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Device).where(Device.device_key == device_key)
        )
        device = result.scalar_one_or_none()

        if device:
            device.last_seen = touch_time
            device.is_active = True
            if name and device.name != name:
                device.name = name
            if description is not None and description != device.description:
                device.description = description
            if metadata is not None and metadata != device.device_metadata:
                device.device_metadata = metadata
            if device_type and device.device_type != device_type:
                device.device_type = device_type
        else:
            device = Device(
                device_key=device_key,
                name=name,
                description=description,
                device_metadata=metadata,
                last_seen=touch_time,
                is_active=True,
                device_type=device_type,
            )
            session.add(device)

        await session.commit()
        await session.refresh(device)

        # Publish device event to notify WebSocket clients of the update
        try:
            await event_broker.publish({
                'type': 'device',
                'device_id': device.device_key,
                'is_active': device.is_active,
                'last_seen': epoch_millis(device.last_seen) if device.last_seen else None,
            })
        except Exception as e:
            logger.debug(f"Failed to publish device event for {device.device_key}: {e}")

        return device


async def sync_device_metrics(
    device_id: int,
    metric_defs: Sequence[Dict[str, Optional[str]]],
) -> Dict[str, Metric]:
    """Ensure metrics exist for the given device and return a keyed mapping."""
    if not metric_defs:
        return {}

    normalized = []
    for definition in metric_defs:
        key = (definition.get("metric_key") or definition.get("key") or "").strip()
        if not key:
            continue
        metric_type = definition.get("metric_type")
        if not metric_type:
            raise ValueError(f"metric_type is required for metric '{key}' but was not provided in discovery data")
        if metric_type not in ["sensor", "actuator"]:
            raise ValueError(f"metric_type must be 'sensor' or 'actuator', got '{metric_type}' for metric '{key}'")

        normalized.append(
            {
                "metric_key": key,
                "display_name": (definition.get("display_name") or definition.get("label") or key) or key,
                "unit": (definition.get("unit") or None) or None,
                "metric_type": metric_type,
            }
        )

    if not normalized:
        return {}

    keys = [item["metric_key"] for item in normalized]

    async with AsyncSessionLocal() as session:
        existing_result = await session.execute(
            select(Metric).where(
                (Metric.device_id == device_id) & (Metric.metric_key.in_(keys))
            )
        )
        existing = {metric.metric_key: metric for metric in existing_result.scalars().all()}

        for item in normalized:
            metric = existing.get(item["metric_key"])
            if metric:
                updated = False
                if item["display_name"] and metric.display_name != item["display_name"]:
                    metric.display_name = item["display_name"]
                    updated = True
                if metric.unit != item["unit"]:
                    metric.unit = item["unit"]
                    updated = True
                if metric.metric_type != item["metric_type"]:
                    metric.metric_type = item["metric_type"]
                    updated = True
                if updated:
                    session.add(metric)
            else:
                metric = Metric(
                    device_id=device_id,
                    metric_key=item["metric_key"],
                    display_name=item["display_name"],
                    unit=item["unit"],
                    metric_type=item["metric_type"],
                )
                session.add(metric)
                existing[item["metric_key"]] = metric

        await session.commit()

        result = await session.execute(
            select(Metric).where(Metric.device_id == device_id)
        )
        return {metric.metric_key: metric for metric in result.scalars().all()}


async def insert_reading(
    metric_id: int,
    value: JsonValue,
    timestamp: Optional[datetime] = None,
) -> Reading:
    """Persist a single metric reading."""
    reading = Reading(
        metric_id=metric_id,
        timestamp=ensure_utc(timestamp) if timestamp else utc_now(),
        value=value,
    )
    async with AsyncSessionLocal() as session:
        session.add(reading)
        await session.commit()
        await session.refresh(reading)
        return reading


async def get_metric_by_key(device_key: str, metric_key: str) -> Optional[Metric]:
    """Return a metric for the given device/metric key combination."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Metric)
            .join(Device, Metric.device_id == Device.id)
            .where(
                (Device.device_key == device_key)
                & (Metric.metric_key == metric_key)
            )
        )
        return result.scalar_one_or_none()


async def get_metric_map(device_key: str) -> Dict[str, Metric]:
    """Return all metrics for a device keyed by metric_key."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Metric)
            .join(Device, Metric.device_id == Device.id)
            .where(Device.device_key == device_key)
        )
        metrics = result.scalars().all()
        return {metric.metric_key: metric for metric in metrics}


async def mark_devices_inactive(cutoff: datetime, device_type: Optional[str] = None) -> None:
    """Mark devices as inactive if they haven't been seen since cutoff time.

    Args:
        cutoff: Timestamp before which devices are considered inactive
        device_type: Optional device type filter. If provided, only affects that type.
    """
    async with AsyncSessionLocal() as session:
        query = update(Device).where(Device.last_seen < cutoff)

        if device_type:
            query = query.where(Device.device_type == device_type)

        query = query.values(is_active=False)
        await session.execute(query)
        await session.commit()


async def delete_old_readings(before: datetime) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(Reading).where(Reading.timestamp < before)
        )
        await session.commit()
