"""Google (Gemini) provider — the new unified `google-genai` SDK.

    from google import genai
    client = genai.Client(api_key=...)
    await client.aio.models.generate_content(model=, contents=, config=...)

The SDK is imported lazily so the system runs without `google-genai` installed.
"""
from __future__ import annotations

from .base import LLMProvider, LLMResponse, is_transient_by_name

# The dropdown should only offer models that can actually do the reconciliation
# job — general **text/vision** generation. A Gemini key often unlocks many other
# model families that *cannot* extract a receipt: image generation (Imagen /
# "Nano Banana"), video (Veo), music (Lyria), speech (TTS), embeddings, and
# specialized agents (Robotics, Computer Use, Deep Research, Antigravity). We drop
# them by capability (must support `generateContent`) and by these id hints, so a
# user can't accidentally pick a music model as their extractor.
_GOOGLE_SKIP = (
    "embedding", "aqa",
    "imagen", "image", "banana",   # image generation (incl. Nano Banana)
    "veo", "video",                # video generation
    "lyria", "music",              # music generation
    "tts", "audio",                # speech
    "robotics", "computer-use",    # specialized agents
    "research", "antigravity",     # research / agent models
    "live",                        # bidirectional live streaming
)


class GoogleProvider(LLMProvider):
    id = "google"
    supports_attachments = True

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._client = None

    def _get_client(self):
        if self._client is None:
            from google import genai  # lazy: optional dependency

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def _generate(self, *, model, contents, system, max_tokens) -> LLMResponse:
        from google.genai import types  # lazy

        client = self._get_client()
        cfg_kwargs: dict = {
            # Gemini 2.5 / 3.x are *thinking* models: the internal reasoning shares
            # the output budget, so a small `max_output_tokens` gets fully consumed
            # by thinking and `resp.text` comes back empty (finish_reason=MAX_TOKENS,
            # which then fails JSON parsing). Floor the budget high enough that the
            # actual answer always has room after the model finishes reasoning.
            "max_output_tokens": max(max_tokens, 2048),
        }
        if system:
            cfg_kwargs["system_instruction"] = system
        resp = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
        try:
            text = resp.text or ""
        except Exception:  # noqa: BLE001 — blocked/empty candidate → treat as no text
            text = ""
        um = getattr(resp, "usage_metadata", None)
        tin = int(getattr(um, "prompt_token_count", 0) or 0)
        # Thinking models bill their internal reasoning at the output rate, and the
        # SDK reports it separately as `thoughts_token_count`. Count it so the cost
        # meter reflects what Google actually charges (otherwise a 10s reasoning
        # call looks like ~40 output tokens).
        tout = int(getattr(um, "candidates_token_count", 0) or 0)
        tout += int(getattr(um, "thoughts_token_count", 0) or 0)
        return LLMResponse(text=text, input_tokens=tin, output_tokens=tout, model=model)

    async def complete(self, *, model, prompt, system=None, max_tokens=512) -> LLMResponse:
        return await self._generate(
            model=model, contents=prompt, system=system, max_tokens=max_tokens
        )

    async def complete_multimodal(
        self, *, model, prompt, attachments, system=None, max_tokens=1024
    ) -> LLMResponse:
        from google.genai import types  # lazy

        # Gemini takes raw bytes inline — images and PDFs use the same Part shape.
        parts = [
            types.Part.from_bytes(data=att.data, mime_type=att.media_type)
            for att in attachments
        ]
        return await self._generate(
            model=model, contents=parts + [prompt], system=system, max_tokens=max_tokens
        )

    async def list_models(self):
        import inspect

        client = self._get_client()
        res = client.aio.models.list()
        pager = await res if inspect.isawaitable(res) else res
        out: list[tuple[str, str]] = []
        async for m in pager:
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue  # skip embedding/aqa/imagen/veo-only models
            name = (getattr(m, "name", "") or "").split("/")[-1]
            if not name:
                continue
            low = name.lower()
            if any(x in low for x in _GOOGLE_SKIP):
                continue
            out.append((name, getattr(m, "display_name", None) or name))
            if len(out) >= 200:
                break
        return out

    def is_transient(self, exc: Exception) -> bool:
        # google.genai raises ServerError (5xx) and ClientError (429 = RESOURCE_EXHAUSTED).
        name = type(exc).__name__
        if name in ("ServerError", "APIError", "ClientError"):
            code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
            if isinstance(code, int):
                return code in (408, 409, 429) or code >= 500
            return name == "ServerError"
        return is_transient_by_name(exc)
