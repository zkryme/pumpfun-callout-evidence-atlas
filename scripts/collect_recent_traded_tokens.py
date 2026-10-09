"""Collect the most recent distinct token mints touched by a wallet.

This is an inventory/discovery pass.  A token transfer is not automatically a
swap, so later position reconstruction must establish buys, sales and proceeds.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from trace_hidden_profit_exits import Helius, ROOT, SOL_MINT, USDC_MINT, iso_timestamp, load_dotenv, write_csv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wallet")
    parser.add_argument("--case-root", required=True)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--pages", type=int, default=40)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required")
    api, cursor, discovered = Helius(key, 8), None, {}
    for _ in range(args.pages):
        records, cursor = api.transfers_page(args.wallet, pagination_token=cursor, sort_order="desc")
        for row in records:
            mint = row.get("mint") or ""
            # Helius can represent merged native SOL with a one-character-short
            # pseudo-mint, while wrapped SOL uses the canonical SPL mint.
            if not mint or mint in (SOL_MINT, f"{SOL_MINT[:-1]}1", USDC_MINT):
                continue
            discovered.setdefault(mint, row)
            if len(discovered) >= args.count:
                break
        if len(discovered) >= args.count or not cursor:
            break
    rows = [{"mint": mint, "latest_timestamp": iso_timestamp(row.get("blockTime")),
             "latest_slot": row.get("slot", ""), "latest_signature": row.get("signature", ""),
             "transfer_type": row.get("type", ""), "is_pump_mint": mint.endswith("pump"),
             "discovery_note": "Most recent non-SOL/USDC token transfer touching this wallet; not automatically a swap."}
            for mint, row in discovered.items()]
    rows.sort(key=lambda row: row["latest_timestamp"], reverse=True)
    output = Path(args.case_root) / "output" / "recent_traded_tokens.csv"
    write_csv(output, rows, list(rows[0]) if rows else ["mint"])
    print(f"Collected {len(rows)} distinct recent token mints to {output}")


if __name__ == "__main__":
    main()
