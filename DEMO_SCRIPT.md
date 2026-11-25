# sudh.AI demo video script

Target runtime: about 2 minutes 30 seconds. Record this against the live public URLs: https://jee-fail.github.io/sudh-ai/ (frontend) and https://sudh-ai-backend.onrender.com/health (backend).

## Talk track and screen actions

### 0:00 to 0:20: Introduce the app

**On screen:** Open the public sudh.AI frontend and show the service status.

**Say:** "Hi, I'm Sudhanshu Kumar, developer of sudh.AI. This is a full-stack AI chat app with multi-turn memory, response caching, per-request usage metadata, and automatic provider failover."

### 0:20 to 0:55: Show a multi-turn conversation

**On screen:** Send "What is the capital of France?" Then ask "What was my first question?"

**Say:** "The client starts a fresh session when the page loads. The backend stores each successful turn in SQLite, so the assistant can refer to earlier messages in this session. The API history endpoint returns the conversation without exposing the system prompt."

### 0:55 to 1:20: Explain response metadata

**On screen:** Point to the assistant reply metadata.

**Say:** "Each reply shows whether it came from live inference or cache, which model answered, the server latency, and input and output token counts for that request only. The key label is an identifier, never the provider secret."

### 1:20 to 1:45: Demonstrate a cache hit

**On screen:** Refresh the page to create a new session. Send the exact same first message used earlier.

**Say:** "The full prompt, including the system prompt and message history, is the cache key. The identical first message in a new session returns the same answer as an LLM cache hit, with the original token usage and no provider key attached."

### 1:45 to 2:10: Demonstrate key failover

**On screen:** In the Failover test control, select "Fail key_1 with 429". Send a new, unique message.

**Say:** "The test control sends the required X-Simulate-Failure header. This request simulates a rate limit on the first key, then returns a real answer from the next configured key. The reply metadata confirms key_2 and one fallback attempt."

### 2:10 to 2:30: Show graceful failure and close

**On screen:** Select "Fail all keys with 429", send a new message, and show the readable error. Optionally run the history request for that new session and show the 404 response.

**Say:** "When every key is unavailable, the API returns a clean 503 response, the interface stays usable, and the failed request is not stored in history. The stack also runs locally with Docker Compose and a named SQLite volume. Thanks for watching."

## Recording checklist

- Use the public frontend and backend URLs in the recording.
- Ensure at least two valid provider keys are configured and `ENABLE_FAILURE_SIMULATION=true`.
- Use a new, unique prompt for each failover test so an earlier cache entry cannot affect the result.
- Keep provider keys, environment settings, and account dashboards off screen.
- Record your own narration. Do not include credentials in the video.
