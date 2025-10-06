"""FastAPI application exposing the gardener agent HTTP facade."""
from __future__ import annotations

from typing import Any, Dict, List, Sequence

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import GardenerAgent
from .config import settings
from .hydro_client import HydroAPIClient
from .llm_providers import ChatMessage, create_provider
from .tools import ToolRegistry, build_tool_registry

app = FastAPI(title="Hydro Gardener", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessagePayload(BaseModel):
    role: str = Field(description="Role of the message, e.g. user or assistant")
    content: str = Field(description="Message content")


class AgentRunRequest(BaseModel):
    messages: Sequence[ChatMessagePayload]
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)


class AgentRunResponse(BaseModel):
    final: str
    trace: List[Dict[str, Any]]


async def get_agent(request: Request) -> GardenerAgent:
    agent: GardenerAgent | None = getattr(request.app.state, "agent", None)
    if not agent:
        raise HTTPException(status_code=503, detail="Agent not initialised")
    return agent


async def get_registry(request: Request) -> ToolRegistry:
    registry: ToolRegistry | None = getattr(request.app.state, "registry", None)
    if not registry:
        raise HTTPException(status_code=503, detail="Tool registry not initialised")
    return registry


@app.on_event("startup")
async def startup_event() -> None:
    client = HydroAPIClient()
    registry = await build_tool_registry(client)
    provider = create_provider()
    agent = GardenerAgent(provider=provider, registry=registry)

    app.state.client = client
    app.state.registry = registry
    app.state.provider = provider
    app.state.agent = agent


@app.on_event("shutdown")
async def shutdown_event() -> None:
    provider = getattr(app.state, "provider", None)
    if provider:
        await provider.aclose()
    client = getattr(app.state, "client", None)
    if client:
        await client.aclose()


@app.get("/health")
async def health() -> Dict[str, Any]:
    client: HydroAPIClient | None = getattr(app.state, "client", None)
    return {
        "status": "ok",
        "hydro_api_base_url": settings.hydro_api_base_url,
        "actuator_dry_run": settings.actuator_dry_run,
        "client_initialised": client is not None,
    }


@app.get("/tools")
async def list_tools(registry: ToolRegistry = Depends(get_registry)) -> Dict[str, Any]:
    await registry.refresh()
    return {
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in registry.all()
        ]
    }


@app.post("/agent/run", response_model=AgentRunResponse)
async def run_agent(
    request: AgentRunRequest,
    agent: GardenerAgent = Depends(get_agent),
) -> AgentRunResponse:
    messages = [ChatMessage(role=msg.role, content=msg.content) for msg in request.messages]
    result = await agent.run(messages=messages, temperature=request.temperature)
    return AgentRunResponse(**result)
