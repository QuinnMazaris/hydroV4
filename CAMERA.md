# Camera Integration Guide

The Hydro system now supports RTSP camera integration for plant monitoring with both live streaming and automated frame capture.

## Features

✅ **Live HLS Streaming** - Real-time video feed in the dashboard
✅ **Automated Frame Capture** - WebP images captured hourly
✅ **LLM-Ready Storage** - Structured database for AI analysis
✅ **Dynamic Device Integration** - Camera appears as a device in your system
✅ **Low Storage Footprint** - ~60-80KB per frame, 30-day retention

---

## Configuration

### 1. Update Environment Variables

Edit `backend/.env` or set in `docker-compose.yml`:

```bash
# Camera Configuration
CAMERA_ENABLED=true
CAMERA_RTSP_URL=rtsp://admin:password@192.168.1.100:554/stream
CAMERA_DEVICE_KEY=camera_1
CAMERA_CAPTURE_INTERVAL=3600  # 1 hour (3600 seconds)
CAMERA_FRAME_QUALITY=80       # WebP quality 0-100
CAMERA_RETENTION_HOURS=720    # 30 days
CAMERA_STREAM_ENABLED=true    # Enable live streaming
```

### 2. Configure Your RTSP Camera

Replace the `CAMERA_RTSP_URL` with your camera's RTSP stream URL:

**Common RTSP URL Formats:**
```bash
# Generic IP Camera
rtsp://username:password@192.168.1.100:554/stream

# Hikvision
rtsp://admin:password@192.168.1.100:554/Streaming/Channels/101

# Dahua
rtsp://admin:password@192.168.1.100:554/cam/realmonitor?channel=1&subtype=0

# Reolink
rtsp://admin:password@192.168.1.100:554/h264Preview_01_main

# TP-Link
rtsp://admin:password@192.168.1.100:554/stream1

# Wyze (with RTSP firmware)
rtsp://username:password@192.168.1.100:554/live
```

### 3. Test RTSP Connection

Before deploying, test your RTSP URL with FFmpeg:

```bash
ffmpeg -i "rtsp://admin:password@192.168.1.100:554/stream" -frames:v 1 test.jpg
```

If successful, you'll get a `test.jpg` image.

---

## Deployment

### Option A: Docker (Recommended)

```bash
# Update your RTSP URL in docker-compose.yml
nano docker-compose.yml

# Rebuild and restart
docker-compose down
docker-compose up -d --build

# Check logs
docker logs -f hydro-production
```

### Option B: Local Development

```bash
# Install FFmpeg
sudo apt-get install ffmpeg

# Install frontend dependencies (includes hls.js)
npm install

# Start backend
cd backend
python -m backend

# Start frontend (separate terminal)
npm run dev
```

---

## Usage

### Live Stream

Once deployed, the camera feed will automatically appear at the top of your dashboard when the camera device is detected.

**Access Points:**
- **Dashboard**: Full-width video player at top
- **Direct Stream**: `http://localhost:8001/api/camera/stream/stream.m3u8`
- **Status**: `http://localhost:8001/api/camera/status`

### Frame Capture

Frames are automatically captured every hour (configurable) and stored in the database.

**API Endpoints:**
```bash
# Get latest frame
GET /api/camera/latest

# Get all frames
GET /api/camera/frames?limit=100

# Get specific frame
GET /api/camera/image/{frame_id}

# Get frame info (with analysis metadata)
GET /api/camera/frames/{frame_id}
```

---

## Storage

### Frame Storage Layout

```
/app/data/camera/
├── frames/
│   ├── 2025-09-29/
│   │   ├── camera_1_14-00-00.webp
│   │   ├── camera_1_15-00-00.webp
│   │   └── camera_1_16-00-00.webp
│   └── 2025-09-30/
│       └── ...
└── hls/
    ├── stream.m3u8
    └── segment_*.ts (ephemeral)
```

### Storage Estimates

| Configuration | Daily Storage | 30-Day Storage |
|---------------|---------------|----------------|
| 1 frame/hour  | ~1.5 MB       | ~45 MB         |
| 1 frame/30min | ~3 MB         | ~90 MB         |
| 1 frame/15min | ~6 MB         | ~180 MB        |

**HLS Stream**: Ephemeral, ~10MB buffer (not stored)

---

## Database Schema

Camera frames are stored in the `camera_frames` table:

```sql
CREATE TABLE camera_frames (
    id INTEGER PRIMARY KEY,
    device_key VARCHAR(100),
    timestamp DATETIME,
    file_path VARCHAR(500),
    file_size INTEGER,
    width INTEGER,
    height INTEGER,
    -- LLM Analysis fields (future use)
    analyzed_at DATETIME,
    analysis_model VARCHAR(100),
    detected_objects JSON,
    plant_health_score INTEGER,
    anomaly_detected BOOLEAN,
    notes TEXT
);
```

---

## Future: LLM Integration

The system is ready for AI-powered plant analysis. Example integration:

```python
from openai import OpenAI
import base64

async def analyze_frame(frame_id: int):
    # Get frame from database
    frame = await get_frame(frame_id)

    # Load image
    with open(frame.file_path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode()

    # Analyze with GPT-4 Vision
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4-vision-preview",
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "Analyze this hydroponic plant. Rate health 0-100 and describe condition."},
                {"type": "image_url", "image_url": f"data:image/webp;base64,{image_data}"}
            ]
        }]
    )

    # Save analysis to database
    frame.analyzed_at = datetime.utcnow()
    frame.notes = response.choices[0].message.content
    await db.commit()
```

---

## Troubleshooting

### Camera Not Appearing in Dashboard

1. Check camera is registered as device:
   ```bash
   curl http://localhost:8001/api/devices
   ```

2. Check camera status:
   ```bash
   curl http://localhost:8001/api/camera/status
   ```

3. Check logs:
   ```bash
   docker logs -f hydro-production | grep -i camera
   ```

### Live Stream Not Working

1. Verify FFmpeg is installed in container:
   ```bash
   docker exec hydro-production ffmpeg -version
   ```

2. Test RTSP connection from container:
   ```bash
   docker exec hydro-production ffmpeg -i "YOUR_RTSP_URL" -frames:v 1 /tmp/test.jpg
   ```

3. Check HLS files are being created:
   ```bash
   docker exec hydro-production ls -lh /app/data/camera/hls/
   ```

### Frame Capture Failing

1. Check FFmpeg can connect to camera
2. Verify write permissions on `/app/data/camera/frames/`
3. Check database for errors:
   ```bash
   sqlite3 hydro.db "SELECT * FROM camera_frames ORDER BY timestamp DESC LIMIT 5;"
   ```

---

## Disabling Camera

To disable camera features:

```bash
# In docker-compose.yml or .env
CAMERA_ENABLED=false
```

Or comment out camera environment variables and rebuild.

---

## Performance Notes

- **CPU Usage**: ~10-20% for continuous HLS streaming on Raspberry Pi 4
- **Memory**: ~100-200MB for FFmpeg processes
- **Network**: ~2-5 Mbps for 1080p RTSP stream
- **Disk I/O**: Minimal (one 60KB write per hour for frames)

For better performance on Raspberry Pi:
- Lower frame capture frequency (e.g., every 2 hours)
- Reduce stream resolution in camera settings
- Disable streaming if only using frame capture

---

## Advanced Configuration

### Custom Capture Schedule

Modify `backend/services/camera_capture.py` to implement custom logic:

```python
# Example: Capture only during daylight hours
from datetime import datetime

async def should_capture_now():
    hour = datetime.now().hour
    return 6 <= hour <= 20  # 6 AM to 8 PM
```

### Multiple Cameras

To add multiple cameras, duplicate the camera configuration with different device keys:

```bash
CAMERA_1_ENABLED=true
CAMERA_1_RTSP_URL=rtsp://...
CAMERA_1_DEVICE_KEY=camera_greenhouse

CAMERA_2_ENABLED=true
CAMERA_2_RTSP_URL=rtsp://...
CAMERA_2_DEVICE_KEY=camera_nursery
```

(Requires code modifications to support multiple cameras)

---

## Support

For issues or questions:
- Check logs: `docker logs hydro-production`
- Test RTSP URL with VLC or FFmpeg
- Verify network connectivity to camera
- Ensure camera supports RTSP protocol