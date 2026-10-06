from __future__ import annotations

import argparse
import os

from trace_hidden_profit_exits import (
    CALLER,
    Helius,
    ROOT,
    SOL_MINT,
    caller_interactions,
    destination_fan_in,
    direct_system_transfers,
    iso_timestamp,
    load_dotenv,
    parse_timestamp,
    read_csv,
    write_csv,
)


SYSTEM_PROGRAM = "11111111111111111111111111111111"


def ordinary_live_wallet(api: Helius, address: str) -> bool:
    result = api.call("getAccountInfo", [address, {"encoding": "base64", "commitment": "finalized"}]) or {}
    value = result.get("value")
    return bool(value and value.get("owner") == SYSTEM_PROGRAM and not value.get("executable"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Follow rapid near-full-value SOL forwarding chains.")
    parser.add_argument("--max-depth", type=int, default=5)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--minimum-retained-ratio", type=float, default=0.70)
    parser.add_argument("--minimum-forward-sol", type=float, default=0.05)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required in .env")
    api = Helius(key, args.requests_per_second)
    seeds = read_csv(ROOT / "output" / "hidden_wallet_cleanup_two_hop.csv")
    states = [{
        "root_hidden_wallet": row["source_hidden_wallet"],
        "symbol": row["symbol"],
        "address": row["second_hop"],
        "amount_sol": float(row["second_hop_amount_sol"]),
        "timestamp": parse_timestamp(row["second_hop_timestamp"]),
        "path": f"{row['first_hop']}>{row['second_hop']}",
        "depth": 2,
    } for row in seeds]

    rows: list[dict[str, object]] = []
    transaction_cache: dict[str, dict | None] = {}
    account_cache: dict[str, bool] = {}
    seen_edges: set[tuple[str, str, str]] = set()
    for depth in range(3, args.max_depth + 1):
        next_states: list[dict[str, object]] = []
        for state in states:
            address = str(state["address"])
            if address not in account_cache:
                account_cache[address] = ordinary_live_wallet(api, address)
            if not account_cache[address]:
                continue
            start = int(state["timestamp"])
            incoming = float(state["amount_sol"])
            records = api.transfers(
                address, mint=SOL_MINT, direction="out", start=start,
                end=start + int(args.hours * 3600),
                minimum_raw_amount=int(max(
                    args.minimum_forward_sol,
                    incoming * args.minimum_retained_ratio,
                ) * 1_000_000_000),
            )
            for record in records:
                signature = record.get("signature", "")
                if signature not in transaction_cache:
                    transaction_cache[signature] = api.transaction(signature)
                tx = transaction_cache[signature]
                if not tx:
                    continue
                for destination, amount in direct_system_transfers(tx, address):
                    if (amount < args.minimum_forward_sol
                            or amount < incoming * args.minimum_retained_ratio
                            or amount > incoming * 1.05):
                        continue
                    edge = (address, signature, destination)
                    if edge in seen_edges or destination in str(state["path"]).split(">"):
                        continue
                    seen_edges.add(edge)
                    timestamp = int(tx.get("blockTime") or start)
                    interactions = caller_interactions(api, destination)
                    inbound_records, unique_sources = destination_fan_in(api, destination)
                    row = {
                        "root_hidden_wallet": state["root_hidden_wallet"],
                        "symbol": state["symbol"],
                        "depth": depth,
                        "source": address,
                        "destination": destination,
                        "incoming_amount_sol": incoming,
                        "forwarded_amount_sol": amount,
                        "retained_ratio": amount / incoming if incoming else 0,
                        "timestamp": iso_timestamp(timestamp),
                        "seconds_after_receipt": timestamp - start,
                        "signature": signature,
                        "direct_to_caller": destination == CALLER,
                        "caller_interaction_count": interactions,
                        "destination_latest_inbound_records": inbound_records,
                        "destination_latest_unique_sources": unique_sources,
                        "destination_service_like": unique_sources >= 20,
                        "path": f"{state['path']}>{destination}",
                    }
                    rows.append(row)
                    next_states.append({
                        "root_hidden_wallet": state["root_hidden_wallet"],
                        "symbol": state["symbol"],
                        "address": destination,
                        "amount_sol": amount,
                        "timestamp": timestamp,
                        "path": row["path"],
                        "depth": depth,
                    })
        states = next_states
        print(f"depth {depth}: {len(next_states)} forwarding edges", flush=True)
        if not states:
            break

    fields = [
        "root_hidden_wallet", "symbol", "depth", "source", "destination",
        "incoming_amount_sol", "forwarded_amount_sol", "retained_ratio", "timestamp",
        "seconds_after_receipt", "signature", "direct_to_caller", "caller_interaction_count",
        "destination_latest_inbound_records", "destination_latest_unique_sources",
        "destination_service_like", "path",
    ]
    write_csv(ROOT / "output" / "hidden_wallet_forwarding_chains.csv", rows, fields)
    print(f"Forwarding edges: {len(rows)}")
    print(f"Caller-linked edges: {sum(bool(row['direct_to_caller'] or row['caller_interaction_count']) for row in rows)}")


if __name__ == "__main__":
    main()
