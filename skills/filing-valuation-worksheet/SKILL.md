---
name: filing-valuation-worksheet
description: Create a filing-cited valuation worksheet and one evidence-bounded judgment from a saved equity-desk run. Use for a single ticker's saved filings, not for screening or price discovery.
---

# Filing Valuation Worksheet

Use this skill after the agent team has saved a run. The script writes facts and calculations from the saved filing evidence, then makes exactly one model call for judgment. It does not let a model calculate a number, select an input, or supply an uncited fact.

Before a production run, the user must write the worksheet settings and persona. Do not supply their valuation method, rates, client purchase size, conviction rule, or persona. Read [the input schema](references/input-schema.md) before validating those files or adapting a price reader.

Run from the project root. The saved run needs `run_manifest.json`, `facts.json`, and saved filing text. Supply market data produced by the user's reader. If the close is `not available`, the worksheet prints `not computed` for price lines and the view.

```sh
python3 /Users/douglasbuckeridge/.codex/skills/filing-valuation-worksheet/scripts/worksheet.py \
  --run-dir runs/SAVED_RUN \
  --settings worksheet_settings.json \
  --persona worksheet_persona.md \
  --market-data worksheet_market_data.json \
  --env-file .env \
  --registry spinoff_runs/registry.json
```

If a registry row matches the saved run's CIK or ticker, the script uses its registration accession to save the Form 10 information-statement documents beside the run. Otherwise, it prints `not applicable` for spinoff facts. It never estimates missing facts. A Form 4 purchase is counted only when the saved Form 4 has transaction code `P`.

The worksheet's dated Markdown file is saved inside `--run-dir`. It prints its owner-cash-flow arithmetic, valuation inputs and results, price date, post-model liquidity comparison, deterministic view, conviction rule, and a not-known list.

Change or add a made-up-data test before changing a rule. Run tests with:

```sh
python3 -m unittest discover -s /Users/douglasbuckeridge/.codex/skills/filing-valuation-worksheet/scripts -p 'test_*.py'
```
