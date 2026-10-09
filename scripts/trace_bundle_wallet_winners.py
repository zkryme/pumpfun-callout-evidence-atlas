"""Rank exit proceeds for wallets in flagged launch cohorts, with full transaction ledgers."""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trace_hidden_profit_exits import Helius, ROOT, SOL_MINT, iso_timestamp, load_dotenv, transaction_deltas, write_csv


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-root", required=True)
    parser.add_argument("--requests-per-second", type=float, default=5)
    args = parser.parse_args()
    case = Path(args.case_root)
    db = sqlite3.connect(case / "trade_inventory_pump" / "analysis.sqlite3")
    db.row_factory = sqlite3.Row
    cohort = db.execute("""SELECT DISTINCT e.mint,e.wallet,e.first_buy_timestamp,e.first_buy_signature
                           FROM early_buyers e JOIN bundle_clusters b ON b.mint=e.mint
                           WHERE b.status LIKE '%BUNDLE%' OR b.status='COORDINATED_EARLY_BUYERS'""").fetchall()
    decimals = {row["mint"]: row["decimals"] for row in db.execute("SELECT mint,decimals FROM tokens")}
    load_dotenv(ROOT / ".env")
    api, tx_cache, events = Helius(os.environ["HELIUS_API_KEY"], args.requests_per_second), {}, []
    for index, row in enumerate(cohort, 1):
        try:
            transfers = api.transfers(row["wallet"], mint=row["mint"], start=row["first_buy_timestamp"] // 1000)
        except RuntimeError:
            continue
        scale = 10 ** int(decimals.get(row["mint"]) or 0)
        for transfer in transfers:
            signature = transfer.get("signature", "")
            if not signature:
                continue
            if signature not in tx_cache:
                try:
                    tx_cache[signature] = api.transaction(signature)
                except RuntimeError:
                    tx_cache[signature] = None
            tx = tx_cache[signature]
            if not tx:
                continue
            sol, tokens = transaction_deltas(tx, row["wallet"])
            token_delta = tokens.get(row["mint"], 0) / scale
            wsol = tokens.get(SOL_MINT, 0) / 1_000_000_000
            if not token_delta or (abs(sol) < 0.000001 and abs(wsol) < 0.000001):
                continue
            events.append({"mint": row["mint"], "wallet": row["wallet"], "first_buy_signature": row["first_buy_signature"],
                           "event_timestamp": iso_timestamp(tx.get("blockTime")), "signature": signature,
                           "token_delta": token_delta, "native_sol_delta": sol, "wsol_delta": wsol,
                           "event_class": "TOKEN_DISPOSAL_WITH_PROCEEDS" if token_delta < 0 and (sol > .001 or wsol > .001) else "TOKEN_ACQUISITION_OR_OTHER",
                           "limitation": "Wallet-level deltas are not cost-basis PnL. Transfers in/out and unavailable history can affect attribution."})
        if index % 25 == 0:
            print(f"{index}/{len(cohort)} cohort wallet-token positions traced", flush=True)
    grouped = defaultdict(list)
    for event in events:
        grouped[(event["wallet"], event["mint"])].append(event)
    positions = []
    for (wallet, mint), values in grouped.items():
        sales = [value for value in values if value["event_class"] == "TOKEN_DISPOSAL_WITH_PROCEEDS"]
        positions.append({"wallet": wallet, "mint": mint, "sale_events": len(sales),
                          "observed_native_sol_receipts": sum(float(value["native_sol_delta"]) for value in sales),
                          "observed_wsol_receipts": sum(float(value["wsol_delta"]) for value in sales),
                          "net_native_sol_delta_all_events": sum(float(value["native_sol_delta"]) for value in values),
                          "classification": "OBSERVED_PROCEEDS_NOT_PROFIT",
                          "limitation": "Ranked by receipts, not profit. Original cost, fees, and any missing or transferred inventory are not reconciled."})
    positions.sort(key=lambda row: row["observed_native_sol_receipts"] + row["observed_wsol_receipts"], reverse=True)
    out = case / "trade_inventory_pump" / "output"
    write_csv(out / "bundle_wallet_exit_ledger.csv", events, list(events[0]) if events else ["mint"])
    write_csv(out / "bundle_wallet_winner_positions.csv", positions, list(positions[0]) if positions else ["wallet"])
    print(f"Wrote {len(events)} ledger events and {len(positions)} wallet-token positions")


if __name__ == "__main__":
    main()
