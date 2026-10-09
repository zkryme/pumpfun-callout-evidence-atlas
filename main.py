from __future__ import annotations

import argparse
import logging
import os
import sys

from pump_analyzer.analysis import Analyzer
from pump_analyzer.clients import PumpClient, SolanaRpcClient, SolscanClient
from pump_analyzer.config import get_settings
from pump_analyzer.http import HttpJsonClient
from pump_analyzer.reporting import Reporter
from pump_analyzer.storage import Database, JsonCache


CHONK = "5pSEZw1iytqYznD7crsGyKNek7q5JkjBqj2KmfqxUVZd"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Analyze Pump.fun callouts, launch buyers, and repeated wallet infrastructure."
    )
    sub = result.add_subparsers(dest="command")
    sub.add_parser("collect-calls", help="Retrieve and persist the complete profile callout history")
    one = sub.add_parser("analyze-token", help="Analyze a single token mint")
    one.add_argument("mint")
    all_cmd = sub.add_parser("analyze-all", help="Resume analysis of every collected call")
    all_cmd.add_argument("--early-buyers", type=int)
    all_cmd.add_argument("--funding-depth", type=int)
    all_cmd.add_argument("--rpc-funding-limit", type=int, default=0,
                         help="Wallets per token to trace through public Solana RPC when Solscan Pro is unavailable")
    sub.add_parser("funding-graph", help="Regenerate reports and graph exports from SQLite")
    sub.add_parser("trace-all-funding", help="Trace every unique early buyer through configured RPC")
    sub.add_parser("report", help="Regenerate CSV, JSON, GraphML, and Markdown outputs")
    chonk = sub.add_parser("test-chonk", help="Run the pipeline on CHONK")
    chonk.add_argument("--rpc-funding-limit", type=int, default=0)
    result.add_argument("--force", action="store_true", help="Ignore completed checkpoints")
    result.add_argument("--verbose", action="store_true")
    result.add_argument("--profile", help="Pump.fun profile wallet to investigate")
    result.add_argument("--analysis-root", help="Separate directory for this investigation's database and exports")
    return result


def build_analyzer():
    settings = get_settings()
    db = Database(settings.database_path)
    cache = JsonCache(settings.data_dir / "cache")
    pump_api = HttpJsonClient("pump", settings.pump_api_url, cache, db,
                              requests_per_minute=120)
    # Pump's trade endpoint applies a tighter burst limit than the profile API.
    # Progressive launch-window queries normally need only one or two calls.
    pump_swap_api = HttpJsonClient("pump-swap", settings.pump_swap_api_url, cache, db,
                                   requests_per_minute=20)
    solscan_api = HttpJsonClient(
        "solscan", settings.solscan_api_url, cache, db,
        headers={"token": settings.solscan_api_key} if settings.solscan_api_key else {},
        requests_per_minute=settings.requests_per_minute,
    )
    rpc_api = HttpJsonClient(
        "solana-rpc", settings.rpc_url, cache, db,
        requests_per_minute=max(1, int(settings.rpc_requests_per_second * 60)),
        minimum_interval_seconds=1 / settings.rpc_requests_per_second,
    )
    analyzer = Analyzer(db, PumpClient(pump_api, pump_swap_api), SolscanClient(solscan_api),
                        SolanaRpcClient(rpc_api), settings)
    return settings, db, analyzer


def make_report(settings, db, analyzer):
    analyzer.rescore_all()
    reporter = Reporter(db, settings.output_dir, settings.pump_profile, analyzer.solscan_status)
    result = reporter.build()
    logging.info("Wrote report and exports to %s", settings.output_dir)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.profile:
        os.environ["PUMP_PROFILE"] = args.profile
    if args.analysis_root:
        os.environ["ANALYSIS_ROOT"] = args.analysis_root
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    settings, db, analyzer = build_analyzer()
    try:
        analyzer.check_sources()
        command = args.command or "analyze-all"
        if command == "collect-calls":
            analyzer.collect_calls()
            make_report(settings, db, analyzer)
        elif command == "analyze-token":
            if not db.conn.execute("SELECT 1 FROM calls WHERE mint=?", (args.mint,)).fetchone():
                analyzer.collect_calls()
            analyzer.analyze_token(args.mint, force=args.force)
            make_report(settings, db, analyzer)
        elif command == "test-chonk":
            analyzer.collect_calls()
            analyzer.analyze_token(CHONK, rpc_wallet_limit=args.rpc_funding_limit, force=args.force)
            make_report(settings, db, analyzer)
        elif command == "analyze-all":
            if args.early_buyers:
                settings.early_buyer_limit = args.early_buyers
            if args.funding_depth:
                settings.funding_trace_depth = args.funding_depth
            analyzer.collect_calls()
            analyzer.analyze_all(args.rpc_funding_limit, args.force)
            make_report(settings, db, analyzer)
        elif command in ("report", "funding-graph"):
            make_report(settings, db, analyzer)
        elif command == "trace-all-funding":
            stats = analyzer.trace_all_wallets(args.force)
            logging.info("Unique-wallet funding trace complete: %s", stats)
            make_report(settings, db, analyzer)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
