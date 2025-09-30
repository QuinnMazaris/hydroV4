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

    # Topic Configuration
    mqtt_base_topic: str = "esp32"
    sensor_data_topic: str = "esp32/data"
    relay_status_topic: str = "esp32/relay/status"
    relay_control_topic: str = "esp32/relay/control"
    discovery_request_topic: str = "esp32/discovery/request"
    discovery_response_topic: str = "esp32/+/discovery"

    # Database Configuration
    database_url: str = "sqlite+aiosqlite:///./hydro.db"

    # API Configuration
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Sensor Discovery Configuration
    sensor_discovery_timeout: int = 300  # 5 minutes
    sensor_heartbeat_interval: int = 60  # 1 minute

    # Data Retention
    data_retention_days: int = 30

    # History snapshot downsampling (for WS initial load)
    # Approximate total points per metric over last 24h
    history_snapshot_target_points: int = 600

    # Camera Configuration
    camera_enabled: bool = True
    camera_rtsp_url: str = "rtsp://admin:password@192.168.1.100:554/stream"
    camera_device_key: str = "camera_1"
    camera_capture_interval: int = 3600  # 1 hour in seconds
    camera_frame_quality: int = 80  # WebP quality 0-100
    camera_retention_hours: int = 720  # 30 days
    camera_stream_enabled: bool = True

    # Logging
    log_level: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
