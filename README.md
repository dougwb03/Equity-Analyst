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

`equity_desk.py` runs the seven deterministic SEC-data steps in a fixed
LangGraph workflow, followed by Research Analyst, Financial Analyst,
Investment Advisor, Strategy Manager, and a mandatory Verifier. Role
instructions are in `roles/`. The OpenAI Agents SDK makes each role call; a
model never selects the next graph node.

Set `SEC_USER_AGENT` in `.env` to an SEC-compliant User-Agent that identifies
you and gives SEC a way to contact you. The program sends that value on every
SEC request.

Set `API_KEY` in `.env`. The program reads this name directly; it does not
require an `OPENAI_API_KEY` alias. By default, the program uses the Purdue
GenAI Studio endpoint and `qwen3.8:27b`. Set `OPENAI_MODEL` or
`OPENAI_BASE_URL` only to override those defaults.

Install the required packages:

```bash
python3 -m pip install -r requirements.txt
```

Run the full desk, with peers and a price:

```bash
python3 equity_desk.py TICKER --peers PEER1 PEER2 --price 123.45
```

Verify a saved run without rerunning any other node:

```bash
python3 equity_desk.py --verify-only runs/COMPANY_DATE_TIME
```

Start the local browser page:

```bash
streamlit run app.py
```

## One-command desk

The desk uses a pipeline join. The agent team saves its filing run first. The
worksheet reads that saved folder. The hook then checks the worksheet itself.
The final desk report stays in the same folder. No role recomputes a skill
number.

Run the full desk for one ticker:

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

The desk command stops at the first failed part and keeps the saved run folder
for replay. It prints the elapsed seconds for each pipeline part.

On the page, enter a ticker or `screen`, then select **Run desk**. The page
runs the same fixed pipeline and displays the saved final desk report.

Each run creates a new company-and-time folder in `runs/`. It saves filing
texts, `facts.json`, every node's JSON input and output, `investment_report.md`,
and `verified_report.md`. The verified report preserves the filings line first
and puts its table of marks directly below it.
