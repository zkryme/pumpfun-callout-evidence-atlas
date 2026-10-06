from __future__ import annotations

import argparse
import os

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


DEFAULT_LINKED_WALLET = "CLiXvfYSJtG5SJTCWyLvXKtDag9pJiA4vSWZGgSKP19X"


def main() -> None:
    parser = argparse.ArgumentParser(description="Calculate token-specific cash flow for a linked wallet.")
    parser.add_argument("--wallet", default=DEFAULT_LINKED_WALLET)
    parser.add_argument("--hours-after-call", type=float, default=24.0)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    parser.add_argument("--output", default="caller_funder_pnl.csv")
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
    for position, buy in enumerate(positions, start=1):
        call = calls[buy["mint"]]
        start = parse_timestamp(buy["first_buy_timestamp"]) - 5
        call_time = parse_timestamp(call["call_timestamp"])
        end = call_time + int(args.hours_after_call * 3600)
        print(f"[{position}/{len(positions)}] {call['symbol']}", flush=True)
        records = api.transfers(args.wallet, mint=buy["mint"], start=start, end=end)
        signatures = list(dict.fromkeys(record["signature"] for record in records))
        sol_spent = 0.0
        sol_received = 0.0
        tokens_bought = 0.0
        tokens_sold = 0.0
        trade_count = 0
        first_sale_time: int | None = None
        last_sale_time: int | None = None
        sale_signatures: list[str] = []
        decimals = max((int(record.get("decimals") or 0) for record in records), default=0)
        for signature in signatures:
            if signature not in transaction_cache:
                transaction_cache[signature] = api.transaction(signature)
            tx = transaction_cache[signature]
            if not tx:
                continue
            sol_delta, token_deltas = transaction_deltas(tx, args.wallet)
            token_delta = token_deltas.get(buy["mint"], 0) / (10 ** decimals)
            if token_delta > 0 and sol_delta < -0.001:
                tokens_bought += token_delta
                sol_spent += -sol_delta
                trade_count += 1
            elif token_delta < 0 and sol_delta > 0.001:
                tokens_sold += -token_delta
                sol_received += sol_delta
                trade_count += 1
                timestamp = int(tx.get("blockTime") or 0)
                first_sale_time = timestamp if first_sale_time is None else min(first_sale_time, timestamp)
                last_sale_time = timestamp if last_sale_time is None else max(last_sale_time, timestamp)
                sale_signatures.append(signature)
        sold_fraction = min(1.0, tokens_sold / tokens_bought) if tokens_bought else 0.0
        allocated_cost = sol_spent * sold_fraction
        realized_pnl = sol_received - allocated_cost
        output.append({
            "wallet": args.wallet,
            "mint": buy["mint"],
            "token": call["token_name"],
            "symbol": call["symbol"],
            "call_timestamp": call["call_timestamp"],
            "window_end": iso_timestamp(end),
            "trade_count": trade_count,
            "tokens_bought": tokens_bought,
            "tokens_sold": tokens_sold,
            "sold_fraction": sold_fraction,
            "sol_spent": sol_spent,
            "sol_received": sol_received,
            "allocated_cost_sol": allocated_cost,
            "estimated_realized_pnl_sol": realized_pnl,
            "return_on_allocated_cost": realized_pnl / allocated_cost if allocated_cost else 0,
            "first_sale_timestamp": iso_timestamp(first_sale_time),
            "first_sale_seconds_after_call": (first_sale_time - call_time) if first_sale_time else "",
            "last_sale_timestamp": iso_timestamp(last_sale_time),
            "sale_signatures": ";".join(sale_signatures),
        })

    fields = [
        "wallet", "mint", "token", "symbol", "call_timestamp", "window_end",
        "trade_count", "tokens_bought", "tokens_sold", "sold_fraction", "sol_spent",
        "sol_received", "allocated_cost_sol", "estimated_realized_pnl_sol",
        "return_on_allocated_cost", "first_sale_timestamp", "first_sale_seconds_after_call",
        "last_sale_timestamp", "sale_signatures",
    ]
    write_csv(ROOT / "output" / args.output, output, fields)
    profitable = [row for row in output if float(row["estimated_realized_pnl_sol"]) > 0]
    print(f"Positions: {len(output)}")
    print(f"Profitable realized positions: {len(profitable)}")
    print(f"Estimated realized PnL: {sum(float(row['estimated_realized_pnl_sol']) for row in output):.6f} SOL")


if __name__ == "__main__":
    main()
