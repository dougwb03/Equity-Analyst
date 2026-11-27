# FIN 67110 — Fall 2026

## Team ownership and joins

We own a pre-registered spinoff factor check in `factor_check_plan.json`; it
uses the declared factors and 90-day holding period before results are read.
The desk uses a pipeline join: the seeker writes a registry when input is
`screen`, the agent team saves its filing run, the worksheet reads that saved
run, and the hook guards the worksheet and final report. No model selects the
order or recomputes a skill result.

Roles: Douglas Buckeridge is Orchestration and Verification Lead; Alex
Lisitsas is Screening and Factor-Check Lead; Kole Williams is Market Data,
Live Record, and Product Manual Lead. The team reviews material changes and
live reports together, discusses evidence, and contributes ideas across roles.

`agent_team.py` runs the seven deterministic SEC-data steps in a fixed
LangGraph workflow, followed by Research Analyst, Financial Analyst,
Investment Advisor, Strategy Manager, and a mandatory Verifier. Role
instructions are in `roles/`. The OpenAI Agents SDK makes each role call; a
model never selects the next graph node. `desk.py` is the only client entry
point: it runs the full desk in the required fixed order.

Set `SEC_USER_AGENT` in `.env` to an SEC-compliant User-Agent that identifies
you and gives SEC a way to contact you. The program sends that value on every
SEC request.

Set `API_KEY` in `.env`. The program reads this name directly; it does not
require an `OPENAI_API_KEY` alias. By default, the program uses the Purdue
GenAI Studio endpoint and `qwen3.8:27b`. Set `OPENAI_MODEL` or
`OPENAI_BASE_URL` only to override those defaults.

Set `LSEG_APP_KEY` in `.env` for the LSEG price-and-volume reader. Before a
desk run, open and sign in to LSEG Workspace Desktop on the same computer.
The reader uses LSEG daily `TRDPRC_1` and `ACVOL_UNS` data. It selects the
last regular-session close available before the 8-K EDGAR Accepted time.

`.env` is ignored by Git. Never add it, copy its values into a source file, or
commit an API key, LSEG App Key, or SEC User-Agent.

Install the required packages:

```bash
python3 -m pip install -r requirements.txt
```

For a fresh clone, create `.env` locally with `SEC_USER_AGENT`, `API_KEY`,
and `LSEG_APP_KEY`. Then install packages, open LSEG Workspace Desktop, and
run the desk with the commands below. A teammate must use their own approved
credentials; credentials are not shared through this repository.

## One-command desk

The desk uses a pipeline join. The agent team saves its filing run first. The
worksheet reads that saved folder. The hook then checks the worksheet itself.
The final desk report stays in the same folder. No role recomputes a skill
number.

The agent team supplies filing-backed operating and financial context. It does
not issue the final client view because it runs before the price reader. The
worksheet supplies the sole authoritative `more` or `less` view after it uses
the LSEG daily close that was available before the earnings 8-K's EDGAR
Accepted time. It labels the annual period used in owner-cash-flow valuation,
the latest saved 10-Q, and the earnings-release period. It does not silently
mix annual cash-flow inputs with quarterly earnings-release figures.

Run the full desk for one ticker:

```bash
python3 desk.py TICKER
```

Write a weekly live record only when the target filing was accepted this week:

```bash
python3 desk.py TICKER --live
```

Run the cited spinoff screen, then use its highest-ranked in-window ticker:

```bash
python3 desk.py screen --live
```

Replay and recheck a completed saved run without fetching any data:

```bash
python3 desk.py --replay runs/live/TICKER_YYYY-MM-DD_HH-MM-SS_ZONE
```

Run the local browser UI:

```bash
streamlit run app.py
```

Run all regression tests before a material change or a live run:

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
python3 -m unittest discover -s .codex/hooks -p 'test_*.py'
python3 -m unittest discover -s skills/sec-spinoff-seeker/scripts -p 'test_*.py'
python3 -m unittest discover -s skills/filing-valuation-worksheet/scripts -p 'test_*.py'
```

The desk command stops at the first failed part and keeps the saved run folder
for replay. It prints the elapsed seconds for each pipeline part.

On the page, enter a ticker or `screen`, then select **Run desk**. The page
runs the same fixed pipeline and displays the saved final desk report.

Each run creates a new company-and-time folder in `runs/`. It saves filing
texts, `facts.json`, every node's JSON input and output, `investment_report.md`,
and `verified_report.md`. The verified report preserves the filings line first
and puts its table of marks directly below it.

## Live record and ready check

Live reports belong in `runs/live/`. Commit and push the completed live run
within 24 hours of the earnings 8-K EDGAR Accepted time. The Git commit time,
not the local report time, is the course record. Each live row is listed in
[`runs/live/README.md`](runs/live/README.md).

For the Session 10 ready check, a teammate who did not write this README must
clone the repository on their own machine, follow this README without help,
run `python3 desk.py TICKER` on a company they select, and record the command,
date, runner, and whether help was needed at the top of `audits.md`.
