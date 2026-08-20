"""LLM provider selection.

This is the ONLY file that knows which model backend is in use. To switch
providers later, change LLM_PROVIDER / LLM_MODEL in .env — no other file
needs to change.

    LLM_PROVIDER=ollama   LLM_MODEL=qwen2.5:7b       (local, no API cost)
    LLM_PROVIDER=claude   LLM_MODEL=claude-sonnet-5  (needs ANTHROPIC_API_KEY)
    LLM_PROVIDER=openai   LLM_MODEL=gpt-4o           (needs OPENAI_API_KEY)

Claude models: claude-opus-5, claude-sonnet-5, claude-haiku-4-5-20251001.
Optional: LLM_MAX_TOKENS (Claude only, default 8192).

Note this loader only reads the .env sitting next to this file, and uses setdefault,
so ANTHROPIC_API_KEY belongs in deliverability_agent/.env to work both under FastAPI
and when the agent is run standalone.
"""

import os
from pathlib import Path


def _load_env():
    """
    Minimal .env loader so we don't add a python-dotenv dependency.

    A real value already in the environment wins, so the shell can override the file.
    But a variable that is present-but-blank does NOT win: an empty exported var (easy to
    create with `export KEY=$(grep ... missing-file)`) would otherwise shadow the real key
    and surface as an unrelated-looking auth error from the provider SDK.
    """
    env_path = Path(__file__).with_name(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not os.environ.get(key, "").strip():
            os.environ[key] = value


_load_env()

PROVIDER = os.getenv("LLM_PROVIDER", "ollama").lower()
MODEL = os.getenv("LLM_MODEL")
TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0"))


def _build_llm():
    if PROVIDER == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(model=MODEL or "qwen2.5:7b", temperature=TEMPERATURE)

    if PROVIDER in ("claude", "anthropic"):
        from langchain_anthropic import ChatAnthropic
        # max_tokens is set explicitly: ChatAnthropic's default is low relative to the
        # multi-section reports SYSTEM_PROMPT asks for, and being cut off surfaces only as
        # stop_reason="max_tokens" rather than an error.
        #
        # temperature is deliberately not passed. The Claude 5 models reject it with
        # "`temperature` is deprecated for this model" (HTTP 400), so LLM_TEMPERATURE
        # applies to the Ollama/OpenAI providers only. Set LLM_CLAUDE_TEMPERATURE=1 to
        # send it anyway on an older Claude model that still accepts it.
        kwargs = {
            "model": MODEL or "claude-sonnet-5",
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "8192")),
        }
        if os.getenv("LLM_CLAUDE_TEMPERATURE", "").strip() in ("1", "true", "yes"):
            kwargs["temperature"] = TEMPERATURE
        return ChatAnthropic(**kwargs)

    if PROVIDER == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=MODEL or "gpt-4o", temperature=TEMPERATURE)

    raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER!r}")


llm = _build_llm()
