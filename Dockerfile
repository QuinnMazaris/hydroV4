# Multi-stage build for Next.js frontend and Python backend
FROM node:18-alpine AS frontend-builder

WORKDIR /app

# Copy package files
COPY package*.json ./
COPY pnpm-lock.yaml ./

# Install dependencies
RUN npm ci

# Copy frontend source
COPY app ./app
COPY components ./components
COPY hooks ./hooks
COPY lib ./lib
COPY public ./public
COPY styles ./styles
COPY config ./config
COPY next.config.mjs ./
COPY tsconfig.json ./
COPY postcss.config.mjs ./
COPY components.json ./

# Build Next.js app with environment variables
ARG API_PORT=8001
ARG MEDIAMTX_WEBRTC_PORT=8889
ENV API_PORT=${API_PORT}
ENV MEDIAMTX_WEBRTC_PORT=${MEDIAMTX_WEBRTC_PORT}
RUN npm run build

# Final stage - Python with Node
FROM python:3.11-slim AS production

WORKDIR /app

# Install system dependencies including Node.js and FFmpeg
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
    ffmpeg \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Copy backend requirements and install Python dependencies
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend/

# Copy database
COPY hydro.db ./

# Copy built frontend from previous stage
COPY --from=frontend-builder /app/.next ./frontend/.next
COPY --from=frontend-builder /app/node_modules ./frontend/node_modules
COPY --from=frontend-builder /app/package.json ./frontend/
COPY --from=frontend-builder /app/next.config.mjs ./frontend/
COPY --from=frontend-builder /app/public ./frontend/public
COPY --from=frontend-builder /app/app ./frontend/app
COPY --from=frontend-builder /app/components ./frontend/components
COPY --from=frontend-builder /app/hooks ./frontend/hooks
COPY --from=frontend-builder /app/lib ./frontend/lib
COPY --from=frontend-builder /app/styles ./frontend/styles

# Set environment variables with defaults
ENV PYTHONPATH=/app
ENV NODE_ENV=production
ENV PORT=${PORT:-3001}
ENV API_PORT=${API_PORT:-8001}
ENV MQTT_BROKER=${MQTT_BROKER:-127.0.0.1}
ENV MQTT_PORT=${MQTT_PORT:-1883}
ENV MEDIAMTX_HOST=${MEDIAMTX_HOST:-localhost}
ENV MEDIAMTX_API_PORT=${MEDIAMTX_API_PORT:-9997}
ENV MEDIAMTX_WEBRTC_PORT=${MEDIAMTX_WEBRTC_PORT:-8889}

# Expose ports
EXPOSE ${PORT:-3001} ${API_PORT:-8001}

# Create startup script that uses PORT variable
RUN echo '#!/bin/bash\n\
    cd /app && alembic -c backend/alembic.ini upgrade head && python -m backend &\n\
    cd /app/frontend && npm start -- -p ${PORT:-3001} &\n\
    wait' > /app/start.sh && chmod +x /app/start.sh

CMD ["/app/start.sh"]
