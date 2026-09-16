from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.shopify.config import settings

logger = logging.getLogger("mia_ai")


class OpenRouterError(RuntimeError):
    pass


class OpenRouterConfig(BaseModel):
    model: Optional[str] = None
    timeout_seconds: float = Field(default=45.0, gt=0, le=120)
    retries: int = Field(default=2, ge=0, le=5)


class OpenRouterService:
    """Server-only OpenRouter client. Credentials never leave this process."""

    def __init__(self, config: Optional[OpenRouterConfig] = None):
        self.config = config or OpenRouterConfig(
            model=settings.openrouter_model or None,
            timeout_seconds=settings.openrouter_timeout_seconds,
            retries=settings.openrouter_retries,
        )

    def _api_key(self) -> str:
        key = (settings.openrouter_api_key or "").strip()
        if not key:
            raise OpenRouterError("OpenRouter API key is not configured")
        return key

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        key = self._api_key()
        headers = kwargs.pop("headers", {})
        headers.update({
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": settings.shopify_app_url,
            "X-Title": settings.app_name,
        })
        last_error: Optional[Exception] = None
        for attempt in range(self.config.retries + 1):
            started = time.perf_counter()
            try:
                async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                    response = await client.request(
                        method,
                        f"https://openrouter.ai/api/v1{path}",
                        headers=headers,
                        **kwargs,
                    )
                logger.info(
                    "openrouter_request path=%s status=%s latency_ms=%s attempt=%s",
                    path,
                    response.status_code,
                    round((time.perf_counter() - started) * 1000, 2),
                    attempt + 1,
                )
                if response.status_code >= 500 and attempt < self.config.retries:
                    continue
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                logger.warning(
                    "openrouter_transport_error path=%s attempt=%s type=%s",
                    path, attempt + 1, type(exc).__name__,
                )
                if attempt < self.config.retries:
                    continue
        raise OpenRouterError("OpenRouter request failed") from last_error

    async def list_models(self) -> list[dict[str, Any]]:
        response = await self._request("GET", "/models")
        if response.status_code >= 400:
            raise OpenRouterError(f"OpenRouter models request failed: HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise OpenRouterError("OpenRouter returned invalid model metadata") from exc
        models = data.get("data") or []
        return [m for m in models if isinstance(m, dict)]

    async def select_model(self) -> str:
        if self.config.model:
            return self.config.model
        models = await self.list_models()
        free: list[dict[str, Any]] = []
        for model in models:
            pricing = model.get("pricing") or {}
            if str(pricing.get("prompt", "")) == "0" and str(pricing.get("completion", "")) == "0":
                free.append(model)
        if not free:
            raise OpenRouterError("No currently listed free OpenRouter model is available")
        free.sort(key=lambda item: (str(item.get("created", "")), str(item.get("id", ""))), reverse=True)
        return str(free[0]["id"])

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.2,
    ) -> tuple[dict[str, Any], str, float]:
        selected_model = model or await self.select_model()
        payload = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        started = time.perf_counter()
        response = await self._request("POST", "/chat/completions", json=payload)
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            logger.warning("openrouter_api_error status=%s", response.status_code)
            raise OpenRouterError(f"OpenRouter chat request failed: HTTP {response.status_code}")
        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("OpenRouter returned malformed JSON content") from exc
        if not isinstance(parsed, dict):
            raise OpenRouterError("OpenRouter JSON response must be an object")
        return parsed, selected_model, latency_ms

    async def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        model: Optional[str] = None,
        temperature: float = 0.5,
    ) -> tuple[str, str, float]:
        selected_model = model or await self.select_model()
        payload = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
        }
        started = time.perf_counter()
        response = await self._request("POST", "/chat/completions", json=payload)
        latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code >= 400:
            logger.warning("openrouter_api_error status=%s", response.status_code)
            raise OpenRouterError(f"OpenRouter chat request failed: HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise OpenRouterError("OpenRouter returned malformed chat content") from exc
        return str(content), selected_model, latency_ms


async def generate_json(messages: list[dict[str, str]], model: Optional[str] = None) -> tuple[dict[str, Any], str, float]:
    return await OpenRouterService().chat_json(messages, model=model)
