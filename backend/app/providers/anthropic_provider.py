"""Anthropic (Claude) provider.

The SDK is imported lazily inside the client factory so the system imports and
runs (mock mode, tests, other providers) even when `anthropic` isn't installed.
Binary attachments map to native content blocks: images → `image` blocks, PDFs →
`document` blocks (Claude reads PDFs natively — no OCR layer needed).
"""
from __future__ import annotations

import base64

from .base import Attachment, LLMProvider, LLMResponse


class AnthropicProvider(LLMProvider):
    id = "anthropic"
    supports_attachments = True

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic  # lazy: optional dependency

            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def _create(self, *, model, content, system, max_tokens) -> LLMResponse:
        client = self._get_client()
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if system:
            kwargs["system"] = system
        resp = await client.messages.create(**kwargs)
        text = resp.content[0].text if getattr(resp, "content", None) else ""
        return LLMResponse(
            text=text,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            model=model,
        )

    async def complete(self, *, model, prompt, system=None, max_tokens=512) -> LLMResponse:
        return await self._create(model=model, content=prompt, system=system, max_tokens=max_tokens)

    async def complete_multimodal(
        self, *, model, prompt, attachments: list[Attachment], system=None, max_tokens=1024
    ) -> LLMResponse:
        content: list[dict] = []
        for att in attachments:
            source = {
                "type": "base64",
                "media_type": att.media_type,
                "data": base64.b64encode(att.data).decode("ascii"),
            }
            block_type = "document" if att.media_type == "application/pdf" else "image"
            content.append({"type": block_type, "source": source})
        content.append({"type": "text", "text": prompt})
        return await self._create(model=model, content=content, system=system, max_tokens=max_tokens)

    async def list_models(self):
        client = self._get_client()
        out: list[tuple[str, str]] = []
        async for m in client.models.list(limit=100):
            out.append((m.id, getattr(m, "display_name", None) or m.id))
            if len(out) >= 100:
                break
        return out
