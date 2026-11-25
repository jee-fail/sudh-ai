# sudh.AI

**Author and developer:** Sudhanshu Kumar

sudh.AI is a full-stack chat application built for the Lyftr AI assignment. It uses React, FastAPI, LiteLLM, and SQLite, with multi-turn chat history, provider failover, and per-reply token metadata.

## Live URLs

- Frontend: not deployed yet
- Backend: not deployed yet
- Public repository: not created yet

Live deployment needs two valid provider keys and your chosen hosting and repository accounts. Real keys must be entered as private environment variables, never committed to the repository.

If the repository owner will deploy from their GitHub account, follow [DEPLOYMENT_HANDOFF.md](DEPLOYMENT_HANDOFF.md).

## Run locally: with Docker and without

### With Docker

Docker Compose starts the API at `http://localhost:8000` and the React app at `http://localhost:3000`. Without a local `.env` file, it uses the offline demo mode, so it starts without provider credentials:

```sh
make up
```

Open `http://localhost:3000`. Stop the services with:

```sh
make down
```

SQLite is stored at `/data/app.db` in the named `chat-data` volume and survives container restarts. `make down` keeps the volume. Avoid `docker compose down -v` unless you intend to delete the saved chat history.

For live inference, copy `.env.example` to `.env`, set `LLM_MODE=litellm`, and replace both `LLM_API_KEYS` placeholders with real keys from the same provider. Keep `.env` private. The default model is `gemini/gemini-2.0-flash`; change `LLM_MODEL` if your provider or account uses a different supported model.

### Without Docker

Create the project-local virtual environment and install the backend and test dependencies:

```sh
make setup
```

In the first terminal, start the backend in offline demo mode:

```sh
export LLM_MODE=mock
export LLM_API_KEYS=demo-key-1,demo-key-2
export LLM_MODEL=demo/mock-chat
export DATABASE_URL=sqlite:///./data/app.db
export ALLOWED_ORIGINS=http://localhost:3000
export ENABLE_FAILURE_SIMULATION=true
cd backend
../.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

In a second terminal, start the frontend:

```sh
cd frontend
npm ci
VITE_API_BASE_URL=http://localhost:8000 npm run dev
```

The backend virtual environment lives at the repository root in `.venv`, is excluded from version control, and does not modify the system Python installation.

## Design decisions: key fallback, caching, SQLite session storage

- **Key pool and fallback:** LiteLLM sends requests using the configured keys in order. A provider error moves the request to the next key. Only key identifiers such as `key_1` are returned to the browser; provider secrets are never returned or logged.
- **Caching:** LiteLLM's local response cache is enabled for live calls. A bounded in-memory response cache keys on the LiteLLM model and the complete message list, including the system prompt, prior turns, and the new message. It preserves the reply and token usage needed to report `LLM_CACHE` accurately. Cache entries expire after the configured TTL.
- **SQLite sessions:** `sessions` holds each session's system prompt and `messages` stores user and assistant turns. Each successful exchange is saved in one transaction. Requests that fail validation or exhaust the provider pool are not saved. The history endpoint never returns the system prompt.
- **Safe local demo:** `LLM_MODE=mock` provides deterministic offline replies for local UI and failover verification. It is not a substitute for live model inference. The deployed app should use `LLM_MODE=litellm` and real provider keys.

## Verification: how to see failover, cache hits and per-message token usage

Run the backend tests from the project root:

```sh
make test
```

Check the health endpoint:

```sh
curl -s http://localhost:8000/health
```

Send the same first message to two new sessions. The first request should report `LIVE_INFERENCE`; the second should report `LLM_CACHE`, with the same reply and saved token counts:

```sh
curl -s http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"cache-demo-one","message":"What is the capital of France?"}'

curl -s http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"cache-demo-two","message":"What is the capital of France?"}'
```

To demonstrate a primary-key failure, use a new message and session. With two provider keys and `ENABLE_FAILURE_SIMULATION=true`, this returns a real answer from `key_2` and reports one fallback attempt:

```sh
curl -s http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: key_1=429' \
  -d '{"session_id":"fallback-demo","message":"Give me a short greeting."}'
```

To simulate all providers being unavailable, use another new session. The request returns `503`, and the session does not appear in history:

```sh
curl -i http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -H 'X-Simulate-Failure: *=429' \
  -d '{"session_id":"all-down-demo","message":"This request should not be saved."}'

curl -i http://localhost:8000/sessions/all-down-demo/messages
```

The React interface includes a failover test selector that sends the same header. Each assistant reply shows its source, model, latency, input tokens, and output tokens. Live provider usage is returned by LiteLLM for that request only. The offline mock uses a local token estimate.

The API exposes only these application endpoints: `GET /health`, `POST /chat`, and `GET /sessions/{session_id}/messages`. FastAPI documentation routes are disabled.

## Setup Used: your editor and AI tools

- Editor and coding environment: OpenCode
- AI assistance: OpenCode coding assistant
- Backend tests: pytest and FastAPI TestClient

## Bonus features (if any): what you added beyond the requirements and why

- Offline demo mode lets reviewers try the full interface without provider credentials.
- The UI has suggested prompts, session status, a system-prompt field, and a failover test selector for easier demonstration.
- The response cache is bounded and time-limited to avoid unbounded process memory growth.
- The backend Docker image runs as a non-root user and excludes test-only dependencies from its runtime image.
- `DEMO_SCRIPT.md` provides a timed narration and recording checklist for the required 2 to 3 minute video.
