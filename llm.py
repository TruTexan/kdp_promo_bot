"""Shared AI writer: ChatGPT (OpenAI) or Claude (Anthropic), zero dependencies.

Pick with env PROMO_LLM = openai | claude | auto (default auto: whichever key is set,
OpenAI first). Returns None when no key is set so callers fall back to templates.
Model names are env-configurable because providers rename them often.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def load_env(path: Path = Path(__file__).resolve().parent / ".env") -> None:
    """Load KEY=value lines from .env into the environment (real env vars win)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def _post(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def provider() -> str | None:
    want = os.environ.get("PROMO_LLM", "auto").lower()
    has_oa, has_cl = bool(os.environ.get("OPENAI_API_KEY")), bool(os.environ.get("ANTHROPIC_API_KEY"))
    if want == "openai":
        return "openai" if has_oa else None
    if want == "claude":
        return "claude" if has_cl else None
    return "openai" if has_oa else "claude" if has_cl else None


_DISABLED = False  # set after an auth/billing error so a run doesn't retry 200+ times


def write(prompt: str, max_tokens: int = 800) -> str | None:
    global _DISABLED
    p = provider()
    if p is None or _DISABLED:
        return None
    system = ("You are a book-marketing copywriter. Follow Amazon KDP rules: never ask for "
              "5-star ratings, never offer anything for reviews, no 'guaranteed' results, "
              "no unverifiable 'bestseller' claims. Output only the requested copy.")
    try:
        if p == "openai":
            r = _post("https://api.openai.com/v1/chat/completions",
                      {"model": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
                       "max_tokens": max_tokens,
                       "messages": [{"role": "system", "content": system},
                                    {"role": "user", "content": prompt}]},
                      {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
            return r["choices"][0]["message"]["content"].strip()
        r = _post("https://api.anthropic.com/v1/messages",
                  {"model": os.environ.get("CLAUDE_MODEL", "claude-sonnet-5-5"),
                   "max_tokens": max_tokens, "system": system,
                   "messages": [{"role": "user", "content": prompt}]},
                  {"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                   "anthropic-version": "2023-06-01"})
        return "".join(b.get("text", "") for b in r.get("content", [])
                       if b.get("type") == "text").strip() or None
    except urllib.error.HTTPError as ex:  # show the provider's reason (billing, bad key...)
        try:
            msg = json.loads(ex.read()).get("error", {}).get("message", "")
        except Exception:
            msg = ""
        print(f"  [llm:{p}] HTTP {ex.code}: {msg or ex.reason} -> using templates")
        if ex.code in (400, 401, 403) and any(w in msg.lower() for w in ("credit", "billing", "key", "auth", "permission")):
            _DISABLED = True
            print(f"  [llm:{p}] AI writing turned off for this run")
        return None
    except Exception as ex:  # network errors -> template fallback
        print(f"  [llm:{p}] failed ({ex}); using templates")
        return None
