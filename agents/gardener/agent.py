"""Conversation loop that coordinates between the LLM provider and hydro tools."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Sequence

from .config import settings
from .llm_providers import ChatMessage, LLMProvider, ProviderResponse
from .tools import ToolRegistry

DEFAULT_SYSTEM_PROMPT = (
    "You are Gardener, the hydroponics farm steward. "
    "Use the provided tools to inspect sensors, review cameras, and adjust actuators. "
    "Be specific with recommendations, narrate the state before acting, and confirm actions."
)


class GardenerAgent:
    """Simple tool-aware agent loop."""

    def __init__(
        self,
        provider: LLMProvider,
        registry: ToolRegistry,
        *,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        max_iterations: int = 20,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._system_prompt = system_prompt
        self._max_iterations = max_iterations

    async def run(self, *, messages: Sequence[Dict[str, str] | ChatMessage], temperature: float = 0.2, max_iterations: int | None = None) -> Dict[str, Any]:
        limit = max_iterations if max_iterations is not None else self._max_iterations
        provider_messages: List[ChatMessage] = [ChatMessage(role="system", content=self._system_prompt)]
        provider_messages.extend(self._coerce_messages(messages))

        await self._registry.refresh()
        tool_specs = self._registry.all()
        trace: List[Dict[str, Any]] = []

        for iteration in range(limit):
            response = await self._provider.complete(provider_messages, tool_specs, temperature=temperature)
            provider_messages.append(response.message)
            trace.append(
                {
                    "iteration": iteration,
                    "assistant": {
                        "content": response.message.content,
                        "tool_calls": [
                            {
                                "name": call.name,
                                "arguments": call.arguments,
                                "id": call.id,
                            }
                            for call in response.tool_calls
                        ],
                    },
                }
            )

            if response.tool_calls:
                await self._handle_tool_calls(response, provider_messages, trace)
                continue

            return {
                "final": response.message.content,
                "trace": trace,
            }

        raise RuntimeError("Agent exceeded maximum iterations without producing a final response")

    def _coerce_messages(self, messages: Sequence[Dict[str, str] | ChatMessage]) -> Iterable[ChatMessage]:
        for message in messages:
            if isinstance(message, ChatMessage):
                yield message
            else:
                yield ChatMessage(role=message["role"], content=message.get("content", ""))

    async def _handle_tool_calls(
        self,
        response: ProviderResponse,
        provider_messages: List[ChatMessage],
        trace: List[Dict[str, Any]],
    ) -> None:
        call_outputs: List[Dict[str, Any]] = []

        for call in response.tool_calls:
            tool_spec = self._registry.get(call.name)
            if not tool_spec:
                result: Dict[str, Any] = {
                    "error": f"Tool '{call.name}' is not available",
                    "arguments": call.arguments,
                }
            else:
                try:
                    result = await tool_spec.handler(call.arguments or {})
                except Exception as exc:  # pragma: no cover - defensive, error logged in response
                    result = {
                        "error": str(exc),
                        "type": exc.__class__.__name__,
                    }
            call_outputs.append({"tool": call.name, "result": result})
            provider_messages.append(
                ChatMessage(
                    role="tool",
                    name=call.name,
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=call.id,
                )
            )

        trace[-1]["tools"] = call_outputs
