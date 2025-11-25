"""LLM provider abstractions for the gardener agent."""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from .config import settings
from .tools import ToolSpec


@dataclass
class ChatMessage:
    role: str
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:  # pragma: no cover - trivial
        data: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            data["name"] = self.name
        if self.tool_calls:
            data["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            data["tool_call_id"] = self.tool_call_id
        return data


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass
class ProviderResponse:
    message: ChatMessage
    tool_calls: List[ToolCall]


class LLMProvider(ABC):
    """Common LLM provider interface."""

    @abstractmethod
    async def complete(
        self,
        messages: List[ChatMessage],
        tools: List[ToolSpec],
        *,
        temperature: float = 0.2,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def aclose(self) -> None:  # pragma: no cover - optional hook
        return None


class MockLLMProvider(LLMProvider):
    """Deterministic mock provider for local testing."""

    async def complete(
        self,
        messages: List[ChatMessage],
        tools: List[ToolSpec],
        *,
        temperature: float = 0.0,
    ) -> ProviderResponse:
        last = messages[-1]
        tool_calls: List[ToolCall] = []
        content = ""
        try:
            payload = json.loads(last.content)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict) and payload.get("tool"):
            tool_name = payload["tool"]
            arguments = payload.get("arguments") or {}
            tool_calls.append(ToolCall(id="mock-call", name=tool_name, arguments=arguments))
            data_tool_calls = [
                {
                    "id": "mock-call",
                    "type": "function",
                    "function": {"name": tool_name, "arguments": json.dumps(arguments)},
                }
            ]
        else:
            content = f"Mock response acknowledging: {last.content}"
            data_tool_calls = None

        return ProviderResponse(
            message=ChatMessage(role="assistant", content=content, tool_calls=data_tool_calls),
            tool_calls=tool_calls,
        )


class _BaseHTTPProvider(LLMProvider):
    endpoint: str
    model: str

    def __init__(self, *, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(timeout=60.0)
        self.model = model

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: List[ChatMessage],
        tools: List[ToolSpec],
        *,
        temperature: float = 0.2,
    ) -> ProviderResponse:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_dict() for message in messages],
            "temperature": temperature,
        }

        if tools:
            payload["tools"] = [self._openai_tool_schema(tool) for tool in tools]
            payload["tool_choice"] = "auto"

        response = await self._client.post(
            self.endpoint,
            headers=self._build_headers(),
            json=payload,
        )
        if response.status_code != 200:
            error_body = response.text
            raise RuntimeError(f"OpenAI API error {response.status_code}: {error_body}")
        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]
        content = message.get("content") or ""

        tool_calls: List[ToolCall] = []
        for call in message.get("tool_calls", []) or []:
            arguments: Dict[str, Any]
            raw_arguments = call.get("function", {}).get("arguments")
            if isinstance(raw_arguments, str):
                try:
                    arguments = json.loads(raw_arguments)
                except json.JSONDecodeError:
                    arguments = {"raw": raw_arguments}
            else:
                arguments = raw_arguments or {}
            tool_calls.append(
                ToolCall(
                    id=call.get("id", ""),
                    name=call.get("function", {}).get("name", ""),
                    arguments=arguments,
                )
            )

        return ProviderResponse(
            message=ChatMessage(
                role=message.get("role", "assistant"),
                content=content,
                tool_calls=message.get("tool_calls"),
            ),
            tool_calls=tool_calls,
        )

    def _openai_tool_schema(self, spec: ToolSpec) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.input_schema,
            },
        }

    @abstractmethod
    def _build_headers(self) -> Dict[str, str]:
        raise NotImplementedError


class OpenAIProvider(_BaseHTTPProvider):
    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(self) -> None:
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required when using the OpenAI provider")
        super().__init__(api_key=settings.openai_api_key, model=settings.openai_model)

    def _build_headers(self) -> Dict[str, str]:  # pragma: no cover - simple mapping
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


class GrokProvider(_BaseHTTPProvider):
    endpoint = "https://api.x.ai/v1/chat/completions"

    def __init__(self) -> None:
        if not settings.grok_api_key:
            raise RuntimeError("GROK_API_KEY is required when using the Grok provider")
        super().__init__(api_key=settings.grok_api_key, model=settings.grok_model)

    def _build_headers(self) -> Dict[str, str]:  # pragma: no cover - simple mapping
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }


def create_provider() -> LLMProvider:
    mapping = {
        "mock": MockLLMProvider,
        "openai": OpenAIProvider,
        "grok": GrokProvider,
    }
    provider_name = settings.llm_provider
    provider_cls = mapping.get(provider_name)
    if not provider_cls:
        raise ValueError(f"Unsupported LLM provider '{provider_name}'")
    return provider_cls()
