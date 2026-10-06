# Pump.fun Callout Infrastructure Analyzer

A resumable local Python/SQLite analysis tool for the Pump.fun callout history of
`6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm`.

It retrieves the canonical Pump.fun callout history, identifies token launch timing and early buyers,
keeps same-slot/same-transaction activity separate from shared-funder evidence, traces funding when an
authorized Solscan Pro key or explicit RPC fallback is available, compares wallets across tokens, and
generates CSV, JSON, Markdown, GraphML, and network JSON exports.

## Run

Python 3.11+ is sufficient; the implementation uses only the standard library.

```powershell
Copy-Item .env.example .env
# Put SOLSCAN_API_KEY in .env
python main.py test-chonk
python main.py analyze-all --early-buyers 50 --funding-depth 3
```

Commands:

```text
python main.py collect-calls
python main.py test-chonk [--rpc-funding-limit N]
python main.py analyze-token <mint>
python main.py analyze-all [--early-buyers 50] [--funding-depth 3] [--rpc-funding-limit N]
python main.py report
python main.py funding-graph
```

`--rpc-funding-limit` is deliberately opt-in because public RPC funding traces require many calls.
Set `HELIUS_API_KEY` in `.env` to use Helius mainnet RPC automatically. The client defaults to
9 requests per second, leaving headroom below the free-plan 10 RPS limit.
Every API response is cached under `data/cache`, progress is checkpointed in `analysis.sqlite3`, and
reruns resume completed work.

## Evidence model

- Same transaction and same slot are launch-timing indicators.
- Shared direct funder and shared upstream funder are reported independently.
- Exchange/service endpoints are not evidence of common control by themselves.
- Missing data is `UNKNOWN`, never silently converted to `CLEAN`.
- Transaction signatures, timestamps, amounts, and raw source records are retained.

## Current APIs

- Pump.fun canonical calls: `GET /callout/list/{userId}` with `pageToken` pagination.
- Pump.fun token identity: `GET /coins-v3/{mint}`.
- Pump.fun early trades: `GET https://swap-api.pump.fun/v2/coins/{mint}/trades` with cursor pagination.
- Solscan Pro v2: `token` request header and official `/v2.0` endpoints.
- Solana JSON-RPC: optional read-only fallback for direct transaction parsing.

## Outputs

See `output/` for the required CSVs, `analysis.json`, `report.md`,
`funding_graph.graphml`, and `funding_graph.json`.
