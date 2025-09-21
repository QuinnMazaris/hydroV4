"""Unified backend entrypoint running the FastAPI app via Uvicorn."""

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
