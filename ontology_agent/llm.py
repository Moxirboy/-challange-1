"""OpenAI-compatible chat/embeddings client: disk-cached, retrying, and
careful never to leak the API key.

Security note (checked at every raise/print site in this file): the API key
is stored on `self._api_key`, these classes are plain classes (not
dataclasses, so there's no auto-generated `__repr__` that could print it),
and every error path below builds its message from the *response* (status +
body excerpt) or from exception class names -- never from the request, the
headers, or the key itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# --------------------------------------------------------------------------
# Defaults + environment resolution
# --------------------------------------------------------------------------

DEFAULT_MODEL = "gemini-3.6-flash"
DEFAULT_EMBED_MODEL = "gemini-embedding-001"
# The spec gives no fallback base_url when every env var is empty. Default to
# Gemini's OpenAI-compatible endpoint since that's consistent with the
# default models above (both name Gemini models); document this choice
# rather than raising, so `LLM.from_env()` never needs a base_url to be set
# explicitly just to construct a client that a cache hit could still serve.
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRIES = 5  # 5 retries => up to 6 total attempts, delays 2/4/8/16/32s

_DOTENV_LOADED = False


def _load_dotenv() -> None:
    """Parse a `.env` (KEY=VALUE per line) from the project root, once per
    process. Never overrides a variable that's already set in the real
    environment, and never prints anything -- this function must stay
    silent about values by construction.
    """
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True

    project_root = Path(__file__).resolve().parent.parent
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def resolve_llm_config() -> tuple[str, str | None, str]:
    """(base_url, api_key, model), first non-empty env var wins per name,
    `.env` loaded first. See module docstring / DESIGN.md §4 for precedence.
    """
    _load_dotenv()
    base_url = _first_env("CHALLENGE_LLM_BASE_URL", "OMNIX_LLM_BASE_URL") or DEFAULT_BASE_URL
    api_key = _first_env("CHALLENGE_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
    model = _first_env("CHALLENGE_LLM_MODEL", "OMNIX_LLM_MODEL") or DEFAULT_MODEL
    return base_url, api_key, model


def resolve_embed_config() -> tuple[str, str | None, str]:
    """Same as resolve_llm_config() but for the embed model name; base_url
    and api_key are shared with the chat client.
    """
    _load_dotenv()
    base_url = _first_env("CHALLENGE_LLM_BASE_URL", "OMNIX_LLM_BASE_URL") or DEFAULT_BASE_URL
    api_key = _first_env("CHALLENGE_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY")
    model = _first_env("CHALLENGE_EMBED_MODEL", "OMNIX_EMBED_MODEL") or DEFAULT_EMBED_MODEL
    return base_url, api_key, model


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class LLMError(Exception):
    """Raised on final request failure. `body_excerpt` is truncated and
    contains only the response body -- never request headers, never the key.
    """

    def __init__(self, status: int | None, body_excerpt: str, message: str | None = None):
        self.status = status
        self.body_excerpt = body_excerpt
        super().__init__(message or f"LLM request failed (status={status}): {body_excerpt}")


# --------------------------------------------------------------------------
# Shared HTTP + cache plumbing
# --------------------------------------------------------------------------


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write temp file + os.replace so a crash or a rate-limit abort mid-run
    never leaves a half-written cache entry that a resumed run would trip on.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def _sleep_before_retry(headers, attempt: int) -> None:
    delay = float(2 ** (attempt + 1))  # attempt 0..4 -> 2,4,8,16,32
    if headers is not None:
        retry_after = headers.get("Retry-After")
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
    time.sleep(delay)


def _http_post_json(url: str, api_key: str | None, payload: dict, timeout: float) -> dict:
    """POST JSON with retry on 429/500/502/503/504 and read-timeout, honouring
    `Retry-After` when present. Any other HTTP status fails immediately (no
    point burning 5 retries on a 401). Raises LLMError on final failure.
    """
    if not api_key:
        raise LLMError(status=None, body_excerpt="no API key configured")

    data = json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_error: LLMError | None = None
    for attempt in range(MAX_RETRIES + 1):
        request = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read()
            excerpt = body.decode("utf-8", errors="replace")[:500]
            last_error = LLMError(exc.code, excerpt)
            if exc.code in RETRYABLE_STATUSES and attempt < MAX_RETRIES:
                _sleep_before_retry(exc.headers, attempt)
                continue
            raise last_error from None
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = LLMError(None, f"network error: {exc.__class__.__name__}")
            if attempt < MAX_RETRIES:
                _sleep_before_retry(None, attempt)
                continue
            raise last_error from None

    raise last_error or LLMError(None, "exhausted retries")  # pragma: no cover - unreachable


# --------------------------------------------------------------------------
# Chat client
# --------------------------------------------------------------------------


class LLM:
    """OpenAI-compatible `/chat/completions` client with a strict JSON-schema
    response format, a resumable disk cache, and retry/backoff.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        cache_dir: str | Path,
        max_tokens: int = 8192,
        temperature: float = 0.0,
        timeout: float = 180,
    ):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._max_tokens = max_tokens
        self._temperature = temperature
        self._timeout = timeout

        self._calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._cached_calls = 0

    @classmethod
    def from_env(cls, cache_dir: str | Path, **overrides) -> "LLM":
        """Construct from the documented environment precedence (DESIGN.md
        §4). `overrides` (max_tokens, temperature, timeout, ...) win over
        env-resolved defaults -- e.g. run.py's --escalation-budget-adjacent
        flags can still force a value.
        """
        base_url, api_key, model = resolve_llm_config()
        kwargs = {"base_url": base_url, "api_key": api_key, "model": model, "cache_dir": cache_dir}
        kwargs.update(overrides)
        return cls(**kwargs)

    def _cache_key(self, system: str, user: str, schema: dict) -> str:
        material = json.dumps(
            {"model": self._model, "base_url": self._base_url, "system": system, "user": user, "schema": schema},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def chat_json(self, system: str, user: str, schema: dict, tag: str) -> dict:
        """Issue a JSON-schema-constrained chat completion and return the
        parsed content object. A cache hit is checked and returned *before*
        any network call, so a rerun after a rate-limit abort costs nothing
        for work already done.
        """
        cache_path = self._cache_dir / f"{self._cache_key(system, user, schema)}.json"
        if cache_path.exists():
            record = json.loads(cache_path.read_text(encoding="utf-8"))
            self._calls += 1
            self._cached_calls += 1
            return record["content"]

        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            # No "models" fallback array: non-OpenRouter endpoints (Gemini,
            # Kimi, ...) reject an unrecognised field outright.
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": tag, "strict": True, "schema": schema},
            },
        }
        url = f"{self._base_url.rstrip('/')}/chat/completions"
        response = _http_post_json(url, self._api_key, payload, self._timeout)

        content = json.loads(response["choices"][0]["message"]["content"])
        usage = response.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)

        self._calls += 1
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens

        _atomic_write_json(
            cache_path,
            {"content": content, "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}},
        )
        return content

    @property
    def usage(self) -> dict:
        return {
            "calls": self._calls,
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "cached_calls": self._cached_calls,
        }


# --------------------------------------------------------------------------
# Embeddings client
# --------------------------------------------------------------------------


class Embedder:
    """OpenAI-compatible `/embeddings` client: per-text disk cache, batching,
    and graceful degradation -- any endpoint failure logs one warning and
    returns `[]` for the whole call so retrieval falls back to lexical-only
    rather than crashing the run.
    """

    def __init__(self, base_url: str, api_key: str | None, model: str, cache_dir: str | Path, batch: int = 64):
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._batch = batch

    @classmethod
    def from_env(cls, cache_dir: str | Path, **overrides) -> "Embedder":
        base_url, api_key, model = resolve_embed_config()
        kwargs = {"base_url": base_url, "api_key": api_key, "model": model, "cache_dir": cache_dir}
        kwargs.update(overrides)
        return cls(**kwargs)

    def _cache_key(self, text: str) -> str:
        material = json.dumps({"model": self._model, "base_url": self._base_url, "text": text}, sort_keys=True)
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def _cache_path(self, text: str) -> Path:
        return self._cache_dir / f"{self._cache_key(text)}.json"

    def _request_embeddings(self, texts: list[str]) -> list[list[float]]:
        url = f"{self._base_url.rstrip('/')}/embeddings"
        payload = {"model": self._model, "input": texts}
        response = _http_post_json(url, self._api_key, payload, timeout=60)
        # OpenAI-style: {"data": [{"embedding": [...], "index": 0}, ...]}
        by_index = sorted(response["data"], key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in by_index]

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self._api_key:
            print("[Embedder] no API key configured; falling back to lexical-only retrieval", file=sys.stderr)
            return []

        results: list[list[float] | None] = [None] * len(texts)
        pending_idx: list[int] = []
        pending_text: list[str] = []
        for i, text in enumerate(texts):
            cache_path = self._cache_path(text)
            if cache_path.exists():
                try:
                    results[i] = json.loads(cache_path.read_text(encoding="utf-8"))["embedding"]
                    continue
                except (json.JSONDecodeError, KeyError, OSError):
                    pass  # fall through and refetch a corrupt/partial cache entry
            pending_idx.append(i)
            pending_text.append(text)

        try:
            for start in range(0, len(pending_text), self._batch):
                chunk_idx = pending_idx[start : start + self._batch]
                chunk_text = pending_text[start : start + self._batch]
                vectors = self._request_embeddings(chunk_text)
                for idx, text, vector in zip(chunk_idx, chunk_text, vectors):
                    results[idx] = vector
                    _atomic_write_json(self._cache_path(text), {"embedding": vector})
        except Exception as exc:
            print(
                f"[Embedder] embeddings request failed ({exc.__class__.__name__}); "
                f"falling back to lexical-only retrieval",
                file=sys.stderr,
            )
            return []

        return results  # every slot filled: either cache hit or freshly fetched
