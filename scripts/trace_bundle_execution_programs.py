"""Classify the actual transaction programs behind flagged launch-buy cohorts."""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from trace_hidden_profit_exits import Helius, ROOT, load_dotenv, write_csv


JITO_GUARD = "jitodontfront111111111111111111nopainnogain"


def programs(tx: dict) -> tuple[list[str], bool]:
    outer = (tx.get("transaction") or {}).get("message", {}).get("instructions") or []
    inner = (tx.get("meta") or {}).get("innerInstructions") or []
    ids = {row.get("programId") for row in outer if row.get("programId")}
    guard = any(JITO_GUARD in (row.get("accounts") or []) for row in outer)
    for group in inner:
        for row in group.get("instructions") or []:
            if row.get("programId"):
                ids.add(row["programId"])
    return sorted(ids), guard


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-root", required=True)
    parser.add_argument("--requests-per-second", type=float, default=4)
    args = parser.parse_args()
    case = Path(args.case_root)
    db = sqlite3.connect(case / "trade_inventory_pump" / "analysis.sqlite3")
    db.row_factory = sqlite3.Row
    flagged = db.execute("""SELECT mint,status,score FROM bundle_clusters
                            WHERE status LIKE '%BUNDLE%' OR status='COORDINATED_EARLY_BUYERS'""").fetchall()
    rows = db.execute("""SELECT e.mint,e.wallet,e.first_buy_signature,e.first_buy_slot,e.first_buy_timestamp,
                               b.status,b.score FROM early_buyers e JOIN bundle_clusters b ON b.mint=e.mint
                        WHERE b.status LIKE '%BUNDLE%' OR b.status='COORDINATED_EARLY_BUYERS'""").fetchall()
    load_dotenv(ROOT / ".env")
    api = Helius(os.environ["HELIUS_API_KEY"], args.requests_per_second)
    cache, events = {}, []
    for index, row in enumerate(rows, 1):
        signature = row["first_buy_signature"]
        if signature not in cache:
            try:
                cache[signature] = api.transaction(signature)
            except RuntimeError:
                cache[signature] = None
        tx = cache[signature]
        ids, guard = programs(tx or {})
        events.append({**dict(row), "program_ids": ";".join(ids), "jito_dont_front_run_guard": guard,
                       "transaction_available": bool(tx),
                       "interpretation": "Jito guard is an execution-protection signal, not proof of a bundle or common ownership." if guard else "No Jito anti-front-run guard detected in parsed outer instructions."})
        if index % 25 == 0:
            print(f"{index}/{len(rows)} early-buy transactions classified", flush=True)
    by_mint = defaultdict(list)
    for row in events:
        by_mint[row["mint"]].append(row)
    summaries = []
    for cluster in flagged:
        group = by_mint[cluster["mint"]]
        sig_counts = Counter(row["first_buy_signature"] for row in group)
        summaries.append({"mint": cluster["mint"], "bundle_status": cluster["status"], "score": cluster["score"],
                          "early_buyers_checked": len(group), "parsed_transactions": sum(row["transaction_available"] for row in group),
                          "same_transaction_buyers": sum(count for count in sig_counts.values() if count > 1),
                          "jito_guard_transactions": len({row["first_buy_signature"] for row in group if row["jito_dont_front_run_guard"]}),
                          "program_frequency": json.dumps(Counter(program for row in group for program in row["program_ids"].split(";") if program), sort_keys=True),
                          "limitation": "Program use and transaction co-occurrence establish execution characteristics only; they do not identify the beneficial owner."})
    out = case / "trade_inventory_pump" / "output"
    write_csv(out / "bundle_execution_programs.csv", events, list(events[0]) if events else ["mint"])
    write_csv(out / "bundle_execution_summary.csv", summaries, list(summaries[0]) if summaries else ["mint"])
    print(f"Classified {len(events)} early-buy records across {len(summaries)} flagged mints")


if __name__ == "__main__":
    main()
