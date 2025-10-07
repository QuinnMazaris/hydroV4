"""Async client for interacting with the hydro backend API."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

import httpx

from .config import settings


@dataclass
class DeviceInfo:
    """Canonical device representation used by the agent."""

    device_key: str
    name: Optional[str]
    device_type: str
    is_active: bool
    description: Optional[str]
    metadata: Dict[str, Any]


@dataclass
class MetricReading:
    """Latest metric reading bundle."""

    metric_key: str
    value: Any
    unit: Optional[str]
    timestamp: str
    display_name: Optional[str]


class HydroAPIClient:
    """Thin convenience wrapper around the hydro backend REST API."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        *,
        dry_run: bool | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url or settings.hydro_api_base_url,
            timeout=timeout or settings.hydro_api_timeout_seconds,
        )
        self._dry_run = settings.actuator_dry_run if dry_run is None else dry_run

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_devices(
        self,
        *,
        device_type: Optional[str] = None,
        active_only: bool = True,
    ) -> List[DeviceInfo]:
        params: Dict[str, Any] = {"active_only": str(active_only).lower()}
        if device_type:
            params["device_type"] = device_type

        response = await self._client.get("/api/devices", params=params)
        response.raise_for_status()
        payload: Iterable[Dict[str, Any]] = response.json()
        devices: List[DeviceInfo] = []
        for item in payload:
            metadata: Dict[str, Any] = {}
            raw_metadata = item.get("device_metadata")
            if isinstance(raw_metadata, str) and raw_metadata:
                try:
                    metadata = json.loads(raw_metadata)
                except json.JSONDecodeError:
                    metadata = {"raw": raw_metadata}
            elif isinstance(raw_metadata, dict):
                metadata = raw_metadata

            devices.append(
                DeviceInfo(
                    device_key=item["device_key"],
                    name=item.get("name"),
                    device_type=item.get("device_type", "unknown"),
                    is_active=bool(item.get("is_active", False)),
                    description=item.get("description"),
                    metadata=metadata,
                )
            )
        return devices

    async def latest_readings(
        self,
        *,
        device_keys: Optional[List[str]] = None,
    ) -> Dict[str, List[MetricReading]]:
        params: Dict[str, Any] = {}
        if device_keys:
            params["device_keys"] = ",".join(device_keys)

        response = await self._client.get("/api/readings/latest", params=params)
        response.raise_for_status()
        payload: Dict[str, Any] = response.json()
        readings: Dict[str, List[MetricReading]] = {}

        for device_key, metrics in payload.get("devices", {}).items():
            parsed_metrics: List[MetricReading] = []
            for metric in metrics:
                parsed_metrics.append(
                    MetricReading(
                        metric_key=metric["metric_key"],
                        value=metric.get("value"),
                        unit=metric.get("unit"),
                        timestamp=metric.get("timestamp"),
                        display_name=metric.get("display_name"),
                    )
                )
            readings[device_key] = parsed_metrics
        return readings

    async def control_actuators(self, commands: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not commands:
            return {"processed": 0, "skipped": 0, "missing": []}

        if self._dry_run:
            return {
                "processed": len(commands),
                "skipped": 0,
                "missing": [],
                "details": commands,
                "dry_run": True,
            }

        response = await self._client.post(
            "/api/actuators/batch-control",
            json={"commands": commands},
        )
        response.raise_for_status()
        return response.json()

    async def capture_camera(self, device_key: str) -> Dict[str, Any]:
        if not device_key:
            raise ValueError("device_key is required")

        response = await self._client.post(f"/api/cameras/{device_key}/capture")
        response.raise_for_status()
        return response.json()

    async def get_camera_image(self, device_key: str, *, days_ago: int = 0) -> Dict[str, Any]:
        params = {"days_ago": days_ago}
        response = await self._client.get(f"/api/cameras/{device_key}/image", params=params)
        # API returns file response; keep metadata minimal to avoid streaming binary in tests
        if response.status_code == 200:
            return {
                "status": "success",
                "content_type": response.headers.get("content-type"),
                "content_length": response.headers.get("content-length"),
            }
        response.raise_for_status()
        return {"status": "unknown"}

    async def get_historical_readings(
        self,
        *,
        device_keys: Optional[List[str]] = None,
        metric_keys: Optional[List[str]] = None,
        hours: int = 24,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "hours": hours,
            "limit": limit,
        }
        if device_keys:
            params["device_keys"] = ",".join(device_keys)
        if metric_keys:
            params["metric_keys"] = ",".join(metric_keys)

        response = await self._client.get("/api/readings/historical", params=params)
        response.raise_for_status()
        return response.json()


@asynccontextmanager
async def hydro_client(**kwargs: Any):
    client = HydroAPIClient(**kwargs)
    try:
        yield client
    finally:
        await client.aclose()
