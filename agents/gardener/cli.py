"""CLI helpers for running the gardener agent in different modes."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager

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


def main() -> None:
    parser = argparse.ArgumentParser(description="Gardener agent utilities")
    parser.add_argument(
        "mode",
        choices=["stdio"],
        help="Mode to run. stdio launches the MCP server over stdio.",
    )

    args = parser.parse_args()

    if args.mode == "stdio":
        asyncio.run(_run_stdio())
    else:  # pragma: no cover - argparse prevents this
        raise SystemExit(f"Unknown mode {args.mode}")


if __name__ == "__main__":
    main()
