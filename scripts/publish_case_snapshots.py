"""Publish isolated investigation snapshots for the static case selector."""
from __future__ import annotations

import shutil
import csv
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROFILES = (
    "EqBQhoU88ty3a3aDphDRCBuiRirjUsFSFi4UHGY64qvv",
    "215nhcAHjQQGgwpQSJQ7zR26etbjjtVdW74NLzwEgQjP",
)


def main() -> None:
    destination = ROOT / "web" / "public" / "data" / "cases"
    destination.mkdir(parents=True, exist_ok=True)
    for profile in PROFILES:
        source = ROOT / "investigations" / profile / "trade_inventory_pump" / "output" / "analysis.json"
        if source.exists():
            payload = json.loads(source.read_text(encoding="utf-8"))
            output = source.parent
            def rows(name: str) -> list[dict[str, str]]:
                path = output / name
                if not path.exists():
                    return []
                with path.open(encoding="utf-8-sig", newline="") as handle:
                    return list(csv.DictReader(handle))
            winners = rows("bundle_wallet_winner_positions.csv")
            winners.sort(key=lambda row: float(row.get("observed_native_sol_receipts") or 0) + float(row.get("observed_wsol_receipts") or 0), reverse=True)
            payload["trade_scope"] = {
                "inventory": "Most recent distinct non-SOL/USDC token mints from public Helius transfer history.",
                "bundle_scope": "Pump-compatible mints only; ordinary SPL assets are retained in the inventory but cannot be tested with Pump launch-trade data.",
                "winner_scope": "Ranked by observed wallet-level sale receipts, not profit. Cost basis, fees, and transferred inventory are not fully reconciled.",
            }
            payload["execution_summary"] = rows("bundle_execution_summary.csv")
            payload["top_winner_positions"] = winners[:20]
            payload["top_winner_forwards"] = rows("top_winner_proceeds_forwards.csv")
            (destination / f"{profile}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
