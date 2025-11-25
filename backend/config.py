from pydantic_settings import BaseSettings
from typing import Optional
import os

class Settings(BaseSettings):
    # MQTT Configuration
    mqtt_broker: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: Optional[str] = None
    mqtt_password: Optional[str] = None
    mqtt_keepalive: int = 60
    mqtt_qos: int = 1
    actuator_publish_rate_hz: float = 50.0  # 50 Hz = 20ms minimum interval between MQTT publishes

    # Topic Configuration
    mqtt_base_topic: str = "esp32"
    sensor_data_topic: str = "esp32/data"
    actuator_control_topic: str = ""  # Dynamic per-device: {mqtt_base_topic}/{device_id}/control
    discovery_request_topic: str = "esp32/discovery/request"
    discovery_response_topic: str = "esp32/+/discovery"

    # Database Configuration
    database_url: str = "sqlite+aiosqlite:///./hydro.db"

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8001

    # MediaMTX Configuration
    mediamtx_host: str = "localhost"
    mediamtx_api_port: int = 9997
    mediamtx_webrtc_port: int = 8889

    # Frontend Configuration
    frontend_port: int = 3001

    # Sensor Discovery Configuration
    sensor_discovery_timeout: int = 300  # 5 minutes
    sensor_heartbeat_interval: int = 60  # 1 minute

    # Data Retention
    data_retention_days: int = 30

    # History snapshot downsampling (for WS initial load)
    # Approximate total points per metric over last 24h
    history_snapshot_target_points: int = 600

    # Logging
    log_level: str = "INFO"

    # Frame Capture Configuration
    frame_capture_enabled: bool = True
    frame_capture_interval_minutes: int = 5
    frame_quality: int = 100  # WebP quality (1-100, higher = better quality) - 100 for lossless
    frame_max_width: int = -1  # Max width (-1 = no scaling, preserve original resolution)
    frame_storage_path: str = "/app/data/camera_frames"
    frame_retention_days: int = 30  # How long to keep frames (0 = forever)

    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
