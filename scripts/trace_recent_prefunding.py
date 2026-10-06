from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from trace_hidden_profit_exits import (
    CALLER,
    DEFAULT_WALLETS,
    Helius,
    ROOT,
    SOL_MINT,
    iso_timestamp,
    load_dotenv,
    parse_timestamp,
    read_csv,
    write_csv,
    direct_system_transfers,
)
import os


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace recent SOL prefunding before pre-call buys.")
    parser.add_argument("--lookback-hours", type=float, default=24.0)
    parser.add_argument("--minimum-sol", type=float, default=0.05)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    parser.add_argument("--verify-max-source-events", type=int, default=30)
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required in .env")
    api = Helius(key, args.requests_per_second)
    calls = {row["mint"]: row for row in read_csv(ROOT / "output" / "calls.csv")}
    tracked = set(DEFAULT_WALLETS) | {CALLER}
    buys = [
        row for row in read_csv(ROOT / "output" / "early_buyers.csv")
        if row["wallet"] in tracked and float(row["seconds_relative_to_call"]) < 0
    ]

    rows: list[dict[str, object]] = []
    for position, buy in enumerate(buys, start=1):
        buy_time = parse_timestamp(buy["first_buy_timestamp"])
        call = calls.get(buy["mint"], {})
        label = str(call.get("symbol", buy["mint"][:8])).encode("ascii", "backslashreplace").decode()
        print(f"[{position}/{len(buys)}] {buy['wallet'][:8]} {label}", flush=True)
        records = api.transfers(
            buy["wallet"], mint=SOL_MINT, direction="in",
            start=buy_time - int(args.lookback_hours * 3600), end=buy_time,
            minimum_raw_amount=int(args.minimum_sol * 1_000_000_000),
        )
        seen: set[tuple[str, str]] = set()
        for record in records:
            source = record.get("fromUserAccount")
            signature = record.get("signature", "")
            if not source or source == buy["wallet"] or (signature, source) in seen:
                continue
            seen.add((signature, source))
            timestamp = int(record.get("blockTime") or 0)
            rows.append({
                "wallet": buy["wallet"],
                "wallet_role": "CALLER" if buy["wallet"] == CALLER else "HIDDEN_CANDIDATE",
                "mint": buy["mint"],
                "symbol": call.get("symbol", ""),
                "buy_timestamp": buy["first_buy_timestamp"],
                "source": source,
                "amount_sol": float(record.get("uiAmount") or 0),
                "funding_timestamp": iso_timestamp(timestamp),
                "seconds_before_buy": buy_time - timestamp,
                "funding_signature": signature,
            })

    source_wallets: dict[str, set[str]] = defaultdict(set)
    source_roles: dict[str, set[str]] = defaultdict(set)
    source_events = Counter()
    for row in rows:
        source = str(row["source"])
        source_wallets[source].add(str(row["wallet"]))
        source_roles[source].add(str(row["wallet_role"]))
        source_events[source] += 1
    for row in rows:
        source = str(row["source"])
        row.update({
            "source_event_count": source_events[source],
            "source_tracked_wallet_count": len(source_wallets[source]),
            "source_funds_caller_and_hidden": source_roles[source] == {"CALLER", "HIDDEN_CANDIDATE"},
            "direct_system_prefunding": False,
        })

    transaction_cache: dict[str, dict | None] = {}
    overlap_sources = {
        str(row["source"]) for row in rows
        if row["source_funds_caller_and_hidden"]
        and source_events[str(row["source"])] <= args.verify_max_source_events
    }
    for row in rows:
        source = str(row["source"])
        if source not in overlap_sources:
            continue
        signature = str(row["funding_signature"])
        if signature not in transaction_cache:
            transaction_cache[signature] = api.transaction(signature)
        tx = transaction_cache[signature]
        if not tx:
            continue
        row["direct_system_prefunding"] = any(
            destination == row["wallet"]
            for destination, _ in direct_system_transfers(tx, source)
        )

    direct_roles: dict[str, set[str]] = defaultdict(set)
    direct_wallets: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["direct_system_prefunding"]:
            source = str(row["source"])
            direct_roles[source].add(str(row["wallet_role"]))
            direct_wallets[source].add(str(row["wallet"]))
    for row in rows:
        source = str(row["source"])
        row["direct_source_funds_caller_and_hidden"] = direct_roles[source] == {"CALLER", "HIDDEN_CANDIDATE"}

    fields = [
        "wallet", "wallet_role", "mint", "symbol", "buy_timestamp", "source",
        "amount_sol", "funding_timestamp", "seconds_before_buy", "funding_signature",
        "source_event_count", "source_tracked_wallet_count", "source_funds_caller_and_hidden",
        "direct_system_prefunding", "direct_source_funds_caller_and_hidden",
    ]
    output = ROOT / "output" / "recent_prefunding.csv"
    write_csv(output, rows, fields)
    overlap = [row for row in rows if row["source_funds_caller_and_hidden"]]
    print(f"Recent funding records: {len(rows)}")
    print(f"Sources funding caller and hidden candidates: {len({row['source'] for row in overlap})}")
    for source in sorted({str(row["source"]) for row in overlap}):
        wallets = sorted(source_wallets[source])
        print(f"  {source}: {len(wallets)} tracked wallets, {source_events[source]} events")
    direct_overlap = {
        str(row["source"]) for row in rows if row["direct_source_funds_caller_and_hidden"]
    }
    print(f"Direct system-transfer sources funding caller and hidden candidates: {len(direct_overlap)}")
    for source in sorted(direct_overlap):
        print(f"  {source}: {len(direct_wallets[source])} tracked wallets")


if __name__ == "__main__":
    main()
