"""Helpers for assembling historical reading responses."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    Device,
    HistoricalReading,
    Metric,
    MetricStatistics,
    Reading,
)


def _parse_history_filters(
    device_keys: Optional[str],
    metric_keys: Optional[str],
) -> Tuple[Optional[List[str]], Optional[List[str]]]:
    """Convert comma-delimited query parameters into lists of keys."""

    def _split(value: Optional[str]) -> Optional[List[str]]:
        if not value:
            return None
        values = [item.strip() for item in value.split(",") if item.strip()]
        return values or None

    return _split(device_keys), _split(metric_keys)


def _determine_downsample_interval(hours: int, downsample_minutes: Optional[int]) -> int:
    """Determine downsampling interval in minutes based on query parameters."""
    if downsample_minutes is not None:
        return downsample_minutes

    if hours < 1:
        return 0
    if hours <= 6:
        return 1
    if hours <= 24:
        return 5
    return 15


async def _fetch_history_rows(
    db: AsyncSession,
    start_time: datetime,
    end_time: datetime,
    device_key_list: Optional[Iterable[str]] = None,
    metric_key_list: Optional[Iterable[str]] = None,
) -> Tuple[Dict[str, Dict[str, List[Dict[str, Any]]]], int]:
    """Fetch historical readings grouped by device and metric."""
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
        .where(Reading.timestamp >= start_time)
        .where(Reading.timestamp <= end_time)
        .order_by(Device.device_key, Metric.metric_key, Reading.timestamp)
    )

    if device_key_list:
        query = query.where(Device.device_key.in_(device_key_list))
    if metric_key_list:
        query = query.where(Metric.metric_key.in_(metric_key_list))

    result = await db.execute(query)
    rows = result.all()

    grouped: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for device_key, metric_key, display_name, unit, timestamp, value in rows:
        grouped[device_key][metric_key].append(
            {
                "display_name": display_name,
                "unit": unit,
                "timestamp": timestamp,
                "value": value,
            }
        )

    # Ensure chronological ordering for downstream processing
    for metrics in grouped.values():
        for readings in metrics.values():
            readings.sort(key=lambda item: item["timestamp"])

    return {device: dict(metrics) for device, metrics in grouped.items()}, len(rows)


def _summarize_metric_series(
    metric_key: str,
    readings_list: List[Dict[str, Any]],
    *,
    include_stats: bool,
    downsample_minutes: int,
    limit: int,
) -> Tuple[List[HistoricalReading], Optional[MetricStatistics], int]:
    """Generate serialized readings and statistics for a metric series."""
    if not readings_list:
        return [], None, 0

    display_name = readings_list[0]["display_name"]
    unit = readings_list[0]["unit"]

    statistics: Optional[MetricStatistics] = None
    if include_stats:
        numeric_values: List[float] = []
        for reading in readings_list:
            try:
                numeric_values.append(float(reading["value"]))
            except (TypeError, ValueError):
                continue

        min_val = readings_list[0]["value"]
        max_val = readings_list[0]["value"]
        if numeric_values:
            for reading in readings_list:
                value = reading["value"]
                if isinstance(value, (int, float)):
                    if value < min_val:
                        min_val = value
                    if value > max_val:
                        max_val = value

        avg_val = sum(numeric_values) / len(numeric_values) if numeric_values else None
        first_val = readings_list[0]["value"]
        last_val = readings_list[-1]["value"]

        change = None
        change_percent = None
        if numeric_values and len(numeric_values) >= 2:
            try:
                change = float(last_val) - float(first_val)
                if float(first_val) != 0:
                    change_percent = (change / float(first_val)) * 100
            except (TypeError, ValueError):
                change = None
                change_percent = None

        statistics = MetricStatistics(
            metric_key=metric_key,
            display_name=display_name,
            unit=unit,
            count=len(readings_list),
            min=min_val,
            max=max_val,
            avg=avg_val,
            first_value=first_val,
            last_value=last_val,
            first_timestamp=readings_list[0]["timestamp"],
            last_timestamp=readings_list[-1]["timestamp"],
            change=change,
            change_percent=change_percent,
        )

    returned_points = 0
    serialized: List[HistoricalReading] = []

    if downsample_minutes > 0:
        buckets: Dict[datetime, List[Dict[str, Any]]] = defaultdict(list)

        for reading in readings_list:
            bucket_ts = reading["timestamp"].replace(second=0, microsecond=0)
            bucket_minutes = (bucket_ts.minute // downsample_minutes) * downsample_minutes
            bucket_ts = bucket_ts.replace(minute=bucket_minutes)
            buckets[bucket_ts].append(reading)

        for bucket_ts in sorted(buckets.keys(), reverse=True):
            bucket_readings = buckets[bucket_ts]
            try:
                avg_value = sum(float(item["value"]) for item in bucket_readings) / len(bucket_readings)
            except (TypeError, ValueError):
                avg_value = bucket_readings[-1]["value"]

            serialized.append(
                HistoricalReading(
                    metric_key=metric_key,
                    display_name=display_name,
                    unit=unit,
                    timestamp=bucket_ts,
                    value=avg_value,
                )
            )
            if len(serialized) >= limit:
                break

        returned_points = len(serialized)
    else:
        for reading in reversed(readings_list[:limit]):
            serialized.append(
                HistoricalReading(
                    metric_key=metric_key,
                    display_name=display_name,
                    unit=unit,
                    timestamp=reading["timestamp"],
                    value=reading["value"],
                )
            )
        returned_points = min(len(readings_list), limit)

    return serialized, statistics, returned_points
