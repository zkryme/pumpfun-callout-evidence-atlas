"""Trace observed post-fan-out sales without turning proceeds into profit."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trace_hidden_profit_exits import Helius, ROOT, SOL_MINT, iso_timestamp, load_dotenv, read_csv, transaction_deltas, write_csv


def timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace cash proceeds after caller inventory fan-outs.")
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required in .env")
    api = Helius(key, args.requests_per_second)
    fanouts = read_csv(ROOT / "web" / "public" / "downloads" / "cluster_recent_caller_fanouts.csv")
    tx_cache: dict[str, dict | None] = {}
    output, seen = [], set()
    for index, fanout in enumerate(fanouts, start=1):
        if fanout["mint"] == SOL_MINT:
            continue
        start = timestamp(fanout["timestamp"])
        end = start + int(args.hours * 3600)
        recipients = [value for value in fanout["recipients"].split(";") if value]
        for wallet in recipients:
            print(f"[{index}/{len(fanouts)}] {fanout['mint'][:8]} to {wallet[:8]}", flush=True)
            try:
                records = api.transfers(wallet, mint=fanout["mint"], start=start, end=end)
            except RuntimeError as exc:
                print(f"skip {wallet[:8]}: {exc}", flush=True)
                continue
            decimals = max((int(record.get("decimals") or 0) for record in records), default=0)
            for record in records:
                signature = record.get("signature", "")
                if signature not in tx_cache:
                    try:
                        tx_cache[signature] = api.transaction(signature)
                    except RuntimeError as exc:
                        print(f"skip tx {signature[:8]}: {exc}", flush=True)
                        tx_cache[signature] = None
                tx = tx_cache[signature]
                if not tx:
                    continue
                sol_delta, token_deltas = transaction_deltas(tx, wallet)
                token_delta = token_deltas.get(fanout["mint"], 0) / (10 ** decimals)
                wsol_delta = token_deltas.get(SOL_MINT, 0) / 1_000_000_000
                if token_delta >= 0 or (sol_delta <= 0.001 and wsol_delta <= 0.001):
                    continue
                # A wallet can receive the same mint in consecutive fan-outs.
                # The export is ordered newest-first, so assign each observed exit
                # only to its closest prior caller fan-out and never sum it twice.
                key_tuple = (wallet, fanout["mint"], signature)
                if key_tuple in seen:
                    continue
                seen.add(key_tuple)
                output.append({
                    "fanout_timestamp": fanout["timestamp"], "fanout_signature": fanout["signature"],
                    "mint": fanout["mint"], "recipient_wallet": wallet,
                    "exit_timestamp": iso_timestamp(tx.get("blockTime")), "exit_signature": signature,
                    "tokens_disposed": -token_delta, "net_native_sol": sol_delta,
                    "net_wsol": wsol_delta, "hours_after_fanout": (int(tx.get("blockTime") or 0) - start) / 3600,
                    "classification": "OBSERVED_CASH_PROCEEDS_NO_COST_BASIS",
                    "limitation": "Token transfer and later disposal are observed; original acquisition cost and beneficial ownership are not established.",
                })
    fields = ["fanout_timestamp", "fanout_signature", "mint", "recipient_wallet", "exit_timestamp",
              "exit_signature", "tokens_disposed", "net_native_sol", "net_wsol", "hours_after_fanout",
              "classification", "limitation"]
    write_csv(ROOT / "output" / "cluster_recent_fanout_exits.csv", output, fields)
    print(f"Observed disposal events: {len(output)}")


if __name__ == "__main__":
    main()
