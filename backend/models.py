from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Union

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

from .utils.time import utc_now

Base = declarative_base()


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    device_key = Column(String(100), unique=True, index=True, nullable=False)
    name = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    last_seen = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    device_type = Column(String(50), default='mqtt_sensor', nullable=False)  # 'mqtt_sensor', 'camera', etc
    device_metadata = Column(Text, nullable=True)  # JSON string for device-specific data
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

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
    control_mode = Column(String(20), default='manual', nullable=True)  # 'manual' or 'auto' (NULL for sensors)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

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
    timestamp = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
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
    device_type: str = Field(default='mqtt_sensor')


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
    metric_type: str = Field(default='sensor')
    control_mode: Optional[Literal['manual', 'auto']] = 'manual'


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


class ActuatorCommand(BaseModel):
    device_id: str
    actuator_key: str
    state: str


class ActuatorBatchControl(BaseModel):
    commands: List[ActuatorCommand]


class CameraFrame(Base):
    __tablename__ = "camera_frames"

    id = Column(Integer, primary_key=True, index=True)
    device_key = Column(String(100), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), default=utc_now, nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    file_size = Column(Integer, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    analyzed_at = Column(DateTime(timezone=True), nullable=True, index=True)
    analysis_model = Column(String(100), nullable=True)
    detected_objects = Column(JSON, nullable=True)
    plant_health_score = Column(Integer, nullable=True)
    anomaly_detected = Column(Boolean, nullable=True)
    notes = Column(Text, nullable=True)


class CameraFrameResponse(BaseModel):
    id: int
    device_key: str
    timestamp: datetime
    file_path: str
    file_size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    analyzed_at: Optional[datetime] = None
    analysis_model: Optional[str] = None
    detected_objects: Optional[Dict[str, Any]] = None
    plant_health_score: Optional[int] = None
    anomaly_detected: Optional[bool] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class CameraFrameCreate(BaseModel):
    device_key: str
    file_path: str
    file_size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None


class LatestMetricSnapshot(BaseModel):
    metric_key: str
    value: JsonValue
    timestamp: datetime
    unit: Optional[str] = None
    display_name: Optional[str] = None


class LatestReadingsResponse(BaseModel):
    devices: Dict[str, List[LatestMetricSnapshot]]


class HistoricalReading(BaseModel):
    metric_key: str
    value: JsonValue
    timestamp: datetime
    unit: Optional[str] = None
    display_name: Optional[str] = None


class MetricStatistics(BaseModel):
    metric_key: str
    display_name: Optional[str] = None
    unit: Optional[str] = None
    count: int
    min: JsonValue
    max: JsonValue
    avg: Optional[float] = None
    first_value: JsonValue
    last_value: JsonValue
    first_timestamp: datetime
    last_timestamp: datetime
    change: Optional[float] = None
    change_percent: Optional[float] = None


class HistoricalReadingsResponse(BaseModel):
    devices: Dict[str, List[HistoricalReading]]
    start_time: datetime
    end_time: datetime
    total_points: int
    returned_points: int
    aggregated: bool = False
    statistics: Optional[Dict[str, List[MetricStatistics]]] = None
