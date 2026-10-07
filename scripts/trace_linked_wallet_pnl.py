from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trace_hidden_profit_exits import (
    Helius,
    ROOT,
    iso_timestamp,
    load_dotenv,
    parse_timestamp,
    read_csv,
    transaction_deltas,
    write_csv,
)
from pump_analyzer.reconciliation import classify_event, reconcile_events


DEFAULT_LINKED_WALLET = "CLiXvfYSJtG5SJTCWyLvXKtDag9pJiA4vSWZGgSKP19X"


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate token-specific cash flow for a linked wallet.")
    parser.add_argument("--wallet", default=DEFAULT_LINKED_WALLET)
    parser.add_argument("--hours-after-call", type=float, default=24.0)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    parser.add_argument("--output", default="caller_funder_pnl.csv")
    # Kept under the existing public download name; this is now a complete
    # event ledger, not the former one-sale-per-token sample.
    parser.add_argument("--ledger-output", default="caller_funder_sales.csv")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required in .env")
    api = Helius(key, args.requests_per_second)
    calls = {row["mint"]: row for row in read_csv(ROOT / "output" / "calls.csv")}
    positions = [
        row for row in read_csv(ROOT / "output" / "early_buyers.csv")
        if row["wallet"] == args.wallet and float(row["seconds_relative_to_call"]) < 0
    ]
    transaction_cache: dict[str, dict | None] = {}
    output: list[dict[str, object]] = []
    ledger: list[dict[str, object]] = []
    for position, buy in enumerate(positions, start=1):
        call = calls[buy["mint"]]
        start = parse_timestamp(buy["first_buy_timestamp"]) - 5
        call_time = parse_timestamp(call["call_timestamp"])
        end = call_time + int(args.hours_after_call * 3600)
        print(f"[{position}/{len(positions)}] {call['symbol']}", flush=True)
        records = api.transfers(args.wallet, mint=buy["mint"], start=start, end=end)
        signatures = list(dict.fromkeys(record["signature"] for record in records))
        events: list[dict[str, object]] = []
        decimals = max((int(record.get("decimals") or 0) for record in records), default=0)
        for signature in signatures:
            if signature not in transaction_cache:
                transaction_cache[signature] = api.transaction(signature)
            tx = transaction_cache[signature]
            if not tx:
                continue
            sol_delta, token_deltas = transaction_deltas(tx, args.wallet)
            token_delta = token_deltas.get(buy["mint"], 0) / (10 ** decimals)
            event_type = classify_event(token_delta, sol_delta)
            if event_type == "OTHER":
                continue
            event = {
                "wallet": args.wallet, "mint": buy["mint"], "token": call["token_name"],
                "symbol": call["symbol"], "call_timestamp": call["call_timestamp"],
                "window_start": iso_timestamp(start), "window_end": iso_timestamp(end),
                "timestamp": iso_timestamp(int(tx.get("blockTime") or 0)), "signature": signature,
                "event_type": event_type, "token_amount": token_delta, "sol_delta": sol_delta,
                "fee_sol": "", "fee_coverage": "unavailable",
            }
            events.append(event)
            ledger.append(event)
        reconciliation = reconcile_events(events)
        sales = [event for event in events if event["event_type"] == "SALE"]
        first_sale_time = min((parse_timestamp(str(event["timestamp"])) for event in sales), default=None)
        last_sale_time = max((parse_timestamp(str(event["timestamp"])) for event in sales), default=None)
        output.append({
            "wallet": args.wallet,
            "mint": buy["mint"],
            "token": call["token_name"],
            "symbol": call["symbol"],
            "call_timestamp": call["call_timestamp"],
            "window_end": iso_timestamp(end),
            **reconciliation,
            "first_sale_timestamp": iso_timestamp(first_sale_time),
            "first_sale_seconds_after_call": (first_sale_time - call_time) if first_sale_time else "",
            "last_sale_timestamp": iso_timestamp(last_sale_time),
            "sale_signatures": ";".join(str(event["signature"]) for event in sales),
        })

    fields = [
        "wallet", "mint", "token", "symbol", "call_timestamp", "window_end",
        "purchase_count", "sale_count", "transfer_count", "tokens_bought", "tokens_sold", "sol_spent",
        "sol_received", "observed_net_sol_cash_flow", "reconciliation_status", "profit_determined", "limitation",
        "first_sale_timestamp", "first_sale_seconds_after_call",
        "last_sale_timestamp", "sale_signatures",
    ]
    write_csv(ROOT / "output" / args.output, output, fields)
    ledger_fields = ["wallet", "mint", "token", "symbol", "call_timestamp", "window_start", "window_end",
                     "timestamp", "signature", "event_type", "token_amount", "sol_delta", "fee_sol", "fee_coverage"]
    write_csv(ROOT / "output" / args.ledger_output, ledger, ledger_fields)
    print(f"Positions: {len(output)}")
    print(f"Ledger events: {len(ledger)}")
    print(f"Incomplete positions: {sum(row['reconciliation_status'] != 'RECONCILED_WITHIN_WINDOW' for row in output)}")


if __name__ == "__main__":
    main()
