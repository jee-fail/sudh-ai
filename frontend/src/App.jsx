import { useEffect, useRef, useState } from "react";

const API_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000")
  .replace(/\/+$/, "");

const FAILURE_MODES = [
  { value: "off", label: "No simulation", header: "" },
  { value: "primary", label: "Fail key_1 with 429", header: "key_1=429" },
  { value: "all", label: "Fail all keys with 429", header: "*=429" },
];

const SUGGESTIONS = [
  "Help me plan a focused week",
  "Explain a complex idea simply",
  "Give me a fresh perspective",
];

function newSessionId() {
  return globalThis.crypto?.randomUUID?.()
    ? `s-${globalThis.crypto.randomUUID()}`
    : `s-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function errorMessage(response, payload) {
  const detail = payload?.detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => item.msg).filter(Boolean).join(". ")
      || "Please check your message and try again.";
  }
  if (detail === "all providers unavailable") {
    return "All AI providers are temporarily unavailable. This message was not saved. Please try again shortly.";
  }
  if (typeof detail === "string" && detail) return detail;
  return `The request failed (${response.status}). Please try again.`;
}

function Welcome({ onChoosePrompt }) {
  return (
    <section className="welcome" aria-labelledby="welcome-title">
      <div className="welcome-mark" aria-hidden="true">
        <span>s</span>
        <i />
      </div>
      <p className="section-kicker">A CLEARER SPACE TO THINK</p>
      <h2 id="welcome-title">What would you like to explore?</h2>
      <p className="welcome-copy">
        Ask a question, work through an idea, or start with one of these.
      </p>
      <div className="suggestion-list" aria-label="Suggested prompts">
        {SUGGESTIONS.map((suggestion) => (
          <button
            className="suggestion"
            key={suggestion}
            onClick={() => onChoosePrompt(suggestion)}
            type="button"
          >
            <span>{suggestion}</span>
            <span className="suggestion-arrow" aria-hidden="true">↗</span>
          </button>
        ))}
      </div>
    </section>
  );
}

function MessageMetadata({ metadata }) {
  return (
    <div className="metadata" aria-label="Response metadata">
      <span
        className={`source-tag ${metadata.resolution_source === "LLM_CACHE" ? "source-cached" : ""}`}
        data-testid="meta-source"
      >
        {metadata.resolution_source}
      </span>
      <span className="meta-item">
        <span className="meta-label">Model</span>
        <span data-testid="meta-model">{metadata.model}</span>
      </span>
      <span className="meta-item">
        <span className="meta-label">Latency</span>
        <span data-testid="meta-latency">{metadata.latency_ms} ms</span>
      </span>
      <span className="meta-item">
        <span className="meta-label">Input</span>
        <span data-testid="meta-input-tokens">{metadata.input_tokens}</span>
        <span className="meta-label">tokens</span>
      </span>
      <span className="meta-item">
        <span className="meta-label">Output</span>
        <span data-testid="meta-output-tokens">{metadata.output_tokens}</span>
        <span className="meta-label">tokens</span>
      </span>
      {metadata.key_id && (
        <span className="meta-item">
          <span className="meta-label">Key</span>
          <span data-testid="meta-key">{metadata.key_id}</span>
        </span>
      )}
      {metadata.fallback_attempts > 0 && (
        <span className="meta-item fallback-tag" data-testid="meta-fallback">
          Fallbacks: {metadata.fallback_attempts}
        </span>
      )}
    </div>
  );
}

export default function App() {
  const [sessionId] = useState(newSessionId);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [failureMode, setFailureMode] = useState("off");
  const [sending, setSending] = useState(false);
  const [service, setService] = useState({ status: "checking", keys: 0 });
  const conversationRef = useRef(null);

  useEffect(() => {
    let active = true;

    async function checkService() {
      try {
        const response = await fetch(`${API_URL}/health`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error("Health check failed");
        const data = await response.json();
        if (active) {
          setService({ status: "online", keys: data.keys_configured });
        }
      } catch {
        if (active) setService({ status: "offline", keys: 0 });
      }
    }

    checkService();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const conversation = conversationRef.current;
    if (conversation) {
      conversation.scrollTo({
        top: conversation.scrollHeight,
        behavior: "smooth",
      });
    }
  }, [messages, sending]);

  async function sendMessage(event) {
    event.preventDefault();
    const message = input.trim();
    if (!message || sending) return;

    setInput("");
    setSending(true);
    setMessages((current) => [
      ...current,
      { id: newSessionId(), role: "user", content: message },
    ]);

    try {
      const selectedFailure = FAILURE_MODES.find(
        (mode) => mode.value === failureMode,
      );
      const headers = { "Content-Type": "application/json" };
      if (selectedFailure?.header) {
        headers["X-Simulate-Failure"] = selectedFailure.header;
      }

      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers,
        body: JSON.stringify({
          session_id: sessionId,
          message,
          ...(systemPrompt.trim()
            ? { system_prompt: systemPrompt.trim() }
            : {}),
        }),
      });

      let data;
      try {
        data = await response.json();
      } catch {
        throw new Error("The chat service returned an invalid response.");
      }

      if (!response.ok) throw new Error(errorMessage(response, data));
      if (!data.reply || !data.metadata) {
        throw new Error("The chat service returned an incomplete response.");
      }

      setMessages((current) => [
        ...current,
        {
          id: newSessionId(),
          role: "assistant",
          content: data.reply,
          metadata: data.metadata,
        },
      ]);
    } catch (error) {
      const messageText = error instanceof TypeError
        ? "Could not reach sudh.AI. Check the backend connection and try again."
        : error.message || "The request could not be completed. Please try again.";
      setMessages((current) => [
        ...current,
        { id: newSessionId(), role: "error", content: messageText },
      ]);
    } finally {
      setSending(false);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <main className="app-shell">
      <section className="chat-card" aria-label="sudh.AI chat workspace">
        <header className="topbar">
          <div className="brand-lockup">
            <div className="brand-mark" aria-hidden="true">
              <span>s</span>
              <i />
            </div>
            <div className="brand-copy">
              <p className="brand-overline">YOUR THOUGHT PARTNER</p>
              <h1>sudh.AI</h1>
            </div>
          </div>

          <div className="topbar-right">
            <div className={`service-status status-${service.status}`}>
              <span className="status-dot" aria-hidden="true" />
              <span>
                {service.status === "online"
                  ? `${service.keys} keys ready`
                  : service.status === "offline"
                    ? "Service unavailable"
                    : "Connecting"}
              </span>
            </div>
            <span className="session-badge">
              <span className="session-badge-dot" aria-hidden="true" />
              Fresh session
            </span>
          </div>
        </header>

        <div className="workspace">
          <aside className="settings-panel" aria-label="Chat settings">
            <div className="panel-heading">
              <p className="section-kicker">WORKSPACE</p>
              <span className="panel-index">01</span>
            </div>

            <div className="current-session">
              <span className="session-icon" aria-hidden="true">✳</span>
              <span>
                <strong>Current chat</strong>
                <small>Starts fresh each visit</small>
              </span>
            </div>

            <div className="panel-divider" />

            <label className="field-label prompt-label" htmlFor="system-prompt">
              System prompt
              <span>OPTIONAL</span>
            </label>
            <textarea
              id="system-prompt"
              className="settings-textarea"
              data-testid="system-prompt-input"
              value={systemPrompt}
              onChange={(event) => setSystemPrompt(event.target.value)}
              placeholder="Set a helpful direction for this chat..."
              maxLength={4000}
              rows={4}
            />
            <p className="field-help prompt-help">
              Used for this conversation and omitted from the history response.
            </p>

            <div className="panel-divider secondary-divider" />

            <label className="field-label failure-label" htmlFor="failure-mode">
              Failover test
              <span>DEMO</span>
            </label>
            <select
              id="failure-mode"
              className="settings-select"
              value={failureMode}
              onChange={(event) => setFailureMode(event.target.value)}
            >
              {FAILURE_MODES.map((mode) => (
                <option key={mode.value} value={mode.value}>
                  {mode.label}
                </option>
              ))}
            </select>
            <p className="field-help failure-help">
              Sends the assignment&apos;s X-Simulate-Failure test header.
            </p>

            <div className="sidebar-spacer" />
            <div className="session-id">
              <span className="section-kicker">SESSION ID</span>
              <code title={sessionId}>{sessionId}</code>
            </div>
          </aside>

          <section className="chat-area" aria-label="Conversation">
            <div className="conversation" ref={conversationRef} aria-live="polite">
              {messages.length === 0 && (
                <Welcome onChoosePrompt={(suggestion) => setInput(suggestion)} />
              )}

              {messages.map((message) => {
                if (message.role === "user") {
                  return (
                    <div className="user-row" key={message.id}>
                      <div className="user-message">{message.content}</div>
                    </div>
                  );
                }

                if (message.role === "error") {
                  return (
                    <div
                      className="error-message"
                      role="alert"
                      key={message.id}
                    >
                      <span className="error-icon" aria-hidden="true">!</span>
                      <span>{message.content}</span>
                    </div>
                  );
                }

                return (
                  <article
                    className="assistant-message"
                    data-testid="assistant-message"
                    key={message.id}
                  >
                    <div className="assistant-avatar" aria-hidden="true">s</div>
                    <div className="assistant-content">
                      <div className="assistant-heading">
                        <strong>sudh.AI</strong>
                        <span>Assistant</span>
                      </div>
                      <p className="reply-text">{message.content}</p>
                      <MessageMetadata metadata={message.metadata} />
                    </div>
                  </article>
                );
              })}

              {sending && (
                <div className="typing-indicator" role="status">
                  <span className="typing-dots" aria-hidden="true">
                    <i />
                    <i />
                    <i />
                  </span>
                  Thinking through it...
                </div>
              )}
            </div>

            <form className="composer" onSubmit={sendMessage}>
              <textarea
                data-testid="chat-input"
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Message sudh.AI..."
                aria-label="Write a message"
                rows={2}
                maxLength={4000}
              />
              <div className="composer-footer">
                <span className="composer-tip">
                  <kbd>Enter</kbd> to send
                  <span className="tip-divider">•</span>
                  <kbd>Shift + Enter</kbd> for a new line
                </span>
                <button
                  type="submit"
                  data-testid="send-button"
                  disabled={!input.trim() || sending}
                >
                  {sending ? "Sending" : "Send"}
                  <span className="send-icon" aria-hidden="true">↗</span>
                </button>
              </div>
            </form>

            <footer className="chat-footer">
              <span>Built by Sudhanshu Kumar</span>
              <span className="footer-divider">•</span>
              <span>AI can make mistakes. Verify important details.</span>
            </footer>
          </section>
        </div>
      </section>
    </main>
  );
}
