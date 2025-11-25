"""FastAPI application exposing the gardener agent HTTP facade."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .agent import GardenerAgent
from .config import settings
from .hydro_client import HydroAPIClient
from .llm_providers import ChatMessage, create_provider
from .rule_manager import RuleManager
from .tools import ToolRegistry, build_tool_registry

logger = logging.getLogger(__name__)

# Shared rule manager instance (singleton pattern)
_rules_path = Path(__file__).parent / 'data' / 'automation_rules.json'
_rule_manager = RuleManager(_rules_path)

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
    content: Optional[str] = Field(None, description="Message content")
    name: Optional[str] = Field(None, description="Name of the author (optional)")
    tool_calls: Optional[List[Dict[str, Any]]] = Field(None, description="Tool calls if any")
    tool_call_id: Optional[str] = Field(None, description="Tool call ID if this is a tool output")


class AgentRunRequest(BaseModel):
    messages: Sequence[ChatMessagePayload]
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    max_iterations: int = Field(default=20, ge=1, le=50)


class AgentRunResponse(BaseModel):
    final: str
    trace: List[Dict[str, Any]]


class AutomationRuleCreate(BaseModel):
    name: str
    description: str = ""
    enabled: bool = False
    protected: bool = False
    priority: int = 100
    conditions: Dict[str, Any]
    actions: List[Dict[str, Any]]


class AutomationRuleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    protected: Optional[bool] = None
    priority: Optional[int] = None
    conditions: Optional[Dict[str, Any]] = None
    actions: Optional[List[Dict[str, Any]]] = None


class AutomationRuleToggle(BaseModel):
    enabled: bool


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
    payload: AgentRunRequest,
    http_request: Request,
    agent: GardenerAgent = Depends(get_agent),
) -> AgentRunResponse:
    messages = [
        ChatMessage(
            role=msg.role,
            content=msg.content or "",
            name=msg.name,
            tool_calls=msg.tool_calls,
            tool_call_id=msg.tool_call_id,
        )
        for msg in payload.messages
    ]
    # Create a new agent instance with the requested max_iterations
    # We need to recreate it because max_iterations is set at init time in the current design
    # Alternatively, we can pass it to run() if we update the method signature. 
    # Let's update agent.run() signature in the next step. For now, we'll assume we can pass it.
    # Wait, looking at agent.py, max_iterations is an __init__ param, not a run() param.
    # I should update agent.py first to accept max_iterations in run() or use the init value.
    # Let's actually update agent.py's run method to accept max_iterations override.
    result = await agent.run(messages=messages, temperature=payload.temperature, max_iterations=payload.max_iterations)

    response = AgentRunResponse(**result)

    hydro_client: HydroAPIClient | None = getattr(http_request.app.state, "client", None)
    if hydro_client:
        try:
            events: List[Dict[str, Any]] = []
            if payload.messages:
                last_message = payload.messages[-1]
                events.append(
                    {
                        "source": "manual",
                        "role": last_message.role,
                        "content": last_message.content,
                        "timestamp": datetime.now(timezone.utc),
                        "metadata": {
                            "temperature": payload.temperature,
                            "message_count": len(payload.messages),
                        },
                    }
                )

            tool_calls: List[Dict[str, Any]] = []
            for entry in result.get("trace", []):
                assistant_block = entry.get("assistant") or {}
                tool_calls.extend(assistant_block.get("tool_calls") or [])

            events.append(
                {
                    "source": "manual",
                    "role": "assistant",
                    "content": result.get("final", ""),
                    "timestamp": datetime.now(timezone.utc),
                    "tool_calls": tool_calls or None,
                    "metadata": {
                        "trace_length": len(result.get("trace", [])),
                    },
                }
            )

            await hydro_client.save_conversation_messages(events)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to persist manual conversation: %s", exc)

    return response


# Automation Rules Management Endpoints (No AI Protection - For Human Use)

@app.get("/automation/rules")
async def list_automation_rules() -> Dict[str, Any]:
    """List all automation rules."""
    return _rule_manager.list_rules()


@app.post("/automation/rules")
async def create_automation_rule(rule: AutomationRuleCreate) -> Dict[str, Any]:
    """Create a new automation rule (humans can create protected rules)."""
    result = _rule_manager.create_rule(
        name=rule.name,
        conditions=rule.conditions,
        actions=rule.actions,
        description=rule.description,
        enabled=rule.enabled,
        protected=rule.protected,  # Humans can set protection
        priority=rule.priority,
        modified_by="api"
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))

    return result


@app.patch("/automation/rules/{rule_id}")
async def update_automation_rule(rule_id: str, rule: AutomationRuleUpdate) -> Dict[str, Any]:
    """Update an existing automation rule (humans can edit protected rules)."""
    result = _rule_manager.update_rule(
        rule_id=rule_id,
        name=rule.name,
        description=rule.description,
        enabled=rule.enabled,
        protected=rule.protected,  # Humans can change protection
        priority=rule.priority,
        conditions=rule.conditions,
        actions=rule.actions,
        modified_by="api"
    )

    if result.get("status") == "error":
        raise HTTPException(status_code=404 if "not found" in result.get("message", "").lower() else 400, detail=result.get("message"))

    return result


@app.delete("/automation/rules/{rule_id}")
async def delete_automation_rule(rule_id: str) -> Dict[str, Any]:
    """Delete an automation rule (humans can delete protected rules)."""
    result = _rule_manager.delete_rule(rule_id, modified_by="api")

    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message"))

    return result


@app.post("/automation/rules/{rule_id}/toggle")
async def toggle_automation_rule(rule_id: str, toggle: AutomationRuleToggle) -> Dict[str, Any]:
    """Enable or disable an automation rule (humans can toggle protected rules)."""
    result = _rule_manager.toggle_rule(rule_id, toggle.enabled, modified_by="api")

    if result.get("status") == "error":
        raise HTTPException(status_code=404, detail=result.get("message"))

    return result
