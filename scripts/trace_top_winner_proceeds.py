"""Forward-trace large observed bundle-wallet proceeds without asserting ownership."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trace_hidden_profit_exits import Helius, ROOT, SOL_MINT, iso_timestamp, load_dotenv, read_csv, write_csv
from trace_forwarding_chains import direct_system_transfers


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--top", type=int, default=30)
    parser.add_argument("--hours", type=float, default=24)
    args = parser.parse_args()
    case = Path(args.case_root)
    sales = [row for row in read_csv(case / "trade_inventory_pump" / "output" / "bundle_wallet_exit_ledger.csv")
             if row["event_class"] == "TOKEN_DISPOSAL_WITH_PROCEEDS" and float(row["native_sol_delta"]) > 0.05]
    sales.sort(key=lambda row: float(row["native_sol_delta"]), reverse=True)
    load_dotenv(ROOT / ".env")
    api, tx_cache, output, seen = Helius(os.environ["HELIUS_API_KEY"], 6), {}, [], set()
    for sale in sales[:args.top]:
        start = int(__import__('datetime').datetime.fromisoformat(sale["event_timestamp"].replace("Z", "+00:00")).timestamp())
        try:
            records = api.transfers(sale["wallet"], mint=SOL_MINT, direction="out", start=start, end=start + int(args.hours * 3600))
        except RuntimeError:
            continue
        for record in records:
            signature = record.get("signature", "")
            if signature not in tx_cache:
                try: tx_cache[signature] = api.transaction(signature)
                except RuntimeError: tx_cache[signature] = None
            tx = tx_cache[signature]
            if not tx: continue
            for destination, amount in direct_system_transfers(tx, sale["wallet"]):
                if amount < .05: continue
                key = (sale["wallet"], signature, destination)
                if key in seen:
                    continue
                seen.add(key)
                output.append({"winner_wallet": sale["wallet"], "mint": sale["mint"], "sale_signature": sale["signature"],
                               "sale_timestamp": sale["event_timestamp"], "sale_native_sol_receipt": sale["native_sol_delta"],
                               "forward_timestamp": iso_timestamp(tx.get("blockTime")), "forward_signature": signature,
                               "destination": destination, "forward_sol": amount, "direct_to_profile": destination == args.profile,
                               "limitation": "Forward transfer is observable; destination ownership and full exchange routing are not established."})
    out = case / "trade_inventory_pump" / "output" / "top_winner_proceeds_forwards.csv"
    fields = list(output[0]) if output else ["winner_wallet", "destination"]
    write_csv(out, output, fields)
    print(f"Forward transfers: {len(output)}; direct-to-profile: {sum(row['direct_to_profile'] for row in output)}")


if __name__ == "__main__":
    main()
