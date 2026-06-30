"""
Code Review Council — Streamlit App (Docket UI)
==================================================

Run with:
    streamlit run streamlit_app.py

Same orchestrator.py underneath — this file is purely the frontend, styled
as a case docket: code goes in as "Exhibit A," each agent files a verdict
with a stamped score, and the chat manager hands down a final ruling.
"""

import streamlit as st
from orchestrator import run_council

st.set_page_config(
    page_title="Code Review Council",
    page_icon="⚖️",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Global styling
# ---------------------------------------------------------------------------
st.markdown("""
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">

<style>
:root {
    --ink: #1c1a14;
    --ink-soft: #4a473d;
    --paper: #f4efe3;
    --paper-raised: #fbf8f1;
    --rule: #d8d0bc;
    --rule-strong: #b8ad8f;
    --accent: #b5651d;

    --security: #9b3a2e;
    --security-tint: #f3e3df;
    --performance: #95650a;
    --performance-tint: #f4ead2;
    --readability: #2c5d63;
    --readability-tint: #e1ecec;
    --verdict: #3d5c3a;
    --verdict-tint: #e6ebe0;
}

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background: var(--paper);
}

#MainMenu, footer, header { visibility: hidden; }

.block-container {
    max-width: 900px;
    padding-top: 2.5rem;
}

/* ---------- Masthead ---------- */
.masthead {
    border-bottom: 3px double var(--ink);
    padding-bottom: 16px;
    margin-bottom: 4px;
}
.masthead-eyebrow {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ink-soft);
    margin-bottom: 6px;
}
.masthead-title {
    font-family: 'Fraunces', serif;
    font-size: 38px;
    font-weight: 600;
    color: var(--ink);
    letter-spacing: -0.01em;
    line-height: 1.05;
    margin: 0;
}
.masthead-sub {
    font-size: 14.5px;
    color: var(--ink-soft);
    margin-top: 8px;
    max-width: 540px;
    line-height: 1.55;
}

/* ---------- Exhibit (code input) ---------- */
.exhibit-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11.5px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ink-soft);
    margin: 28px 0 8px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.exhibit-label::after {
    content: "";
    flex: 1;
    border-bottom: 1px solid var(--rule);
}

div[data-testid="stTextArea"] textarea {
    background: var(--paper-raised) !important;
    border: 1px solid var(--rule-strong) !important;
    border-radius: 2px !important;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 13px !important;
    color: var(--ink) !important;
    line-height: 1.7 !important;
}
div[data-testid="stTextArea"] textarea:focus {
    border-color: var(--accent) !important;
    box-shadow: 0 0 0 1px var(--accent) !important;
}

div[data-testid="stSelectbox"] label {
    color: var(--ink-soft) !important;
    font-size: 11.5px !important;
    text-transform: uppercase !important;
    letter-spacing: 0.06em !important;
    font-family: 'JetBrains Mono', monospace !important;
}
div[data-testid="stSelectbox"] > div > div {
    background: var(--paper-raised) !important;
    border: 1px solid var(--rule-strong) !important;
    border-radius: 2px !important;
}
div[data-testid="stSelectbox"] * {
    color: var(--ink) !important;
    font-family: 'Inter', sans-serif !important;
}
div[data-testid="stSelectbox"] svg {
    fill: var(--ink-soft) !important;
}
ul[data-testid="stSelectboxVirtualDropdown"] {
    background: var(--paper-raised) !important;
}
ul[data-testid="stSelectboxVirtualDropdown"] li,
ul[data-testid="stSelectboxVirtualDropdown"] li * {
    color: var(--ink) !important;
}
ul[data-testid="stSelectboxVirtualDropdown"] li:hover {
    background: var(--rule) !important;
}

.stButton button {
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 12.5px !important;
    letter-spacing: 0.06em !important;
    text-transform: uppercase !important;
    background: var(--ink) !important;
    color: var(--paper) !important;
    border: none !important;
    border-radius: 2px !important;
    padding: 10px 0 !important;
    font-weight: 500 !important;
    transition: background 0.15s ease;
}
.stButton button:hover { background: var(--accent) !important; color: #fff !important; }
.stButton button:disabled { opacity: 0.5 !important; }

/* ---------- Docket entries (agent cards) ---------- */
.docket-head {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11.5px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--ink-soft);
    margin: 36px 0 14px;
    display: flex;
    align-items: center;
    gap: 8px;
}
.docket-head::after {
    content: "";
    flex: 1;
    border-bottom: 1px solid var(--rule);
}

.entry {
    background: var(--paper-raised);
    border: 1px solid var(--rule);
    border-left: 3px solid var(--entry-color, var(--ink));
    border-radius: 2px;
    padding: 18px 20px 16px;
    margin-bottom: 14px;
    position: relative;
}

.entry-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--rule);
}
.entry-title {
    font-family: 'Fraunces', serif;
    font-size: 17px;
    font-weight: 600;
    color: var(--ink);
    margin: 0 0 2px;
}
.entry-role {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--ink-soft);
}

.stamp {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    border: 1.5px solid var(--stamp-color, var(--ink));
    color: var(--stamp-color, var(--ink));
    border-radius: 3px;
    padding: 5px 10px;
    transform: rotate(-2deg);
    white-space: nowrap;
    flex-shrink: 0;
    text-align: center;
}

.entry-body {
    font-size: 13.5px;
    line-height: 1.75;
    color: var(--ink);
    max-height: 380px;
    overflow-y: auto;
}
.entry-body code {
    background: var(--rule);
    padding: 1px 5px;
    border-radius: 2px;
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
}

/* ---------- Verdict (consensus) ---------- */
.verdict-box {
    background: var(--verdict-tint);
    border: 1px solid var(--verdict);
    border-radius: 2px;
    padding: 26px 28px;
    margin-top: 18px;
    position: relative;
}
.verdict-seal {
    position: absolute;
    top: 20px;
    right: 24px;
    width: 56px;
    height: 56px;
    border: 2px solid var(--verdict);
    border-radius: 50%;
    display: flex;
    align-items: center;
    justify-content: center;
    transform: rotate(8deg);
    font-family: 'Fraunces', serif;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: var(--verdict);
    text-align: center;
    line-height: 1.2;
}
.verdict-eyebrow {
    font-family: 'JetBrains Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--verdict);
    margin-bottom: 6px;
}
.verdict-title {
    font-family: 'Fraunces', serif;
    font-size: 22px;
    font-weight: 600;
    color: #1f3320;
    margin: 0 0 16px;
    max-width: 75%;
}
.verdict-body {
    font-size: 14px;
    line-height: 1.8;
    color: #233824;
    max-width: 88%;
}

.tally-row {
    display: flex;
    gap: 28px;
    margin-top: 22px;
    padding-top: 18px;
    border-top: 1px solid var(--verdict);
}
.tally-item { text-align: left; }
.tally-num {
    font-family: 'Fraunces', serif;
    font-size: 26px;
    font-weight: 600;
    color: #1f3320;
}
.tally-label {
    font-family: 'JetBrains Mono', monospace;
    font-size: 10.5px;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    color: var(--verdict);
    margin-top: 2px;
}

/* ---------- misc ---------- */
.stAlert { border-radius: 2px !important; }

div[data-testid="stSpinner"] p,
div[data-testid="stStatusWidget"] p {
    color: var(--ink) !important;
}
div[data-testid="stSpinner"] {
    color: var(--ink) !important;
}
.stMarkdown, .stMarkdown p {
    color: var(--ink);
}
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Masthead
# ---------------------------------------------------------------------------
st.markdown("""
<div class="masthead">
    <div class="masthead-eyebrow">Docket No. CRC-001 · Code review council</div>
    <h1 class="masthead-title">The council convenes</h1>
    <p class="masthead-sub">
        Submit a code exhibit. Three specialists file independent findings —
        security, performance, readability — then the chat manager hands
        down a final ruling.
    </p>
</div>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Exhibit (code input)
# ---------------------------------------------------------------------------
st.markdown('<div class="exhibit-label">Exhibit A — submitted code</div>', unsafe_allow_html=True)

default_code = '''def get_user(user_id):
    query = "SELECT * FROM users WHERE id = " + user_id
    result = db.execute(query)
    return result'''

col1, col2 = st.columns([4, 1.1])
with col1:
    code = st.text_area(
        "code",
        value=default_code,
        height=230,
        label_visibility="collapsed",
        placeholder="Paste any code here — Python, JavaScript, Go, etc.",
    )
with col2:
    language = st.selectbox(
        "Language",
        ["Auto-detect", "Python", "JavaScript", "TypeScript", "Go", "Java", "C#", "Rust", "SQL"],
    )
    st.write("")
    run_btn = st.button("Convene council", use_container_width=True)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------
if run_btn:
    if not code.strip():
        st.error("No exhibit submitted. Paste some code first.")
    else:
        lang_arg = "" if language == "Auto-detect" else language
        with st.spinner("The council is deliberating…"):
            try:
                st.session_state["result"] = run_council(code, lang_arg)
            except Exception as e:
                err_str = str(e)
                if "503" in err_str or "UNAVAILABLE" in err_str:
                    st.error("Google's servers are briefly overloaded — this isn't your quota or your code. Wait a minute and try again.")
                elif "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                    st.error("Daily free-tier quota reached. It resets at midnight Pacific time — try again then, or check usage at ai.dev/rate-limit.")
                else:
                    st.error(f"The council could not convene: {e}")
                st.session_state.pop("result", None)


# ---------------------------------------------------------------------------
# Docket entries + verdict
# ---------------------------------------------------------------------------
AGENT_META = {
    "security": {
        "title": "Security agent",
        "role": "Counsel for vulnerabilities",
        "color": "var(--security)",
    },
    "performance": {
        "title": "Performance agent",
        "role": "Counsel for efficiency",
        "color": "var(--performance)",
    },
    "readability": {
        "title": "Readability agent",
        "role": "Counsel for maintainability",
        "color": "var(--readability)",
    },
}


def stamp_for_score(score_str):
    """Returns (label, color) for the stamp based on score out of 10."""
    if score_str == "—":
        return "UNSCORED", "var(--ink-soft)"
    try:
        n = int(score_str.split("/")[0])
    except (ValueError, IndexError):
        return "UNSCORED", "var(--ink-soft)"
    if n >= 8:
        return "CLEARED", "var(--verdict)"
    if n >= 5:
        return "FLAGGED", "var(--performance)"
    return "OBJECTION", "var(--security)"


def render_body(text):
    """Light formatting: strip the leading SCORE line (already shown as
    the stamp), then escape, turn **bold** into <strong>, `code` into
    <code>, and newlines into <br>."""
    import html
    import re
    # Remove a leading "X_SCORE: N/10" line if present, plus any blank
    # line that follows it.
    text = re.sub(r"^\s*\w+_SCORE:\s*\d+/10\s*\n+", "", text)
    escaped = html.escape(text)
    escaped = escaped.replace("\n", "<br>")
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"`(.+?)`", r"<code>\1</code>", escaped)
    return escaped


if "result" in st.session_state:
    result = st.session_state["result"]

    st.markdown('<div class="docket-head">Findings filed by the council</div>', unsafe_allow_html=True)

    cols = st.columns(3)
    for col, agent_id in zip(cols, ["security", "performance", "readability"]):
        meta = AGENT_META[agent_id]
        score = result["scores"][agent_id]
        stamp_label, stamp_color = stamp_for_score(score)
        body_html = render_body(result["agent_reports"][agent_id])

        with col:
            st.markdown(f"""
            <div class="entry" style="--entry-color: {meta['color']};">
                <div class="entry-header">
                    <div>
                        <div class="entry-title">{meta['title']}</div>
                        <div class="entry-role">{meta['role']}</div>
                    </div>
                    <div class="stamp" style="--stamp-color: {stamp_color};">{stamp_label}<br>{score}</div>
                </div>
                <div class="entry-body">{body_html}</div>
            </div>
            """, unsafe_allow_html=True)

    consensus_html = render_body(result["consensus"])
    st.markdown(f"""
    <div class="verdict-box">
        <div class="verdict-seal">FINAL<br>RULING</div>
        <div class="verdict-eyebrow">Handed down by the chat manager</div>
        <div class="verdict-title">Council consensus</div>
        <div class="verdict-body">{consensus_html}</div>
        <div class="tally-row">
            <div class="tally-item">
                <div class="tally-num">{result['scores']['security']}</div>
                <div class="tally-label">Security</div>
            </div>
            <div class="tally-item">
                <div class="tally-num">{result['scores']['performance']}</div>
                <div class="tally-label">Performance</div>
            </div>
            <div class="tally-item">
                <div class="tally-num">{result['scores']['readability']}</div>
                <div class="tally-label">Readability</div>
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)