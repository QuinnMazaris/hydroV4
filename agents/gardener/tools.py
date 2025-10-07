"""Tool registration and handlers for the gardener agent."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .hydro_client import DeviceInfo, HydroAPIClient

ToolHandler = Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]]


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: Dict[str, Any]
    handler: ToolHandler


class ToolRegistry:
    """Maintains the list of tools exposed to the LLM layer."""

    def __init__(self, client: HydroAPIClient) -> None:
        self._client = client
        self._tools: Dict[str, ToolSpec] = {}
        self._device_cache: List[DeviceInfo] = []
        self._lock = asyncio.Lock()

    @property
    def devices(self) -> List[DeviceInfo]:  # pragma: no cover - trivial
        return self._device_cache

    async def refresh(self) -> None:
        """Refresh the device cache and rebuild tool catalog."""

        async with self._lock:
            self._device_cache = await self._client.list_devices(active_only=True)
            cameras = [d for d in self._device_cache if d.device_type == "camera"]
            sensors = [d for d in self._device_cache if d.device_type != "camera"]

            camera_listing = ", ".join(
                f"{(device.name or device.device_key)} [{device.device_key}]"
                for device in cameras
            ) or "no cameras currently online"

            sensor_listing = ", ".join(
                f"{(device.name or device.device_key)} [{device.device_key}]"
                for device in sensors
            ) or "no sensors currently online"

            self._tools = {
                "get_sensor_snapshot": ToolSpec(
                    name="get_sensor_snapshot",
                    description=(
                        "Return the most recent readings for the hydro devices. "
                        f"Current active sensor devices: {sensor_listing}. "
                        "Provide an optional list of device_keys to filter."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "device_keys": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of device_keys to include."
                            }
                        },
                        "additionalProperties": False,
                    },
                    handler=self._handle_sensor_snapshot,
                ),
                "control_actuators": ToolSpec(
                    name="control_actuators",
                    description=(
                        "Toggle actuators on hydro devices. Commands will be validated against "
                        "known actuators before publishing."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "commands": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["device_id", "actuator_key", "state"],
                                    "properties": {
                                        "device_id": {"type": "string"},
                                        "actuator_key": {"type": "string"},
                                        "state": {
                                            "type": "string",
                                            "enum": ["on", "off"],
                                        },
                                    },
                                    "additionalProperties": False,
                                },
                                "minItems": 1,
                            }
                        },
                        "required": ["commands"],
                        "additionalProperties": False,
                    },
                    handler=self._handle_control_actuators,
                ),
                "get_camera_image": ToolSpec(
                    name="get_camera_image",
                    description=(
                        "Get camera image (latest by default, or historical). "
                        f"Current active cameras: {camera_listing}. "
                        "Images are auto-captured every 5 minutes."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "device_key": {
                                "type": "string",
                                "description": "Camera device_key"
                            },
                            "days_ago": {
                                "type": "integer",
                                "description": "Get image from N days ago (0 = latest, max 30)",
                                "minimum": 0,
                                "maximum": 30,
                                "default": 0
                            }
                        },
                        "required": ["device_key"],
                        "additionalProperties": False,
                    },
                    handler=self._handle_get_camera_image,
                ),
                "get_historical_readings": ToolSpec(
                    name="get_historical_readings",
                    description=(
                        "Get historical sensor readings over a time range. "
                        "Useful for analyzing trends, comparing conditions, or reviewing past data."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "device_keys": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of device_keys to filter (optional)"
                            },
                            "metric_keys": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of metric_keys to filter, e.g. ['temperature', 'humidity'] (optional)"
                            },
                            "hours": {
                                "type": "integer",
                                "description": "Number of hours of history (default 24, max 720 = 30 days)",
                                "minimum": 1,
                                "maximum": 720,
                                "default": 24
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Max data points to return (default 1000, max 10000)",
                                "minimum": 1,
                                "maximum": 10000,
                                "default": 1000
                            }
                        },
                        "additionalProperties": False,
                    },
                    handler=self._handle_historical_readings,
                ),
                "list_devices": ToolSpec(
                    name="list_devices",
                    description="Return the current device roster with metadata for reference.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    handler=self._handle_list_devices,
                ),
            }

    async def _handle_sensor_snapshot(self, args: Dict[str, Any]) -> Dict[str, Any]:
        device_keys = args.get("device_keys")
        readings = await self._client.latest_readings(device_keys=device_keys)
        return {
            "devices": {
                device_key: [reading.__dict__ for reading in metrics]
                for device_key, metrics in readings.items()
            }
        }

    async def _handle_control_actuators(self, args: Dict[str, Any]) -> Dict[str, Any]:
        commands = args.get("commands", [])
        result = await self._client.control_actuators(commands)
        return result

    async def _handle_get_camera_image(self, args: Dict[str, Any]) -> Dict[str, Any]:
        device_key = args["device_key"]
        days_ago = args.get("days_ago", 0)
        result = await self._client.get_camera_image(device_key, days_ago=days_ago)
        return result

    async def _handle_historical_readings(self, args: Dict[str, Any]) -> Dict[str, Any]:
        device_keys = args.get("device_keys")
        metric_keys = args.get("metric_keys")
        hours = args.get("hours", 24)
        limit = args.get("limit", 1000)
        result = await self._client.get_historical_readings(
            device_keys=device_keys,
            metric_keys=metric_keys,
            hours=hours,
            limit=limit,
        )
        return result

    async def _handle_list_devices(self, _: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "devices": [
                {
                    "device_key": device.device_key,
                    "name": device.name,
                    "device_type": device.device_type,
                    "is_active": device.is_active,
                    "description": device.description,
                    "metadata": device.metadata,
                }
                for device in self._device_cache
            ]
        }

    def all(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)


async def build_tool_registry(client: HydroAPIClient) -> ToolRegistry:
    registry = ToolRegistry(client)
    await registry.refresh()
    return registry
