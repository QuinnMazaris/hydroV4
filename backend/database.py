from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from .models import Base
from .config import settings
import asyncio

# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True
)

# Create async session factory
AsyncSessionLocal = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

async def init_db():
    """Initialize database tables"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def get_db():
    """Dependency to get database session"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def cleanup_old_data():
    """Clean up old sensor readings based on retention policy"""
    from datetime import datetime, timedelta
    from sqlalchemy import delete
    from models import SensorReading

    cutoff_date = datetime.utcnow() - timedelta(days=settings.data_retention_days)

    async with AsyncSessionLocal() as session:
        # Delete old sensor readings
        stmt = delete(SensorReading).where(SensorReading.timestamp < cutoff_date)
        await session.execute(stmt)
        await session.commit()