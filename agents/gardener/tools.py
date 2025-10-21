"""Tool registration and handlers for the gardener agent."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .hydro_client import DeviceInfo, HydroAPIClient
from .rule_manager import RuleManager

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

        # Use shared rule manager with AI protection layer
        rules_path = Path(__file__).parent / 'data' / 'automation_rules.json'
        self._rule_manager = RuleManager(rules_path)

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
                        "Get historical sensor readings with automatic downsampling and statistics. "
                        "Returns both time-series data AND summary stats (min/max/avg/change) for each metric. "
                        "Auto-downsamples to prevent token overflow: <1h=raw, 1-6h=1min avg, 6-24h=5min avg, 24h+=15min avg. "
                        "Use 'statistics' field to quickly identify trends/events (e.g. water fills, temp spikes). "
                        "Use 'devices' field for detailed time-series when needed."
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
                                "description": "Max data points per metric (default 100, max 1000). Higher = more detail but more tokens.",
                                "minimum": 1,
                                "maximum": 1000,
                                "default": 100
                            },
                            "downsample_minutes": {
                                "type": "integer",
                                "description": "Downsample interval in minutes (optional, auto-calculated if not set)",
                                "minimum": 1,
                                "maximum": 1440
                            },
                            "include_stats": {
                                "type": "boolean",
                                "description": "Include summary statistics (default true). Recommended: always true.",
                                "default": True
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
                "list_automation_rules": ToolSpec(
                    name="list_automation_rules",
                    description="Get all automation rules with their current status and configuration.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    handler=self._handle_list_automation_rules,
                ),
                "create_automation_rule": ToolSpec(
                    name="create_automation_rule",
                    description=(
                        "Create a new automation rule. Rule can have time-based and/or sensor-based conditions. "
                        "Supports 'all_of' (AND) and 'any_of' (OR) condition logic. "
                        "Actions can control actuators that are in AUTO mode."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "description": "Rule name"},
                            "description": {"type": "string", "description": "Rule description"},
                            "enabled": {"type": "boolean", "default": False, "description": "Enable rule immediately"},
                            "priority": {"type": "integer", "default": 100, "description": "Rule priority (higher = evaluated first)"},
                            "conditions": {
                                "type": "object",
                                "description": "Rule conditions (all_of for AND logic, any_of for OR logic)",
                                "properties": {
                                    "all_of": {"type": "array", "items": {"type": "object"}},
                                    "any_of": {"type": "array", "items": {"type": "object"}},
                                }
                            },
                            "actions": {
                                "type": "array",
                                "description": "Actions to execute when conditions are met",
                                "items": {"type": "object"}
                            }
                        },
                        "required": ["name", "conditions", "actions"],
                        "additionalProperties": False,
                    },
                    handler=self._handle_create_automation_rule,
                ),
                "update_automation_rule": ToolSpec(
                    name="update_automation_rule",
                    description="Update an existing automation rule by ID.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "rule_id": {"type": "string", "description": "Rule ID to update"},
                            "name": {"type": "string", "description": "New rule name"},
                            "description": {"type": "string", "description": "New rule description"},
                            "enabled": {"type": "boolean", "description": "Enable/disable rule"},
                            "priority": {"type": "integer", "description": "New rule priority"},
                            "conditions": {"type": "object", "description": "New conditions"},
                            "actions": {"type": "array", "description": "New actions"}
                        },
                        "required": ["rule_id"],
                        "additionalProperties": False,
                    },
                    handler=self._handle_update_automation_rule,
                ),
                "delete_automation_rule": ToolSpec(
                    name="delete_automation_rule",
                    description="Delete an automation rule by ID.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "rule_id": {"type": "string", "description": "Rule ID to delete"}
                        },
                        "required": ["rule_id"],
                        "additionalProperties": False,
                    },
                    handler=self._handle_delete_automation_rule,
                ),
                "toggle_automation_rule": ToolSpec(
                    name="toggle_automation_rule",
                    description="Enable or disable an automation rule by ID.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "rule_id": {"type": "string", "description": "Rule ID to toggle"},
                            "enabled": {"type": "boolean", "description": "True to enable, False to disable"}
                        },
                        "required": ["rule_id", "enabled"],
                        "additionalProperties": False,
                    },
                    handler=self._handle_toggle_automation_rule,
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

        # Return MCP-formatted content for image display
        if result.get("status") == "success" and "image_base64" in result:
            return {
                "content": [
                    {
                        "type": "image",
                        "data": result["image_base64"],
                        "mimeType": result.get("content_type", "image/webp")
                    }
                ],
                "isError": False
            }
        return result

    async def _handle_historical_readings(self, args: Dict[str, Any]) -> Dict[str, Any]:
        device_keys = args.get("device_keys")
        metric_keys = args.get("metric_keys")
        hours = args.get("hours", 24)
        limit = args.get("limit", 100)
        downsample_minutes = args.get("downsample_minutes")
        include_stats = args.get("include_stats", True)
        result = await self._client.get_historical_readings(
            device_keys=device_keys,
            metric_keys=metric_keys,
            hours=hours,
            limit=limit,
            downsample_minutes=downsample_minutes,
            include_stats=include_stats,
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

    async def _handle_list_automation_rules(self, _: Dict[str, Any]) -> Dict[str, Any]:
        """List all automation rules."""
        return self._rule_manager.list_rules()

    async def _handle_create_automation_rule(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new automation rule (AI cannot create protected rules)."""
        # AI-created rules are never protected
        return self._rule_manager.create_rule(
            name=args["name"],
            conditions=args["conditions"],
            actions=args["actions"],
            description=args.get("description", ""),
            enabled=args.get("enabled", False),
            protected=False,  # AI cannot create protected rules
            priority=args.get("priority", 100),
            modified_by="llm_tool"
        )

    async def _handle_update_automation_rule(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Update an existing automation rule (AI protection enforced)."""
        rule_id = args["rule_id"]

        # Check if rule is protected first
        data = self._rule_manager.load_rules_file()
        result = self._rule_manager.find_rule_by_id(data.get("rules", []), rule_id)

        if result is None:
            return {
                "status": "error",
                "message": f"Rule with ID '{rule_id}' not found"
            }

        _, rule = result

        # AI cannot modify protected rules
        if rule.get("protected", False):
            return {
                "status": "error",
                "message": f"Rule '{rule.get('name')}' is protected and cannot be modified"
            }

        # AI cannot change protection status
        if "protected" in args:
            return {
                "status": "error",
                "message": "Cannot modify protection status of rules"
            }

        # Use rule manager to update (without protected field)
        return self._rule_manager.update_rule(
            rule_id=rule_id,
            name=args.get("name"),
            description=args.get("description"),
            enabled=args.get("enabled"),
            protected=None,  # AI cannot change protection
            priority=args.get("priority"),
            conditions=args.get("conditions"),
            actions=args.get("actions"),
            modified_by="llm_tool"
        )

    async def _handle_delete_automation_rule(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Delete an automation rule (AI protection enforced)."""
        rule_id = args["rule_id"]

        # Check if rule is protected first
        data = self._rule_manager.load_rules_file()
        result = self._rule_manager.find_rule_by_id(data.get("rules", []), rule_id)

        if result is None:
            return {
                "status": "error",
                "message": f"Rule with ID '{rule_id}' not found"
            }

        _, rule = result

        # AI cannot delete protected rules
        if rule.get("protected", False):
            return {
                "status": "error",
                "message": f"Rule '{rule.get('name')}' is protected and cannot be deleted"
            }

        return self._rule_manager.delete_rule(rule_id, modified_by="llm_tool")

    async def _handle_toggle_automation_rule(self, args: Dict[str, Any]) -> Dict[str, Any]:
        """Enable or disable an automation rule (AI protection enforced)."""
        rule_id = args["rule_id"]
        enabled = args["enabled"]

        # Check if rule is protected first
        data = self._rule_manager.load_rules_file()
        result = self._rule_manager.find_rule_by_id(data.get("rules", []), rule_id)

        if result is None:
            return {
                "status": "error",
                "message": f"Rule with ID '{rule_id}' not found"
            }

        _, rule = result

        # AI cannot toggle protected rules
        if rule.get("protected", False):
            return {
                "status": "error",
                "message": f"Rule '{rule.get('name')}' is protected and cannot be enabled/disabled"
            }

        return self._rule_manager.toggle_rule(rule_id, enabled, modified_by="llm_tool")

    def all(self) -> List[ToolSpec]:
        return list(self._tools.values())

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)


async def build_tool_registry(client: HydroAPIClient) -> ToolRegistry:
    registry = ToolRegistry(client)
    await registry.refresh()
    return registry
