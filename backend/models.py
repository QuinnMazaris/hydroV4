from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship

from pydantic import BaseModel, Field

Base = declarative_base()


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_key = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    last_seen = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    device_type = Column(String(50), default='mqtt_sensor', nullable=False)  # 'mqtt_sensor', 'camera', etc
    device_metadata = Column(Text, nullable=True)  # JSON string for device-specific data
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    metrics = relationship(
        "Metric",
        back_populates="device",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    metric_key = Column(String(100), nullable=False)
    display_name = Column(String(200), nullable=True)
    unit = Column(String(50), nullable=True)
    metric_type = Column(String(20), nullable=False)  # 'sensor' or 'actuator'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    device = relationship("Device", back_populates="metrics")
    readings = relationship(
        "Reading",
        back_populates="metric",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("device_id", "metric_key", name="uq_metric_device_key"),
        Index("ix_metrics_device_id", "device_id"),
    )


class Reading(Base):
    __tablename__ = "readings"

    id = Column(Integer, primary_key=True, index=True)
    metric_id = Column(Integer, ForeignKey("metrics.id", ondelete="CASCADE"), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    value = Column(JSON, nullable=False)

    metric = relationship("Metric", back_populates="readings")

    __table_args__ = (
        Index("ix_readings_metric_id", "metric_id"),
        Index("ix_readings_metric_ts", "metric_id", "timestamp"),
    )


JsonPrimitive = Union[str, int, float, bool, None]
JsonValue = Union[JsonPrimitive, Dict[str, Any], List[Any]]


class DeviceBase(BaseModel):
    device_key: str = Field(..., description="Unique external identifier for the device")
    name: Optional[str] = None
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


class MetricBase(BaseModel):
    metric_key: str
    display_name: Optional[str] = None
    unit: Optional[str] = None


class MetricCreate(MetricBase):
    device_id: int


class MetricResponse(MetricBase):
    id: int
    device_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class ReadingCreate(BaseModel):
    metric_id: int
    value: JsonValue
    timestamp: Optional[datetime] = None


class ReadingResponse(ReadingCreate):
    id: int
    timestamp: datetime

    class Config:
        from_attributes = True


class ActuatorControl(BaseModel):
    actuator_key: str  # The metric key (e.g. "relay1", "pump", "valve_a")
    state: str  # "on", "off", or other actuator-specific states


class CameraFrame(Base):
    """Store camera frame captures with metadata for LLM analysis"""
    __tablename__ = "camera_frames"

    id = Column(Integer, primary_key=True, index=True)
    device_key = Column(String(100), nullable=False)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer)
    width = Column(Integer)
    height = Column(Integer)

    # LLM analysis results (nullable until processed)
    analyzed_at = Column(DateTime, nullable=True)
    analysis_model = Column(String(100), nullable=True)
    detected_objects = Column(JSON, nullable=True)
    plant_health_score = Column(Integer, nullable=True)
    anomaly_detected = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)

    __table_args__ = (
        Index("ix_camera_frames_timestamp", "timestamp"),
        Index("ix_camera_frames_analyzed", "analyzed_at"),
    )


class CameraFrameResponse(BaseModel):
    id: int
    device_key: str
    timestamp: datetime
    file_path: str
    file_size: Optional[int]
    width: Optional[int]
    height: Optional[int]
    analyzed_at: Optional[datetime]
    analysis_model: Optional[str]
    detected_objects: Optional[List[str]]
    plant_health_score: Optional[int]
    anomaly_detected: bool
    notes: Optional[str]

    class Config:
        from_attributes = True
