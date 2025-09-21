#!/usr/bin/env python3
"""
Hydroponic System MQTT Backend
Main entry point for the MQTT communication layer
"""

import asyncio
import signal
import sys
from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from database import init_db, cleanup_old_data
from mqtt_client import mqtt_client

class HydroMQTTService:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()
        self.running = False

    async def start(self):
        """Start the MQTT service"""
        try:
            logger.info("Starting Hydroponic MQTT Service")

            # Initialize database
            logger.info("Initializing database...")
            await init_db()

            # Connect to MQTT broker
            logger.info("Connecting to MQTT broker...")
            await mqtt_client.connect()

            # Start message processor
            await mqtt_client.start_message_processor()

            # Setup scheduled tasks
            self._setup_scheduler()

            # Start scheduler
            self.scheduler.start()

            self.running = True
            logger.info("Hydroponic MQTT Service started successfully")

            # Keep service running
            await self._run_forever()

        except Exception as e:
            logger.error(f"Failed to start service: {e}")
            await self.stop()
            sys.exit(1)

    def _setup_scheduler(self):
        """Setup scheduled tasks"""
        # Cleanup old data daily at 2 AM
        self.scheduler.add_job(
            cleanup_old_data,
            'cron',
            hour=2,
            minute=0,
            id='cleanup_old_data'
        )

        # Mark inactive devices every 5 minutes
        self.scheduler.add_job(
            mqtt_client.mark_inactive_devices,
            'interval',
            minutes=5,
            id='mark_inactive_devices'
        )

        logger.info("Scheduled tasks configured")

    async def _run_forever(self):
        """Keep the service running"""
        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Service shutdown requested")

    async def stop(self):
        """Stop the MQTT service"""
        logger.info("Stopping Hydroponic MQTT Service")

        self.running = False

        # Stop scheduler
        if self.scheduler.running:
            self.scheduler.shutdown()

        # Disconnect MQTT client
        await mqtt_client.disconnect()

        logger.info("Service stopped")

    def setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown"""
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            asyncio.create_task(self.stop())

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

async def main():
    """Main entry point"""
    # Configure logging
    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )

    # Create and start service
    service = HydroMQTTService()
    service.setup_signal_handlers()

    try:
        await service.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    finally:
        await service.stop()

if __name__ == "__main__":
    asyncio.run(main())