# Environment Variables Migration Summary

## Overview
All hardcoded values in the codebase have been replaced with environment variables that have sensible defaults. This makes the system more flexible and easier to deploy in different environments.

## Files Modified

### Backend

#### 1. `backend/config.py`
**Added new configuration options:**
- `api_port`: Backend API port (default: 8001)
- `mediamtx_host`: MediaMTX hostname (default: localhost)
- `mediamtx_api_port`: MediaMTX API port (default: 9997)
- `mediamtx_webrtc_port`: MediaMTX WebRTC/WHEP port (default: 8889)
- `frontend_port`: Frontend port (default: 3001)

#### 2. `backend/services/camera_sync.py`
**Changes:**
- Removed hardcoded `MEDIAMTX_API_URL = "http://localhost:9997"`
- Added `get_mediamtx_api_url()` function that uses settings
- Added `get_mediamtx_whep_url()` function that uses settings
- Now dynamically builds URLs from environment variables

### Frontend

#### 3. `next.config.mjs`
**Changes:**
- Added `NEXT_PUBLIC_MEDIAMTX_WEBRTC_PORT` environment variable
- Passes MediaMTX WebRTC port to frontend code
- Already had `NEXT_PUBLIC_API_PORT` for API port

#### 4. `hooks/use-cameras.ts`
**Changes:**
- Removed hardcoded `:8889` port
- Now uses `process.env.NEXT_PUBLIC_MEDIAMTX_WEBRTC_PORT || "8889"`
- Dynamically builds WHEP URLs from environment variable

#### 5. `components/camera-feed.tsx`
**Changes:**
- Removed hardcoded `:8889` port
- Now uses `process.env.NEXT_PUBLIC_MEDIAMTX_WEBRTC_PORT || '8889'`
- Dynamically builds WHEP URLs from environment variable

### Docker Configuration

#### 6. `docker-compose.yml`
**Changes:**
- Reorganized environment variables into logical groups
- Added all configurable environment variables:
  - `PORT` (Frontend port)
  - `API_PORT` (Backend API port)
  - `MQTT_BROKER` & `MQTT_PORT`
  - `MEDIAMTX_HOST`, `MEDIAMTX_API_PORT`, `MEDIAMTX_WEBRTC_PORT`
- All variables use `${VAR:-default}` syntax for defaults
- Updated healthcheck to use `${FRONTEND_PORT:-3001}`

#### 7. `Dockerfile`
**Changes:**
- Added build-time arguments for Next.js build:
  - `ARG API_PORT=8001`
  - `ARG MEDIAMTX_WEBRTC_PORT=8889`
- Added runtime environment variables with defaults:
  - `PORT`, `API_PORT`, `MQTT_BROKER`, `MQTT_PORT`
  - `MEDIAMTX_HOST`, `MEDIAMTX_API_PORT`, `MEDIAMTX_WEBRTC_PORT`
- Updated startup script to use `${PORT:-3001}` variable

### Documentation

#### 8. `CONFIGURATION.md` (NEW)
**Added comprehensive documentation:**
- Lists all environment variables
- Provides descriptions and defaults
- Includes usage examples
- Explains how to customize configuration

## Environment Variables Reference

| Variable | Default | Where Used | Description |
|----------|---------|------------|-------------|
| `FRONTEND_PORT` | `3001` | Docker, Next.js | Frontend web server port |
| `API_PORT` | `8001` | Docker, FastAPI, Next.js | Backend API port |
| `MQTT_BROKER` | `127.0.0.1` | Backend | MQTT broker hostname |
| `MQTT_PORT` | `1883` | Backend | MQTT broker port |
| `MEDIAMTX_HOST` | `localhost` | Backend, Frontend | MediaMTX server hostname |
| `MEDIAMTX_API_PORT` | `9997` | Backend | MediaMTX REST API port |
| `MEDIAMTX_WEBRTC_PORT` | `8889` | Backend, Frontend | MediaMTX WebRTC/WHEP port |

## How to Customize

### Option 1: Using .env file
Create a `.env` file in the project root:
```bash
# .env
API_PORT=8080
MEDIAMTX_WEBRTC_PORT=9000
MQTT_BROKER=192.168.1.100
```

Docker Compose will automatically pick up these values.

### Option 2: Inline with docker-compose
```bash
API_PORT=8080 MEDIAMTX_WEBRTC_PORT=9000 docker-compose up -d
```

### Option 3: Export environment variables
```bash
export API_PORT=8080
export MEDIAMTX_WEBRTC_PORT=9000
docker-compose up -d
```

## Backward Compatibility

✅ All changes are **fully backward compatible**:
- If no environment variables are set, the system uses the original hardcoded defaults
- Existing deployments will continue to work without any changes
- The default values match the previous hardcoded values exactly

## Testing

Verified working:
- ✅ Backend API running on configured port (8001)
- ✅ Frontend connecting to backend via proxy
- ✅ Camera sync using MediaMTX API URL from settings
- ✅ Camera feeds loading with WebRTC from configured port
- ✅ All environment variables properly passed through Docker

## Benefits

1. **Flexibility**: Easy to change ports without modifying code
2. **Multi-environment**: Can run multiple instances with different configurations
3. **Security**: Sensitive values (like MQTT credentials) can be injected at runtime
4. **Deployment**: Easier to deploy to different hosting environments
5. **Documentation**: Clear list of all configurable options

