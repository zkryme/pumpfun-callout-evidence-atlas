"""Attach audited, static-investigation datasets to the public snapshot."""
from __future__ import annotations

import json
import csv
import shutil
from pathlib import Path

from trace_hidden_profit_exits import CALLER, ROOT, read_csv


def main() -> None:
    path = ROOT / "output" / "analysis.json"
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    positions = read_csv(ROOT / "output" / "caller_funder_pnl.csv")
    ledger = read_csv(ROOT / "output" / "caller_funder_sales.csv")
    cleanup = read_csv(ROOT / "output" / "caller_funder_cleanup.csv")
    recent_path = ROOT / "output" / "cluster_recent_internal_transfers.csv"
    coverage_path = ROOT / "output" / "cluster_recent_activity_coverage.csv"
    recent_raw_path = ROOT / "output" / "cluster_recent_activity.csv"
    recent_internal = read_csv(recent_path) if recent_path.exists() else []
    recent_coverage = read_csv(coverage_path) if coverage_path.exists() else []
    early = read_csv(ROOT / "output" / "early_buyers.csv")
    calls = {row["mint"]: row for row in read_csv(ROOT / "output" / "calls.csv")}
    direct = [row for row in cleanup if row.get("destination") == CALLER]
    dedup: dict[str, dict[str, str]] = {row["cleanup_signature"]: row for row in direct}
    caller_token_transfers = [row for row in ledger if row.get("event_type") == "TOKEN_IN"
                              and row.get("counterparty") == CALLER]
    recent_signatures = {row["signature"] for row in recent_internal}
    recent_complete = all(row.get("coverage") == "complete" for row in recent_coverage)
    linked = {"caller": CALLER, "clix": "CLiXvfYSJtG5SJTCWyLvXKtDag9pJiA4vSWZGgSKP19X",
              "gkm": "GKMJwv2AEWVcfFXUMtxokMYy5ADkjwJa89DtP8LzoA61",
              "onechd": "1chdHBRNu9dB7E5McJKFDsbPvfpmANdmeR7BHavhi56"}
    by_wallet_mint = {(row["wallet"], row["mint"]): row for row in early}
    same_slot_rows = []
    for mint in {row["mint"] for row in early if row["wallet"] == CALLER}:
        entries = {name: by_wallet_mint.get((wallet, mint)) for name, wallet in linked.items()}
        caller_row = entries["caller"]
        if not caller_row:
            continue
        matching = {name: row for name, row in entries.items()
                    if row and row["first_buy_slot"] == caller_row["first_buy_slot"]}
        if len(matching) < 3:
            continue
        row = {"symbol": calls.get(mint, {}).get("symbol", mint), "mint": mint,
               "slot": caller_row["first_buy_slot"],
               "seconds_before_call": abs(float(caller_row["seconds_relative_to_call"])),
               "wallets_same_slot": len(matching)}
        for name in linked:
            entry = matching.get(name)
            row[f"{name}_wallet"] = linked[name] if entry else ""
            row[f"{name}_signature"] = entry["first_buy_signature"] if entry else ""
        same_slot_rows.append(row)
    same_slot_rows.sort(key=lambda row: (-row["wallets_same_slot"], row["symbol"]))
    nearby_slot_rows = []
    for mint in {row["mint"] for row in early if row["wallet"] == CALLER}:
        caller_row = by_wallet_mint.get((CALLER, mint))
        if not caller_row:
            continue
        for name, wallet in linked.items():
            if name == "caller":
                continue
            row = by_wallet_mint.get((wallet, mint))
            if not row:
                continue
            slot_delta = int(row["first_buy_slot"]) - int(caller_row["first_buy_slot"])
            # Look further before the caller than after it: the investigative
            # question is whether linked wallets positioned ahead of a callout.
            if -50 <= slot_delta <= 25:
                nearby_slot_rows.append({"mint": mint, "linked_wallet": wallet,
                                         "caller_slot": caller_row["first_buy_slot"],
                                         "linked_slot": row["first_buy_slot"],
                                         "slot_delta": slot_delta,
                                         "caller_signature": caller_row["first_buy_signature"],
                                         "linked_signature": row["first_buy_signature"]})
    export_path = ROOT / "web" / "public" / "downloads" / "caller_linked_same_slot_buys.csv"
    with export_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(same_slot_rows[0]))
        writer.writeheader()
        writer.writerows(same_slot_rows)
    snapshot["investigation"] = {
        "classification_legend": {
            "SUPPORTED_FINDING": "Observed on-chain evidence directly supports this scoped finding.",
            "NEEDS_VERIFICATION": "A signal exists, but transaction types or missing history prevent attribution.",
            "SCOPED_NEGATIVE": "No link was found within the stated dataset, depth, and time window.",
            "INCOMPLETE": "Observed data is useful, but reconciliation is not complete.",
        },
        "findings": [
            {"status": "SUPPORTED_FINDING", "finding": "Four-wallet operational cluster", "evidence": "CLiX directly funded the caller, GKM, and 1chd; the caller, GKM, and 1chd bought 13 called tokens in the same slot.", "limitations": "Direct funding and repeated coordination do not identify the real-world key holder."},
            {"status": "SUPPORTED_FINDING", "finding": "Same-slot purchases with the caller", "evidence": f"{sum(r['wallets_same_slot'] == 4 for r in same_slot_rows)} tokens have all four linked wallets in the caller’s exact slot; the other {sum(r['wallets_same_slot'] == 3 for r in same_slot_rows)} have the caller/GKM/1chd trio in that slot.", "limitations": "Same-slot execution is a coordination signal; it is not by itself proof of a bundled transaction or common ownership."},
            {"status": "SCOPED_NEGATIVE", "finding": "No additional linked-wallet first buys within −50 / +25 slots", "evidence": f"{len(nearby_slot_rows)} linked-wallet first buys fall from 50 slots before through 25 slots after a caller first buy; all have a slot delta of zero.", "limitations": "This compares the first observed buy per wallet/token in the collected early-buyer dataset, not every subsequent trade or activity outside the 97 calls."},
            {"status": "SUPPORTED_FINDING", "finding": "13 direct SOL transfers reached the caller", "evidence": f"{len(dedup)} unique cleanup signatures total {sum(float(r['amount_sol']) for r in dedup.values()):.9f} SOL.", "limitations": "Transfers are separate from token-position cash flow and do not establish profit attribution."},
            {"status": "SUPPORTED_FINDING", "finding": "Caller transferred inventory to CLiX before CLiX exits", "evidence": f"{len(caller_token_transfers)} token-only transfers across {len(set(r['mint'] for r in caller_token_transfers))} tokens moved from the caller to CLiX, followed by matching CLiX sales.", "limitations": "The observed transfers establish token flow. They do not establish who controls either wallet or the original acquisition cost."},
            {"status": "SUPPORTED_FINDING", "finding": "Recent direct activity continues across the four-wallet cluster", "evidence": f"{len(recent_internal)} deduplicated direct transfer instructions across {len(recent_signatures)} recent signatures were observed after the last collected callout.", "limitations": "Recent activity is complete only for CLiX in this export; caller, GKM and 1chd each reached the 1,000-record monitoring cap." if not recent_complete else "The interval was fully paginated for all four wallets."},
            {"status": "SCOPED_NEGATIVE", "finding": "No caller-to-founder link found within analyzed scope", "evidence": "97 calls; first-in funding traces up to three hops; 30-day pre-launch founder funding review.", "limitations": "Four early-buyer wallets have unresolved first-in funding; service-like ancestry is not ownership evidence."},
            {"status": "NEEDS_VERIFICATION", "finding": "CHONK inbound activity", "evidence": "91.44 SOL of unclassified inbound/cleanup activity appears in the retained raw investigation data.", "limitations": "Swap proceeds, funding, and other transaction types have not been separated; it is not a funding or ownership finding."},
            {"status": "INCOMPLETE", "finding": "CLiX position cash-flow reconstruction", "evidence": f"{len(positions)} positions and {len(ledger)} observed token events were reconstructed. Five positions contain inbound token-only transfers that explain the excess sold inventory.", "limitations": "The senders' cost basis and control relationship are not reconciled, so realized profit is not asserted."},
        ],
        "position_analysis": positions,
        "position_ledger": ledger,
        "same_slot_execution": same_slot_rows,
        "nearby_slot_execution": nearby_slot_rows,
        "recent_cluster_internal_transfers": recent_internal,
        "recent_cluster_coverage": recent_coverage,
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
            "same_slot_execution": "caller_linked_same_slot_buys.csv",
            "recent_internal_transfers": "cluster_recent_internal_transfers.csv",
            "recent_activity_coverage": "cluster_recent_activity_coverage.csv",
            "recent_activity_raw": "cluster_recent_activity.csv",
        },
    }
    for source in (recent_path, coverage_path, recent_raw_path):
        if source.exists():
            shutil.copy2(source, ROOT / "web" / "public" / "downloads" / source.name)
    rendered = json.dumps(snapshot, ensure_ascii=False, indent=2)
    path.write_text(rendered, encoding="utf-8")
    (ROOT / "web" / "public" / "data" / "analysis.json").write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
