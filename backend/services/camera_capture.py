import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import subprocess

from loguru import logger
from sqlalchemy import select

from ..database import AsyncSessionLocal
from ..models import CameraFrame
from ..config import settings


class CameraFrameCapture:
    """Handles periodic frame capture from RTSP camera"""

    def __init__(self):
        self.capture_dir = Path("/app/data/camera/frames")
        self.capture_dir.mkdir(parents=True, exist_ok=True)
        self.is_running = False

    async def capture_frame(self, rtsp_url: str, device_key: str) -> Optional[CameraFrame]:
        """Capture single WebP frame from RTSP stream"""
        try:
            timestamp = datetime.utcnow()

            # Organized by date for easy browsing
            date_dir = self.capture_dir / timestamp.strftime("%Y-%m-%d")
            date_dir.mkdir(exist_ok=True)

            filename = f"{device_key}_{timestamp.strftime('%H-%M-%S')}.webp"
            output_path = date_dir / filename

            # FFmpeg: Capture one frame as WebP
            process = await asyncio.create_subprocess_exec(
                'ffmpeg', '-rtsp_transport', 'tcp',
                '-i', rtsp_url,
                '-vframes', '1',
                '-c:v', 'libwebp',
                '-quality', str(settings.camera_frame_quality),
                '-y',  # Overwrite if exists
                str(output_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )

            _, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"FFmpeg capture failed: {stderr.decode()}")
                return None

            # Get file stats
            stat = output_path.stat()

            # Parse dimensions from ffprobe
            width, height = await self._get_image_dimensions(output_path)

            # Store in database
            async with AsyncSessionLocal() as db:
                frame = CameraFrame(
                    device_key=device_key,
                    timestamp=timestamp,
                    file_path=str(output_path.relative_to(Path("/app/data"))),
                    file_size=stat.st_size,
                    width=width,
                    height=height,
                )
                db.add(frame)
                await db.commit()
                await db.refresh(frame)

            logger.info(f"Captured frame {frame.id} for {device_key} ({stat.st_size} bytes)")
            return frame

        except Exception as e:
            logger.error(f"Failed to capture frame: {e}")
            return None

    async def _get_image_dimensions(self, image_path: Path) -> tuple[int, int]:
        """Get image width and height using ffprobe"""
        try:
            process = await asyncio.create_subprocess_exec(
                'ffprobe', '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height',
                '-of', 'csv=p=0',
                str(image_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL
            )

            stdout, _ = await process.communicate()

            if process.returncode == 0:
                parts = stdout.decode().strip().split(',')
                if len(parts) == 2:
                    return int(parts[0]), int(parts[1])
        except Exception as e:
            logger.warning(f"Failed to get image dimensions: {e}")

        return 0, 0

    async def cleanup_old_frames(self, retention_hours: int):
        """Delete frames older than retention period"""
        cutoff = datetime.utcnow() - timedelta(hours=retention_hours)

        async with AsyncSessionLocal() as db:
            # Get old frames
            result = await db.execute(
                select(CameraFrame).where(CameraFrame.timestamp < cutoff)
            )
            old_frames = result.scalars().all()

            deleted_count = 0
            deleted_size = 0

            # Delete files and DB records
            for frame in old_frames:
                try:
                    file_path = Path("/app/data") / frame.file_path
                    if file_path.exists():
                        deleted_size += file_path.stat().st_size
                        file_path.unlink()
                    await db.delete(frame)
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"Failed to delete frame {frame.id}: {e}")

            await db.commit()

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old frames ({deleted_size / 1024 / 1024:.1f} MB)")

    async def start_periodic_capture(self):
        """Start periodic frame capture loop"""
        if self.is_running:
            logger.warning("Camera capture already running")
            return

        self.is_running = True
        logger.info(f"Starting camera capture (interval: {settings.camera_capture_interval}s)")

        while self.is_running:
            try:
                # Capture frame
                await self.capture_frame(
                    settings.camera_rtsp_url,
                    settings.camera_device_key
                )

                # Cleanup old frames every 24 captures (once per day at hourly intervals)
                # This way we don't check too frequently
                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(CameraFrame.id)
                        .where(CameraFrame.device_key == settings.camera_device_key)
                    )
                    frame_count = len(result.all())

                    if frame_count % 24 == 0:
                        await self.cleanup_old_frames(settings.camera_retention_hours)

                # Wait for next capture
                await asyncio.sleep(settings.camera_capture_interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in camera capture loop: {e}")
                await asyncio.sleep(60)  # Wait a bit before retrying

        logger.info("Camera capture stopped")

    def stop(self):
        """Stop periodic capture"""
        self.is_running = False


# Global instance
camera_capture = CameraFrameCapture()