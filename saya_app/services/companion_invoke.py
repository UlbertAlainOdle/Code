"""Shared blocking model invoke for companion features.

Conflicts / echo / dream need occasional one-shot completions outside the
chat stream. They resolve credentials exactly like the conversation service
(env first, then the stored credentials file), never log the key, and book
every call into the API usage ledger. Blocking by design - callers run them
from FastAPI's threadpool or asyncio.to_thread.
"""

import os

import requests

from core_services import log_to_file
from saya_app.config import KNOWLEDGE_BASE_DIR
from saya_app.services import api_usage
from saya_app.services.conversation import conversation_service

_INVOKE_TIMEOUT = (10, 90)


def _chat_completions_url(api_url):
    base = str(api_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def blocking_model_invoke(prompt, max_tokens=600, temperature=0.3, system_hint="",
                          expect_json=False):
    """One completion -> content string, or None (never raises).

    ``expect_json`` gates the provider's json_object response format: several
    gateways (DeepSeek among them) reject that parameter outright when the
    prompt does not itself mention JSON, so prose calls (the diary) must not
    request it.
    """
    creds = conversation_service.credentials()
    if not creds.get("configured"):
        return None
    url = _chat_completions_url(creds.get("api_url"))
    model = str(creds.get("model") or "").strip()
    if not url or not model:
        return None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {creds['api_key']}"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_hint or "Follow the user's output contract exactly."},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    model_lower = model.lower()
    if model_lower.startswith("deepseek-v4"):
        payload["thinking"] = {"type": "disabled"}
    if expect_json:
        payload["response_format"] = {"type": "json_object"}
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=_INVOKE_TIMEOUT)
    except requests.RequestException as exc:
        log_to_file(f"Companion invoke unreachable: {exc}")
        api_usage.record_turn(str(KNOWLEDGE_BASE_DIR), model, None, completed=False)
        return None
    completed = response.status_code == 200
    usage = None
    content = None
    if completed:
        try:
            data = response.json()
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else None
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                content = str(message.get("content") or "")
        except ValueError:
            completed = False
    else:
        log_to_file(f"Companion invoke HTTP {response.status_code}: {(response.text or '')[:200]}")
    api_usage.record_turn(str(KNOWLEDGE_BASE_DIR), model, usage, completed=completed)
    return content or None if completed else None
