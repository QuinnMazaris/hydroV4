"""Conversation loop that coordinates between the LLM provider and hydro tools."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Sequence

import httpx

from .config import settings
from .llm_providers import ChatMessage, LLMProvider, ProviderResponse
from .tools import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT = (
    "You are Gardener, the hydroponics farm steward. "
    "Use the provided tools to inspect sensors, review cameras, and adjust actuators. "
    "Be specific with recommendations, narrate the state before acting, and confirm actions."
)

# Maximum characters for tool results to prevent context overflow
MAX_TOOL_RESULT_LENGTH = 10000


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
            
            # Check if result contains an image - if so, describe it via vision API
            # and only store the description to avoid context overflow
            result_for_context = await self._process_tool_result(result, call.name)
            
            call_outputs.append({"tool": call.name, "result": result})
            provider_messages.append(
                ChatMessage(
                    role="tool",
                    name=call.name,
                    content=result_for_context,
                    tool_call_id=call.id,
                )
            )

        trace[-1]["tools"] = call_outputs
    
    async def _process_tool_result(self, result: Dict[str, Any], tool_name: str) -> str:
        """Process tool result, converting images to descriptions to avoid context overflow."""
        
        # Check for MCP-style image content
        if isinstance(result.get("content"), list):
            for item in result["content"]:
                if isinstance(item, dict) and item.get("type") == "image":
                    # Extract image data and describe it
                    image_data = item.get("data", "")
                    mime_type = item.get("mimeType", "image/jpeg")
                    logger.info(f"Tool '{tool_name}' returned image ({mime_type}, {len(image_data)} chars base64) - sending to vision API")
                    description = await self._describe_image(image_data, mime_type)
                    logger.info(f"Vision API returned description for '{tool_name}': {description[:150]}...")
                    return json.dumps({
                        "status": "success",
                        "image_description": description,
                        "note": "Image was analyzed by vision AI and described to save context space"
                    }, ensure_ascii=False)
        
        # For non-image results, serialize normally but truncate if too long
        serialized = json.dumps(result, ensure_ascii=False)
        if len(serialized) > MAX_TOOL_RESULT_LENGTH:
            logger.warning(f"Tool '{tool_name}' result too large ({len(serialized)} chars), truncating")
            return json.dumps({
                "status": "truncated",
                "message": f"Result was {len(serialized)} chars, truncated to prevent context overflow",
                "partial": serialized[:MAX_TOOL_RESULT_LENGTH - 200] + "..."
            }, ensure_ascii=False)
        
        return serialized
    
    async def _describe_image(self, base64_data: str, mime_type: str) -> str:
        """Use vision API to describe an image, returning just the description."""
        
        vision_prompt = (
            "Describe this hydroponics/garden camera image concisely. "
            "Focus on: plant health, growth stage, any visible issues (pests, wilting, discoloration), "
            "water levels if visible, lighting conditions. Be specific and actionable."
        )
        
        # Try OpenAI first if key is available
        if settings.openai_api_key:
            logger.info("Using OpenAI vision API to describe camera image")
            try:
                # Clean base64 data - remove any whitespace/newlines
                clean_base64 = base64_data.replace("\n", "").replace("\r", "").replace(" ", "")
                
                # Build the data URL
                data_url = f"data:{mime_type};base64,{clean_base64}"
                logger.debug(f"Image data URL length: {len(data_url)}, first 100 chars: {data_url[:100]}")
                
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.openai_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "gpt-4o",  # Use full gpt-4o for reliable vision
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": vision_prompt},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": data_url,
                                                "detail": "auto"  # Let the model decide detail level
                                            }
                                        }
                                    ]
                                }
                            ],
                            "max_tokens": 500,
                        },
                    )
                    
                    if response.status_code != 200:
                        error_text = response.text
                        logger.error(f"OpenAI vision API error {response.status_code}: {error_text}")
                        return f"Vision API error: {response.status_code} - {error_text[:200]}"
                    
                    data = response.json()
                    description = data["choices"][0]["message"]["content"]
                    logger.info(f"OpenAI vision description: {description[:100]}...")
                    return description
                    
            except Exception as e:
                logger.exception("OpenAI vision API failed")
                return f"OpenAI vision failed: {str(e)}"
        
        # Try Grok vision if OpenAI not available
        if settings.grok_api_key:
            logger.info("Using Grok vision API to describe camera image")
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.post(
                        "https://api.x.ai/v1/chat/completions",
                        headers={
                            "Authorization": f"Bearer {settings.grok_api_key}",
                            "Content-Type": "application/json",
                        },
                        json={
                            "model": "grok-vision-beta",
                            "messages": [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "text", "text": vision_prompt},
                                        {
                                            "type": "image_url",
                                            "image_url": {
                                                "url": f"data:{mime_type};base64,{base64_data}",
                                                "detail": "low"
                                            }
                                        }
                                    ]
                                }
                            ],
                            "max_tokens": 300,
                        },
                    )
                    
                    if response.status_code != 200:
                        error_text = response.text
                        logger.error(f"Grok vision API error {response.status_code}: {error_text}")
                        return f"Grok vision error: {response.status_code} - {error_text[:200]}"
                    
                    data = response.json()
                    description = data["choices"][0]["message"]["content"]
                    logger.info(f"Grok vision description: {description[:100]}...")
                    return description
                    
            except Exception as e:
                logger.exception("Grok vision API failed")
                return f"Grok vision failed: {str(e)}"
        
        # No vision API available
        logger.warning("No vision API keys configured - cannot analyze camera image")
        return (
            "Camera image was retrieved but cannot be analyzed - "
            "no vision API key is configured. Set GARDENER_OPENAI_API_KEY or GARDENER_GROK_API_KEY "
            "to enable image analysis."
        )
