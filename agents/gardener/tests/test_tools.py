import asyncio
import json

import pytest

from agents.gardener.agent import GardenerAgent
from agents.gardener.hydro_client import DeviceInfo, MetricReading
from agents.gardener.llm_providers import ChatMessage, MockLLMProvider
from agents.gardener.tools import ToolRegistry


class FakeClient:
    def __init__(self) -> None:
        self.commands = []

    async def list_devices(self, **kwargs):
        return [
            DeviceInfo(
                device_key="cam-1",
                name="North Camera",
                device_type="camera",
                is_active=True,
                description=None,
                metadata={},
            ),
            DeviceInfo(
                device_key="env-1",
                name="Env Sensor",
                device_type="sensor",
                is_active=True,
                description=None,
                metadata={},
            ),
        ]

    async def latest_readings(self, device_keys=None):
        return {
            "env-1": [
                MetricReading(
                    metric_key="temp",
                    value=23.5,
                    unit="C",
                    timestamp="2024-01-01T00:00:00Z",
                    display_name="Water Temp",
                )
            ]
        }

    async def control_actuators(self, commands):
        self.commands.extend(commands)
        return {"processed": len(commands), "details": commands}

    async def capture_camera(self, device_key):
        return {"device_key": device_key, "status": "captured"}


@pytest.mark.asyncio
async def test_registry_instructions_include_devices():
    client = FakeClient()
    registry = ToolRegistry(client)
    await registry.refresh()

    camera_tool = registry.get("capture_camera_frame")
    assert camera_tool is not None
    assert "North Camera" in camera_tool.description


@pytest.mark.asyncio
async def test_agent_handles_tool_roundtrip():
    client = FakeClient()
    registry = ToolRegistry(client)
    await registry.refresh()
    provider = MockLLMProvider()
    agent = GardenerAgent(provider=provider, registry=registry)

    messages = [ChatMessage(role="user", content=json.dumps({"tool": "get_sensor_snapshot"}))]
    result = await agent.run(messages=messages)

    assert "Mock response acknowledging" in result["final"]
    tool_trace = result["trace"][0]["tools"][0]
    assert tool_trace["tool"] == "get_sensor_snapshot"
    assert "env-1" in tool_trace["result"]["devices"]
