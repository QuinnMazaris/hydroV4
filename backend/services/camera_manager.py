"""
Camera management system that supports multiple cameras dynamically.
Loads camera configurations from cameras.json and manages their lifecycle.
"""
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime

from loguru import logger

from ..config import settings
from .camera_capture import CameraFrameCapture
from .camera_stream import CameraStreamManager
from .persistence import upsert_device, sync_device_metrics


class CameraConfig:
    """Configuration for a single camera"""
    def __init__(self, config: dict):
        self.device_key = config['device_key']
        self.name = config.get('name', f"Camera {self.device_key}")
        self.description = config.get('description', 'RTSP camera')
        self.rtsp_url = config['rtsp_url']
        self.capture_interval = config.get('capture_interval', 3600)
        self.frame_quality = config.get('frame_quality', 80)
        self.retention_hours = config.get('retention_hours', 720)
        self.stream_enabled = config.get('stream_enabled', True)


class Camera:
    """Represents a single camera instance with its capture and stream managers"""
    def __init__(self, config: CameraConfig):
        self.config = config
        self.capture = CameraFrameCapture()
        self.stream = CameraStreamManager()
        self.capture_task: Optional[asyncio.Task] = None
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.is_running = False

    async def start(self):
        """Start camera capture and streaming"""
        if self.is_running:
            logger.warning(f"Camera {self.config.device_key} already running")
            return

        logger.info(f"Starting camera {self.config.device_key}: {self.config.name}")

        # Register camera as a device in database
        await upsert_device(
            device_key=self.config.device_key,
            name=self.config.name,
            description=self.config.description,
            metadata=None,
            last_seen=datetime.utcnow(),
            device_type='camera'
        )

        # Start capture loop
        self.capture_task = asyncio.create_task(
            self._capture_loop()
        )

        # Start heartbeat to keep device active
        self.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop()
        )

        # Start HLS stream if enabled
        if self.config.stream_enabled:
            await self.stream.start_stream(self.config.rtsp_url)

        self.is_running = True
        logger.info(f"Camera {self.config.device_key} started successfully")

    async def stop(self):
        """Stop camera capture and streaming"""
        if not self.is_running:
            return

        logger.info(f"Stopping camera {self.config.device_key}")
        self.is_running = False

        # Stop capture loop
        if self.capture_task:
            self.capture_task.cancel()
            try:
                await self.capture_task
            except asyncio.CancelledError:
                pass

        # Stop heartbeat
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass

        # Stop stream
        if self.config.stream_enabled:
            await self.stream.stop_stream()

        logger.info(f"Camera {self.config.device_key} stopped")

    async def _capture_loop(self):
        """Periodic frame capture loop"""
        logger.info(f"Starting capture loop for {self.config.device_key} (interval: {self.config.capture_interval}s)")

        while self.is_running:
            try:
                # Capture frame
                await self.capture.capture_frame(
                    self.config.rtsp_url,
                    self.config.device_key
                )

                # Cleanup old frames periodically
                await self.capture.cleanup_old_frames(self.config.retention_hours)

                # Wait for next capture
                await asyncio.sleep(self.config.capture_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in capture loop for {self.config.device_key}: {e}")
                await asyncio.sleep(60)  # Wait before retry

    async def _heartbeat_loop(self):
        """Update device last_seen timestamp to keep it marked as active"""
        logger.info(f"Starting heartbeat for {self.config.device_key}")

        while self.is_running:
            try:
                # Check if FFmpeg process is still alive
                is_streaming = self.stream.is_streaming() if self.config.stream_enabled else True

                if is_streaming:
                    # Update last_seen timestamp
                    await upsert_device(
                        device_key=self.config.device_key,
                        name=self.config.name,
                        description=self.config.description,
                        last_seen=datetime.utcnow(),
                        device_type='camera'
                    )
                    logger.debug(f"Heartbeat: Camera {self.config.device_key} is alive")
                else:
                    logger.warning(f"Camera {self.config.device_key} stream is not active")
                    # Attempt to restart stream
                    if self.config.stream_enabled:
                        await self.stream.restart_stream(self.config.rtsp_url)

                # Send heartbeat every 60 seconds (same as ESP32)
                await asyncio.sleep(settings.sensor_heartbeat_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in heartbeat loop for {self.config.device_key}: {e}")
                await asyncio.sleep(60)


class CameraManager:
    """Manages multiple cameras dynamically"""
    def __init__(self):
        self.cameras: Dict[str, Camera] = {}

    def load_config(self, config_path: str = "/app/backend/cameras.json") -> List[CameraConfig]:
        """Load camera configurations from JSON file"""
        config_file = Path(config_path)

        if not config_file.exists():
            logger.warning(f"Camera config file not found: {config_path}")
            return []

        try:
            with open(config_file, 'r') as f:
                data = json.load(f)

            configs = [CameraConfig(cam) for cam in data]
            logger.info(f"Loaded {len(configs)} camera configurations")
            return configs
        except Exception as e:
            logger.error(f"Failed to load camera config: {e}")
            return []

    async def start_all(self):
        """Start all cameras from configuration"""
        configs = self.load_config()

        if not configs:
            logger.info("No cameras configured")
            return

        for config in configs:
            camera = Camera(config)
            self.cameras[config.device_key] = camera
            await camera.start()

    async def stop_all(self):
        """Stop all cameras"""
        for camera in self.cameras.values():
            await camera.stop()
        self.cameras.clear()

    def get_camera(self, device_key: str) -> Optional[Camera]:
        """Get camera by device key"""
        return self.cameras.get(device_key)


# Global instance
camera_manager = CameraManager()