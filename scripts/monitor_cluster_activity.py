"""Export recent transfer activity for the four-wallet evidence cluster.

This is a bounded watchlist, not an attribution engine: the source may return
at most 100 transfer records per wallet for the requested interval.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trace_hidden_profit_exits import Helius, ROOT, iso_timestamp, load_dotenv, read_csv, write_csv


WALLETS = {
    "caller": "6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm",
    "clix": "CLiXvfYSJtG5SJTCWyLvXKtDag9pJiA4vSWZGgSKP19X",
    "gkm": "GKMJwv2AEWVcfFXUMtxokMYy5ADkjwJa89DtP8LzoA61",
    "onechd": "1chdHBRNu9dB7E5McJKFDsbPvfpmANdmeR7BHavhi56",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch recent activity for the linked-wallet cluster.")
    parser.add_argument("--since", help="UTC ISO timestamp; defaults to the newest collected call")
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    parser.add_argument("--max-pages", type=int, default=50,
                        help="Maximum 100-record pages per wallet; status reports any cap.")
    parser.add_argument("--sort-order", choices=("asc", "desc"), default="desc",
                        help="Descending returns the most recent records first.")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required in .env")
    calls = read_csv(ROOT / "output" / "calls.csv")
    default_since = max(row["call_timestamp"] for row in calls)
    since = args.since or default_since
    start = int(datetime.fromisoformat(since.replace("Z", "+00:00")).timestamp())
    api = Helius(key, args.requests_per_second)
    rows = []
    coverage = []
    for label, wallet in WALLETS.items():
        records = []
        token = None
        pages = 0
        while pages < args.max_pages:
            page, token = api.transfers_page(wallet, pagination_token=token, start=start,
                                              sort_order=args.sort_order)
            records.extend(page)
            pages += 1
            if not token:
                break
        coverage.append({"wallet_label": label, "wallet": wallet, "since": since,
                         "records_returned": len(records), "pages_read": pages,
                         "record_cap": args.max_pages * 100,
                         "sort_order": args.sort_order,
                         "coverage": "complete" if not token else "capped_at_max_pages"})
        for record in records:
            rows.append({
                "wallet_label": label, "wallet": wallet, "since": since,
                "timestamp": iso_timestamp(record.get("blockTime")), "slot": record.get("slot", ""),
                "signature": record.get("signature", ""), "mint": record.get("mint", ""),
                "amount": record.get("amount", ""), "decimals": record.get("decimals", ""),
                "from_wallet": record.get("fromUserAccount", ""), "to_wallet": record.get("toUserAccount", ""),
                "transfer_type": record.get("type", ""), "instruction_index": record.get("instructionIdx", ""),
                "inner_instruction_index": record.get("innerInstructionIdx", ""),
            })
    fields = ["wallet_label", "wallet", "since", "timestamp", "slot", "signature", "mint", "amount",
              "decimals", "from_wallet", "to_wallet", "transfer_type"]
    fields.extend(["instruction_index", "inner_instruction_index"])
    write_csv(ROOT / "output" / "cluster_recent_activity.csv", rows, fields)
    write_csv(ROOT / "output" / "cluster_recent_activity_coverage.csv", coverage,
              ["wallet_label", "wallet", "since", "records_returned", "pages_read", "record_cap", "sort_order", "coverage"])
    wallet_set = set(WALLETS.values())
    direct: dict[tuple[str, str, str, str, str], dict] = {}
    for row in rows:
        if row["from_wallet"] not in wallet_set or row["to_wallet"] not in wallet_set:
            continue
        key = (str(row["signature"]), str(row["instruction_index"]),
               str(row["inner_instruction_index"]), str(row["mint"]), str(row["to_wallet"]))
        direct[key] = row
    direct_rows = sorted(direct.values(), key=lambda row: row["timestamp"], reverse=True)
    write_csv(ROOT / "output" / "cluster_recent_internal_transfers.csv", direct_rows, fields)
    print(f"Since {since}: {len(rows)} transfer records across {len(WALLETS)} wallets")
    for row in coverage:
        print(f"{row['wallet_label']}: {row['records_returned']} ({row['coverage']})")


if __name__ == "__main__":
    main()
