from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel
import json

Base = declarative_base()

class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(100), unique=True, index=True)
    device_type = Column(String(50))  # "sensor", "actuator"
    name = Column(String(200))
    description = Column(Text, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)
    device_metadata = Column(Text, nullable=True)  # JSON string for device-specific data
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    sensor_readings = relationship("SensorReading", back_populates="device")
    actuator_states = relationship("ActuatorState", back_populates="device")

class SensorReading(Base):
    __tablename__ = "sensor_readings"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(100), ForeignKey("devices.device_id"))
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)

    # Environmental sensors (BME680)
    temperature = Column(Float, nullable=True)
    pressure = Column(Float, nullable=True)
    humidity = Column(Float, nullable=True)
    gas_kohms = Column(Float, nullable=True)

    # Light sensor
    lux = Column(Float, nullable=True)

    # Water sensors
    water_temp_c = Column(Float, nullable=True)
    tds_ppm = Column(Float, nullable=True)
    ph = Column(Float, nullable=True)

    # Distance sensor
    distance_mm = Column(Float, nullable=True)

    # Calculated values
    vpd_kpa = Column(Float, nullable=True)

    # Raw data for extensibility
    raw_data = Column(Text, nullable=True)  # JSON string

    # Relationships
    device = relationship("Device", back_populates="sensor_readings")

class ActuatorState(Base):
    __tablename__ = "actuator_states"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(100), ForeignKey("devices.device_id"))
    actuator_type = Column(String(50))  # "relay", "pump", "valve", etc.
    actuator_number = Column(Integer)  # 1-16 for relays
    state = Column(String(10))  # "on", "off"
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    command_source = Column(String(50), default="manual")  # "manual", "automation", "schedule"

    # Relationships
    device = relationship("Device", back_populates="actuator_states")

# Pydantic models for API
class DeviceBase(BaseModel):
    device_id: str
    device_type: str
    name: str
    description: Optional[str] = None
    device_metadata: Optional[str] = None

class DeviceCreate(DeviceBase):
    pass

class DeviceResponse(DeviceBase):
    id: int
    last_seen: datetime
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True

class SensorReadingCreate(BaseModel):
    device_id: str
    temperature: Optional[float] = None
    pressure: Optional[float] = None
    humidity: Optional[float] = None
    gas_kohms: Optional[float] = None
    lux: Optional[float] = None
    water_temp_c: Optional[float] = None
    tds_ppm: Optional[float] = None
    ph: Optional[float] = None
    distance_mm: Optional[float] = None
    vpd_kpa: Optional[float] = None
    raw_data: Optional[str] = None

class SensorReadingResponse(SensorReadingCreate):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True

class RelayControl(BaseModel):
    relay: int  # 1-16
    state: str  # "on" or "off"

class RelayStatus(BaseModel):
    device_id: str
    relays: Dict[str, str]  # {"relay1": "off", "relay2": "on", ...}

class ActuatorStateCreate(BaseModel):
    device_id: str
    actuator_type: str
    actuator_number: int
    state: str
    command_source: str = "manual"

class ActuatorStateResponse(ActuatorStateCreate):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True