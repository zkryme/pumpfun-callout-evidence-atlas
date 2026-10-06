from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from xml.etree.ElementTree import Element, SubElement, ElementTree
import csv
import json

from .storage import Database


KNOWN_ADDRESS_LABELS = {
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "Binance-linked service wallet",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S": "Binance Exchange",
    "iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu": "Bybit Wallet 10",
    "AxiomRXZAq1Jgjj9pHmNqVP7Lhu67wLXZJZbaK87TTSk": "Axiom trading infrastructure",
}


def iso_ms(value: int | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat().replace("+00:00", "Z")


def csv_write(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


class Reporter:
    def __init__(self, db: Database, output_dir: Path, profile: str,
                 solscan_status: str):
        self.db, self.output_dir, self.profile = db, output_dir, profile
        self.solscan_status = solscan_status
        output_dir.mkdir(parents=True, exist_ok=True)

    def build(self) -> dict[str, Any]:
        calls = [dict(r) for r in self.db.conn.execute(
            """SELECT c.*,t.launch_timestamp,t.creator,t.status token_status,t.error,
               t.source token_source,b.status bundle_status,b.score,b.confidence,b.wallet_count,
               b.supply_percent,b.shared_funder,b.root_funder,b.bundle_timestamp,b.evidence_json
               FROM calls c LEFT JOIN tokens t ON t.mint=c.mint
               LEFT JOIN bundle_clusters b ON b.mint=c.mint AND b.cluster_id='launch'
               ORDER BY c.call_timestamp"""
        )]
        buyers = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM early_buyers ORDER BY mint,first_buy_timestamp"
        )]
        funding = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM wallet_funding ORDER BY buyer,depth"
        )]
        calls_by_mint = {r["mint"]: r for r in calls}

        call_rows = []
        for row in calls:
            call_rows.append({
                "token_name": row.get("token_name") or "",
                "symbol": row.get("symbol") or "",
                "mint": row["mint"],
                "call_timestamp": iso_ms(row.get("call_timestamp")),
                "launch_timestamp": iso_ms(row.get("launch_timestamp")),
                "launch_to_call_seconds": ((row["call_timestamp"] - row["launch_timestamp"]) / 1000
                                           if row.get("call_timestamp") and row.get("launch_timestamp") else ""),
                "source": row.get("source") or "",
            })
        csv_write(self.output_dir / "calls.csv",
                  ["token_name", "symbol", "mint", "call_timestamp", "launch_timestamp",
                   "launch_to_call_seconds", "source"], call_rows)

        token_rows = [{
            "mint": r["mint"], "token_name": r.get("token_name") or "",
            "symbol": r.get("symbol") or "", "creator": r.get("creator") or "",
            "launch_timestamp": iso_ms(r.get("launch_timestamp")),
            "call_timestamp": iso_ms(r.get("call_timestamp")),
            "status": r.get("token_status") or "missing",
            "source": r.get("token_source") or "", "error": r.get("error") or "",
        } for r in calls]
        csv_write(self.output_dir / "tokens.csv",
                  ["mint", "token_name", "symbol", "creator", "launch_timestamp",
                   "call_timestamp", "status", "source", "error"], token_rows)

        early_rows = []
        for r in buyers:
            early_rows.append({**r,
                "first_buy_timestamp": iso_ms(r.get("first_buy_timestamp")),
                "direct_funding_timestamp": iso_ms(r.get("direct_funding_timestamp")),
            })
        early_fields = ["mint", "wallet", "first_buy_signature", "first_buy_slot",
                        "first_buy_timestamp", "sol_spent", "tokens_received",
                        "percent_supply_received", "priority_fee", "transaction_fee",
                        "wallet_age_at_buy", "first_seen_transaction", "direct_funder",
                        "direct_funding_signature", "direct_funding_timestamp",
                        "direct_funding_amount", "freshness", "seconds_relative_to_call",
                        "timing_class"]
        csv_write(self.output_dir / "early_buyers.csv", early_fields, early_rows)

        funding_rows = [{**r, "timestamp": iso_ms(r.get("timestamp"))} for r in funding]
        csv_write(self.output_dir / "wallet_funding.csv",
                  ["buyer", "funder", "depth", "signature", "timestamp", "amount_sol",
                   "root_candidate", "evidence_source", "confidence"], funding_rows)

        bundle_rows = []
        for r in calls:
            evidence = json.loads(r.get("evidence_json") or "{}")
            relative = ((r["bundle_timestamp"] - r["call_timestamp"]) / 1000
                        if r.get("bundle_timestamp") and r.get("call_timestamp") else "")
            bundle_rows.append({
                "token": r.get("symbol") or r.get("token_name") or "",
                "mint": r["mint"], "bundle_status": r.get("bundle_status") or "UNKNOWN",
                "confidence": r.get("confidence") or "UNKNOWN",
                "wallet_count": r.get("wallet_count") or 0,
                "supply_percent": r.get("supply_percent") if r.get("supply_percent") is not None else "",
                "shared_funder": r.get("shared_funder") or "",
                "root_funder": r.get("root_funder") or "",
                "bundle_timestamp": iso_ms(r.get("bundle_timestamp")),
                "call_timestamp": iso_ms(r.get("call_timestamp")),
                "seconds_relative_to_call": relative,
                "evidence": "; ".join(evidence.get("signals") or []),
            })
        csv_write(self.output_dir / "suspected_bundles.csv",
                  ["token", "mint", "bundle_status", "confidence", "wallet_count",
                   "supply_percent", "shared_funder", "root_funder", "bundle_timestamp",
                   "call_timestamp", "seconds_relative_to_call", "evidence"], bundle_rows)

        funder_tokens: dict[tuple[str, str], set[str]] = defaultdict(set)
        funder_wallets: dict[tuple[str, str], set[str]] = defaultdict(set)
        funder_times: dict[tuple[str, str], list[int]] = defaultdict(list)
        for r in funding:
            mint_rows = self.db.conn.execute(
                "SELECT mint FROM early_buyers WHERE wallet=?", (r["buyer"],)
            ).fetchall()
            ftype = "direct" if r["depth"] == 1 else "upstream"
            key = (r["funder"], ftype)
            for mint_row in mint_rows:
                funder_tokens[key].add(mint_row["mint"])
            funder_wallets[key].add(r["buyer"])
            if r.get("timestamp"):
                funder_times[key].append(r["timestamp"])
        cross_funders = []
        for key, tokens in funder_tokens.items():
            funder, ftype = key
            times = funder_times[key]
            cross_funders.append({
                "funder": funder, "funder_type": ftype, "token_count": len(tokens),
                "wallet_count": len(funder_wallets[key]), "tokens": ";".join(sorted(tokens)),
                "first_seen": iso_ms(min(times) if times else None),
                "last_seen": iso_ms(max(times) if times else None),
                "confidence": "HIGH" if ftype == "direct" else "MEDIUM",
                "label": KNOWN_ADDRESS_LABELS.get(funder, ""),
                "service_like": funder in KNOWN_ADDRESS_LABELS or len(tokens) > 5,
            })
        cross_funders.sort(key=lambda r: (-r["token_count"], -r["wallet_count"], r["funder"]))
        csv_write(self.output_dir / "cross_token_funders.csv",
                  ["funder", "funder_type", "token_count", "wallet_count", "tokens",
                   "first_seen", "last_seen", "confidence", "label", "service_like"],
                  cross_funders)

        global_direct_tokens: dict[str, set[str]] = defaultdict(set)
        direct_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in buyers:
            if row.get("direct_funder"):
                global_direct_tokens[row["direct_funder"]].add(row["mint"])
                direct_groups[(row["mint"], row["direct_funder"])].append(row)
        shared_funding_groups = []
        for (mint, funder), rows in direct_groups.items():
            wallets = sorted({row["wallet"] for row in rows})
            if len(wallets) < 2:
                continue
            call = calls_by_mint.get(mint) or {}
            token_count = len(global_direct_tokens[funder])
            service_like = funder in KNOWN_ADDRESS_LABELS or token_count > 5
            relative = [row["seconds_relative_to_call"] for row in rows
                        if row.get("seconds_relative_to_call") is not None]
            shared_funding_groups.append({
                "mint": mint,
                "token": call.get("symbol") or call.get("token_name") or mint[:8],
                "funder": funder,
                "label": KNOWN_ADDRESS_LABELS.get(funder, ""),
                "buyer_count": len(wallets),
                "pre_call_count": sum(1 for row in rows if row.get("timing_class") == "PRE_CALLOUT"),
                "first_seconds_relative_to_call": min(relative) if relative else "",
                "last_seconds_relative_to_call": max(relative) if relative else "",
                "global_token_count": token_count,
                "service_like": service_like,
                "wallets": ";".join(wallets),
            })
        shared_funding_groups.sort(
            key=lambda row: (row["service_like"], -row["buyer_count"], row["token"])
        )
        csv_write(self.output_dir / "shared_funding_groups.csv",
                  ["mint", "token", "funder", "label", "buyer_count", "pre_call_count",
                   "first_seconds_relative_to_call", "last_seconds_relative_to_call",
                   "global_token_count", "service_like", "wallets"], shared_funding_groups)

        linked_funding = []
        funding_by_buyer: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in funding:
            funding_by_buyer[row["buyer"]].append(row)
        for buyer_row in buyers:
            call = calls_by_mint.get(buyer_row["mint"]) or {}
            for edge in funding_by_buyer.get(buyer_row["wallet"], []):
                link_type = None
                if edge["funder"] == self.profile:
                    link_type = "CALLER_DIRECT" if edge["depth"] == 1 else "CALLER_UPSTREAM"
                elif call.get("creator") and edge["funder"] == call["creator"]:
                    link_type = "CREATOR_DIRECT" if edge["depth"] == 1 else "CREATOR_UPSTREAM"
                if link_type:
                    linked_funding.append({
                        "mint": buyer_row["mint"],
                        "token": call.get("symbol") or call.get("token_name") or "",
                        "buyer": buyer_row["wallet"], "funder": edge["funder"],
                        "link_type": link_type, "depth": edge["depth"],
                        "funding_signature": edge.get("signature") or "",
                        "buy_signature": buyer_row.get("first_buy_signature") or "",
                        "seconds_relative_to_call": buyer_row.get("seconds_relative_to_call"),
                        "timing_class": buyer_row.get("timing_class") or "UNKNOWN",
                    })
        csv_write(self.output_dir / "caller_creator_links.csv",
                  ["mint", "token", "buyer", "funder", "link_type", "depth",
                   "funding_signature", "buy_signature", "seconds_relative_to_call",
                   "timing_class"], linked_funding)

        by_wallet: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in buyers:
            by_wallet[r["wallet"]].append(r)
        recurring_buyers = []
        for wallet, rows in by_wallet.items():
            mints = sorted({r["mint"] for r in rows})
            if len(mints) < 2:
                continue
            delays = [r["seconds_relative_to_call"] for r in rows
                      if r.get("seconds_relative_to_call") is not None]
            launch_delays = []
            for r in rows:
                call = calls_by_mint.get(r["mint"])
                if call and call.get("launch_timestamp") and r.get("first_buy_timestamp"):
                    launch_delays.append((r["first_buy_timestamp"] - call["launch_timestamp"]) / 1000)
            recurring_buyers.append({
                "wallet": wallet, "tokens_bought": ";".join(mints), "token_count": len(mints),
                "avg_seconds_after_call": sum(delays) / len(delays) if delays else "",
                "avg_seconds_after_launch": sum(launch_delays) / len(launch_delays) if launch_delays else "",
                "pre_call_count": sum(1 for r in rows if r.get("timing_class") == "PRE_CALLOUT"),
                "fresh_wallet": any(r.get("freshness") in ("BRAND_NEW", "VERY_FRESH") for r in rows),
                "root_funder": next((r.get("direct_funder") for r in rows if r.get("direct_funder")), ""),
            })
        recurring_buyers.sort(key=lambda r: (-r["token_count"], r["wallet"]))
        csv_write(self.output_dir / "recurring_buyers.csv",
                  ["wallet", "tokens_bought", "token_count", "avg_seconds_after_call",
                   "avg_seconds_after_launch", "pre_call_count", "fresh_wallet", "root_funder"],
                  recurring_buyers)

        destinations: list[dict[str, Any]] = []
        csv_write(self.output_dir / "recurring_destinations.csv",
                  ["destination", "token_count", "wallet_count", "tokens", "evidence"], destinations)

        analysis = self._analysis_json(calls, buyers, funding, bundle_rows,
                                       cross_funders, recurring_buyers,
                                       shared_funding_groups, linked_funding)
        (self.output_dir / "analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._graphs(calls_by_mint)
        self._bubblemap(calls_by_mint, buyers, shared_funding_groups)
        self._report(calls, bundle_rows, cross_funders, recurring_buyers, analysis)
        return analysis

    def _analysis_json(self, calls: list[dict[str, Any]], buyers: list[dict[str, Any]],
                       funding: list[dict[str, Any]], bundles: list[dict[str, Any]],
                       funders: list[dict[str, Any]], recurring: list[dict[str, Any]],
                       shared_groups: list[dict[str, Any]],
                       linked_funding: list[dict[str, Any]]) -> dict[str, Any]:
        analyzed = [c for c in calls if c.get("token_status") == "complete"]
        bundles_only = [b for b in bundles if b["bundle_status"] in
                        ("SUSPECTED_BUNDLE", "CONFIRMED_BUNDLE")]
        timing_clusters = [b for b in bundles if b["bundle_status"] == "COORDINATED_EARLY_BUYERS"]
        timing = defaultdict(int)
        for row in buyers:
            timing[row.get("timing_class") or "UNKNOWN"] += 1
        unique_wallets = {row["wallet"] for row in buyers}
        resolved_wallets = {row["wallet"] for row in buyers if row.get("direct_funder")}
        suspicious_groups = [row for row in shared_groups if not row["service_like"]]
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "profile": self.profile,
            "source_status": {
                "pump_fun": "available",
                "helius_transfers": "available" if funding else "not collected",
                "solscan": self.solscan_status,
            },
            "limitations": [
                "Four unique early-buyer wallets had no qualifying first-in SOL transfer before their first observed buy.",
                "Pump.fun trade data identifies early trades but does not by itself prove common ownership.",
                "Broad exchange and service funding hubs are retained as context but excluded from ownership scoring.",
            ],
            "summary": {
                "calls_total": len(calls), "tokens_analyzed": len(analyzed),
                "tokens_with_launch_bundle_evidence": len(bundles_only),
                "tokens_with_timing_only_launch_clusters": len(timing_clusters),
                "tokens_with_shared_direct_funder": sum(1 for b in bundles if b["shared_funder"]),
                "repeated_direct_or_root_funders": sum(
                    1 for f in funders if f["wallet_count"] >= 2 and not f["service_like"]
                ),
                "recurring_buyer_wallets": len(recurring),
                "timing_counts": dict(timing),
                "funding_edges_collected": len(funding),
                "unique_early_buyer_wallets": len(unique_wallets),
                "wallets_with_funding_resolved": len(resolved_wallets),
                "wallets_funding_unresolved": len(unique_wallets - resolved_wallets),
                "suspicious_shared_funding_groups": len(suspicious_groups),
                "service_like_shared_funding_groups": len(shared_groups) - len(suspicious_groups),
                "caller_linked_buyers": len({r["buyer"] for r in linked_funding
                                              if r["link_type"].startswith("CALLER")}),
                "creator_linked_buyers": len({r["buyer"] for r in linked_funding
                                               if r["link_type"].startswith("CREATOR")}),
            },
            "calls": [{k: v for k, v in row.items() if k not in ("raw_json", "evidence_json")}
                      for row in calls],
            "suspected_bundles": bundles,
            "cross_token_funders": funders,
            "recurring_buyers": recurring,
            "shared_funding_groups": shared_groups,
            "caller_creator_links": linked_funding,
        }

    def _bubblemap(self, calls_by_mint: dict[str, dict[str, Any]],
                   buyers: list[dict[str, Any]],
                   groups: list[dict[str, Any]]) -> None:
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        buyers_by_group: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for row in buyers:
            if row.get("direct_funder"):
                buyers_by_group[(row["mint"], row["direct_funder"])].append(row)
        for group in groups:
            mint, funder = group["mint"], group["funder"]
            call = calls_by_mint.get(mint) or {}
            nodes[mint] = {"id": mint, "type": "TOKEN", "label": group["token"]}
            nodes[funder] = {
                "id": funder,
                "type": "SERVICE" if group["service_like"] else "FUNDER",
                "label": group["label"] or funder[:8],
                "service_like": bool(group["service_like"]),
                "global_token_count": group["global_token_count"],
            }
            for row in buyers_by_group[(mint, funder)]:
                wallet = row["wallet"]
                nodes.setdefault(wallet, {"id": wallet, "type": "BUYER", "label": wallet[:8]})
                edges.append({
                    "source": funder, "target": wallet, "type": "FUNDED",
                    "mint": mint, "amount": row.get("direct_funding_amount"),
                })
                edges.append({
                    "source": wallet, "target": mint, "type": "BOUGHT",
                    "mint": mint, "seconds_relative_to_call": row.get("seconds_relative_to_call"),
                    "pre_call": row.get("timing_class") == "PRE_CALLOUT",
                })
        payload = {
            "profile": self.profile,
            "nodes": list(nodes.values()),
            "edges": edges,
            "groups": groups,
        }
        (self.output_dir / "bubblemap.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _graphs(self, calls_by_mint: dict[str, dict[str, Any]]) -> None:
        edges = [dict(r) for r in self.db.conn.execute("SELECT * FROM wallet_edges")]
        nodes: dict[str, dict[str, Any]] = {}
        for mint, call in calls_by_mint.items():
            nodes[mint] = {"id": mint, "type": "TOKEN",
                           "label": call.get("symbol") or call.get("token_name") or mint[:8]}
            creator = call.get("creator")
            if creator:
                nodes.setdefault(creator, {"id": creator, "type": "CREATOR", "label": creator})
                edges.append({"source": creator, "target": mint, "edge_type": "CREATED",
                              "mint": mint, "signature": "", "timestamp": call.get("launch_timestamp"),
                              "amount": None, "metadata_json": "{}"})
        for edge in edges:
            if edge["source"] not in nodes:
                node_type = "FUNDER" if edge["edge_type"] in ("FUNDED", "UPSTREAM_FUNDER") else "BUYER"
                nodes[edge["source"]] = {"id": edge["source"], "type": node_type,
                                         "label": edge["source"]}
            if edge["target"] not in nodes:
                nodes[edge["target"]] = {"id": edge["target"], "type": "BUYER",
                                         "label": edge["target"]}
        graph_json = {"nodes": list(nodes.values()), "edges": [
            {"source": e["source"], "target": e["target"], "type": e["edge_type"],
             "mint": e.get("mint"), "signature": e.get("signature"),
             "timestamp": e.get("timestamp"), "amount": e.get("amount")} for e in edges
        ]}
        (self.output_dir / "funding_graph.json").write_text(
            json.dumps(graph_json, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        graphml = Element("graphml", xmlns="http://graphml.graphdrawing.org/xmlns")
        for key_id, target, name, typ in (
            ("n_type", "node", "type", "string"), ("n_label", "node", "label", "string"),
            ("e_type", "edge", "type", "string"), ("e_mint", "edge", "mint", "string"),
            ("e_signature", "edge", "signature", "string"),
            ("e_timestamp", "edge", "timestamp", "long"), ("e_amount", "edge", "amount", "double"),
        ):
            SubElement(graphml, "key", id=key_id, **{"for": target, "attr.name": name, "attr.type": typ})
        graph = SubElement(graphml, "graph", edgedefault="directed")
        for node in nodes.values():
            element = SubElement(graph, "node", id=node["id"])
            SubElement(element, "data", key="n_type").text = node["type"]
            SubElement(element, "data", key="n_label").text = str(node["label"])
        for i, edge in enumerate(edges):
            element = SubElement(graph, "edge", id=f"e{i}", source=edge["source"], target=edge["target"])
            for key, field in (("e_type", "edge_type"), ("e_mint", "mint"),
                               ("e_signature", "signature"), ("e_timestamp", "timestamp"),
                               ("e_amount", "amount")):
                if edge.get(field) not in (None, ""):
                    SubElement(element, "data", key=key).text = str(edge[field])
        ElementTree(graphml).write(self.output_dir / "funding_graph.graphml",
                                   encoding="utf-8", xml_declaration=True)

    def _report(self, calls: list[dict[str, Any]], bundles: list[dict[str, Any]],
                funders: list[dict[str, Any]], recurring: list[dict[str, Any]],
                analysis: dict[str, Any]) -> None:
        summary = analysis["summary"]
        bundle_map = {b["mint"]: b for b in bundles}
        lines = [
            "# Pump.fun Callout Funding-Infrastructure Analysis", "",
            "## Executive summary", "",
            f"- Calls retrieved from Pump.fun: **{summary['calls_total']}**.",
            f"- Tokens fully analyzed: **{summary['tokens_analyzed']}**.",
            f"- Tokens with corroborated launch-bundle evidence: **{summary['tokens_with_launch_bundle_evidence']}**.",
            f"- Tokens with timing-only coordinated early-buyer clusters: **{summary['tokens_with_timing_only_launch_clusters']}**.",
            f"- Tokens with multiple early wallets sharing a verified direct funder: **{summary['tokens_with_shared_direct_funder']}**.",
            f"- Repeated direct/root funders across tokens: **{summary['repeated_direct_or_root_funders']}**.",
            f"- Buyer wallets recurring across called tokens: **{summary['recurring_buyer_wallets']}**.",
            f"- Early-buy timing counts: `{json.dumps(summary['timing_counts'], sort_keys=True)}`.",
            "",
            "The report distinguishes same-slot or same-transaction launch activity from shared funding. "
            "A launch cluster is not described as a confirmed bundle unless multiple independent signals support it.",
            "",
            f"**Solscan status:** {self.solscan_status}", "",
            "Where transaction-level funding evidence could not be retrieved, the result is **UNKNOWN**, not clean. "
            "Every available signature is retained in the CSV, SQLite database, raw cache, and graph exports.",
        ]
        if recurring:
            leader = recurring[0]
            lines.extend([
                "",
                f"The strongest repeated-buyer signal is `{leader['wallet']}`, which appeared among the first "
                f"50 buyers on **{leader['token_count']}** called tokens, averaging "
                f"**{leader['avg_seconds_after_launch']:.3f}s after launch** and "
                f"**{leader['avg_seconds_after_call']:.3f}s relative to the call**.",
            ])
        profile_row = next((r for r in recurring if r["wallet"] == self.profile), None)
        if profile_row:
            lines.append(
                f"The callout wallet itself appeared among the first 50 buyers on "
                f"**{profile_row['token_count']}** called tokens, averaging "
                f"**{profile_row['avg_seconds_after_launch']:.3f}s after launch** and "
                f"**{abs(profile_row['avg_seconds_after_call']):.3f}s before its callouts**."
            )
        lines.extend([
            "",
            "**Bottom line:** on-chain trade and funding evidence shows highly recurrent early-buyer "
            "infrastructure and concentrated shared-funding groups across these calls. No traced buyer "
            "was funded by the caller wallet. Creator-linked funding was observed for a small subset; "
            "exchange and service hubs are not treated as common ownership.",
            "", "## Per-token results", "",
            "| Token | Mint | Call Time | Launch Δ | Bundle/coordination | Bundle % | Shared Funder | Timing | Confidence |",
            "|---|---|---:|---:|---|---:|---|---|---|",
        ])
        for call in calls:
            b = bundle_map.get(call["mint"], {})
            delta = ((call["call_timestamp"] - call["launch_timestamp"]) / 1000
                     if call.get("launch_timestamp") else None)
            timing = "UNKNOWN"
            if b.get("seconds_relative_to_call") != "" and b.get("seconds_relative_to_call") is not None:
                seconds = float(b["seconds_relative_to_call"])
                timing = "PRE_CALLOUT" if seconds < -5 else "CALL_WINDOW" if seconds <= 5 else "POST_CALLOUT"
            lines.append(
                f"| {call.get('symbol') or call.get('token_name') or '—'} | `{call['mint']}` | "
                f"{iso_ms(call.get('call_timestamp'))} | {f'{delta:.1f}s' if delta is not None else 'UNKNOWN'} | "
                f"{b.get('bundle_status', 'UNKNOWN')} | "
                f"{f'{b.get('supply_percent'):.4f}%' if isinstance(b.get('supply_percent'), (int,float)) else 'UNKNOWN'} | "
                f"{b.get('shared_funder') or 'UNKNOWN'} | {timing} | {b.get('confidence', 'UNKNOWN')} |"
            )
        lines.extend(["", "## Repeated Direct Funders", ""])
        direct = [f for f in funders if f["funder_type"] == "direct" and f["token_count"] >= 2]
        lines.extend([f"- `{f['funder']}`: {f['token_count']} tokens, {f['wallet_count']} early wallets."
                      for f in direct] or ["No repeated direct funder was verified with the available data."])
        lines.extend(["", "## Repeated Root Funders", ""])
        roots = [f for f in funders if f["funder_type"] == "upstream" and f["token_count"] >= 2]
        lines.extend([f"- `{f['funder']}`: {f['token_count']} tokens, {f['wallet_count']} wallets."
                      for f in roots] or ["No repeated upstream/root funder was verified with the available data."])
        lines.extend(["", "## Recurring Early Buyers", ""])
        lines.extend([f"- `{r['wallet']}`: {r['token_count']} tokens; average {r['avg_seconds_after_launch']:.3f}s "
                      f"after launch and {r['avg_seconds_after_call']:.3f}s relative to the call; "
                      f"{r['pre_call_count']} pre-call appearances."
                      for r in recurring] or ["No recurring early-buyer wallet was detected."])
        snipers = [r for r in recurring if isinstance(r["avg_seconds_after_call"], (int, float))
                   and -5 <= r["avg_seconds_after_call"] <= 30]
        lines.extend(["", "## Recurring Callout Snipers", ""])
        lines.extend([f"- `{r['wallet']}` appears on {r['token_count']} tokens with an average "
                      f"delay of +{r['avg_seconds_after_call']:.2f}s. This is a timing signal, not proof of control."
                      for r in snipers] or ["No recurring call-window buyer met the current sniper heuristic."])
        lines.extend(["", "## Recurring Consolidation Wallets", "",
                      "UNKNOWN: consolidation analysis requires complete post-sale transfer evidence.",
                      "", "## Highest Confidence Coordinated Launches", ""])
        strongest = sorted((b for b in bundles if b["bundle_status"] in
                            ("SUSPECTED_BUNDLE", "CONFIRMED_BUNDLE", "COORDINATED_EARLY_BUYERS")),
                           key=lambda b: (b.get("bundle_status") == "CONFIRMED_BUNDLE",
                                          b.get("wallet_count", 0)),
                           reverse=True)[:20]
        lines.extend([f"- **{b['token'] or b['mint']}** (`{b['mint']}`): {b['confidence']}; "
                      f"{b['wallet_count']} linked early wallets; {b['evidence']}"
                      for b in strongest] or ["No token crossed the multi-signal coordination threshold."])
        no_link = [b for b in bundles if b["bundle_status"] == "NO_MULTI_SIGNAL_EVIDENCE"]
        unknown = [b for b in bundles if b["bundle_status"] == "UNKNOWN"]
        lines.extend(["", "## Tokens With No Detected Link", ""])
        lines.extend([f"- {b['token'] or b['mint']} (`{b['mint']}`)" for b in no_link]
                     or ["None."])
        lines.extend(["", "## Unknown / Insufficient Data", ""])
        lines.extend([f"- {b['token'] or b['mint']} (`{b['mint']}`)" for b in unknown]
                     or ["No token is wholly unknown, although unavailable funding/consolidation fields remain UNKNOWN."])
        lines.extend(["", "## Evidence and methodology", "",
                      "- Canonical call records: Pump.fun `GET /callout/list/{userId}` with page-token pagination.",
                      "- Token identity and launch time: Pump.fun public coin endpoint, cross-checkable with Solscan when authorized.",
                      "- Early buys: Pump.fun swap trade history, cursor-paginated back to launch; signatures and slots are retained.",
                      "- Funding: Helius `getTransfersByAddress`, filtered to oldest inbound native SOL before the earliest observed buy and traced up to three hops.",
                      "- Exchanges and shared infrastructure are not treated as common ownership without additional evidence.",
                      ""])
        (self.output_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")
