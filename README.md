# Hydro V4 - Hydroponics Management System

A modern hydroponics management system built with Next.js frontend and Python FastAPI backend, featuring real-time monitoring, device control, and data visualization.

## Current Architecture

- **Frontend**: Next.js application with real-time WebSocket connections
- **Backend**: Python FastAPI with SQLite database
- **Communication**: MQTT for device messaging
- **Deployment**: Docker containers with docker-compose

## Quick Start

```bash
# Build and run with Docker
docker-compose up -d

# Access the application
http://localhost:3001
```

## TODO: Future Enhancements

### 1. Automations & LLM Container
- [ ] Create new Docker container for automation engine
- [ ] Integrate LLM for intelligent decision making
- [ ] Add rule-based automation system
- [ ] Implement smart scheduling based on sensor data
- [ ] Add natural language processing for user commands
- [ ] Create automation rules UI in frontend
- [ ] Add machine learning models for predictive analytics
- [ ] Implement alert system with LLM-generated recommendations

### 2. RTSP Streaming Camera Integration
- [ ] Add camera container to docker-compose.yml
- [ ] Configure RTSP stream ingestion
- [ ] Implement video streaming in frontend
- [ ] Add camera controls (pan, tilt, zoom if supported)
- [ ] Create time-lapse recording functionality
- [ ] Add motion detection capabilities
- [ ] Implement plant growth monitoring with computer vision
- [ ] Add image analysis for plant health assessment
- [ ] Create automated photo capture schedules
- [ ] Add security monitoring features

### 3. Container Architecture Updates
- [ ] Update docker-compose.yml for multi-container setup
- [ ] Add inter-container communication
- [ ] Implement service discovery
- [ ] Add container health checks
- [ ] Configure shared volumes for data persistence
- [ ] Add container orchestration with proper networking
- [ ] Implement backup and restore functionality
- [ ] Add monitoring and logging aggregation

### 4. Database & Data Management
- [ ] Migrate from SQLite to PostgreSQL for better scalability
- [ ] Add data retention policies
- [ ] Implement data backup strategies
- [ ] Add data export/import functionality
- [ ] Create data analytics dashboard
- [ ] Add historical data visualization

### 5. Security & Authentication
- [ ] Add user authentication system
- [ ] Implement role-based access control
- [ ] Add API key management
- [ ] Implement secure MQTT communication
- [ ] Add SSL/TLS encryption
- [ ] Create audit logging

## Current Features

- Real-time sensor monitoring (pH, EC, temperature, water level)
- Device control (pumps, lights, fans)
- WebSocket-based live updates
- MQTT device communication
- SQLite database for data persistence
- Docker containerization
- Responsive web interface

## Technology Stack

- **Frontend**: Next.js, React, TypeScript, Tailwind CSS
- **Backend**: Python, FastAPI, SQLAlchemy, SQLite
- **Communication**: MQTT, WebSockets
- **Deployment**: Docker, Docker Compose
- **Database**: SQLite (planned migration to PostgreSQL)

## Development

```bash
# Frontend development
cd frontend
npm install
npm run dev

# Backend development
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

## Configuration

See `backend/README.md` for detailed configuration options including MQTT settings, database configuration, and environment variables.