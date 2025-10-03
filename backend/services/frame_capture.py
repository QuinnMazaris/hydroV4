"""Frame capture service for capturing and storing images from MediaMTX streams."""

import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from loguru import logger
from sqlalchemy import delete, select

from ..config import settings
from ..database import AsyncSessionLocal
from ..models import CameraFrame


def get_mediamtx_rtsp_url(path_name: str) -> str:
    """Get the local MediaMTX RTSP URL for a given camera path."""
    # MediaMTX RTSP server runs on port 8554 (from mediamtx.yml)
    return f"rtsp://{settings.mediamtx_host}:8554/{path_name}"


async def get_active_camera_paths() -> List[str]:
    """
    Get list of active camera paths from MediaMTX API.
    Returns list of path names (e.g. ['camera_1', 'camera_2']).
    """
    camera_paths = []
    try:
        mediamtx_api_url = f"http://{settings.mediamtx_host}:{settings.mediamtx_api_port}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Try v3 API first, fall back to v2
            response = await client.get(f"{mediamtx_api_url}/v3/paths/list")

            if response.status_code == 404:
                response = await client.get(f"{mediamtx_api_url}/v2/paths/list")

            if response.status_code != 200:
                logger.warning(f"MediaMTX API returned status {response.status_code}")
                return camera_paths

            data = response.json()
            items = data.get("items", []) if isinstance(data, dict) else data

            for item in items:
                path_name = item.get("name") or item.get("path") or ""
                is_ready = item.get("ready", False)

                # Skip system paths and non-ready cameras
                if path_name and not path_name.startswith("_") and is_ready:
                    camera_paths.append(path_name)

    except Exception as e:
        logger.error(f"Failed to get camera paths from MediaMTX: {e}")

    return camera_paths


async def capture_single_frame(
    camera_name: str,
    output_path: str,
    rtsp_url: Optional[str] = None,
    timeout: int = 10
) -> Optional[Dict[str, any]]:
    """
    Capture a single frame from a MediaMTX camera stream using FFmpeg.

    Args:
        camera_name: Name of the camera (e.g. 'camera_1')
        output_path: Full path where the frame should be saved
        rtsp_url: Optional custom RTSP URL (defaults to MediaMTX local stream)
        timeout: Max seconds to wait for capture

    Returns:
        Dict with capture info (file_path, file_size, width, height) or None on failure
    """
    if rtsp_url is None:
        rtsp_url = get_mediamtx_rtsp_url(camera_name)

    # Ensure output directory exists
    output_dir = os.path.dirname(output_path)
    os.makedirs(output_dir, exist_ok=True)

    # Build FFmpeg command
    cmd = [
        'ffmpeg',
        '-rtsp_transport', 'tcp',
        '-i', rtsp_url,
        '-frames:v', '1',
    ]
    
    # Only add scaling if frame_max_width is set (not -1)
    if settings.frame_max_width > 0:
        cmd.extend(['-vf', f'scale={settings.frame_max_width}:-1'])  # Maintain aspect ratio
    
    cmd.extend([
        '-q:v', str(settings.frame_quality),
        '-f', 'webp',
        '-y',  # Overwrite output file
        output_path
    ])

    try:
        # Execute FFmpeg with timeout
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout
        )

        if process.returncode != 0:
            logger.error(f"FFmpeg failed for {camera_name}: {stderr.decode()}")
            return None

        # Verify file was created and get info
        if not os.path.exists(output_path):
            logger.error(f"Frame file not created: {output_path}")
            return None

        file_size = os.path.getsize(output_path)

        # Try to get dimensions from FFmpeg output
        width, height = None, None
        try:
            # Parse stderr for video info (FFmpeg logs to stderr)
            stderr_text = stderr.decode()
            if 'Video:' in stderr_text:
                # Extract dimensions if available (basic parsing)
                for line in stderr_text.split('\n'):
                    if 'Stream' in line and 'Video:' in line:
                        parts = line.split(',')
                        for part in parts:
                            if 'x' in part and part.strip().replace('x', '').replace(' ', '').isdigit():
                                dims = part.strip().split('x')
                                if len(dims) == 2:
                                    width = int(dims[0].strip())
                                    height = int(dims[1].strip())
                                    break
        except Exception:
            pass  # Dimensions are optional

        logger.debug(f"Captured frame for {camera_name}: {file_size} bytes")

        return {
            'file_path': output_path,
            'file_size': file_size,
            'width': width,
            'height': height,
        }

    except asyncio.TimeoutError:
        logger.error(f"Timeout capturing frame for {camera_name}")
        try:
            process.kill()
        except Exception:
            pass
        return None
    except Exception as e:
        logger.error(f"Error capturing frame for {camera_name}: {e}")
        return None


async def save_frame_to_db(
    device_key: str,
    file_path: str,
    file_size: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Optional[CameraFrame]:
    """Save frame metadata to database."""
    try:
        async with AsyncSessionLocal() as db:
            frame = CameraFrame(
                device_key=device_key,
                timestamp=datetime.utcnow(),
                file_path=file_path,
                file_size=file_size,
                width=width,
                height=height,
            )
            db.add(frame)
            await db.commit()
            await db.refresh(frame)
            logger.info(f"Saved frame metadata for {device_key}: {file_path}")
            return frame
    except Exception as e:
        logger.error(f"Failed to save frame metadata for {device_key}: {e}")
        return None


async def capture_frame_for_camera(camera_name: str) -> Optional[CameraFrame]:
    """
    Capture a single frame for a camera and save to database.

    Args:
        camera_name: Name of the camera to capture from

    Returns:
        CameraFrame record or None on failure
    """
    # Generate output path with timestamp
    now = datetime.utcnow()
    date_dir = now.strftime("%Y-%m-%d")
    timestamp_str = now.strftime("%H-%M-%S")

    # Use relative path for storage (relative to app root)
    relative_path = f"data/camera_frames/{date_dir}/{camera_name}_{timestamp_str}.webp"

    # Full filesystem path
    full_path = os.path.join("/app", relative_path)

    # Capture the frame
    capture_info = await capture_single_frame(camera_name, full_path)

    if capture_info is None:
        return None

    # Save to database
    frame = await save_frame_to_db(
        device_key=camera_name,
        file_path=relative_path,
        file_size=capture_info.get('file_size'),
        width=capture_info.get('width'),
        height=capture_info.get('height'),
    )

    return frame


async def capture_all_cameras() -> Dict[str, any]:
    """
    Capture frames from all active cameras.

    Returns:
        Summary dict with capture statistics
    """
    if not settings.frame_capture_enabled:
        logger.debug("Frame capture is disabled")
        return {"captured": 0, "failed": 0, "skipped": 0}

    summary = {
        "captured": 0,
        "failed": 0,
        "cameras": [],
    }

    try:
        # Get active cameras from MediaMTX
        camera_paths = await get_active_camera_paths()

        if not camera_paths:
            logger.debug("No active cameras found")
            return summary

        logger.info(f"Capturing frames for {len(camera_paths)} cameras")

        # Capture frames concurrently
        tasks = [capture_frame_for_camera(camera) for camera in camera_paths]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for camera_name, result in zip(camera_paths, results):
            if isinstance(result, Exception):
                logger.error(f"Exception capturing {camera_name}: {result}")
                summary["failed"] += 1
            elif result is not None:
                summary["captured"] += 1
                summary["cameras"].append(camera_name)
            else:
                summary["failed"] += 1

        logger.info(
            f"Frame capture complete: {summary['captured']} captured, "
            f"{summary['failed']} failed"
        )

    except Exception as e:
        logger.error(f"Error in capture_all_cameras: {e}")

    return summary


async def cleanup_old_frames():
    """
    Delete old frames based on retention policy.
    Removes both database records and files from disk.
    """
    if settings.frame_retention_days <= 0:
        return  # Retention disabled

    try:
        cutoff = datetime.utcnow() - timedelta(days=settings.frame_retention_days)

        async with AsyncSessionLocal() as db:
            # Get old frames to delete
            result = await db.execute(
                select(CameraFrame).where(CameraFrame.timestamp < cutoff)
            )
            old_frames = result.scalars().all()

            if not old_frames:
                return

            logger.info(f"Cleaning up {len(old_frames)} old frames")

            # Delete files from disk
            deleted_files = 0
            for frame in old_frames:
                try:
                    full_path = os.path.join("/app", frame.file_path)
                    if os.path.exists(full_path):
                        os.remove(full_path)
                        deleted_files += 1
                except Exception as e:
                    logger.warning(f"Failed to delete file {frame.file_path}: {e}")

            # Delete from database
            await db.execute(
                delete(CameraFrame).where(CameraFrame.timestamp < cutoff)
            )
            await db.commit()

            logger.info(
                f"Cleanup complete: {deleted_files} files deleted, "
                f"{len(old_frames)} database records removed"
            )

    except Exception as e:
        logger.error(f"Error cleaning up old frames: {e}")
