"""CLI helpers for running the gardener agent in different modes."""
from __future__ import annotations

import argparse
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from .automation_runner import AutomationEngine
from .hydro_client import HydroAPIClient
from .mcp_server import serve_stdio
from .tools import build_tool_registry


@asynccontextmanager
async def _managed_client():
    client = HydroAPIClient()
    try:
        yield client
    finally:
        await client.aclose()


async def _run_stdio() -> None:
    async with _managed_client() as client:
        registry = await build_tool_registry(client)
        await serve_stdio(registry)


async def _run_automation(interval: int = 30) -> None:
    """Run the automation engine in a continuous loop.

    Args:
        interval: Seconds between evaluation cycles (default 30)
    """
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Setup paths
    base_path = Path(__file__).parent
    rules_path = base_path / 'data' / 'automation_rules.json'

    # Create hydro client and engine
    async with _managed_client() as client:
        engine = AutomationEngine(rules_path, client)

        try:
            await engine.run_loop(interval=interval)
        except KeyboardInterrupt:
            logging.info("Automation engine stopped by user")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gardener agent utilities")
    parser.add_argument(
        "mode",
        choices=["stdio", "automation"],
        help="Mode to run. 'stdio' launches the MCP server, 'automation' runs the automation engine.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=30,
        help="Automation loop interval in seconds (default: 30)",
    )

    args = parser.parse_args()

    if args.mode == "stdio":
        asyncio.run(_run_stdio())
    elif args.mode == "automation":
        asyncio.run(_run_automation(interval=args.interval))
    else:  # pragma: no cover - argparse prevents this
        raise SystemExit(f"Unknown mode {args.mode}")


if __name__ == "__main__":
    main()
