---
name: sec-spinoff-seeker
description: Find U.S. corporate spinoffs in SEC EDGAR filings after a supplied distribution-date start date, keep cited facts in a registry, obtain first regular-way closing prices through a supplied closes reader, and rank names in a supplied post-distribution window. Do not use for general SEC research or recommendations.
---

# SEC Spinoff Seeker

Use this skill for a reproducible U.S. spinoff screen. Run `scripts/seeker.py`; do not replace its SEC evidence with web summaries, estimates, or a price provider. It sends `SEC_USER_AGENT` from the supplied `.env` in every SEC request and stays at or below eight SEC requests per second.

The seeker reads daily EDGAR indexes for Forms 10-12B, 10-12G, their amendments, CERT, and the relevant Form 8-K filings. It then reads each candidate filing folder and documents. It accepts a candidate only when filing evidence shows a parent will distribute or did distribute the business. It excludes funds, registrations of already-held shares, and registrations without an information statement unless a later filing proves a parent distribution.

Run it from the project that contains `.env`. The required start date is inclusive. Supply your closes-reader command as a shell template. It must print one JSON object with `date` and `close`, and must return the first regular-way closing price strictly after `{date}`. The seeker rejects a price on or before the distribution date.

```sh
python3 /Users/douglasbuckeridge/.codex/skills/sec-spinoff-seeker/scripts/seeker.py \
  --start-date 2025-01-01 \
  --env-file .env \
  --output-dir spinoff_runs \
  --factors factors.json \
  --closes-reader 'python3 /Users/douglasbuckeridge/.codex/skills/sec-spinoff-seeker/scripts/closes_reader.py --symbol {symbol} --distribution-date {date} --regular-way-date {regular_way_date}'
```

Read [the input and output schema](references/schema.md) before creating the factor file or a closes-reader adapter. The report lists missing ranking dates and facts instead of filling them. A later run updates `registry.json` in the output directory while preserving the cited evidence.

Before changing a rule, change or add its made-up-data test first. Run the full test suite with:

```sh
python3 -m unittest discover -s /Users/douglasbuckeridge/.codex/skills/sec-spinoff-seeker/scripts -p 'test_*.py'
```
