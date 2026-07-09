# Code Review Council

🔗 **Live demo: [code-review-council.main.streamlit.app](https://code-review-council-ccgciqkfvkavydnyrr5uzb.streamlit.app/)** — replace this with your actual Streamlit Cloud URL

Three specialist agents independently draft findings on your code, then **debate**: each one reads its peers' drafts and gets a chance to revise or defend its score. A chat manager reads the full multi-pass debate — plus a rolling memory of your last 5 sessions — and hands down a final consensus ruling.

Styled as a case docket: your code is submitted as **Exhibit A**, each agent files a stamped finding, and the manager's synthesis appears as a sealed **Final Ruling**. Past sessions are browsable from a sidebar, like a docket log.

## How it works

```
                    ┌── Security Agent ──┐
Your code (Exhibit) ├── Performance Agent ─┤──▶ Pass 1 drafts
                    └── Readability Agent ┘
                              │
              (each agent reads its peers' Pass 1 drafts)
                              ▼
                    ┌── Security Agent ──┐
                    ├── Performance Agent ─┤──▶ Pass 2 finalized findings
                    └── Readability Agent ┘         (scores may change)
                              │
              + last 5 sessions' chat history
                              ▼
                        Chat Manager
                              │
                              ▼
                      Final Ruling (consensus)
```

Both passes run **concurrently** within themselves — all three agents fire at once via LangChain's `RunnableParallel`, not a sequential loop. The Chat Manager is a hard synchronization point: it never runs until every agent (across both passes) has resolved, whether that took 2 seconds or 20.

If an individual agent's chain fails entirely (API outage, local Ollama crash, whatever), that seat is marked empty and the Chat Manager is told explicitly which specialist(s) are missing — it still produces a valid ruling from whichever agents succeeded.

## Stack

- **Backend**: `orchestrator.py` — built entirely on **LangChain** primitives (`ChatPromptTemplate`, `RunnableParallel`, `StrOutputParser`, `MessagesPlaceholder`, `.with_retry()`, `.with_fallbacks()`). No hand-rolled HTTP calls, no manual thread pools.
- **Frontend**: `streamlit_app.py` — docket-themed UI with a sidebar case history (click any past ruling to reload it) and a live "debate trail" showing which agents changed their score between passes.
- **Evaluation**: `eval_harness.py` — standalone offline LLM-as-judge test suite; run outside Streamlit to regression-test review quality and compare Gemini vs. local Ollama output on the same code.
- **LLM provider**: switchable between a local [Ollama](https://ollama.com) model (free, offline) and [Gemini 2.5 Flash-Lite](https://ai.google.dev) (free tier, cloud). Gemini is primary in the default configuration, with an **automatic LangChain-native fallback** to Ollama (`.with_fallbacks()`) if Gemini fails for any reason after its own retries — no manual provider-switching needed during a session.

## Setup

### 1. Choose a provider

**Local (Ollama)** — no API key, runs entirely on your machine:
```bash
ollama pull qwen2.5-coder
ollama serve
```
```bash
export COUNCIL_PROVIDER=ollama
```
qwen2.5-coder is code-specialized and outperforms generic chat models like llama3.1 for this task. Pull a larger tag (`:3b`, `:7b`) if your machine can handle it — bigger means better judgment calls, smaller means faster and lighter. Override the exact model/host with `OLLAMA_MODEL` / `OLLAMA_HOST` if needed.

**Cloud (Gemini)** — needs a free key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey):
```bash
export COUNCIL_PROVIDER=gemini
export GOOGLE_API_KEY=your_key_here
```
(`GEMINI_API_KEY` also works if you already have that set — the orchestrator checks both.) This is the default if `COUNCIL_PROVIDER` isn't set at all. The live demo runs on `gemini-2.5-flash-lite` specifically, since it has a much higher free-tier daily quota than regular `gemini-2.5-flash`.

> **Migrating from an older setup?** Earlier versions of this project used `LLM_PROVIDER` instead of `COUNCIL_PROVIDER`. `LLM_PROVIDER` is still honored as a fallback for compatibility, but `COUNCIL_PROVIDER` is the name going forward — update your env vars / Streamlit secrets to match.

### 2. Install dependencies

```bash
python -m venv venv
# Windows:
.\venv\Scripts\Activate.ps1
# Mac/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

Key packages: `streamlit`, `langchain-core`, `langchain-google-genai`, `langchain-ollama`. No `langgraph` — orchestration is plain LCEL (LangChain Expression Language), not the agent-framework layer.

### 3. Run

```bash
streamlit run streamlit_app.py
```

Opens automatically at `http://localhost:8501`.

### 4. (Optional) Run the offline evaluation suite

```bash
python eval_harness.py --provider ollama --fast
```
Runs a small suite of known-issue code snippets (SQL injection, N+1 queries, poor readability, plus a clean-code false-positive control) through the real council, then scores each ruling with a separate LLM-as-judge call. Useful for catching prompt regressions before you ship a change. See `python eval_harness.py --help` for all options, including `--provider both` to compare Gemini vs. Ollama output quality directly.

## Configuration

| Env var | Default | Description |
|---|---|---|
| `COUNCIL_PROVIDER` | `gemini` | `gemini` or `ollama`. (`LLM_PROVIDER` honored as a legacy fallback.) |
| `COUNCIL_ENABLE_DEBATE` | `true` | Set `false` to skip the Pass 2 cross-review/debate round and run single-pass — roughly halves LLM call volume, useful for quota-constrained testing. |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | — | Required if `COUNCIL_PROVIDER=gemini` (or as the fallback target when Ollama is primary and Gemini backs it up — not applicable in that direction currently). |
| `OLLAMA_MODEL` | `qwen2.5-coder` | Any model you've pulled locally. |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server address. |

When deployed on Streamlit Community Cloud, secrets are set via the app's **Settings → Secrets** panel instead of local env vars — `orchestrator.py` checks `st.secrets` first and falls back to env vars automatically.

**Cloud deployment limitation:** Ollama cannot run on Streamlit Community Cloud — there's no local model server available there. This means the automatic Gemini→Ollama fallback only actually helps in local development. On the deployed version, if Gemini's quota is exhausted, there is currently no working fallback and the app will surface a rate-limit error instead.

## Project structure

```
code-review-council/
├── orchestrator.py       # core orchestration logic (LangChain-based) — reusable, no UI dependency
├── streamlit_app.py      # docket-themed frontend (deployed version)
├── eval_harness.py       # standalone offline LLM-as-judge evaluation suite
├── requirements.txt      # for local install + Streamlit Cloud deployment
└── README.md
```

`orchestrator.py` has no dependency on the frontend — `run_council(code, language)` can be called directly from a Python shell or `eval_harness.py` to test the council without touching Streamlit at all.

## What the two-pass debate actually does

- **Pass 1 (Drafting)**: all three specialists evaluate the code independently and concurrently, with no visibility into each other's findings.
- **Pass 2 (Cross-review)**: each specialist is shown its own Pass 1 draft *and* its peers' Pass 1 drafts, and is asked to either revise its score (if a peer caught something structural it missed) or explicitly defend its original score.
- The Streamlit UI surfaces this as a **"Pass 1 → Pass 2 — the debate trail"** panel showing exactly which agents changed their mind and by how much, rather than burying that inside the Chat Manager's prose.

This roughly doubles LLM call volume per session (3 → 6 specialist calls, plus 1 Chat Manager call). Set `COUNCIL_ENABLE_DEBATE=false` if you're rate-limited and want single-pass behavior instead.

## Extending it

- **Add a 4th agent**: add an entry to `AGENT_META` in `orchestrator.py` and its key to `AGENT_ORDER` — both passes and the Chat Manager already iterate over `AGENT_ORDER`, so no other orchestration code changes are needed.
- **N-pass debate**: currently hardcoded to exactly 2 passes. Generalizing to a configurable number of rounds (stop early if no agent's score changes between rounds) is a natural next step.
- **Human-in-the-loop**: insert a step between Pass 2 and the Chat Manager where a person can add a comment to the debate thread before the manager reads it.
- **Persistent storage**: session history (both the sidebar case log and the Chat Manager's memory) currently lives only in `st.session_state` and resets on page refresh. Swapping in a real database (SQLite for local, Postgres for deployed) would make it durable across sessions.

## Known limitations

- **Free-tier rate limits**: the deployed Gemini-backed version is subject to Google's free-tier daily/per-minute quotas. LangChain's `.with_retry()` handles transient failures automatically; `.with_fallbacks()` drops to local Ollama on persistent failure **in local dev only** (see Cloud deployment limitation above).
- **Two-pass debate roughly doubles API cost** per session compared to single-pass — see `COUNCIL_ENABLE_DEBATE` above if this matters for your quota.
- **Local model quality**: small local models (1.5B–7B parameters) reliably catch obvious issues (string-concatenated SQL, hardcoded secrets) but may miss subtler logic bugs, and don't always follow formatting instructions exactly. If a score shows `—` in the UI, this is now logged server-side (`_extract_score` warns via the standard `logging` module) — check your logs to distinguish "model didn't follow the score format" from "this seat failed entirely."
- **Guardrail is character-based, not token-based**: the 15,000-character input limit (`MAX_CODE_CHARS` in `orchestrator.py`) is a rough safety threshold, not an exact token count.
