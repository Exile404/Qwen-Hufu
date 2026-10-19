"""Thin wrapper over an OpenAI-compatible chat endpoint (local Ollama by default)."""

import time
from typing import Any

from openai import OpenAI

from hufu.config import get_settings


def client() -> OpenAI:
    s = get_settings()
    return OpenAI(base_url=s.llm_base_url, api_key=s.llm_api_key)


def chat(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> Any:
    s = get_settings()
    kwargs: dict[str, Any] = {"model": s.llm_model, "messages": messages, "max_tokens": s.llm_max_tokens}
    if tools:
        kwargs["tools"] = tools
    if s.llm_reasoning_effort:
        kwargs["reasoning_effort"] = s.llm_reasoning_effort
    return client().chat.completions.create(**kwargs).choices[0].message


_PROBE_TOOL = {
    "type": "function",
    "function": {
        "name": "get_balance",
        "description": "Return the wallet balance for a token",
        "parameters": {
            "type": "object",
            "properties": {"token": {"type": "string", "enum": ["tHKD", "tUSD"]}},
            "required": ["token"],
        },
    },
}


def probe() -> dict[str, Any]:
    """Check the model is reachable and emits a tool call. Used by /health and the doctor."""
    t0 = time.perf_counter()
    try:
        msg = chat(
            [
                {"role": "system", "content": "You are a wallet agent. Use tools when they help."},
                {"role": "user", "content": "What is my tHKD balance?"},
            ],
            tools=[_PROBE_TOOL],
        )
    except Exception as e:  # noqa: BLE001 - surface any connection/model error to the caller
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    calls = msg.tool_calls or []
    return {
        "ok": bool(calls) and calls[0].function.name == "get_balance",
        "model": get_settings().llm_model,
        "tool_call": calls[0].function.name if calls else None,
        "latency_s": round(time.perf_counter() - t0, 2),
    }
