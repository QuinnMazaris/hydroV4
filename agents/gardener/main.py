"""Entrypoint for running the gardener FastAPI service via uvicorn."""
from __future__ import annotations

import asyncio

import uvicorn

from .app import app
from .config import settings


def run_http() -> None:
    log_level = "debug" if settings.request_log_sample_rate >= 1.0 else "info"
    uvicorn.run(
        "agents.gardener.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level=log_level,
    )


def main() -> None:
    run_http()


if __name__ == "__main__":
    main()
