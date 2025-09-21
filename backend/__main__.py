"""
Unified backend entrypoint.
Starts the FastAPI app, which on startup initializes the DB, connects MQTT,
and starts the MQTT message processor.
"""

import uvicorn
from .config import settings


def main() -> None:
    uvicorn.run(
        "backend.api:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()


