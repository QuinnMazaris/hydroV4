"""Run database migration script."""
import asyncio
from pathlib import Path
from sqlalchemy import text
from database import AsyncSessionLocal

async def run_migration():
    """Execute migration SQL file."""
    migration_file = Path(__file__).parent / "migrations" / "001_add_control_mode.sql"

    if not migration_file.exists():
        print(f"❌ Migration file not found: {migration_file}")
        return False

    # Read migration SQL
    with open(migration_file, 'r') as f:
        sql = f.read()

    # Execute migration
    async with AsyncSessionLocal() as db:
        try:
            # Split by semicolon and execute each statement
            statements = [s.strip() for s in sql.split(';') if s.strip() and not s.strip().startswith('--')]

            for statement in statements:
                if statement:
                    print(f"Executing: {statement[:100]}...")
                    await db.execute(text(statement))

            await db.commit()
            print("✅ Migration completed successfully")
            return True

        except Exception as exc:
            print(f"❌ Migration failed: {exc}")
            await db.rollback()
            return False

if __name__ == "__main__":
    success = asyncio.run(run_migration())
    exit(0 if success else 1)
