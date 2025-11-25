"""Async client for interacting with the hydro backend API."""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
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

    async def control_actuators(
        self, 
        commands: List[Dict[str, Any]], 
        *, 
        source: str = "ai",
        force: bool = False,
    ) -> Dict[str, Any]:
        """Control actuators with source-based permissions.
        
        Args:
            commands: List of actuator commands
            source: Who is making the request ("ai", "automation", "user")
            force: Emergency override for user in AUTO mode
            
        Mode logic:
            AUTO mode: AI and automation can control. User blocked unless force=True.
            MANUAL mode: User can control. AI and automation blocked.
        """
        if not commands:
            return {"processed": 0, "skipped": 0, "missing": []}

        if self._dry_run:
            return {
                "processed": len(commands),
                "skipped": 0,
                "missing": [],
                "details": commands,
                "dry_run": True,
                "source": source,
            }

        response = await self._client.post(
            "/api/actuators/batch-control",
            json={
                "commands": commands,
                "source": source,
                "force": force,
            },
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
        if response.status_code == 200:
            image_bytes = response.content
            image_base64 = base64.b64encode(image_bytes).decode('utf-8')
            content_type = response.headers.get("content-type", "image/webp")

            return {
                "status": "success",
                "content_type": content_type,
                "content_length": response.headers.get("content-length"),
                "image_base64": image_base64
            }
        response.raise_for_status()
        return {"status": "unknown"}

    async def get_historical_readings(
        self,
        *,
        device_keys: Optional[List[str]] = None,
        metric_keys: Optional[List[str]] = None,
        hours: int = 24,
        limit: int = 100,
        downsample_minutes: Optional[int] = None,
        include_stats: bool = True,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "hours": hours,
            "limit": limit,
            "include_stats": str(include_stats).lower(),
        }
        if device_keys:
            params["device_keys"] = ",".join(device_keys)
        if metric_keys:
            params["metric_keys"] = ",".join(metric_keys)
        if downsample_minutes is not None:
            params["downsample_minutes"] = downsample_minutes

        response = await self._client.get("/api/readings/historical", params=params)
        response.raise_for_status()
        return response.json()

    async def get_actuator_modes(
        self,
        *,
        device_keys: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, str]]:
        """Get control modes for all actuators."""
        params = {}
        if device_keys:
            params["device_keys"] = ",".join(device_keys)

        response = await self._client.get("/api/actuators/modes", params=params)
        response.raise_for_status()
        return response.json().get("modes", {})

    async def set_actuator_mode(
        self,
        device_key: str,
        actuator_key: str,
        mode: str,
    ) -> Dict[str, Any]:
        """Set control mode for a specific actuator."""
        if mode not in ("manual", "auto"):
            raise ValueError("Mode must be 'manual' or 'auto'")

        response = await self._client.post(
            f"/api/actuators/{device_key}/{actuator_key}/mode",
            params={"mode": mode},
        )
        response.raise_for_status()
        return response.json()



    async def control_actuator(
        self,
        device_key: str,
        actuator_key: str,
        state: str,
        *,
        source: str = "ai",
    ) -> bool:
        """Control a single actuator.

        Args:
            device_key: Device identifier
            actuator_key: Actuator/metric key
            state: Desired state (e.g., 'on', 'off')
            source: Who is making the request ("ai", "automation", "user")

        Returns:
            True if successful, False otherwise
        """
        commands = [
            {
                "device_id": device_key,
                "actuator_key": actuator_key,
                "state": state,
            }
        ]

        result = await self.control_actuators(commands, source=source)

        # Check if command was processed successfully
        processed = result.get("processed", 0)
        return processed > 0

    async def save_conversation_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Persist conversation messages via the backend API."""

        if not messages:
            return []

        payload: List[Dict[str, Any]] = []
        for message in messages:
            serialised = dict(message)
            timestamp = serialised.get("timestamp")
            if isinstance(timestamp, datetime):
                serialised["timestamp"] = timestamp.isoformat()
            created_at = serialised.get("created_at")
            if isinstance(created_at, datetime):
                serialised["created_at"] = created_at.isoformat()
            payload.append(serialised)

        response = await self._client.post("/api/conversations", json=payload)
        response.raise_for_status()
        return response.json()


@asynccontextmanager
async def hydro_client(**kwargs: Any):
    client = HydroAPIClient(**kwargs)
    try:
        yield client
    finally:
        await client.aclose()
