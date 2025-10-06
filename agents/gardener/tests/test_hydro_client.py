import pytest
import httpx

from agents.gardener.hydro_client import HydroAPIClient


@pytest.mark.asyncio
async def test_latest_readings_parsing():
    payload = {
        "devices": {
            "env-1": [
                {
                    "metric_key": "temp",
                    "value": 21.0,
                    "unit": "C",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "display_name": "Water Temp",
                }
            ]
        }
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    client = HydroAPIClient(base_url="http://hydro.test")
    await client._client.aclose()
    client._client = httpx.AsyncClient(transport=transport, base_url="http://hydro.test")

    readings = await client.latest_readings()
    await client.aclose()

    assert "env-1" in readings
    snapshot = readings["env-1"][0]
    assert snapshot.metric_key == "temp"
    assert snapshot.value == 21.0
    assert snapshot.display_name == "Water Temp"
