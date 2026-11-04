#!/usr/bin/env python3
"""Local browser interface for the fixed-order equity research desk."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import streamlit as st


ROOT = Path(__file__).resolve().parent
NODE_PATTERN = re.compile(
    r"^(?:Step [1-7]|Research Analyst|Financial Analyst|Investment Advisor|"
    r"Strategy Manager|Permitted rework|Verifier) starting"
)


def peers_from_text(value: str) -> list[str]:
    """Accept comma- or space-separated peer ticker symbols."""
    peers = [item.upper() for item in re.split(r"[\s,]+", value.strip()) if item]
    invalid = [item for item in peers if not re.fullmatch(r"[A-Z.]{1,10}", item)]
    if invalid:
        raise ValueError("Use ticker symbols of up to 10 letters, with an optional period.")
    return peers


def report_for_page(report: str) -> str:
    """Hide verifier metadata that a browser Markdown renderer can misread.

    The saved report keeps the HTML comments for machine verification. The
    page already shows each citation in the Verification Marks table, so the
    reader does not need those hidden records in the rendered prose.
    """
    return re.sub(r"\s*<!--\s*\[source:.*?\]\s*-->", "", report, flags=re.I)


def run_team(ticker: str, peers: list[str], price: float | None) -> tuple[int, str, Path | None]:
    """Run the unchanged command and return its terminal log and report path."""
    command = [sys.executable, "-u", str(ROOT / "agent_team.py"), ticker]
    if peers:
        command.extend(["--peers", ",".join(peers)])
    if price is not None:
        command.extend(["--price", str(price)])

    log: list[str] = []
    report_path: Path | None = None
    status = st.empty()
    terminal = st.empty()
    completed = st.progress(0, text="Starting the agent team")
    stages: list[str] = []

    with subprocess.Popen(
        command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    ) as process:
        assert process.stdout is not None
        for raw_line in process.stdout:
            line = raw_line.rstrip()
            log.append(line)
            if NODE_PATTERN.match(line):
                stages.append(line.removesuffix(" starting..."))
                status.info(f"Running: {stages[-1]}")
                completed.progress(min(len(stages) * 7, 98), text=f"Running: {stages[-1]}")
            if line.startswith("Verified report:"):
                report_path = Path(line.removeprefix("Verified report:").strip())
            terminal.code("\n".join(log[-80:]), language="text")
        exit_code = process.wait()

    if exit_code == 0 and report_path and report_path.exists():
        completed.progress(100, text="Verified report complete")
        status.success("The Verifier completed.")
    else:
        completed.empty()
        status.error("The run did not complete. Copy the terminal output below for debugging.")
    return exit_code, "\n".join(log), report_path


st.set_page_config(page_title="Equity Research Desk", page_icon="📈", layout="wide")
st.title("Equity Research Desk")
st.caption("Enter a ticker, optional peer tickers, and an optional share price. The Verifier always runs.")

with st.form("run_desk"):
    ticker = st.text_input("Ticker", placeholder="AAPL", max_chars=10).strip().upper()
    peers_text = st.text_input("Peers (optional)", placeholder="MSFT, AMZN, META")
    price_text = st.text_input("Share price (optional)", placeholder="354.02")
    submitted = st.form_submit_button("Run agent team", type="primary")

if submitted:
    try:
        if not re.fullmatch(r"[A-Z.]{1,10}", ticker):
            raise ValueError("Enter one valid ticker symbol.")
        peers = peers_from_text(peers_text)
        price = float(price_text) if price_text.strip() else None
        if price is not None and price <= 0:
            raise ValueError("Share price must be greater than zero.")
    except ValueError as error:
        st.error(str(error))
    else:
        st.subheader("Run progress")
        exit_code, log, report_path = run_team(ticker, peers, price)
        st.session_state["last_log"] = log
        if exit_code == 0 and report_path:
            st.session_state["last_report"] = str(report_path)

report = Path(st.session_state["last_report"]) if st.session_state.get("last_report") else None
if report and report.exists():
    st.divider()
    st.subheader("Verified report")
    st.caption(str(report))
    st.markdown(report_for_page(report.read_text(encoding="utf-8")), unsafe_allow_html=False)
