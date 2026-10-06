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
    load_dotenv,
    parse_timestamp,
    read_csv,
    write_csv,
    iso_timestamp,
)


EXCLUDED_INFRASTRUCTURE = {
    "GDQnERSchwsFseR2Gj723HThQFtyZZLX8LZ43Ugvbg1b",  # Terminal/Padre-linked aggregation
    "J5XGHmzrRmnYWbmw45DbYkdZAU2bwERFZ11qCDXPvFB5",  # high-frequency fee/tip recipient
}
SYSTEM_PROGRAM = "11111111111111111111111111111111"


def ordinary_live_wallet(api: Helius, address: str) -> bool:
    result = api.call("getAccountInfo", [address, {"encoding": "base64", "commitment": "finalized"}]) or {}
    value = result.get("value")
    return bool(value and value.get("owner") == SYSTEM_PROGRAM and not value.get("executable"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace private-looking cleanup destinations one hop farther.")
    parser.add_argument("--minimum-first-hop-sol", type=float, default=0.5)
    parser.add_argument("--minimum-second-hop-sol", type=float, default=0.05)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required in .env")
    api = Helius(key, args.requests_per_second)
    cleanup = read_csv(ROOT / "output" / "hidden_wallet_cleanup.csv")
    candidates = [
        row for row in cleanup
        if float(row["amount_sol"]) >= args.minimum_first_hop_sol
        and row["destination"] not in EXCLUDED_INFRASTRUCTURE
        and row["service_like"].lower() == "false"
    ]

    wallet_status: dict[str, bool] = {}
    output: list[dict[str, object]] = []
    transaction_cache: dict[str, dict | None] = {}
    seen: set[tuple[str, str, str]] = set()
    for position, first in enumerate(candidates, start=1):
        first_hop = first["destination"]
        if first_hop not in wallet_status:
            wallet_status[first_hop] = ordinary_live_wallet(api, first_hop)
        print(f"[{position}/{len(candidates)}] {first_hop[:8]} live={wallet_status[first_hop]}", flush=True)
        if not wallet_status[first_hop]:
            continue
        start = parse_timestamp(first["cleanup_timestamp"])
        records = api.transfers(
            first_hop, mint=SOL_MINT, direction="out", start=start,
            end=start + int(args.hours * 3600),
            minimum_raw_amount=int(args.minimum_second_hop_sol * 1_000_000_000),
        )
        for record in records:
            signature = record.get("signature", "")
            if signature not in transaction_cache:
                transaction_cache[signature] = api.transaction(signature)
            tx = transaction_cache[signature]
            if not tx:
                continue
            for second_hop, amount in direct_system_transfers(tx, first_hop):
                key_tuple = (first_hop, signature, second_hop)
                if key_tuple in seen or amount < args.minimum_second_hop_sol:
                    continue
                seen.add(key_tuple)
                output.append({
                    "source_hidden_wallet": first["wallet"],
                    "symbol": first["symbol"],
                    "sale_signature": first["sale_signature"],
                    "first_hop": first_hop,
                    "first_hop_amount_sol": first["amount_sol"],
                    "first_hop_timestamp": first["cleanup_timestamp"],
                    "second_hop": second_hop,
                    "second_hop_amount_sol": amount,
                    "second_hop_timestamp": iso_timestamp(tx.get("blockTime")),
                    "seconds_after_first_hop": (tx.get("blockTime") or start) - start,
                    "second_hop_signature": signature,
                })

    classification: dict[str, tuple[int, int, int]] = {}
    for row in output:
        destination = str(row["second_hop"])
        if destination in classification:
            continue
        interactions = caller_interactions(api, destination)
        inbound_records, unique_sources = destination_fan_in(api, destination)
        classification[destination] = (interactions, inbound_records, unique_sources)
    for row in output:
        interactions, inbound_records, unique_sources = classification[str(row["second_hop"])]
        row.update({
            "direct_to_caller": row["second_hop"] == CALLER,
            "caller_interaction_count": interactions,
            "second_hop_latest_inbound_records": inbound_records,
            "second_hop_latest_unique_sources": unique_sources,
            "second_hop_service_like": unique_sources >= 20,
        })

    fields = [
        "source_hidden_wallet", "symbol", "sale_signature", "first_hop",
        "first_hop_amount_sol", "first_hop_timestamp", "second_hop",
        "second_hop_amount_sol", "second_hop_timestamp", "seconds_after_first_hop",
        "second_hop_signature", "direct_to_caller", "caller_interaction_count",
        "second_hop_latest_inbound_records", "second_hop_latest_unique_sources",
        "second_hop_service_like",
    ]
    write_csv(ROOT / "output" / "hidden_wallet_cleanup_two_hop.csv", output, fields)
    caller_linked = [row for row in output if row["direct_to_caller"] or row["caller_interaction_count"]]
    print(f"Two-hop transfers: {len(output)}")
    print(f"Caller-linked two-hop transfers: {len(caller_linked)}")


if __name__ == "__main__":
    main()
