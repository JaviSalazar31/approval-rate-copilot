# Sample data

One day of raw files (13 Aug 2026, the day of the Colombian debit incident) and all reference tables, so the
formats can be browsed on GitHub. The full 90 days (about 170 MB) are not committed: regenerate them with

```
python data_generator/generate_data.py --out data/full --seed 42
```

| Folder | Format |
|---|---|
| `raw/orchestrator` | Payment intents as logged by the orchestrator |
| `raw/acquirer_a` | ISO-8601 UTC timestamps, decimal amounts, ISO 8583 response codes, some webhook re-deliveries |
| `raw/acquirer_b` | Local time plus UTC offset, amounts in minor units, `R`-prefixed reason codes |
| `raw/acquirer_c` | Epoch milliseconds, comma decimals, Portuguese column names and status labels |
| `reference` | Merchants, issuers, acquirers, routing rules, decline-code map, change log, FX rates |

All data is synthetic. Merchants, issuers and acquirers are fictional.
