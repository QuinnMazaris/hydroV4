from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select, update

from ..database import AsyncSessionLocal
from ..models import (
    ActuatorState,
    ActuatorStateCreate,
    Device,
    SensorReading,
    SensorReadingCreate,
)


async def save_sensor_reading(reading: SensorReadingCreate) -> None:
    async with AsyncSessionLocal() as session:
        db_reading = SensorReading(**reading.model_dump())
        session.add(db_reading)
        await session.commit()


async def save_actuator_state(state: ActuatorStateCreate) -> None:
    async with AsyncSessionLocal() as session:
        db_state = ActuatorState(**state.model_dump())
        session.add(db_state)
        await session.commit()


async def mark_devices_inactive(cutoff: datetime) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(Device)
            .where(Device.last_seen < cutoff)
            .values(is_active=False)
        )
        await session.commit()


async def delete_old_readings(before: datetime) -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(SensorReading).where(SensorReading.timestamp < before)
        )
        await session.commit()


async def latest_actuator_state(
    device_id: str,
    actuator_type: str,
    actuator_number: int,
) -> Optional[ActuatorState]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(ActuatorState)
            .where(
                (ActuatorState.device_id == device_id)
                & (ActuatorState.actuator_type == actuator_type)
                & (ActuatorState.actuator_number == actuator_number)
            )
            .order_by(ActuatorState.timestamp.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()
