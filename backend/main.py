import hashlib
import json
import logging
import os
import sqlite3
import time
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Awaitable, Callable

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("sudh-ai")
_litellm_cache_ready = False


@dataclass(frozen=True)
class Config:
    mode: str
    keys: tuple[str, ...]
    model: str
    database_url: str
    allowed_origins: tuple[str, ...]
    failure_simulation: bool
    default_prompt: str = "You are a helpful assistant. Keep replies clear and concise."
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 512
    llm_temperature: float = 0.2
    cache_ttl_seconds: int = 3600
    cache_max_entries: int = 256

    @classmethod
    def from_env(cls) -> "Config":
        mode = os.getenv("LLM_MODE", "mock").strip().lower()
        if mode not in {"mock", "litellm"}:
            raise ValueError("LLM_MODE must be either mock or litellm")

        raw_keys = os.getenv("LLM_API_KEYS", "")
        keys = tuple(key.strip() for key in raw_keys.split(",") if key.strip())
        if not keys and mode == "mock":
            keys = ("demo-key-1", "demo-key-2")

        default_model = "demo/mock-chat" if mode == "mock" else "gemini/gemini-2.0-flash"
        default_prompt = os.getenv(
            "DEFAULT_SYSTEM_PROMPT",
            "You are a helpful assistant. Keep replies clear and concise.",
        ).strip()
        origins = tuple(
            origin.strip()
            for origin in os.getenv(
                "ALLOWED_ORIGINS",
                "http://localhost:3000,http://127.0.0.1:3000",
            ).split(",")
            if origin.strip()
        )
        return cls(
            mode=mode,
            keys=keys,
            model=os.getenv("LLM_MODEL", default_model).strip() or default_model,
            database_url=os.getenv("DATABASE_URL", "sqlite:///./data/app.db").strip(),
            allowed_origins=origins,
            failure_simulation=os.getenv(
                "ENABLE_FAILURE_SIMULATION", "false"
            ).strip().lower()
            == "true",
            default_prompt=default_prompt
            or "You are a helpful assistant. Keep replies clear and concise.",
            llm_timeout_seconds=max(1.0, float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))),
            llm_max_tokens=max(1, int(os.getenv("LLM_MAX_TOKENS", "512"))),
            llm_temperature=max(
                0.0,
                min(2.0, float(os.getenv("LLM_TEMPERATURE", "0.2"))),
            ),
            cache_ttl_seconds=max(1, int(os.getenv("CACHE_TTL_SECONDS", "3600"))),
            cache_max_entries=max(1, int(os.getenv("CACHE_MAX_ENTRIES", "256"))),
        )


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    system_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("session_id", "message", "system_prompt", mode="before")
    @classmethod
    def trim_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value


def database_path(database_url: str) -> str:
    if database_url == "sqlite:///:memory:":
        return ":memory:"
    if database_url.startswith("sqlite:///"):
        return database_url.removeprefix("sqlite:///")
    raise ValueError("DATABASE_URL must use SQLite")


class Store:
    def __init__(self, url: str):
        self.path = database_path(url)
        self.connection: sqlite3.Connection | None = None
        self.lock = RLock()

    def initialize(self) -> None:
        if self.path != ":memory:":
            Path(self.path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(
            self.path,
            timeout=10,
            check_same_thread=False,
        )
        self.connection.row_factory = sqlite3.Row
        if self.path != ":memory:":
            self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS messages_session_id_id
                ON messages(session_id, id);
            """
        )
        self.connection.commit()

    def ping(self) -> None:
        with self.lock:
            self._connection().execute("SELECT 1").fetchone()

    def system_prompt(self, session_id: str) -> str | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT system_prompt FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row["system_prompt"] if row else None

    def history(self, session_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT role, content, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY id ASC
                """,
                (session_id,),
            ).fetchall()
        result = []
        for row in rows:
            item: dict[str, Any] = {
                "role": row["role"],
                "content": row["content"],
            }
            if row["metadata"]:
                item["metadata"] = json.loads(row["metadata"])
            result.append(item)
        return result

    def has_session(self, session_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return row is not None

    def save_exchange(
        self,
        session_id: str,
        system_prompt: str,
        user_message: str,
        reply: str,
        metadata: dict[str, Any],
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.lock:
            connection = self._connection()
            try:
                connection.execute("BEGIN")
                connection.execute(
                    """
                    INSERT INTO sessions(session_id, system_prompt, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET system_prompt = excluded.system_prompt
                    """,
                    (session_id, system_prompt, now),
                )
                connection.executemany(
                    """
                    INSERT INTO messages(session_id, role, content, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (session_id, "user", user_message, None, now),
                        (
                            session_id,
                            "assistant",
                            reply,
                            json.dumps(metadata),
                            now,
                        ),
                    ],
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("database is not initialized")
        return self.connection


class ResponseCache:
    """Bounded response metadata cache layered over LiteLLM's response cache."""

    def __init__(self, max_entries: int, ttl_seconds: int):
        self.max_entries = max(1, max_entries)
        self.ttl_seconds = max(1, ttl_seconds)
        self.entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self.lock = RLock()

    def get(self, cache_key: str) -> dict[str, Any] | None:
        with self.lock:
            entry = self.entries.get(cache_key)
            if entry is None:
                return None

            expires_at, response = entry
            if expires_at <= time.monotonic():
                self.entries.pop(cache_key, None)
                return None

            self.entries.move_to_end(cache_key)
            return dict(response)

    def set(self, cache_key: str, response: dict[str, Any]) -> None:
        with self.lock:
            self.entries[cache_key] = (
                time.monotonic() + self.ttl_seconds,
                dict(response),
            )
            self.entries.move_to_end(cache_key)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)


def setup_litellm_cache(ttl_seconds: int = 3600) -> None:
    global _litellm_cache_ready
    if _litellm_cache_ready:
        return
    import litellm
    from litellm.caching import Cache

    if getattr(litellm, "cache", None) is None:
        litellm.cache = Cache(type="local", ttl=ttl_seconds)
    _litellm_cache_ready = True


async def litellm_completion(
    model: str,
    messages: list[dict[str, str]],
    api_key: str,
    use_cache: bool,
    timeout: float = 30.0,
    max_tokens: int = 512,
    temperature: float = 0.2,
    cache_ttl_seconds: int = 3600,
) -> Any:
    setup_litellm_cache(cache_ttl_seconds)
    import litellm

    return await litellm.acompletion(
        model=model,
        messages=messages,
        api_key=api_key,
        caching=use_cache,
        num_retries=0,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
    )


def mock_completion(messages: list[dict[str, str]], model: str) -> dict[str, Any]:
    user_messages = [item["content"] for item in messages if item["role"] == "user"]
    latest = user_messages[-1] if user_messages else ""
    lowered = latest.lower()
    if "capital of france" in lowered:
        reply = "The capital of France is Paris."
    elif "what did i ask" in lowered or "what was my first question" in lowered:
        earlier = user_messages[-2] if len(user_messages) > 1 else "nothing before this"
        reply = f"Your earlier question was: {earlier}"
    else:
        reply = f"You said: {latest}"

    input_tokens = sum(max(1, len(item["content"].split())) for item in messages)
    output_tokens = max(1, len(reply.split()))
    return {
        "model": model,
        "choices": [{"message": {"content": reply}}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
        },
    }


def value(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def parse_simulated_failures(
    header: str | None,
    enabled: bool,
    key_count: int,
) -> dict[str, int]:
    if not enabled or not header:
        return {}

    valid_keys = {"*"} | {f"key_{index}" for index in range(1, key_count + 1)}
    failures: dict[str, int] = {}
    for item in header.split(","):
        key, separator, status = item.strip().partition("=")
        if not separator or key not in valid_keys:
            continue
        try:
            status_code = int(status)
        except ValueError:
            continue
        if status_code in {429, 500, 502, 503}:
            failures[key] = status_code
    return failures


def count_words(messages: list[dict[str, str]]) -> int:
    return sum(max(1, len(item["content"].split())) for item in messages)


def count_input_tokens(model: str, messages: list[dict[str, str]]) -> int:
    try:
        import litellm

        return max(1, int(litellm.token_counter(model=model, messages=messages)))
    except Exception:
        return count_words(messages)


def count_output_tokens(model: str, reply: str) -> int:
    try:
        import litellm

        return max(1, int(litellm.token_counter(model=model, text=reply)))
    except Exception:
        return max(1, len(reply.split()))


def create_app(
    config: Config | None = None,
    completion: Callable[..., Awaitable[Any]] | None = None,
) -> FastAPI:
    config = config or Config.from_env()
    store = Store(config.database_url)
    response_cache = ResponseCache(
        max_entries=config.cache_max_entries,
        ttl_seconds=config.cache_ttl_seconds,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        store.initialize()
        try:
            if config.mode == "litellm" and completion is None:
                setup_litellm_cache(config.cache_ttl_seconds)
            yield
        finally:
            store.close()

    app = FastAPI(
        title="sudh.AI",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
    )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_, exc: Exception):
        logger.error("Request failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={"detail": "internal server error"},
        )

    @app.get("/health")
    async def health():
        store.ping()
        if len(config.keys) < 2:
            raise HTTPException(status_code=503, detail="key pool unavailable")
        return {
            "status": "ok",
            "keys_configured": len(config.keys),
            "cache_enabled": True,
            "model": config.model,
        }

    @app.post("/chat")
    async def chat(
        request: ChatRequest,
        simulate_failure: str | None = Header(
            default=None,
            alias="X-Simulate-Failure",
        ),
    ):
        prior_prompt = store.system_prompt(request.session_id)
        system_prompt = (
            request.system_prompt
            or prior_prompt
            or config.default_prompt
        )
        prior_history = store.history(request.session_id)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(
            {"role": item["role"], "content": item["content"]}
            for item in prior_history
        )
        messages.append({"role": "user", "content": request.message})

        failure_rules = parse_simulated_failures(
            simulate_failure,
            config.failure_simulation,
            len(config.keys),
        )
        cache_key = hashlib.sha256(
            json.dumps(
                [config.model, messages],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        cached = response_cache.get(cache_key) if not failure_rules else None
        if cached is not None:
            metadata = {
                "resolution_source": "LLM_CACHE",
                "model": cached["model"],
                "latency_ms": 0,
                "input_tokens": cached["input_tokens"],
                "output_tokens": cached["output_tokens"],
                "key_id": None,
                "fallback_attempts": 0,
            }
            store.save_exchange(
                request.session_id,
                system_prompt,
                request.message,
                cached["reply"],
                metadata,
            )
            return {
                "session_id": request.session_id,
                "reply": cached["reply"],
                "metadata": metadata,
            }

        failures = 0
        for index, api_key in enumerate(config.keys, start=1):
            key_id = f"key_{index}"
            if key_id in failure_rules or "*" in failure_rules:
                failures += 1
                continue

            started = time.perf_counter()
            try:
                if config.mode == "mock":
                    result = mock_completion(messages, config.model)
                elif completion is not None:
                    result = await completion(
                        model=config.model,
                        messages=messages,
                        api_key=api_key,
                        use_cache=not bool(failure_rules),
                    )
                else:
                    result = await litellm_completion(
                        model=config.model,
                        messages=messages,
                        api_key=api_key,
                        use_cache=not bool(failure_rules),
                        timeout=config.llm_timeout_seconds,
                        max_tokens=config.llm_max_tokens,
                        temperature=config.llm_temperature,
                        cache_ttl_seconds=config.cache_ttl_seconds,
                    )
                choices = value(result, "choices", []) or []
                first_choice = choices[0] if choices else {}
                answer_message = value(first_choice, "message", {}) or {}
                reply = value(answer_message, "content", "") or ""
                if isinstance(reply, list):
                    reply = "".join(
                        str(value(part, "text", ""))
                        for part in reply
                    )
                reply = str(reply).strip()
                if not reply:
                    raise ValueError("empty model response")

                usage = value(result, "usage", {}) or {}
                model = value(result, "model", None) or config.model
                input_tokens = int(
                    value(usage, "prompt_tokens", 0)
                    or count_input_tokens(model, messages)
                )
                output_tokens = int(
                    value(usage, "completion_tokens", 0)
                    or count_output_tokens(model, reply)
                )
                hidden_params = value(result, "_hidden_params", {}) or {}
                litellm_cache_hit = bool(value(hidden_params, "cache_hit", False))
                metadata = {
                    "resolution_source": (
                        "LLM_CACHE" if litellm_cache_hit else "LIVE_INFERENCE"
                    ),
                    "model": model,
                    "latency_ms": max(
                        1,
                        int((time.perf_counter() - started) * 1000),
                    ),
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "key_id": None if litellm_cache_hit else key_id,
                    "fallback_attempts": 0 if litellm_cache_hit else failures,
                }
            except Exception as exc:
                logger.warning(
                    "Provider call failed for %s: %s",
                    key_id,
                    type(exc).__name__,
                )
                failures += 1
                continue

            store.save_exchange(
                request.session_id,
                system_prompt,
                request.message,
                reply,
                metadata,
            )
            response_cache.set(
                cache_key,
                {
                    "reply": reply,
                    "model": model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                },
            )
            return {
                "session_id": request.session_id,
                "reply": reply,
                "metadata": metadata,
            }

        raise HTTPException(
            status_code=503,
            detail="all providers unavailable",
        )

    @app.get("/sessions/{session_id}/messages")
    async def messages_for_session(session_id: str):
        if not store.has_session(session_id):
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session_id": session_id,
            "messages": store.history(session_id),
        }

    return app


app = create_app()
