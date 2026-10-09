"""Run the bundle/funding pipeline against a separately collected trade inventory."""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pump_analyzer.analysis import Analyzer
from pump_analyzer.clients import PumpClient, SolanaRpcClient, SolscanClient
from pump_analyzer.config import get_settings
from pump_analyzer.http import HttpJsonClient
from pump_analyzer.reporting import Reporter
from pump_analyzer.storage import Database, JsonCache


ROOT = Path(__file__).resolve().parent.parent


def timestamp_ms(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile")
    parser.add_argument("--case-root", required=True)
    parser.add_argument("--rpc-wallet-limit", type=int, default=50)
    args = parser.parse_args()
    source_case = Path(args.case_root)
    trade_root = source_case / "trade_inventory_pump"
    os.environ["PUMP_PROFILE"] = args.profile
    os.environ["ANALYSIS_ROOT"] = str(trade_root)
    settings = get_settings()
    db = Database(settings.database_path)
    cache = JsonCache(settings.data_dir / "cache")
    pump = PumpClient(HttpJsonClient("pump", settings.pump_api_url, cache, db, requests_per_minute=120),
                      HttpJsonClient("pump-swap", settings.pump_swap_api_url, cache, db, requests_per_minute=20))
    analyzer = Analyzer(db, pump,
                        SolscanClient(HttpJsonClient("solscan", settings.solscan_api_url, cache, db,
                                                     headers={"token": settings.solscan_api_key} if settings.solscan_api_key else {}, requests_per_minute=settings.requests_per_minute)),
                        SolanaRpcClient(HttpJsonClient("solana-rpc", settings.rpc_url, cache, db,
                                                       requests_per_minute=max(1, int(settings.rpc_requests_per_second * 60)), minimum_interval_seconds=1 / settings.rpc_requests_per_second)), settings)
    try:
        analyzer.check_sources()
        with (source_case / "output" / "recent_traded_tokens.csv").open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        # Pump's launch-trade endpoint is only authoritative for Pump mints.
        # Keep the full discovery inventory for PnL work, but do not burn bundle
        # analysis calls on unrelated SPL assets such as JUP or USDC pairs.
        rows = [row for row in rows if row.get("is_pump_mint") == "True"]
        with db.transaction() as conn:
            for row in rows:
                mint = row["mint"]
                conn.execute("""INSERT OR IGNORE INTO calls(call_id,mint,call_timestamp,source,source_url,raw_json)
                                VALUES(?,?,?,?,?,?)""",
                             (f"recent-trade:{mint}", mint, timestamp_ms(row["latest_timestamp"]),
                              "helius recent token inventory", f"https://solscan.io/account/{args.profile}", "{}"))
        analyzer.analyze_all(args.rpc_wallet_limit)
        analyzer.rescore_all()
        Reporter(db, settings.output_dir, args.profile, analyzer.solscan_status).build()
    finally:
        db.close()


if __name__ == "__main__":
    main()
