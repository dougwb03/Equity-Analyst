# Team Ledger — Douglas Buckeridge, Alex Lisitsas, Kole Williams

Client: an investor seeking absolute return.

## Desk design

```text
                         ┌──────────────────────────────┐
                         │ Client: ticker or `screen`    │
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ `desk.py`                    │
                         │ fixed code order             │
                         └──────────────┬───────────────┘
                                        │
             ┌──────────────────────────┴──────────────────────────┐
             │                                                     │
             ▼                                                     ▼
 ┌──────────────────────────┐                       ┌──────────────────────────┐
 │ input = `screen`         │                       │ input = ticker           │
 │ SEC Spinoff Seeker       │                       │ use supplied ticker      │
 │ registry + screen report │                       └──────────────┬───────────┘
 └──────────────┬───────────┘                                      │
                ▼                                                  │
 ┌──────────────────────────┐                                      │
 │ filing-backed ticker     │                                      │
 │ selection                │                                      │
 └──────────────┬───────────┘                                      │
                └──────────────────────────┬───────────────────────┘
                                           ▼
                         ┌──────────────────────────────┐
                         │ Agent team + verifier        │
                         │ saved filings + facts.json   │
                         │ verified_report.md           │
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ Valuation Worksheet          │
                         │ dated worksheet + market data│
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ Evidence hook                │
                         │ checks worksheet + report    │
                         └──────────────┬───────────────┘
                                        ▼
                         ┌──────────────────────────────┐
                         │ desk_report.md               │
                         │ verified report + view       │
                         └──────────────────────────────┘

        Every step reads from and writes to one timestamped run folder.
```

The desk uses a pipeline join. `desk.py` fixes the order: seeker when the input is `screen`, ticker selection, agent team, worksheet, and hook checks. The agent team writes the saved filing packet and verified report. The worksheet reads those saved files and does not recompute the team’s work. The hook guards the worksheet and final report. The model does not select the order.

## Team ownership and roles

We own a pre-registered spinoff factor check. Its rules are fixed in
[`factor_check_plan.json`](../factor_check_plan.json) before return results are
read: filing-backed first regular-way entry, a 90-calendar-day holding period,
and the declared market-cap, price-return, and insider-ownership factors.

| Member | Role and contribution |
|---|---|
| **Douglas Buckeridge** | **Orchestration and Verification Lead:** owns the fixed-order desk integration, agent-team graph, and mandatory verifier. |
| **Alex Lisitsas** | **Screening and Factor-Check Lead:** owns seeker inputs and the pre-registered factor check. |
| **Kole Williams** | **Market Data, Live Record, and Product Manual Lead:** owns closes-reader operations, weekly live reports, and the manual. |

These are accountable ownership roles, not isolated work. All three members
review material changes and live reports, discuss filing evidence and judgment,
and contribute ideas across the desk before a client report is committed.

## Live record so far

Live-record folder: <https://github.com/dougwb03/fin67110-fall2026/tree/main/runs/live>

| Ticker | Release / filing | EDGAR Accepted time (Eastern) | Commit time | Inside 24-hour window? |
|---|---|---|---|---|
| CRWV | 8-K, 0001769628-26-000461 | 2026-11-10 16:05:53 | commit `7aa2fa0` | No — late catch-up run. It is retained for the record but is not a clean live report. |
| NVDA | 8-K, 0001045810-26-000088 | 2026-11-17 16:20:51 | commit `c62cdc4`, 2026-11-17 18:47:35 Eastern | Yes — committed about 2 hours and 27 minutes after EDGAR Accepted the filing. |
| DELL | 8-K, 0001571996-26-000050 | 2026-11-24 16:09:13 | commit `45609ee`, 2026-11-25 11:25:47 Eastern | Yes — committed about 19 hours and 17 minutes after EDGAR Accepted the filing. |

## Evidence for the desk design

- One-command practice run: [`runs/MSGR_2026-11-17_13-31-59_EST/desk_report.md`](../runs/MSGR_2026-11-17_13-31-59_EST/desk_report.md).
- The first line lists the Form 10/A, 10-Q, and 8-K read by the combined run.
- Network-free replay passed with `python3 desk.py --replay runs/MSGR_2026-11-17_13-31-59_EST`.
- The hook checks the worksheet and final report. The clean worksheet check exits silently.

## Before upload

1. Confirm the GitHub branch in the live-record link. This draft assumes `main`.
2. Upload this single Markdown file to Brightspace → Checkpoint 3.
