# Worksheet inputs

The user writes `worksheet_settings.json`. The script supports these two value methods. The user chooses the method and all numeric values; the script does not select them.

```json
{
  "client_purchase_size": 0,
  "valuation": {
    "method": "owner_cash_flow_multiple",
    "low_rate": 0,
    "mid_rate": 0
  },
  "conviction": {
    "high_if_all_of": ["owner_cash_flow", "low_value_per_share", "price", "liquidity"]
  }
}
```

`method` is either `owner_cash_flow_multiple`, which computes equity value as owner cash flow times the stated rate, or `capitalization_rate`, which computes equity value as owner cash flow divided by the stated rate. The script divides equity value by a filing-sourced share count. It leaves the value blank if owner cash flow or share count is unavailable.

`worksheet_persona.md` is the persona the user wrote. It is used only for one judgment call after code has established the filing facts, calculations, and price comparison.

The price reader must write `worksheet_market_data.json` in this shape. `close` must be a regular closing price. The script does not infer regular-way status from a price series.

```json
{
  "source": "user's closes and volumes reader",
  "rows": [
    {"date": "YYYY-MM-DD", "close": 0, "volume": 0}
  ]
}
```

If no close is available, write:

```json
{"close":"not available"}
```
