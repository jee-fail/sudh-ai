# sudh.AI deployment handoff

The application is deployed and live. This guide records the actual configuration so it can be recreated or handed over. The app's author and developer byline remains Sudhanshu Kumar.

## Live URLs

- Frontend (GitHub Pages): https://jee-fail.github.io/sudh-ai/
- Backend health check (Render): https://sudh-ai-backend.onrender.com/health
- Public repository: https://github.com/jee-fail/sudh-ai

No `.env` file, provider keys, database, or tokens are committed. `.gitignore` excludes `.env`, `.env.*`, `.venv`, `*.db`, `node_modules`, and `dist`.

## 1. Repository

The public repository `jee-fail/sudh-ai` uses `main` as its default branch. It was created from the submission ZIP and now contains the deployment configuration in addition to the original source.

To clone it locally:

```sh
git clone https://github.com/jee-fail/sudh-ai.git
cd sudh-ai
```

The commit author identity is configured locally in this clone; anyone pushing should use an identity connected to their own GitHub account. Do not set `user.name` or `user.email` with `--global`.

## 2. Backend on Render

The backend is defined by `render.yaml` at the repository root and was created with the Render Blueprint deeplink:

```
https://dashboard.render.com/blueprint/new?repo=https://github.com/jee-fail/sudh-ai
```

Configuration applied by the Blueprint:

- Service: `sudh-ai-backend`, Docker runtime, root directory `backend`, Dockerfile path `Dockerfile`.
- Health check path: `/health`.
- Plan: **free**, with **no persistent disk**. SQLite writes to `/data/app.db` inside the container filesystem, so chat history resets when the service restarts or the instance spins down. A paid plan with a persistent disk would be required to keep history across restarts.
- Render supplies the `PORT` value; the Docker image listens on it.
- Environment variables (provider keys are entered in the Render dashboard only, as `sync: false` values):

```text
LLM_MODE=litellm
LLM_API_KEYS=<OPENROUTER_KEY_1>,<OPENROUTER_KEY_2>
LLM_MODEL=openrouter/nvidia/nemotron-3.5-lightning:free
DATABASE_URL=sqlite:////data/app.db
ALLOWED_ORIGINS=https://jee-fail.github.io
ENABLE_FAILURE_SIMULATION=true
DEFAULT_SYSTEM_PROMPT=You are a helpful assistant. Keep replies clear and concise.
LLM_TIMEOUT_SECONDS=30
LLM_MAX_TOKENS=512
LLM_TEMPERATURE=0.2
CACHE_TTL_SECONDS=3600
CACHE_MAX_ENTRIES=256
LOG_LEVEL=INFO
```

Notes:

- Keys must be raw OpenRouter keys (`sk-or-…`), comma-separated, with no quotes or `Bearer` prefix. The app requires at least two keys for the failover demo.
- `ALLOWED_ORIGINS` is the exact GitHub Pages origin, with no trailing slash and no `/sudh-ai` path.
- The original `gemini/gemini-2.0-flash` model was shut down on 2026-06-01. OpenRouter's free Nemotron model is used instead; change `LLM_MODEL` if you switch providers.
- Free Render instances spin down with inactivity, so the first request after idle may be delayed by about 50 seconds.

Verify with:

```sh
curl -s https://sudh-ai-backend.onrender.com/health
```

A healthy response is HTTP 200 with `status: ok`, `keys_configured` of at least 2, and the active `model`.

## 3. Frontend on GitHub Pages

GitHub Pages is enabled with **GitHub Actions** as its source. The workflow is `.github/workflows/deploy-pages.yml`.

- Build command: `npm ci && npm run build` from the `frontend/` directory, with Node 20.
- Output published: `frontend/dist`.
- `frontend/vite.config.js` sets `base: "./"` so asset URLs work at the `/sudh-ai/` project path.
- Repository variable: `VITE_API_BASE_URL=https://sudh-ai-backend.onrender.com`. It is baked into the bundle at build time, so set it before building. The workflow skips when the variable is unset.
- Site URL: https://jee-fail.github.io/sudh-ai/

## 4. Deployment verification (completed)

| Check | Result |
| --- | --- |
| `GET /health` | HTTP 200, `keys_configured: 4`, active model reported |
| First message in a session | `LIVE_INFERENCE`, served by `key_1` |
| Same message in a fresh session | `LLM_CACHE` |
| `X-Simulate-Failure: key_1=429` | served by `key_2`, `fallback_attempts: 1` |
| `X-Simulate-Failure: *=429` | HTTP 503, readable error, session not saved |
| CORS header | `Access-Control-Allow-Origin: https://jee-fail.github.io` |
| Pages assets | HTTP 200 for `./assets/*.js` and `*.css` |

For the full walkthrough, see `README.md`; for the recorded demo, follow `DEMO_SCRIPT.md`.

## 5. Updating the deployment

- A push to `main` triggers the Pages workflow and a Render Blueprint auto-sync.
- Keep provider keys in the Render dashboard and `VITE_API_BASE_URL` as a GitHub Actions variable. Never commit secrets.
- If CORS errors appear in the browser, confirm `ALLOWED_ORIGINS` on Render matches the Pages origin exactly.

Never commit `.env` files, API keys, Render environment values, or GitHub tokens. Deployment credentials stay in the hosting dashboards.
