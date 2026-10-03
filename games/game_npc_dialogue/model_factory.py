# -*- coding: utf-8 -*-
"""Build a chat model for DashScope, OpenAI-compatible APIs, or Ollama."""
from __future__ import annotations

import os

from agentscope.credential import (
    DashScopeCredential,
    OllamaCredential,
    OpenAICredential,
)
from agentscope.model import (
    ChatModelBase,
    DashScopeChatModel,
    OllamaChatModel,
    OpenAIChatModel,
)

from mock_model import ScriptedNpcModel

PROVIDERS = ("dashscope", "openai", "ollama", "mock")

_DEFAULT_MODELS = {
    "dashscope": "qwen-plus",
    "openai": "gpt-4o-mini",
    "ollama": "qwen2.5:7b",
    "mock": "scripted-npc",
}


def resolve_provider(explicit: str | None) -> str:
    """CLI flag, then ``NPC_MODEL_PROVIDER``, then DashScope."""
    provider = explicit or os.environ.get("NPC_MODEL_PROVIDER") or "dashscope"
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        known = ", ".join(PROVIDERS)
        raise SystemExit(
            f"Unknown provider '{provider}'. Use one of: {known}.",
        )
    return provider


def build_chat_model(
    provider: str,
    model_name: str | None = None,
) -> ChatModelBase:
    """Construct the chat model for ``provider``.

    ``model_name`` overrides ``NPC_MODEL`` and the provider default.
    """
    name = (
        model_name or os.environ.get("NPC_MODEL") or _DEFAULT_MODELS[provider]
    )
    if provider == "mock":
        return ScriptedNpcModel()
    if provider == "dashscope":
        api_key = os.environ.get("DASHSCOPE_API_KEY")
        if not api_key:
            raise SystemExit(
                "DASHSCOPE_API_KEY is not set. Export it, or run with "
                "--provider mock.",
            )
        return DashScopeChatModel(
            credential=DashScopeCredential(api_key=api_key),
            model=name,
            stream=False,
        )
    if provider == "openai":
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise SystemExit(
                "OPENAI_API_KEY is not set. Export it, or run with "
                "--provider mock. Set OPENAI_BASE_URL for an "
                "OpenAI-compatible endpoint.",
            )
        return OpenAIChatModel(
            credential=OpenAICredential(
                api_key=api_key,
                base_url=os.environ.get("OPENAI_BASE_URL"),
            ),
            model=name,
            stream=False,
        )
    if provider == "ollama":
        return OllamaChatModel(
            credential=OllamaCredential(host=os.environ.get("OLLAMA_HOST")),
            model=name,
            stream=False,
        )
    raise SystemExit(f"Unknown provider '{provider}'.")
