# Camera System Architecture

This document summarises how cameras are configured, ingested, tracked, and rendered in Hydro V4 after the MediaMTX integration clean-up.

## 1. Manual Configuration (Required)
- **File**: `mediamtx.yml`
- **Purpose**: Declare RTSP sources for each camera path.
- **Action**: Edit this file when adding or removing cameras.
- **Example**:
  ```yaml
  paths:
    camera_1:
      source: rtsp://user:pass@10.0.0.46:555/live/ch1
      rtspTransport: tcp
      sourceOnDemand: no
  ```
  MediaMTX handles the RTSP ingest for every configured path.

## 2. MediaMTX Container (Infrastructure)
- **Service**: `mediamtx` (Docker, host network)
- **Important ports**: 8889 (WebRTC/WHEP), 9997 (REST API), 8554 (RTSP ingest)
- **Responsibilities**:
  - Pull RTSP streams defined in `mediamtx.yml`
  - Serve WebRTC sessions at `http://localhost:8889/{path}/whep`
  - Report path status through the REST API at `http://localhost:9997/v3/paths/list`

## 3. Backend Database Sync (Auto-Discovery)
- **Module**: `backend/services/camera_sync.py`
  - Queries `http://localhost:9997/v3/paths/list`
  - Upserts each camera path into the `devices` table with `device_type='camera'`
  - Captures metadata (`ready`, `source_ready`, `tracks`, `readers`, `whep_url`, etc.)
- **Maintenance Loop**: `backend/api.py` → `maintenance_loop()`
  1. Runs `sync_cameras_to_db()` every 60 seconds
  2. Marks cameras inactive after five minutes with no healthy sync via `mark_devices_inactive()`
- **Persistence Helpers**: `backend/services/persistence.py`
  - `upsert_device()` stores state and metadata
  - `mark_devices_inactive()` expires stale devices
- **Model**: `backend/models.py`
  - `Device` rows hold `device_key`, `device_type='camera'`, `last_seen`, `is_active`, and JSON metadata

## 4. Backend API Surface
- **Active endpoint**: `GET /api/devices`
  - Optional query params: `device_type`, `active_only`
  - Returns `DeviceResponse` objects, including `device_type` and `device_metadata`
- **Removed**: direct MediaMTX proxy endpoints (`/api/camera/list`, `/api/camera/health`) to ensure a single source of truth (the database)

## 5. Frontend Display
- **Hook**: `hooks/use-cameras.ts`
  - Fetches `GET http://localhost:8001/api/devices?device_type=camera&active_only=false`
  - Parses `device_metadata` JSON to recover WHEP URL, readiness, track list, etc.
  - Exposes `cameras`, `isLoading`, and `error`
- **Component**: `components/camera-feed.tsx`
  - Establishes a WebRTC connection to `http://{hostname}:8889/{deviceKey}/whep`
  - Shows live video, connection spinner, and retry UI when necessary
- **Dashboard**: `app/page.tsx`
  - Invokes `useCameras()` and renders a `<CameraFeed>` for each discovered camera
  - Displays loading, error, and empty states when appropriate

## Complete Data Flow
```
1. Edit mediamtx.yml → define camera path(s)
2. MediaMTX container ingests RTSP and exposes WHEP + status API
3. Backend maintenance loop syncs MediaMTX paths into the devices table
4. Frontend requests /api/devices?device_type=camera
5. Dashboard renders <CameraFeed> for each camera entry
```
- **Heartbeat**: healthy cameras refresh `last_seen` every sync; offline cameras expire (is_active → False) after 5 minutes.
- **Single Source of Truth**: the database now represents camera availability, keeping the frontend and other services consistent.

## Operations Summary
1. **Add/Remove Camera**: update `mediamtx.yml`, restart MediaMTX if needed.
2. **Verify Sync**: `curl http://localhost:8001/api/devices?device_type=camera&active_only=false`
3. **View in UI**: reload the dashboard; offline cameras show the retry state from `<CameraFeed>`.

This unified approach keeps MediaMTX responsible for streaming while the Hydro backend/database manages presence and health.
