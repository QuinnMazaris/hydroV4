# Camera System Architecture

## Overview

The camera system is now **dynamic** - cameras are automatically discovered from MediaMTX configuration and displayed in the frontend without hardcoding.

## Architecture Layers

### 1. **MediaMTX (Manual Configuration)**
- **File**: `mediamtx.yml`
- **Purpose**: RTSP source configuration and WebRTC streaming
- **Manual Step**: Add camera paths here

```yaml
paths:
  camera_1:
    source: rtsp://USER:PASS@IP:PORT/path
    rtspTransport: tcp
    sourceOnDemand: no

  camera_2:  # Add new cameras here
    source: rtsp://USER:PASS@IP2:PORT/path
    rtspTransport: tcp
    sourceOnDemand: no
```

### 2. **Backend API (Dynamic Discovery)**
- **Endpoints**:
  - `GET /api/camera/list` - Query MediaMTX API to list all camera paths
  - `GET /api/camera/health` - Get real-time health status of all cameras
- **Technology**: FastAPI + httpx to query MediaMTX API (port 9997)
- **Auto-discovery**: Reads MediaMTX paths and exposes them to frontend

### 3. **Frontend (Dynamic Rendering)**
- **Hook**: `useCameras()` - Fetches camera list from backend API
- **Component**: `<CameraFeed>` - WebRTC WHEP player
- **Rendering**: `page.tsx` maps over cameras array and renders dynamically

## Data Flow

```
User adds camera to mediamtx.yml
    ↓
docker compose restart mediamtx
    ↓
MediaMTX ingests RTSP stream and creates path
    ↓
Backend queries MediaMTX API (/v3/paths/list)
    ↓
Frontend fetches from /api/camera/list
    ↓
useCameras() hook updates state
    ↓
page.tsx renders <CameraFeed> for each camera
    ↓
WebRTC connection via WHEP protocol
```

## Integration with Hydro Device System

### Current State
- **Cameras**: Separate system via MediaMTX
- **Devices**: MQTT-based sensors/actuators in database

### Integration Options

#### Option 1: **Separate Systems** (Current - Recommended)
**Pros:**
- Clean separation of concerns
- Cameras don't pollute device database
- Different lifecycle (cameras are infrastructure, devices are dynamic)
- MediaMTX handles all camera logic

**Cons:**
- Two separate systems to manage

#### Option 2: **Register Cameras as Devices**
Auto-register cameras in the database:
```python
# On startup or periodic sync
for camera in mediamtx_cameras:
    await upsert_device(
        device_key=camera.path,
        name=camera.name,
        device_type='camera',
        last_seen=datetime.utcnow()
    )
```

**Pros:**
- Unified device view in dashboard
- Cameras appear in "Device Overview"
- Can track camera metrics/uptime

**Cons:**
- Cameras aren't really MQTT devices
- Adds complexity
- Database becomes source of truth for infrastructure

#### Option 3: **Hybrid Approach**
- Keep cameras in MediaMTX
- Show them in UI alongside devices
- Don't store in database
- Use device_key naming convention to group them

**Recommended:** **Option 1** - Keep systems separate. Cameras are infrastructure (like MediaMTX), devices are application-level (like sensors).

## Adding New Cameras

### Steps:
1. **Update mediamtx.yml**:
   ```yaml
   paths:
     new_camera:
       source: rtsp://USER:PASS@IP:PORT/path
       rtspTransport: tcp
       sourceOnDemand: no
   ```

2. **Restart MediaMTX**:
   ```bash
   docker compose restart mediamtx
   ```

3. **Automatic from here**:
   - Backend discovers new camera via API
   - Frontend auto-renders new feed
   - No code changes needed!

## Future Enhancements

1. **Camera Controls**:
   - PTZ controls via MediaMTX
   - Snapshot capture endpoint
   - Recording controls

2. **Analytics Integration**:
   - Send snapshots to LLM for plant health analysis
   - Store analysis results in database
   - Display insights in UI

3. **Multi-view Layouts**:
   - Grid view for multiple cameras
   - Fullscreen toggle
   - Picture-in-picture

4. **Camera Settings UI**:
   - Add cameras via UI (updates mediamtx.yml)
   - Configure quality/bitrate
   - Enable/disable cameras

## API Reference

### GET /api/camera/list
Returns list of cameras from MediaMTX.

**Response:**
```json
{
  "cameras": [
    {
      "device_key": "camera_1",
      "name": "camera_1",
      "ready": true,
      "source_ready": true,
      "tracks": ["H264", "MPEG-4 Audio"],
      "readers": 1,
      "whep_url": "http://localhost:8889/camera_1/whep"
    }
  ],
  "count": 1
}
```

### GET /api/camera/health
Returns health status of all cameras.

**Response:**
```json
{
  "cameras": [
    {
      "device_key": "camera_1",
      "online": true,
      "ready": true,
      "source_ready": true,
      "bytes_sent": 1048576,
      "readers": 1
    }
  ]
}
```
