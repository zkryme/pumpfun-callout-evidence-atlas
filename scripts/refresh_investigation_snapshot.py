"""Attach audited, static-investigation datasets to the public snapshot."""
from __future__ import annotations

import json
from pathlib import Path

from trace_hidden_profit_exits import CALLER, ROOT, read_csv


def main() -> None:
    path = ROOT / "output" / "analysis.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    positions = read_csv(ROOT / "output" / "caller_funder_pnl.csv")
    ledger = read_csv(ROOT / "output" / "caller_funder_sales.csv")
    cleanup = read_csv(ROOT / "output" / "caller_funder_cleanup.csv")
    direct = [row for row in cleanup if row.get("destination") == CALLER]
    dedup: dict[str, dict[str, str]] = {row["cleanup_signature"]: row for row in direct}
    snapshot["investigation"] = {
        "classification_legend": {
            "SUPPORTED_FINDING": "Observed on-chain evidence directly supports this scoped finding.",
            "NEEDS_VERIFICATION": "A signal exists, but transaction types or missing history prevent attribution.",
            "SCOPED_NEGATIVE": "No link was found within the stated dataset, depth, and time window.",
            "INCOMPLETE": "Observed data is useful, but reconciliation is not complete.",
        },
        "findings": [
            {"status": "SUPPORTED_FINDING", "finding": "Four-wallet operational cluster", "evidence": "CLiX directly funded the caller, GKM, and 1chd; the caller, GKM, and 1chd bought 13 called tokens in the same slot.", "limitations": "Direct funding and repeated coordination do not identify the real-world key holder."},
            {"status": "SUPPORTED_FINDING", "finding": "13 direct SOL transfers reached the caller", "evidence": f"{len(dedup)} unique cleanup signatures total {sum(float(r['amount_sol']) for r in dedup.values()):.9f} SOL.", "limitations": "Transfers are separate from token-position cash flow and do not establish profit attribution."},
            {"status": "SCOPED_NEGATIVE", "finding": "No caller-to-founder link found within analyzed scope", "evidence": "97 calls; first-in funding traces up to three hops; 30-day pre-launch founder funding review.", "limitations": "Four early-buyer wallets have unresolved first-in funding; service-like ancestry is not ownership evidence."},
            {"status": "NEEDS_VERIFICATION", "finding": "CHONK inbound activity", "evidence": "91.44 SOL of unclassified inbound/cleanup activity appears in the retained raw investigation data.", "limitations": "Swap proceeds, funding, and other transaction types have not been separated; it is not a funding or ownership finding."},
            {"status": "INCOMPLETE", "finding": "CLiX position cash-flow reconstruction", "evidence": f"{len(positions)} positions and {len(ledger)} observed token events were reconstructed from the 24-hour windows.", "limitations": "Five positions sell more tokens than the window observed buying; cost basis and realized profit are therefore not asserted."},
        ],
        "position_analysis": positions,
        "position_ledger": ledger,
        "direct_caller_transfers": list(dedup.values()),
        "coverage": {
            "pump_fun": "available: calls, launch times, first-50 trades and slots",
            "helius": "available: transfer and parsed transaction records used for linked-wallet audit",
            "solscan": "unavailable during collection; identifiers link out for independent inspection",
            "fees": "unavailable for 4,850 early-buyer records; not displayed as zero",
            "root_funder": "unavailable for 97 classification rows; hidden from default tables",
            "funding": "3,098 / 3,102 early-buyer wallets resolved to a qualifying first-in transfer",
        },
        "data_catalog": {
            "calls": "calls.csv", "early_buyers": "early_buyers.csv", "funding": "wallet_funding.csv",
            "linked_positions": "caller_funder_pnl.csv", "linked_event_ledger": "caller_funder_sales.csv",
            "cleanup": "caller_funder_cleanup.csv", "cluster": "coordinated_wallet_cluster.csv",
        },
    }
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2)
    path.write_text(rendered, encoding="utf-8")
    (ROOT / "web" / "public" / "data" / "analysis.json").write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
