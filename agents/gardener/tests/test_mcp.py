import pytest

from agents.gardener.hydro_client import DeviceInfo
from agents.gardener.mcp_server import create_server
from agents.gardener.tools import ToolRegistry
from mcp import types


class DummyClient:
    async def list_devices(self, **kwargs):
        return [
            DeviceInfo(
                device_key="cam1",
                name="Deck Cam",
                device_type="camera",
                is_active=True,
                description=None,
                metadata={},
            )
        ]

    async def latest_readings(self, **kwargs):
        return {"cam1": []}

    async def control_actuators(self, commands):
        return {"processed": len(commands)}

    async def capture_camera(self, device_key):
        return {"device_key": device_key, "status": "captured"}


@pytest.mark.asyncio
async def test_mcp_list_tools_includes_camera_list():
    registry = ToolRegistry(DummyClient())
    await registry.refresh()
    server = create_server(registry)

    handler = server.request_handlers[types.ListToolsRequest]
    result = await handler(types.ListToolsRequest())
    tool_descriptions = {tool.name: tool.description for tool in result.root.tools}

    assert "Deck Cam" in tool_descriptions["capture_camera_frame"]
