"""Camera synchronization service for syncing MediaMTX cameras to the database."""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from ..services.persistence import upsert_device


MEDIAMTX_API_URL = "http://localhost:9997"


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
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Try v3 API first, fall back to v2
            response = await client.get(f"{MEDIAMTX_API_URL}/v3/paths/list")

            if response.status_code == 404:
                response = await client.get(f"{MEDIAMTX_API_URL}/v2/paths/list")

            if response.status_code != 200:
                error_msg = f"MediaMTX API returned status {response.status_code}"
                logger.warning(f"Camera sync failed: {error_msg}")
                summary["errors"].append(error_msg)
                return summary

            data = response.json()
            items = data.get("items", []) if isinstance(data, dict) else data

            current_time = datetime.utcnow()

            for item in items:
                try:
                    # Extract camera path name
                    path_name = item.get("name") or item.get("path", "")

                    # Skip system paths (starting with underscore)
                    if not path_name or path_name.startswith("_"):
                        continue

                    # Determine if camera is healthy (MediaMTX v3 uses "ready" field)
                    is_ready = item.get("ready", False)
                    is_healthy = is_ready

                    # Build camera metadata
                    camera_metadata = {
                        "ready": is_ready,
                        "tracks": item.get("tracks", []),
                        "readers": item.get("readers", 0),
                        "bytes_sent": item.get("bytesSent", 0),
                        "whep_url": f"http://localhost:8889/{path_name}/whep",
                        "last_sync": current_time.isoformat(),
                    }

                    # Only update last_seen for healthy cameras
                    # Unhealthy cameras will naturally expire after timeout
                    last_seen = current_time if is_healthy else None

                    # Upsert device record
                    await upsert_device(
                        device_key=path_name,
                        name=path_name,  # Use path name as display name
                        description=f"Camera stream via MediaMTX",
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
