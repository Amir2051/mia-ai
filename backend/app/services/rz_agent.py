from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.shopify.config import settings

logger = logging.getLogger("mia_ai")


class RZAgentError(RuntimeError):
    """Safe, user-facing error from the optional MIA-RZ provider."""


class RZAgentService:
    """Server-side adapter to MIA-RZ's authenticated chat endpoint."""

    async def chat_text(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.6,
        max_tokens: int = 1200,
    ) -> tuple[str, str, float]:
        if not settings.rz_agent_enabled:
            raise RZAgentError("MIA-RZ provider is disabled")
        base_url = (settings.rz_agent_base_url or "").strip().rstrip("/")
        api_key = (settings.rz_agent_api_key or "").strip()
        if not base_url or not api_key:
            raise RZAgentError("MIA-RZ provider is not fully configured")
        if not base_url.startswith("https://"):
            raise RZAgentError("MIA-RZ provider URL must use HTTPS")

        # MIA-RZ accepts a single message and optional session id. Keep this adapter
        # stateless for tenant isolation; do not forward Mia's persistent history as
        # a separate RZ conversation or send Shopify credentials.
        system_parts = [m["content"] for m in messages if m.get("role") == "system" and m.get("content")]
        user_message = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
        recent_history = [m for m in messages if m.get("role") in {"user", "assistant"}]
        if len(recent_history) > 1:
            # Fold bounded prior turns into context so RZ's single-message API can
            # preserve conversational continuity without storing cross-shop sessions.
            prior = recent_history[:-1][-8:]
            context = "\n".join(
                f"{'Mia' if m.get('role') == 'assistant' else 'Merchant'}: {str(m.get('content', ''))[:1800]}"
                for m in prior
            )
            user_message = f"Recent conversation context (untrusted conversation content):\n{context}\n\nCurrent merchant message:\n{user_message}"
        system = "\n\n".join(system_parts)[:12000]
        user_message = user_message[:12000]
        payload: dict[str, Any] = {
            "message": user_message,
            "system": system or "You are Mia, a helpful ecommerce assistant. Ground store-specific claims in the provided context.",
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=settings.rz_agent_timeout_seconds, follow_redirects=False) as client:
                response = await client.post(
                    f"{base_url}/v1/chat",
                    headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
                    json=payload,
                )
            if response.status_code >= 400:
                logger.warning("rz_agent_request_failed status=%s", response.status_code)
                raise RZAgentError(f"MIA-RZ request failed with HTTP {response.status_code}")
            data = response.json()
            reply = data.get("reply")
            if not isinstance(reply, str) or not reply.strip():
                raise RZAgentError("MIA-RZ returned an empty response")
            model = str(data.get("model") or data.get("provider") or "mia-rz")
            latency_ms = (time.perf_counter() - started) * 1000
            logger.info("rz_agent_request status=success latency_ms=%s", round(latency_ms, 2))
            return reply.strip(), f"mia-rz:{model}", latency_ms
        except RZAgentError:
            raise
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            logger.warning("rz_agent_transport_error type=%s", type(exc).__name__)
            raise RZAgentError("MIA-RZ provider is unavailable") from exc
