# Code Review Council ⚖️

Three specialist agents independently review your code, debate trade-offs, and a chat manager hands down a final consensus — a working implementation of the **group chat orchestration** pattern (also called multiagent debate, maker-checker, or council orchestration).

Styled as a case docket: your code is submitted as **Exhibit A**, each agent files a stamped finding, and the manager's synthesis appears as a sealed **Final Ruling**.

## How it works

```
Your code  →  Security Agent ─┐
               Performance Agent ─┤→  Accumulating thread  →  Chat Manager  →  Consensus
               Readability Agent ─┘
```

Three agents review the code in parallel, each scoped to one concern via a tightly written system prompt. Their findings are appended to a shared thread. A fourth call — the chat manager — reads that whole thread and produces a final synthesis: where the agents agree, the one real tension between them, and a prioritized top-3 fix list.

There's no orchestration framework involved — just a Python list that accumulates agent outputs, plus one more LLM call that reads it.

## Stack

- **Backend**: `orchestrator.py` — provider-agnostic LLM calls, agent definitions, the orchestration loop
- **Frontend**: `streamlit_app.py` — docket-themed UI (Streamlit + custom CSS)
- **LLM provider**: switchable between a local [Ollama](https://ollama.com) model (free, offline) and [Gemini 2.5 Flash](https://ai.google.dev) (free tier, cloud)

## Setup

### 1. Choose a provider

**Local (Ollama)** — no API key, runs entirely on your machine:
```bash
ollama pull qwen2.5-coder:1.5b
```
qwen2.5-coder is code-specialized and outperforms generic chat models like llama3.1 for this task. Use `:3b` or `:7b` instead of `:1.5b` if your machine can handle it — bigger means better judgment calls, smaller means faster.

**Cloud (Gemini)** — needs a free key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey):
```bash
export LLM_PROVIDER=gemini
export GEMINI_API_KEY=your_key_here
```

### 2. Install dependencies

```bash
python -m venv venv
# Windows:
.\venv\Scripts\Activate.ps1
# Mac/Linux:
source venv/bin/activate

pip install streamlit requests
# add google-genai too if using the Gemini provider
```

### 3. Run

```bash
streamlit run streamlit_app.py
```

Opens automatically at `http://localhost:8501`.

## Configuration

| Env var | Default | Description |
|---|---|---|
| `LLM_PROVIDER` | `ollama` | `ollama` or `gemini` |
| `OLLAMA_MODEL` | `qwen2.5-coder:7b` | Any model you've pulled locally |
| `OLLAMA_HOST` | `http://localhost:11434` | Ollama server address |
| `GEMINI_API_KEY` | — | Required if `LLM_PROVIDER=gemini` |

## Project structure

```
code-review-council/
├── orchestrator.py      # core orchestration logic — reusable, no UI dependency
├── streamlit_app.py      # docket-themed frontend
├── app.py                # alternate Flask + HTML frontend (optional)
├── templates/index.html  # Flask frontend's UI
└── README.md
```

`orchestrator.py` has no dependency on either frontend — run `python orchestrator.py` directly to test the agent council from the command line.

## Extending it

- **Add a 4th agent**: append a new dict to the `AGENTS` list in `orchestrator.py` with its own system prompt. The orchestration loop already iterates over the list, so no other code changes are needed.
- **True maker-checker loop**: currently each agent runs once. To make it iterative — propose a fix, have the manager re-check it, repeat until approved or a max iteration cap — add a loop around a single agent's call that feeds back the manager's critique.
- **Human-in-the-loop**: insert a step between the agent reports and the manager synthesis where a person can add a comment to the thread before the manager reads it.
- **True parallel execution**: agents currently run sequentially in a for-loop since they're independent. Swap in `concurrent.futures.ThreadPoolExecutor` to run all three at once and cut wall-clock time roughly 3x.

## Notes on local models

7B-class and smaller local models are noticeably weaker than cloud frontier models at nuanced judgment. They reliably catch obvious issues (string-concatenated SQL, hardcoded secrets) but may miss subtler logic bugs, and don't always follow formatting instructions exactly — if a score shows `—` in the UI, check the raw `agent_reports` field in the response; the model likely phrased its score line differently than the parser expects.
