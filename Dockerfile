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
COPY next.config.mjs ./
COPY tsconfig.json ./
COPY postcss.config.mjs ./
COPY components.json ./

# Build Next.js app with API_PORT environment variable
ENV API_PORT=8001
RUN npm run build

# Final stage - Python with Node
FROM python:3.11-slim AS production

WORKDIR /app

# Install system dependencies including Node.js
RUN apt-get update && apt-get install -y \
    gcc \
    curl \
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

# Set environment variables
ENV PYTHONPATH=/app
ENV NODE_ENV=production
ENV PORT=3001
ENV MQTT_BROKER=127.0.0.1

# Expose ports
EXPOSE 3001 8000

# Create startup script
RUN echo '#!/bin/bash\n\
cd /app && python -m backend &\n\
cd /app/frontend && npm start -- -p 3001 &\n\
wait' > /app/start.sh && chmod +x /app/start.sh

CMD ["/app/start.sh"]
