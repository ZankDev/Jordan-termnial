"""Free language-model connector (OpenAI-compatible chat API) used to read evidence against a market's rules.

Providers (all have a free tier; the key is stored in meta `llm_api_key`, provider in `llm_provider`):
  groq        https://console.groq.com/keys          model openai/gpt-oss-120b          (fast, generous free tier)
  gemini      https://aistudio.google.com/apikey     model gemini-2.5-flash             (free tier, OpenAI-compatible endpoint)
  openrouter  https://openrouter.ai/keys             model meta-llama/llama-3.3-70b-instruct:free
  ollama      local, no key                          model qwen2.5:7b                    (needs Ollama installed; slow on a laptop)
The key never leaves this machine except to the chosen provider, and is never logged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import requests

from app import db

logger = logging.getLogger(__name__)
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-2.5-flash"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "meta-llama/llama-3.3-70b-instruct:free"),
    "ollama": ("http://127.0.0.1:11434/v1/chat/completions", "qwen2.5:7b"),
}


def config() -> dict[str, Any] | None:
    prov = (db.meta_get("llm_provider") or os.environ.get("LLM_PROVIDER") or "").strip().lower()
    key = db.meta_get("llm_api_key") or os.environ.get("LLM_API_KEY") or ""
    if prov not in PROVIDERS or (prov != "ollama" and not key):
        return None
    url, model = PROVIDERS[prov]
    return {"provider": prov, "url": url, "model": db.meta_get("llm_model") or model, "key": key}


def set_config(provider: str, key: str = "", model: str = "") -> dict[str, Any]:
    provider = provider.strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    db.meta_set("llm_provider", provider)
    if key:
        db.meta_set("llm_api_key", key.strip())
    db.meta_set("llm_model", model.strip())
    return status()


def status() -> dict[str, Any]:
    c = config()
    return {"configured": bool(c), "provider": c["provider"] if c else None, "model": c["model"] if c else None}


def chat_json(system: str, user: str, max_tokens: int = 900, temperature: float = 0.0, model: str | None = None,
              reasoning: str | None = None, retries: int = 3) -> dict[str, Any] | None:
    """One chat call that must answer with a JSON object. Returns None when no model is configured or the call fails.
    `model` overrides the configured model (same provider); `reasoning` sets reasoning_effort for reasoning models (low/medium/high)."""
    c = config()
    if not c:
        return None
    if model:
        c = {**c, "model": model}
    headers = {"Content-Type": "application/json"}
    if c["key"]:
        headers["Authorization"] = f"Bearer {c['key']}"
    body = {"model": c["model"], "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if c["provider"] in ("groq", "openrouter", "ollama"):
        body["response_format"] = {"type": "json_object"}
    if reasoning and c["provider"] == "groq" and "gpt-oss" in c["model"]:
        body["reasoning_effort"] = reasoning
    for attempt in range(retries):
        try:
            r = requests.post(c["url"], headers=headers, json=body, timeout=90)
            if r.status_code == 429:
                logger.info("llm rate limited (%s %s)", c["provider"], c["model"])
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            txt = r.json()["choices"][0]["message"]["content"]
            m = re.search(r"\{.*\}", txt, re.S)
            return json.loads(m.group(0)) if m else None
        except Exception as e:  # noqa: BLE001
            logger.warning("llm call failed (%s): %s", c["provider"], type(e).__name__)
            time.sleep(2)
    return None
