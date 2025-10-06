import os
from datetime import datetime

import pytest

DB_PATH = "test_gardener.db"
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./{DB_PATH}"

from backend.api import get_latest_readings  # noqa: E402
from backend.database import AsyncSessionLocal, init_db  # noqa: E402
from backend.models import Device, Metric, Reading  # noqa: E402


@pytest.mark.asyncio
async def test_latest_readings_endpoint_returns_metric_snapshot():
    await init_db()

    async with AsyncSessionLocal() as session:
        device = Device(
            device_key="env-1",
            device_type="sensor",
            is_active=True,
            name="Env Sensor",
            last_seen=datetime.utcnow(),
        )
        session.add(device)
        await session.flush()

        metric = Metric(
            device_id=device.id,
            metric_key="temp",
            display_name="Water Temp",
            unit="C",
            metric_type="sensor",
        )
        session.add(metric)
        await session.flush()

        reading = Reading(
            metric_id=metric.id,
            value=23.4,
            timestamp=datetime.utcnow(),
        )
        session.add(reading)
        await session.commit()

    async with AsyncSessionLocal() as session:
        response = await get_latest_readings(device_keys=None, db=session)

    assert "env-1" in response.devices
    temp_metric = response.devices["env-1"][0]
    assert temp_metric.metric_key == "temp"
    assert temp_metric.value == pytest.approx(23.4)
    assert temp_metric.unit == "C"
    assert temp_metric.display_name == "Water Temp"
