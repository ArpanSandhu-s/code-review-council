"""
orchestrator.py
Code Review Council — stateful multi-agent orchestration layer, built
entirely on LangChain primitives. Zero UI dependencies; streamlit_app.py
only ever calls run_council() and reads the returned dict.

MENTOR-REVIEW FOLLOW-UP (this revision addresses 5 issues raised in review):

  1. FAILURE DETECTION IS NO LONGER STRING-BASED. Previously, an agent
     failure was encoded as a magic substring inside the output text
     itself, and detected via `"EMPTY SEAT..." in text`. That's fragile -
     a legitimate finding that happened to discuss "technical failure"
     could be misread as a broken seat. Failure is now tracked in a
     dedicated `failure_tracker` dict (agent_key -> bool), populated
     directly by `_invoke_agent_safely` and never inferred from text.

  2. THE DEBATE PHASE (PASS 2) IS NOW CONFIGURABLE. Two-pass debate
     roughly doubles LLM call volume (3 -> 6 specialist calls per
     session), which matters a lot on a rate-limited free tier. Set
     COUNCIL_ENABLE_DEBATE=false to skip Pass 2 entirely and run
     single-pass (only Pass 1 drafts go straight to the Chat Manager).
     Defaults to enabled (true).

  3. PASS 1 -> PASS 2 SCORE DELTAS ARE NOW SURFACED IN THE RETURN VALUE.
     `draft_scores`, `final_scores` (== `scores`, kept for compatibility)
     and `score_deltas` (signed int per agent, or None if not comparable)
     are all returned, so the UI can show "Performance: 6 -> 8 (+2, changed
     its mind after Pass 2)" instead of hiding that entirely inside the
     Chat Manager's prose.

  4. get_model() IS NOW CACHED. Previously every specialist call (Pass 1,
     Pass 2) and the Chat Manager rebuilt a fresh model + retry +
     fallback wrapper from scratch - 7 rebuilds per session. It's now
     wrapped in functools.lru_cache(maxsize=1), since PROVIDER is fixed
     for the process lifetime. LangChain Runnables are safe to share and
     invoke concurrently from multiple threads (no mutable per-call
     state), so this is safe under RunnableParallel.

  5. SCORE-PARSE FAILURES ARE NOW LOGGED. If a model doesn't follow the
     'KEY_SCORE: X/10' format, `_extract_score` used to silently return
     "—" with no trace. It now logs a warning via the standard `logging`
     module so this is visible in server logs instead of being
     indistinguishable from "the report just doesn't have a score".

RETURN SHAPE (all ADDITIVE beyond the original 4 keys - existing UI code
reading "status"/"scores"/"agent_reports"/"consensus" is unaffected):
  {
    "status":          "ok" | "rejected",
    "scores":          {"security": "N/10", ...}   # == final_scores
    "agent_reports":   {"security": "...", ...}    # == final report text
    "consensus":       "<Chat Manager's final synthesis text>",
    "draft_scores":    {"security": "N/10", ...}   # Pass 1 scores
    "final_scores":    {"security": "N/10", ...}   # alias of "scores"
    "score_deltas":    {"security": 2, ...}         # final - draft, or None
    "debate_enabled":  bool,
    "failed_agents":   ["security", ...]            # empty list if none
  }
"""

import os
import re
import time
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Optional

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableParallel, RunnableLambda
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Provider / model construction
# --------------------------------------------------------------------------

PROVIDER = os.environ.get("COUNCIL_PROVIDER") or os.environ.get("LLM_PROVIDER", "gemini")
# COUNCIL_PROVIDER is the canonical name going forward. LLM_PROVIDER is
# honored as a fallback ONLY for backward compatibility with the old
# README/setup instructions that predate the LangChain rewrite - if
# you're setting this fresh, use COUNCIL_PROVIDER.

# FIX 2: debate phase toggle. Defaults to enabled.
ENABLE_DEBATE = os.environ.get("COUNCIL_ENABLE_DEBATE", "true").strip().lower() not in (
    "false", "0", "no", "off",
)

_OLLAMA_MODEL_NAME = os.environ.get("OLLAMA_MODEL", "qwen2.5-coder")
_OLLAMA_BASE_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
_GEMINI_MODEL_NAME = "gemini-2.5-flash-lite"


def _resolve_gemini_api_key() -> Optional[str]:
    """Check both common env var names, plus Streamlit secrets if
    available. Returns None (rather than raising) so callers can decide
    whether to fall back to local-only mode instead of hard-crashing."""
    key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not key:
        try:
            import streamlit as st
            key = st.secrets.get("GOOGLE_API_KEY") or st.secrets.get("GEMINI_API_KEY")
        except Exception:
            pass
    return key


def _make_ollama_runnable():
    """Local backend, LangChain-native, with its own retry (transient
    local-server hiccups only)."""
    return ChatOllama(model=_OLLAMA_MODEL_NAME, base_url=_OLLAMA_BASE_URL).with_retry(
        stop_after_attempt=3, wait_exponential_jitter=True
    )


# FIX 4: cache the built model/chain. PROVIDER and the API key are fixed
# for the lifetime of the process, so there's no reason to reconstruct
# ChatGoogleGenerativeAI + retry + fallback wrappers on every single one
# of the 7 calls in a council session. LangChain Runnables carry no
# mutable per-invocation state, so sharing one cached instance across
# concurrent RunnableParallel branches (different threads) is safe.
@lru_cache(maxsize=1)
def get_model():
    """
    Gemini is PRIMARY (retried), with automatic LangChain-native fallback
    to local Ollama via .with_fallbacks() if Gemini fails after its own
    retries (quota, 503, network, anything). If no Gemini key is
    configured at all, skip straight to Ollama-only. Result is cached -
    call get_model.cache_clear() if you need to force a rebuild (e.g. in
    tests that swap env vars).
    """
    if PROVIDER == "ollama":
        return _make_ollama_runnable()

    api_key = _resolve_gemini_api_key()
    if not api_key:
        return _make_ollama_runnable()

    primary = ChatGoogleGenerativeAI(
        model=_GEMINI_MODEL_NAME,
        google_api_key=api_key,
    ).with_retry(stop_after_attempt=2, wait_exponential_jitter=True)

    fallback = _make_ollama_runnable()
    return primary.with_fallbacks([fallback])


# --------------------------------------------------------------------------
# Input validation / cost guardrails
# --------------------------------------------------------------------------

MAX_CODE_CHARS = 15_000  # safety threshold; not a real tokenizer count


def _check_payload_size(code: str) -> Optional[dict]:
    """Runs BEFORE any LLM call. Returns a rejection dict (matching the
    normal return schema so the UI never KeyErrors) if the payload is too
    large, or None if it's safe to proceed."""
    if len(code) > MAX_CODE_CHARS:
        message = "Payload too large for Council evaluation. Please truncate your snippet."
        placeholder_scores = {key: "—" for key in AGENT_ORDER}
        return {
            "status": "rejected",
            "message": message,
            "scores": placeholder_scores,
            "agent_reports": {key: "" for key in AGENT_ORDER},
            "consensus": message,
            "draft_scores": placeholder_scores,
            "final_scores": placeholder_scores,
            "score_deltas": {key: None for key in AGENT_ORDER},
            "debate_enabled": ENABLE_DEBATE,
            "failed_agents": [],
        }
    return None


# --------------------------------------------------------------------------
# Agent definitions
# --------------------------------------------------------------------------

AGENT_META = {
    "security": {
        "title": "Security Agent",
        "system": (
            "You are the Security reviewer on a code review council. "
            "Output your numeric score (0-10) as the VERY FIRST LINE in the "
            "format 'SECURITY_SCORE: X/10', then your findings. Focus strictly "
            "on vulnerabilities such as SQL injection, XSS, unsafe "
            "deserialization, secrets handling, and injection risks."
        ),
    },
    "performance": {
        "title": "Performance Agent",
        "system": (
            "You are the Performance reviewer on a code review council. "
            "Output your numeric score (0-10) as the VERY FIRST LINE in the "
            "format 'PERFORMANCE_SCORE: X/10', then your findings. Focus "
            "strictly on bottlenecks such as N+1 queries, poor time "
            "complexity, unnecessary allocations, and blocking I/O."
        ),
    },
    "readability": {
        "title": "Readability Agent",
        "system": (
            "You are the Readability reviewer on a code review council. "
            "Output your numeric score (0-10) as the VERY FIRST LINE in the "
            "format 'READABILITY_SCORE: X/10', then your findings. Focus "
            "strictly on code smells, naming conventions, structure, and style."
        ),
    },
}

CHAT_MANAGER_SYSTEM = (
    "You are the Chat Manager / Judge for a code review council. You will "
    "be given the findings thread from the Security, Performance, and "
    "Readability agents - either a single Pass 1 draft round, or a full "
    "two-pass debate (Pass 1 drafts plus Pass 2 finalized findings), "
    "depending on configuration; adapt to whichever you're given. "
    "Synthesize a single unified consensus report: note where agents "
    "agree, flag any conflicts between their scores, note anywhere an "
    "agent changed its score between passes (if two-pass data is "
    "present), and produce one final overall score (0-10) as the first "
    "line in the format 'FINAL SCORE: X/10'. If any specialist seat is "
    "marked EMPTY DUE TO TECHNICAL FAILURE, explicitly acknowledge that "
    "in your ruling and base your consensus only on the remaining active "
    "agents. You may also be shown up to the last 5 turns of this "
    "session's prior council rulings as context - use that only to note "
    "recurring patterns or consistency with past submissions if "
    "relevant; do not let it override your independent judgment of the "
    "CURRENT code exhibit."
)

AGENT_ORDER = ["security", "performance", "readability"]

# --------------------------------------------------------------------------
# Chat history trimming (for the Chat Manager's MessagesPlaceholder)
# --------------------------------------------------------------------------

MAX_HISTORY_MESSAGES = 10  # 5 user/assistant turns


def _trim_chat_history(chat_history: Optional[list]) -> list:
    """Keeps only the most recent MAX_HISTORY_MESSAGES messages (5 turns).
    Accepts a list of LangChain BaseMessage objects or None/empty, and
    always returns a list - safe to pass straight into a
    MessagesPlaceholder."""
    if not chat_history:
        return []
    return list(chat_history[-MAX_HISTORY_MESSAGES:])


# --------------------------------------------------------------------------
# FIX 1: typed failure tracking (no more substring-based detection)
# --------------------------------------------------------------------------

@dataclass
class AgentResult:
    agent_name: str
    role_label: str
    output: str
    duration_seconds: float
    failed: bool = False


def _invoke_agent_safely(chain, inputs: dict, meta: dict, role_label: str,
                          on_agent_complete: Optional[Callable[[AgentResult], None]]) -> tuple[str, bool]:
    """Runs one agent's chain. ANY exception - Gemini failing, its Ollama
    fallback also failing, whatever - is caught here so it can never
    propagate up and kill RunnableParallel's other branches.

    Returns (output_text, failed) as an explicit tuple - failure state is
    NEVER encoded inside the text itself, so downstream logic can't be
    fooled by a legitimate report that happens to mention "failure"."""
    start = time.time()
    failed = False
    try:
        output = chain.invoke(inputs)
    except Exception as e:
        failed = True
        output = f"[Technical failure - this seat is empty: {type(e).__name__}: {e}]"
        logger.warning("Agent '%s' failed: %s: %s", role_label, type(e).__name__, e)
    duration = time.time() - start
    if on_agent_complete:
        on_agent_complete(AgentResult(
            agent_name=meta["title"],
            role_label=role_label,
            output=output,
            duration_seconds=duration,
            failed=failed,
        ))
    return output, failed


def _extract_score(text: str, agent_key: str, failed: bool = False) -> str:
    """Pulls 'N/10' out of a leading 'AGENTKEY_SCORE: N/10' line. Returns
    '—' for known-failed agents (no attempt to parse failure text), or if
    the model just didn't follow the expected format - and LOGS a warning
    in the latter case (FIX 5), since a silent '—' used to be
    indistinguishable from an intentional failure."""
    if failed:
        return "—"
    match = re.search(rf"{agent_key.upper()}_SCORE:\s*(\d+)\s*/\s*10", text)
    if not match:
        logger.warning(
            "Could not parse '%s_SCORE: N/10' from agent output - model may "
            "have deviated from the required format. First 120 chars: %r",
            agent_key.upper(), text[:120],
        )
        return "—"
    return f"{match.group(1)}/10"


def _score_to_int(score_str: str) -> Optional[int]:
    if score_str == "—":
        return None
    try:
        return int(score_str.split("/")[0])
    except (ValueError, IndexError):
        return None


# --------------------------------------------------------------------------
# Pass 1 (drafting) chain builders
# --------------------------------------------------------------------------

def _build_pass1_runnable(agent_key: str,
                           on_agent_complete: Optional[Callable[[AgentResult], None]],
                           failure_tracker: dict):
    """Pass 1: each specialist evaluates the code in isolation - identical
    inputs shared across all three branches, dispatched concurrently by
    RunnableParallel. Writes its own success/failure into failure_tracker
    (keyed by agent_key - safe under concurrent threads since each branch
    only ever writes its own key)."""
    meta = AGENT_META[agent_key]
    prompt = ChatPromptTemplate.from_messages([
        ("system", meta["system"]),
        ("human", "Language: {language}\n\nCode to review:\n```{language}\n{code}\n```"),
    ])
    chain = prompt | get_model() | StrOutputParser()

    def _invoke(inputs: dict) -> str:
        text, failed = _invoke_agent_safely(chain, inputs, meta, agent_key.upper(), on_agent_complete)
        failure_tracker[agent_key] = failed
        return text

    return RunnableLambda(_invoke)


# --------------------------------------------------------------------------
# Pass 2 (cross-review / debate) chain builders
# --------------------------------------------------------------------------

def _build_pass2_prompt(agent_key: str) -> ChatPromptTemplate:
    meta = AGENT_META[agent_key]
    system = meta["system"] + (
        "\n\nThis is PASS 2 - the Cross-Review phase. You already produced "
        "a draft in Pass 1. Now read your peers' Pass 1 drafts below. "
        "Adjust your score if they caught a structural issue you missed, "
        "or defend your original score if you disagree. Output your "
        "FINALIZED score as the VERY FIRST LINE in the same "
        f"'{agent_key.upper()}_SCORE: X/10' format, then your finalized report."
    )
    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("human",
         "Language: {language}\n\nCode under review:\n```{language}\n{code}\n```\n\n"
         "Your own PASS 1 draft:\n{own_draft}\n\n"
         "Your peers' PASS 1 drafts:\n{peer_drafts}"),
    ])


def _build_pass2_runnable(agent_key: str, fixed_inputs: dict,
                           on_agent_complete: Optional[Callable[[AgentResult], None]],
                           failure_tracker: dict):
    """Pass 2 branches need DIFFERENT inputs per agent (each one's own
    draft differs), but RunnableParallel.invoke() passes the SAME shared
    input to every branch. Fix: each branch's own inputs are captured as a
    default argument (avoiding Python's late-binding closure bug in a
    loop), and the branch ignores whatever shared input RunnableParallel
    actually passes it."""
    meta = AGENT_META[agent_key]
    chain = _build_pass2_prompt(agent_key) | get_model() | StrOutputParser()

    def _invoke(_shared_input: dict, _fixed=fixed_inputs) -> str:
        text, failed = _invoke_agent_safely(chain, _fixed, meta, agent_key.upper(), on_agent_complete)
        failure_tracker[agent_key] = failed
        return text

    return RunnableLambda(_invoke)


# --------------------------------------------------------------------------
# Main orchestration entry point
# --------------------------------------------------------------------------

def run_council(
    code: str,
    language: str = "",
    on_agent_complete: Optional[Callable[[AgentResult], None]] = None,
    chat_history: Optional[list] = None,
) -> dict:
    """
    Fault-tolerant, guardrail-protected council session. Runs a two-pass
    debate by default (Pass 1 drafts -> Pass 2 cross-review), or
    single-pass if COUNCIL_ENABLE_DEBATE=false.

    Args:
        code: raw source code string to review.
        language: language selection, or "" for auto-detect / unspecified.
        on_agent_complete: optional per-agent progress callback.
        chat_history: optional list of LangChain BaseMessage objects
            (HumanMessage/AIMessage) - only the last 10 (5 turns) are
            actually sent to the Chat Manager.

    Returns:
        See module docstring for the full return shape.
    """
    # --- Guardrail: short-circuits before any LLM call ---
    rejection = _check_payload_size(code)
    if rejection:
        return rejection

    lang_for_prompt = language or "unspecified (auto-detect)"
    base_inputs = {"language": lang_for_prompt, "code": code}

    # failure_tracker: agent_key -> bool. Populated directly by each
    # agent's own invocation - never inferred from output text (FIX 1).
    failure_tracker: dict = {}

    # --- Pass 1: draft findings, concurrent, fault-isolated per branch ---
    pass1_chain = RunnableParallel(**{
        key: _build_pass1_runnable(key, on_agent_complete, failure_tracker) for key in AGENT_ORDER
    })
    draft_reports: dict = pass1_chain.invoke(base_inputs)

    draft_scores = {
        key: _extract_score(draft_reports[key], key, failed=failure_tracker.get(key, False))
        for key in AGENT_ORDER
    }

    succeeded_keys = [k for k in AGENT_ORDER if not failure_tracker.get(k, False)]

    # --- Pass 2: cross-review / debate - configurable, only for agents with a draft to defend ---
    final_reports: dict = dict(draft_reports)  # failed seats stay failed, unchanged
    debate_ran = False

    if ENABLE_DEBATE and succeeded_keys:
        debate_ran = True
        pass2_branches = {}
        for key in succeeded_keys:
            peer_drafts_text = "\n\n".join(
                f"[{AGENT_META[other]['title']} DRAFT]\n{draft_reports[other]}"
                for other in AGENT_ORDER
                if other != key and other in succeeded_keys
            ) or "(No peer drafts available - all other seats failed in Pass 1.)"

            fixed_inputs = {
                **base_inputs,
                "own_draft": draft_reports[key],
                "peer_drafts": peer_drafts_text,
            }
            pass2_branches[key] = _build_pass2_runnable(key, fixed_inputs, on_agent_complete, failure_tracker)

        pass2_chain = RunnableParallel(**pass2_branches)
        # Shared input is ignored by every branch (each carries its own
        # fixed_inputs captured at build time) - {} is just a placeholder.
        pass2_results: dict = pass2_chain.invoke({})

        for key in succeeded_keys:
            # A seat can still fail freshly in Pass 2 even if Pass 1 succeeded
            # (e.g. transient outage on the second round) - failure_tracker
            # gets overwritten with the up-to-date status for that key.
            final_reports[key] = pass2_results[key]

    failed_keys = [k for k in AGENT_ORDER if failure_tracker.get(k, False)]

    final_scores = {
        key: _extract_score(final_reports[key], key, failed=failure_tracker.get(key, False))
        for key in AGENT_ORDER
    }

    # FIX 3: Pass 1 -> Pass 2 score deltas, surfaced for the UI.
    score_deltas: dict = {}
    for key in AGENT_ORDER:
        d, f = _score_to_int(draft_scores[key]), _score_to_int(final_scores[key])
        score_deltas[key] = (f - d) if (d is not None and f is not None) else None

    # --- Chat Manager payload explicitly flags empty seats ---
    thread_sections = []
    if failed_keys:
        empty_titles = ", ".join(AGENT_META[k]["title"] for k in failed_keys)
        thread_sections.append(
            f"NOTE: The following specialist seat(s) are EMPTY due to "
            f"technical failure and contain no usable findings: "
            f"{empty_titles}. Base your consensus only on the remaining "
            f"active agents."
        )

    for key in AGENT_ORDER:
        title = AGENT_META[key]["title"]
        if key in failed_keys:
            thread_sections.append(f"[{title} - SEAT EMPTY]\n{final_reports[key]}")
        elif debate_ran:
            thread_sections.append(
                f"[{title} - PASS 1 DRAFT]\n{draft_reports[key]}\n\n"
                f"[{title} - PASS 2 FINALIZED]\n{final_reports[key]}"
            )
        else:
            thread_sections.append(f"[{title} - FINDINGS (single-pass mode)]\n{final_reports[key]}")

    combined_thread_text = "\n\n---\n\n".join(thread_sections)

    # --- Chat Manager: hard sync point, runs only after Pass 2 (or Pass 1
    # if debate is disabled) resolves. Trimmed chat_history gives the
    # Judge continuity across prior council sessions via MessagesPlaceholder.
    trimmed_history = _trim_chat_history(chat_history)

    manager_chain = (
        ChatPromptTemplate.from_messages([
            ("system", CHAT_MANAGER_SYSTEM),
            MessagesPlaceholder("chat_history"),
            ("human", "{thread}"),
        ])
        | get_model()
        | StrOutputParser()
    )
    try:
        consensus = manager_chain.invoke({
            "thread": combined_thread_text,
            "chat_history": trimmed_history,
        })
    except Exception as e:
        logger.warning("Chat Manager failed: %s: %s", type(e).__name__, e)
        consensus = (
            f"[COUNCIL RULING UNAVAILABLE - Chat Manager failed: "
            f"{type(e).__name__}: {e}]\n\nRaw findings below:\n\n{combined_thread_text}"
        )

    return {
        "status": "ok",
        "scores": final_scores,
        "agent_reports": final_reports,
        "consensus": consensus,
        "draft_scores": draft_scores,
        "final_scores": final_scores,
        "score_deltas": score_deltas,
        "debate_enabled": debate_ran,
        "failed_agents": failed_keys,
    }