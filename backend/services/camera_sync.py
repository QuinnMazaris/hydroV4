"""Camera synchronization service for syncing MediaMTX cameras to the database."""

import json
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from ..config import settings
from ..services.persistence import upsert_device
from ..utils.time import utc_now


def get_mediamtx_api_url() -> str:
    """Get the MediaMTX API URL from settings."""
    return f"http://{settings.mediamtx_host}:{settings.mediamtx_api_port}"


def get_mediamtx_whep_url(path_name: str) -> str:
    """Get the MediaMTX WHEP URL for a given path."""
    return f"http://{settings.mediamtx_host}:{settings.mediamtx_webrtc_port}/{path_name}/whep"


async def sync_cameras_to_db() -> Dict[str, Any]:
    """
    Query MediaMTX API and sync all cameras to the devices table.

    Returns a summary of the sync operation with counts and any errors.
    """
    summary = {
        "synced": 0,
        "healthy": 0,
        "unhealthy": 0,
        "errors": [],
    }

    try:
        mediamtx_api_url = get_mediamtx_api_url()
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Try v3 API first, fall back to v2
            response = await client.get(f"{mediamtx_api_url}/v3/paths/list")

            if response.status_code == 404:
                response = await client.get(f"{mediamtx_api_url}/v2/paths/list")

            if response.status_code != 200:
                error_msg = f"MediaMTX API returned status {response.status_code}"
                logger.warning(f"Camera sync failed: {error_msg}")
                summary["errors"].append(error_msg)
                return summary

            data = response.json()
            items = data.get("items", []) if isinstance(data, dict) else data

            current_time = utc_now()

            for item in items:
                try:
                    # Extract camera path name
                    raw_path = item.get("path") or item.get("name") or ""
                    display_name = item.get("name") or raw_path
                    path_name = raw_path

                    # Skip system paths (starting with underscore)
                    if not path_name or path_name.startswith("_"):
                        continue

                    # Determine if camera is healthy (MediaMTX v3 uses "ready" field)
                    is_ready = item.get("ready", False)
                    source_ready = item.get("sourceReady", False)
                    is_healthy = is_ready

                    # Build camera metadata
                    camera_metadata = {
                        "ready": is_ready,
                        "source_ready": source_ready,
                        "tracks": item.get("tracks", []),
                        "readers": item.get("readers", 0),
                        "bytes_sent": item.get("bytesSent", 0),
                        "whep_url": get_mediamtx_whep_url(path_name),
                        "last_sync": current_time.isoformat(),
                    }

                    # Only update last_seen for healthy cameras
                    # Unhealthy cameras will naturally expire after timeout
                    last_seen = current_time if is_healthy else None

                    # Upsert device record
                    await upsert_device(
                        device_key=path_name,
                        name=display_name or path_name,
                        description="Camera stream via MediaMTX",
                        metadata=json.dumps(camera_metadata),
                        last_seen=last_seen,
                        device_type='camera',
                    )

                    summary["synced"] += 1
                    if is_healthy:
                        summary["healthy"] += 1
                    else:
                        summary["unhealthy"] += 1

                    logger.debug(f"Synced camera {path_name} (healthy: {is_healthy})")

                except Exception as e:
                    error_msg = f"Failed to sync camera {path_name}: {str(e)}"
                    logger.error(error_msg)
                    summary["errors"].append(error_msg)

            if summary["synced"] > 0:
                logger.info(
                    f"Camera sync complete: {summary['synced']} cameras "
                    f"({summary['healthy']} healthy, {summary['unhealthy']} unhealthy)"
                )

    except httpx.RequestError as e:
        error_msg = f"Failed to connect to MediaMTX API: {str(e)}"
        logger.warning(error_msg)
        summary["errors"].append(error_msg)
    except Exception as e:
        error_msg = f"Unexpected error during camera sync: {str(e)}"
        logger.error(error_msg)
        summary["errors"].append(error_msg)

    return summary
