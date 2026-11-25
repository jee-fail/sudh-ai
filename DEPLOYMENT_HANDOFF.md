# sudh.AI deployment handoff

This guide is for the owner of the GitHub account that will hold and deploy the repository. The source ZIP has no `.git` history, `.venv`, `node_modules`, `.env`, database, or provider keys. The Git commits created from it will belong to the account that configures and pushes them. The app's author and developer byline remains Sudhanshu Kumar.

## 1. Create the GitHub repository under the correct account

Create an empty public repository named `sudh-ai` under the account or organization that should own it. Do not initialize it with a README or license, since those files are already in the ZIP.

Extract `sudh-ai-submission.zip`. It contains a top-level `sudh-ai/` folder. From that folder, create the repository and commit using the repository owner's identity:

```sh
cd sudh-ai
git init -b main
git config user.name "Repository Owner Name"
git config user.email "verified-email-or-github-noreply-address"
git add .
git commit -m "Add sudh.AI full-stack chat app"
git remote add origin https://github.com/OWNER/sudh-ai.git
git push -u origin main
```

The `git config` commands above are local to this repository. Do not add `--global`. Use an email connected to the repository owner's GitHub account so GitHub attributes the commit to them. If the repository already exists, replace the `git remote add` URL with its URL and follow the owner's branch rules.

If using GitHub's web interface, extract the ZIP first, then upload the files inside `sudh-ai/`. Uploading the ZIP as a single file does not create the repository contents.

For an organization repository, the owner may also need to authorize the Vercel and Render GitHub integrations for that repository.

## 2. Deploy the backend on Render

1. In the repository owner's Render account, create a Web Service from the GitHub repository.
2. Set the root directory to `backend`, choose the Docker runtime, and set the Dockerfile path to `Dockerfile` relative to that root.
3. Set the health check path to `/health`.
4. Attach a persistent disk mounted at `/data`. SQLite must use this disk to survive restarts. Confirm the current Render plan and disk price with the owner before creating a billable service.
5. Add these environment variables in Render. Enter provider keys using Render's secret environment variable controls, not in GitHub:

```text
LLM_MODE=litellm
LLM_API_KEYS=<GEMINI_KEY_1>,<GEMINI_KEY_2>
LLM_MODEL=openrouter/google/gemma-4-31b-it:free
DATABASE_URL=sqlite:////data/app.db
ALLOWED_ORIGINS=http://localhost:3000
ENABLE_FAILURE_SIMULATION=true
DEFAULT_SYSTEM_PROMPT=You are a helpful assistant. Keep replies clear and concise.
LLM_TIMEOUT_SECONDS=30
LLM_MAX_TOKENS=512
LLM_TEMPERATURE=0.2
CACHE_TTL_SECONDS=3600
CACHE_MAX_ENTRIES=256
LOG_LEVEL=INFO
```

Render supplies the service `PORT` value. The backend Docker image listens on that value. The `ALLOWED_ORIGINS` value above is temporary. Replace it after the Vercel deployment with the exact Vercel origin, such as `https://sudh-ai.vercel.app`.
If the configured Gemini model is not available to the account, change `LLM_MODEL` to a currently supported LiteLLM Gemini model.

Deploy the service and verify that `https://<render-service>.onrender.com/health` returns HTTP 200 with `keys_configured` equal to 2.

## 3. Deploy the frontend on Vercel

1. In the repository owner's Vercel account, import the same GitHub repository.
2. Set the project root directory to `frontend` and select the Vite framework preset.
3. Use `npm run build` as the build command and `dist` as the output directory.
4. Add the environment variable `VITE_API_BASE_URL` with the Render service base URL, for example `https://<render-service>.onrender.com`.
5. Deploy and note the Vercel site origin.

Return to the Render service and change `ALLOWED_ORIGINS` to the exact Vercel origin, with no trailing slash. Redeploy the backend so browser requests pass CORS checks.

## 4. Verify the live deployment

- Open the Vercel URL and send a message. Confirm the reply displays the model, latency, input tokens, and output tokens.
- Send the same first message in a fresh session. Confirm the second result reports `LLM_CACHE`.
- Select `Fail key_1 with 429` in the app and send a unique message. Confirm the response uses `key_2` and reports one fallback attempt.
- Select `Fail all keys with 429`. Confirm the UI shows a readable error and the failed message is not in session history.
- Check the API directly at `/health` and use the history and failover commands in `README.md`.
- Record the required 2 to 3 minute demo in your own voice, following `DEMO_SCRIPT.md`.

Never commit `.env` files, API keys, Render environment values, or GitHub tokens. The repository owner should keep deployment credentials in the hosting dashboards.
