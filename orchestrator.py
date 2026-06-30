"""
Code Review Council — Group Chat Orchestration
=================================================

This is the core orchestration engine. It implements the pattern from your
diagrams:

    Input -> Group Chat Manager -> [Agent 1, Agent 2, Agent N] -> Accumulating
    Thread -> Result

Three specialist agents (Security, Performance, Readability) independently
review a piece of code. A Chat Manager then synthesizes their findings,
surfaces disagreements, and drives the group toward a consensus report.

Every agent call and manager call goes through `call_claude()`, which is
just a wrapped Anthropic API call. The "accumulating chat thread" is simply
a Python list of dicts that we keep appending to — that's the whole trick
behind this pattern. There's no magic; it's just disciplined message
passing.
"""

import os
import requests

# ---------------------------------------------------------------------------
# Setup — provider is switchable via the LLM_PROVIDER env var (local) or
# Streamlit secrets (when deployed on Streamlit Community Cloud).
# ---------------------------------------------------------------------------
#   LLM_PROVIDER=gemini   -> uses Gemini 2.5 Flash (needs GEMINI_API_KEY, free tier)
#   LLM_PROVIDER=ollama   -> uses local Ollama (needs `ollama serve` running)
#
# Default: ollama, since that's what you're running locally with no setup.
# On Streamlit Cloud, set these in the app's Settings -> Secrets panel
# instead of env vars — they get read the same way via st.secrets.


def _get_config(key: str, default: str = "") -> str:
    """Reads config from Streamlit secrets first (for cloud deployment),
    falling back to environment variables (for local runs). Streamlit
    secrets aren't available unless running inside `streamlit run`, so
    this degrades gracefully when imported elsewhere (e.g. the Flask app
    or the CLI test block at the bottom of this file)."""
    try:
        import streamlit as st
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.environ.get(key, default)


PROVIDER = _get_config("LLM_PROVIDER", "ollama").lower()

OLLAMA_MODEL = _get_config("OLLAMA_MODEL", "qwen2.5-coder:7b")
OLLAMA_HOST = _get_config("OLLAMA_HOST", "http://localhost:11434")

if PROVIDER == "gemini":
    from google import genai
    from google.genai import types
    _gemini_client = genai.Client(api_key=_get_config("GEMINI_API_KEY"))
    GEMINI_MODEL = "gemini-2.5-flash-lite"


def _call_gemini(system_prompt: str, user_message: str, max_tokens: int) -> str:
    """Calls Gemini with automatic retry on transient errors:
    - 429 (rate limit, free tier allows only a handful of requests/minute)
    - 503 (Google's servers temporarily overloaded — unrelated to your quota)
    Both are worth waiting out and retrying rather than failing the whole
    council on a blip."""
    import time

    max_retries = 4
    for attempt in range(max_retries):
        try:
            response = _gemini_client.models.generate_content(
                model=GEMINI_MODEL,
                contents=user_message,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=max_tokens,
                ),
            )
            return response.text or ""
        except Exception as e:
            err_str = str(e)
            is_rate_limit = "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
            is_overloaded = "503" in err_str or "UNAVAILABLE" in err_str
            if (is_rate_limit or is_overloaded) and attempt < max_retries - 1:
                # Rate limits need a longer wait than a server blip does.
                wait = (20 * (attempt + 1)) if is_rate_limit else (5 * (attempt + 1))
                time.sleep(wait)
                continue
            raise


def _call_ollama(system_prompt: str, user_message: str, max_tokens: int) -> str:
    """Calls a local Ollama server via its REST API (no SDK needed —
    Ollama just speaks plain HTTP on localhost:11434 by default)."""
    response = requests.post(
        f"{OLLAMA_HOST}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens},
        },
        timeout=120,
    )
    response.raise_for_status()
    return response.json()["message"]["content"]


def call_claude(system_prompt: str, user_message: str, max_tokens: int = 1500) -> str:
    """A single call to an 'agent'. Each agent is just an LLM with a
    different system prompt — that's it. This function is the building
    block everything else is made of.

    (Kept the name call_claude so the rest of the orchestration logic below
    doesn't need to change — swap providers via LLM_PROVIDER env var.)
    """
    if PROVIDER == "gemini":
        return _call_gemini(system_prompt, user_message, max_tokens)
    elif PROVIDER == "ollama":
        return _call_ollama(system_prompt, user_message, max_tokens)
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {PROVIDER}. Use 'ollama' or 'gemini'.")


# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------
# Each agent is defined by: an id, a display name, and a system prompt that
# narrows its focus to one concern. This is the entire trick to specializing
# agents — no fine-tuning, no separate models, just a tightly scoped prompt.

AGENTS = [
    {
        "id": "security",
        "name": "Security Agent",
        "system_prompt": """You are the Security Agent in a code review council. Your ONLY job is finding security vulnerabilities.

Check for: SQL injection, XSS, CSRF, insecure deserialization, hardcoded secrets,
auth/authorization flaws, missing input validation, dangerous eval/exec usage,
path traversal, race conditions, insecure dependencies, and similar issues.

Format your response EXACTLY as follows, in this order:
1. First line, exactly: SECURITY_SCORE: X/10
2. Second line: one-sentence verdict (e.g. "This code has 2 critical vulnerabilities.")
3. Then a bulleted list of specific issues found, each with: what the issue is,
   why it's dangerous, and a concrete fix. If there are no issues, say so.

Keep your full response under 250 words. Be concise but specific. Reference exact
lines or patterns from the code. The score line MUST be the very first line, before
anything else, no exceptions.""",
    },
    {
        "id": "performance",
        "name": "Performance Agent",
        "system_prompt": """You are the Performance Agent in a code review council. Your ONLY job is finding performance bottlenecks.

Check for: N+1 queries, O(n^2) or worse algorithms where better exists, missing
indexes/caching, unnecessary re-computation, blocking I/O on hot paths, memory
leaks, excessive allocations, inefficient data structures, and similar issues.

Format your response EXACTLY as follows, in this order:
1. First line, exactly: PERFORMANCE_SCORE: X/10
2. Second line: one-sentence verdict.
3. Then a bulleted list of specific bottlenecks found, each with: what the issue
   is, its cost (e.g. time/space complexity, or real-world impact), and a concrete
   fix. If there are no issues, say so.

Keep your full response under 250 words. Be concise but specific. Reference exact
lines or patterns from the code. The score line MUST be the very first line, before
anything else, no exceptions.""",
    },
    {
        "id": "readability",
        "name": "Readability Agent",
        "system_prompt": """You are the Readability Agent in a code review council. Your ONLY job is evaluating code clarity and maintainability.

Check for: unclear naming, missing/poor error handling, poor structure, long
functions doing too much, magic numbers, missing comments on complex logic,
tight coupling, missing type hints/annotations, inconsistent style.

Format your response EXACTLY as follows, in this order:
1. First line, exactly: READABILITY_SCORE: X/10
2. Second line: one-sentence verdict.
3. Then a bulleted list of specific issues found, each with: what the issue is, why
   it hurts maintainability, and a concrete fix. If there are no issues, say so.

Keep your full response under 250 words. Be concise but specific. Reference exact
lines or patterns from the code. The score line MUST be the very first line, before
anything else, no exceptions.""",
    },
]

# The chat manager's job: read everything the agents said (the accumulated
# thread) and produce a synthesis. This is the "Group chat manager" box in
# your diagram — it doesn't review code itself, it manages the conversation.

MANAGER_SYSTEM_PROMPT = """You are the Chat Manager of a Code Review Council.
Three specialist agents (Security, Performance, Readability) have each
independently reviewed the same code. You did not review the code yourself —
your job is to manage the discussion and produce a final consensus.

Given their three reports, do the following:
1. Note where the agents agree (1-2 sentences).
2. Call out ONE real tension or trade-off between agents, if one exists
   (e.g. a security fix that could hurt performance, or a performance
   optimization that hurts readability). If there's truly no tension, say so.
3. Produce a prioritized action list: the top 3 issues to fix first, ranked
   by severity/impact across all three domains, regardless of which agent
   raised them.

Keep your entire response under 200 words. Be decisive — you are closing
the debate, not reopening it."""


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_council(code: str, language: str = "") -> dict:
    """Runs the full group chat orchestration:

        1. Each agent reviews the code independently (their findings become
           part of the accumulating thread).
        2. The chat manager reads the full thread and produces a consensus.

    Returns a dict containing the full thread (for transparency/auditability,
    as your diagram calls out) plus the final consensus result.
    """

    lang_note = f" The language is {language}." if language else ""
    user_message = f"Review this code:{lang_note}\n\n```\n{code}\n```"

    # --- Step 1: accumulating chat thread -----------------------------
    # This list IS the "Accumulating chat thread" box in your diagram.
    # Every agent's output gets appended to it.
    thread = []

    agent_reports = {}
    for i, agent in enumerate(AGENTS):
        # Gemini's free tier allows only 5 requests/minute — space calls
        # out a little so a normal review doesn't trip the limit.
        if PROVIDER == "gemini" and i > 0:
            import time
            time.sleep(5)
        output = call_claude(agent["system_prompt"], user_message)
        agent_reports[agent["id"]] = output
        thread.append({"speaker": agent["name"], "role": agent["id"], "content": output})

    # --- Step 2: chat manager synthesizes the thread -------------------
    # The manager only sees what's in the thread, never the raw code review
    # task itself — it's purely managing the conversation, just like in
    # your diagram where the manager sits between agents and the result.
    debate_context = "\n\n---\n\n".join(
        f"{agent['name']}:\n{agent_reports[agent['id']]}" for agent in AGENTS
    )
    manager_input = (
        f"Here are the three agent reports for this code review:\n\n"
        f"{debate_context}\n\nSynthesize these into a final consensus."
    )
    consensus = call_claude(MANAGER_SYSTEM_PROMPT, manager_input, max_tokens=900)
    thread.append({"speaker": "Chat Manager", "role": "manager", "content": consensus})

    # --- Step 3: extract scores for the UI -----------------------------
    scores = {
        "security": _extract_score(agent_reports["security"], "SECURITY_SCORE"),
        "performance": _extract_score(agent_reports["performance"], "PERFORMANCE_SCORE"),
        "readability": _extract_score(agent_reports["readability"], "READABILITY_SCORE"),
    }

    return {
        "thread": thread,
        "agent_reports": agent_reports,
        "consensus": consensus,
        "scores": scores,
    }


def _extract_score(text: str, keyword: str) -> str:
    """Pulls 'X/10' out of a line like 'SECURITY_SCORE: 7/10'."""
    import re
    match = re.search(rf"{keyword}:\s*(\d+)", text)
    return f"{match.group(1)}/10" if match else "—"


# ---------------------------------------------------------------------------
# Standalone CLI usage (run this file directly to test without the web app)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    sample_code = '''
def get_user(user_id):
    query = "SELECT * FROM users WHERE id = " + user_id
    result = db.execute(query)
    return result
'''
    result = run_council(sample_code, language="Python")

    print("\n=== AGENT REPORTS ===\n")
    for msg in result["thread"]:
        print(f"--- {msg['speaker']} ---")
        print(msg["content"])
        print()

    print("=== SCORES ===")
    print(result["scores"])