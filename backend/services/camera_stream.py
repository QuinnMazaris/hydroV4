import asyncio
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

from ..config import settings


class CameraStreamManager:
    """Manages HLS live stream from RTSP camera"""

    def __init__(self):
        self.stream_dir = Path("/app/data/camera/hls")
        self.stream_dir.mkdir(parents=True, exist_ok=True)
        self.process: Optional[subprocess.Popen] = None
        self.viewer_count = 0

    async def start_stream(self, rtsp_url: str):
        """Start HLS streaming from RTSP source"""
        if self.process:
            logger.info("HLS stream already running")
            return

        try:
            # Clean up old segments
            for segment in self.stream_dir.glob("segment_*.ts"):
                segment.unlink()
            playlist = self.stream_dir / "stream.m3u8"
            if playlist.exists():
                playlist.unlink()

            logger.info(f"Starting HLS stream from {rtsp_url}")

            # FFmpeg: RTSP → HLS with ultra-low latency settings
            self.process = subprocess.Popen([
                'ffmpeg',
                '-rtsp_transport', 'tcp',
                '-fflags', 'nobuffer',  # Disable buffering
                '-flags', 'low_delay',  # Low delay mode
                '-i', rtsp_url,
                '-c:v', 'copy',  # Copy video stream directly (no re-encoding)
                '-f', 'hls',
                '-hls_time', '1',  # 1-second segments for lower latency
                '-hls_list_size', '2',  # Keep only 2 segments (~2s buffer)
                '-hls_flags', 'delete_segments+append_list+omit_endlist',
                '-hls_segment_type', 'mpegts',
                '-hls_segment_filename', str(self.stream_dir / 'segment_%03d.ts'),
                '-start_number', '0',
                str(self.stream_dir / 'stream.m3u8')
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

            logger.info("HLS stream started successfully")

        except Exception as e:
            logger.error(f"Failed to start HLS stream: {e}")
            self.process = None

    async def stop_stream(self):
        """Stop HLS streaming"""
        if not self.process:
            return

        try:
            logger.info("Stopping HLS stream")
            self.process.terminate()

            # Wait for graceful shutdown
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning("FFmpeg didn't stop gracefully, killing...")
                self.process.kill()

            self.process = None
            logger.info("HLS stream stopped")

        except Exception as e:
            logger.error(f"Error stopping stream: {e}")

    def is_streaming(self) -> bool:
        """Check if stream is currently active"""
        if not self.process:
            return False

        # Check if process is still running
        if self.process.poll() is not None:
            logger.warning("FFmpeg process died unexpectedly")
            self.process = None
            return False

        return True

    async def restart_stream(self, rtsp_url: str):
        """Restart the stream (useful for error recovery)"""
        await self.stop_stream()
        await asyncio.sleep(1)
        await self.start_stream(rtsp_url)

    def increment_viewers(self):
        """Track viewer count (for future optimization)"""
        self.viewer_count += 1
        logger.debug(f"Camera viewers: {self.viewer_count}")

    def decrement_viewers(self):
        """Track viewer count"""
        self.viewer_count = max(0, self.viewer_count - 1)
        logger.debug(f"Camera viewers: {self.viewer_count}")


# Global instance
camera_stream = CameraStreamManager()