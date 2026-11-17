# FIN 67110 — Fall 2026

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
python3 desk.py --ticker TICKER --live
```

Run the cited spinoff screen without selecting a name:

```bash
python3 desk.py --screen --start-date YYYY-MM-DD
```

The `--ticker` command stops at the first failed part and keeps the saved run
folder for replay. It prints the elapsed seconds for each pipeline part.

On the page, enter a ticker, optional peers, and an optional share price, then
select **Run agent team**. The page shows each node as it starts and displays
the verified report when the mandatory Verifier completes. The terminal
commands above continue to work unchanged.

Each run creates a new company-and-time folder in `runs/`. It saves filing
texts, `facts.json`, every node's JSON input and output, `investment_report.md`,
and `verified_report.md`. The verified report preserves the filings line first
and puts its table of marks directly below it.
