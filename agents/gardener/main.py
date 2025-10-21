"""Entrypoint for running the gardener service with both HTTP API and automation engine."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import uvicorn

from .agent import GardenerAgent
from .app import app
from .automation_runner import AutomationEngine
from .config import settings
from .hydro_client import HydroAPIClient
from .llm_providers import create_provider
from .tools import build_tool_registry

logger = logging.getLogger(__name__)


async def run_automation_engine(
    rules_path: Path,
    client: HydroAPIClient,
    agent: GardenerAgent,
    interval: int = 30
) -> None:
    """Run the automation engine in a continuous loop.

    Args:
        rules_path: Path to automation rules JSON file
        client: Hydro API client
        agent: Gardener agent instance for AI actions
        interval: Seconds between evaluation cycles (default 30)
    """
    engine = AutomationEngine(rules_path, client, agent)

    try:
        await engine.run_loop(interval=interval)
    except asyncio.CancelledError:
        logger.info("Automation engine stopped")
        raise


async def run_services() -> None:
    """Run both the HTTP API server and automation engine concurrently."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    logger.info("Starting Gardener services...")

    # Initialize shared resources
    client = HydroAPIClient()
    registry = await build_tool_registry(client)
    provider = create_provider()
    agent = GardenerAgent(provider=provider, registry=registry)

    # Store in app state for HTTP endpoints
    app.state.client = client
    app.state.registry = registry
    app.state.provider = provider
    app.state.agent = agent

    # Setup paths
    base_path = Path(__file__).parent
    rules_path = base_path / 'data' / 'automation_rules.json'

    # Create uvicorn config
    log_level = "debug" if settings.request_log_sample_rate >= 1.0 else "info"
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=log_level,
    )
    server = uvicorn.Server(config)

    # Run both HTTP server and automation engine concurrently
    try:
        await asyncio.gather(
            server.serve(),
            run_automation_engine(rules_path, client, agent, interval=30)
        )
    except KeyboardInterrupt:
        logger.info("Services stopped by user")
    finally:
        # Cleanup
        await provider.aclose()
        await client.aclose()


def main() -> None:
    """Main entry point."""
    asyncio.run(run_services())


if __name__ == "__main__":
    main()
