# Inputs and outputs

`factors.json` must contain a positive integer `window_days` and a `factors` array. Each factor has `name`, numeric `weight`, `direction` (`higher` or `lower`), and `field`. Values are read from the optional `--factor-values` JSON file, keyed by new-company CIK, then by `field`. Missing values get zero points and are stated in the report. For each factor, the best available value receives `abs(weight)` points, the worst receives zero, and intermediate values receive a linear share of the weight: `weight * (value - minimum) / (maximum - minimum)` for `higher`, or `weight * (maximum - value) / (maximum - minimum)` for `lower`. If all available values are equal, each receives the factor weight. This allows a negative return to score as a lower return; it does not infer any selling. Changing one factor’s weight changes only that factor’s points.

```json
{
  "window_days": 180,
  "factors": [
    {"name": "Example factor", "field": "example", "weight": 10, "direction": "higher"}
  ]
}
```

The optional factor-values file is a JSON object. Its keys are zero-padded ten-digit CIKs.

```json
{"0000000001": {"example": 12.5}}
```

The closes-reader shell template receives `{symbol}`, `{cik}`, `{date}`, and `{regular_way_date}`. It must write exactly one JSON object such as `{"date":"2025-01-03","close":17.25}` to standard output. `{date}` is the distribution date. The seeker calls the reader only when EDGAR cites a regular-way trading start date, and rejects a returned price before that date or on/before the distribution date. If either date or the symbol is absent, first price stays blank.

`scripts/closes_reader.py` uses the installed `yfinance` package. It returns the first unadjusted Yahoo close on or after the later of (a) the day after distribution and (b) the filing-cited regular-way start date. Yahoo historical data does not label a historical bar as regular-way or when-issued. Therefore the reader does not independently prove trading condition; the cited EDGAR regular-way date is the guard. It leaves the price blank when that filing fact is absent.

Each `registry.json` row has values and a source object for each of `parent_company`, `distribution_ratio`, `record_date`, and `distribution_date`. A source includes the SEC filing URL, accession, form, and the nearest detected section heading. Blank values have no invented source. `excluded` rows state a cited reason. `report_YYYY-MM-DD_HH-MM-SS.md` begins with the run date, price date, and index date.
