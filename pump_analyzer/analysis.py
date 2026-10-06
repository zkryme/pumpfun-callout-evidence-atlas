from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any
import json
import logging
import math
import time

from .clients import PumpClient, SolanaRpcClient, SolscanClient, timestamp_ms
from .http import ApiError
from .storage import Database


LOG = logging.getLogger(__name__)


def slot_from_trade(trade: dict[str, Any]) -> int | None:
    value = str(trade.get("slotIndexId") or "")
    try:
        return int(value[:12]) if len(value) >= 12 else None
    except ValueError:
        return None


def fnum(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def timing_class(seconds: float | None, call_window: int) -> str:
    if seconds is None:
        return "UNKNOWN"
    if seconds < -call_window:
        return "PRE_CALLOUT"
    if seconds <= call_window:
        return "CALL_WINDOW"
    return "POST_CALLOUT"


def freshness_class(age_seconds: int | None, brand_new: int = 600,
                    very_fresh: int = 86400) -> str:
    if age_seconds is None or age_seconds < 0:
        return "UNKNOWN"
    if age_seconds < brand_new:
        return "BRAND_NEW"
    if age_seconds < very_fresh:
        return "VERY_FRESH"
    return "ESTABLISHED"


def confidence(score: int, unknown: bool = False) -> str:
    if unknown and score == 0:
        return "UNKNOWN"
    if score >= 80:
        return "CONFIRMED"
    if score >= 60:
        return "HIGH"
    if score >= 35:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "NONE"


class Analyzer:
    def __init__(self, db: Database, pump: PumpClient, solscan: SolscanClient,
                 rpc: SolanaRpcClient, settings: Any):
        self.db, self.pump, self.solscan, self.rpc, self.settings = db, pump, solscan, rpc, settings
        self.solscan_available = False
        self.solscan_status = "not checked"

    def check_sources(self) -> None:
        self.solscan_available, self.solscan_status = self.solscan.check_access()
        LOG.info(self.solscan_status)

    def collect_calls(self) -> int:
        LOG.info("Collecting complete Pump.fun callout history for %s", self.settings.pump_profile)
        calls = self.pump.get_all_callouts(self.settings.pump_profile)
        raw_path = self.settings.data_dir / "raw" / "pump_callouts.json"
        raw_path.write_text(json.dumps(calls, ensure_ascii=False, indent=2), encoding="utf-8")
        source_url = f"https://pump.fun/profile/{self.settings.pump_profile}?tab=activity"
        with self.db.transaction() as conn:
            for call in calls:
                conn.execute(
                    """INSERT INTO calls(call_id,mint,token_name,symbol,call_timestamp,source,source_url,raw_json)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(call_id) DO UPDATE SET
                       mint=excluded.mint,call_timestamp=excluded.call_timestamp,
                       source=excluded.source,source_url=excluded.source_url,raw_json=excluded.raw_json""",
                    (call.get("calloutId"), call.get("coinMint"), None, None,
                     int(call.get("createdAt") or 0), "pump.fun", source_url,
                     json.dumps(call, ensure_ascii=False, sort_keys=True)),
                )
        self.db.checkpoint("collect-calls", self.settings.pump_profile, "complete",
                           details={"count": len(calls), "source_url": source_url})
        LOG.info("Collected %s unique calls", len(calls))
        return len(calls)

    def collect_token(self, mint: str, force: bool = False) -> dict[str, Any]:
        if not force and self.db.is_complete("token-metadata", mint):
            row = self.db.conn.execute("SELECT * FROM tokens WHERE mint=?", (mint,)).fetchone()
            return dict(row) if row else {}
        pump_coin = self.pump.get_coin(mint)
        solscan_meta: dict[str, Any] = {}
        if self.solscan_available:
            try:
                solscan_meta = self.solscan.get_token_metadata(mint)
            except ApiError as exc:
                LOG.warning("Solscan token metadata failed for %s: %s", mint, exc)
        launch_ms = timestamp_ms(pump_coin.get("created_timestamp") or solscan_meta.get("created_time"))
        supply_raw = fnum(pump_coin.get("total_supply") or solscan_meta.get("supply"))
        decimals = int(pump_coin.get("base_decimals") or solscan_meta.get("decimals") or 0)
        supply = supply_raw / (10 ** decimals) if supply_raw is not None and decimals else supply_raw
        record = {
            "mint": mint,
            "token_name": pump_coin.get("name") or solscan_meta.get("name"),
            "symbol": pump_coin.get("symbol") or solscan_meta.get("symbol"),
            "creator": pump_coin.get("creator") or solscan_meta.get("creator"),
            "launch_timestamp": launch_ms,
            "created_signature": solscan_meta.get("create_tx") or solscan_meta.get("first_mint_tx"),
            "supply": supply,
            "decimals": decimals,
            "bonding_curve": pump_coin.get("bonding_curve"),
            "program": pump_coin.get("program"),
            "status": "metadata_complete",
            "source": "pump.fun" + (" + solscan" if solscan_meta else ""),
            "raw_json": json.dumps({"pump": pump_coin, "solscan": solscan_meta}, ensure_ascii=False),
        }
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT INTO tokens(mint,token_name,symbol,creator,launch_timestamp,created_signature,
                   supply,decimals,bonding_curve,program,status,source,raw_json,updated_at)
                   VALUES(:mint,:token_name,:symbol,:creator,:launch_timestamp,:created_signature,
                   :supply,:decimals,:bonding_curve,:program,:status,:source,:raw_json,:updated_at)
                   ON CONFLICT(mint) DO UPDATE SET token_name=excluded.token_name,symbol=excluded.symbol,
                   creator=excluded.creator,launch_timestamp=excluded.launch_timestamp,
                   created_signature=excluded.created_signature,supply=excluded.supply,
                   decimals=excluded.decimals,bonding_curve=excluded.bonding_curve,
                   program=excluded.program,status=excluded.status,source=excluded.source,
                   raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                {**record, "updated_at": int(time.time())},
            )
            conn.execute("UPDATE calls SET token_name=?,symbol=? WHERE mint=?",
                         (record["token_name"], record["symbol"], mint))
        token_path = self.settings.data_dir / "tokens" / f"{mint}.json"
        token_path.write_text(json.dumps({"pump": pump_coin, "solscan": solscan_meta},
                                         ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.checkpoint("token-metadata", mint, "complete")
        return {**record, "pump_coin": pump_coin}

    def collect_early_buyers(self, mint: str, force: bool = False) -> int:
        if not force and self.db.is_complete("early-buyers", mint):
            return self.db.conn.execute(
                "SELECT COUNT(*) FROM early_buyers WHERE mint=?", (mint,)
            ).fetchone()[0]
        token = self.collect_token(mint)
        raw = json.loads(token.get("raw_json") or "{}")
        coin = raw.get("pump") or token.get("pump_coin") or {}
        trades = self.pump.get_launch_trades(
            mint, coin, unique_buyers=self.settings.early_buyer_limit
        )
        launch_ms = int(token.get("launch_timestamp") or 0)
        call_row = self.db.conn.execute(
            "SELECT call_timestamp FROM calls WHERE mint=? ORDER BY call_timestamp LIMIT 1", (mint,)
        ).fetchone()
        call_ms = int(call_row[0]) if call_row else None
        first: dict[str, dict[str, Any]] = {}
        for trade in trades:
            if trade.get("type") != "buy" or not trade.get("userAddress"):
                continue
            wallet = str(trade["userAddress"])
            t_ms = timestamp_ms(trade.get("timestamp"))
            if t_ms is None or t_ms < launch_ms:
                continue
            current = first.get(wallet)
            if current is None or t_ms < (timestamp_ms(current.get("timestamp")) or 2**63):
                first[wallet] = trade
        ordered = sorted(first.items(), key=lambda item: (
            timestamp_ms(item[1].get("timestamp")) or 0,
            str(item[1].get("slotIndexId") or ""),
        ))[: self.settings.early_buyer_limit]
        supply = fnum(token.get("supply"))
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM early_buyers WHERE mint=?", (mint,))
            conn.execute("DELETE FROM wallet_edges WHERE mint=? AND edge_type='BOUGHT'", (mint,))
            for wallet, trade in ordered:
                buy_ms = timestamp_ms(trade.get("timestamp"))
                sol = fnum(trade.get("amountSol"))
                tokens = fnum(trade.get("baseAmount"))
                percent = (tokens / supply * 100) if tokens is not None and supply else None
                relative = ((buy_ms - call_ms) / 1000) if buy_ms is not None and call_ms else None
                slot = slot_from_trade(trade)
                conn.execute(
                    """INSERT INTO early_buyers(mint,wallet,first_buy_signature,first_buy_slot,
                       first_buy_timestamp,sol_spent,tokens_received,percent_supply_received,
                       seconds_relative_to_call,timing_class,raw_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (mint, wallet, trade.get("tx"), slot, buy_ms, sol, tokens, percent,
                     relative, timing_class(relative, self.settings.call_window_seconds),
                     json.dumps(trade, ensure_ascii=False, sort_keys=True)),
                )
                conn.execute(
                    """INSERT OR REPLACE INTO wallet_edges(source,target,edge_type,mint,signature,
                       timestamp,amount,metadata_json) VALUES(?,?,?,?,?,?,?,?)""",
                    (wallet, mint, "BOUGHT", mint, trade.get("tx") or "", buy_ms, tokens,
                     json.dumps({"sol_spent": sol, "slot": slot})),
                )
        path = self.settings.data_dir / "tokens" / f"{mint}_launch_trades.json"
        path.write_text(json.dumps(trades, ensure_ascii=False, indent=2), encoding="utf-8")
        self.db.checkpoint("early-buyers", mint, "complete",
                           details={"buyers": len(ordered), "trades_examined": len(trades)})
        return len(ordered)

    @staticmethod
    def _parse_inbound_sol(tx: dict[str, Any] | None, wallet: str) -> list[dict[str, Any]]:
        if not tx:
            return []
        result: list[dict[str, Any]] = []
        message = ((tx.get("transaction") or {}).get("message") or {})
        instructions = list(message.get("instructions") or [])
        for inner in ((tx.get("meta") or {}).get("innerInstructions") or []):
            instructions.extend(inner.get("instructions") or [])
        for instruction in instructions:
            parsed = instruction.get("parsed") if isinstance(instruction, dict) else None
            if not isinstance(parsed, dict) or parsed.get("type") not in ("transfer", "transferWithSeed"):
                continue
            info = parsed.get("info") or {}
            destination = info.get("destination")
            source = info.get("source")
            lamports = info.get("lamports")
            if destination == wallet and source and lamports is not None:
                result.append({"funder": source, "amount_sol": float(lamports) / 1e9})
        return result

    def trace_wallet_rpc(self, wallet: str, buy_signature: str, buy_ms: int,
                         depth: int | None = None) -> list[dict[str, Any]]:
        depth = depth or self.settings.funding_trace_depth
        edges: list[dict[str, Any]] = []
        current, before = wallet, buy_signature
        visited = {wallet}
        for level in range(1, depth + 1):
            signatures, history_complete = self.rpc.get_signature_history_before(
                current, before=before, max_pages=self.settings.rpc_history_pages
            )
            if not history_complete:
                LOG.info(
                    "Skipping first-IN claim for %s: history exceeds %s pages",
                    current, self.settings.rpc_history_pages,
                )
                break
            chosen: dict[str, Any] | None = None
            # RPC history is newest-to-oldest. Reverse it to select the first
            # explicit inbound native-SOL transfer, like explorer "funded by".
            for item in list(reversed(signatures))[:self.settings.rpc_transaction_scan_limit]:
                block_time = item.get("blockTime")
                if block_time and block_time * 1000 > buy_ms:
                    continue
                tx = self.rpc.get_transaction(item.get("signature"))
                inbound = self._parse_inbound_sol(tx, current)
                if inbound:
                    first_meaningful = max(inbound, key=lambda e: e["amount_sol"])
                    chosen = {**first_meaningful, "signature": item.get("signature"),
                              "timestamp": block_time * 1000 if block_time else None,
                              "depth": level}
                    break
            if not chosen or chosen["funder"] in visited:
                break
            edges.append(chosen)
            visited.add(chosen["funder"])
            current, before = chosen["funder"], chosen["signature"]
        return edges

    def trace_wallet_helius(self, wallet: str, buy_ms: int,
                            depth: int | None = None) -> list[dict[str, Any]]:
        """Trace oldest inbound SOL funding with Helius parsed transfer history."""
        depth = depth or self.settings.funding_trace_depth
        edges: list[dict[str, Any]] = []
        current, cutoff_ms = wallet, buy_ms
        visited = {wallet}
        for level in range(1, depth + 1):
            chosen = self.rpc.get_first_inbound_sol_transfer(current, cutoff_ms)
            if not chosen or chosen["funder"] in visited:
                break
            chosen["depth"] = level
            edges.append(chosen)
            visited.add(chosen["funder"])
            current = chosen["funder"]
            if chosen.get("timestamp"):
                cutoff_ms = chosen["timestamp"]
        return edges

    def trace_funding(self, mint: str, rpc_wallet_limit: int = 0, force: bool = False) -> int:
        rows = self.db.conn.execute(
            "SELECT * FROM early_buyers WHERE mint=? ORDER BY first_buy_timestamp", (mint,)
        ).fetchall()
        if force:
            with self.db.transaction() as conn:
                conn.execute(
                    "DELETE FROM wallet_edges WHERE mint=? AND edge_type IN ('FUNDED','UPSTREAM_FUNDER')",
                    (mint,),
                )
        traced = 0
        for index, row in enumerate(rows):
            wallet = row["wallet"]
            if not self.solscan_available and index >= rpc_wallet_limit:
                continue
            if not force and self.db.is_complete("funding", f"{mint}:{wallet}"):
                continue
            edges: list[dict[str, Any]] = []
            evidence_source = "UNKNOWN"
            if self.solscan_available:
                try:
                    funded = self.solscan.get_funded_by(wallet)
                    candidate = funded.get("funded_by") or funded.get("funder")
                    if candidate:
                        edges = [{"funder": candidate, "depth": 1,
                                  "signature": funded.get("tx_hash") or funded.get("signature"),
                                  "timestamp": timestamp_ms(funded.get("block_time") or funded.get("timestamp")),
                                  "amount_sol": fnum(funded.get("amount"))}]
                    evidence_source = "solscan"
                except ApiError:
                    evidence_source = "solscan-unavailable"
            if not edges and index < rpc_wallet_limit and row["first_buy_signature"]:
                try:
                    edges = self.trace_wallet_rpc(wallet, row["first_buy_signature"],
                                                  row["first_buy_timestamp"])
                    evidence_source = "solana-rpc"
                except ApiError as exc:
                    LOG.warning("RPC funding trace failed for %s: %s", wallet, exc)
            root = edges[-1]["funder"] if edges else None
            with self.db.transaction() as conn:
                if force:
                    conn.execute("DELETE FROM wallet_funding WHERE buyer=?", (wallet,))
                    conn.execute(
                        """UPDATE early_buyers SET direct_funder=NULL,
                           direct_funding_signature=NULL,direct_funding_timestamp=NULL,
                           direct_funding_amount=NULL,wallet_age_at_buy=NULL,
                           first_seen_transaction=NULL,freshness='UNKNOWN'
                           WHERE mint=? AND wallet=?""",
                        (mint, wallet),
                    )
                for edge in edges:
                    conn.execute(
                        """INSERT OR REPLACE INTO wallet_funding(buyer,funder,depth,signature,
                           timestamp,amount_sol,root_candidate,evidence_source,confidence,raw_json)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (wallet, edge["funder"], edge["depth"], edge.get("signature"),
                         edge.get("timestamp"), edge.get("amount_sol"), root, evidence_source,
                         "HIGH" if evidence_source == "solscan" else "MEDIUM", json.dumps(edge)),
                    )
                    source = edge["funder"]
                    target = wallet if edge["depth"] == 1 else edges[edge["depth"] - 2]["funder"]
                    conn.execute(
                        """INSERT OR REPLACE INTO wallet_edges(source,target,edge_type,mint,signature,
                           timestamp,amount,metadata_json) VALUES(?,?,?,?,?,?,?,?)""",
                        (source, target, "FUNDED" if edge["depth"] == 1 else "UPSTREAM_FUNDER",
                         mint, edge.get("signature") or "", edge.get("timestamp"),
                         edge.get("amount_sol"), json.dumps({"depth": edge["depth"]})),
                    )
                if edges:
                    direct = edges[0]
                    # Wallet age is based on the buyer's own first inbound
                    # transfer, not an older timestamp from an upstream wallet.
                    first_activity = direct.get("timestamp")
                    age = ((row["first_buy_timestamp"] - first_activity) // 1000
                           if first_activity else None)
                    fresh = freshness_class(age)
                    conn.execute(
                        """UPDATE early_buyers SET direct_funder=?,direct_funding_signature=?,
                           direct_funding_timestamp=?,direct_funding_amount=?,wallet_age_at_buy=?,
                           first_seen_transaction=?,freshness=? WHERE mint=? AND wallet=?""",
                        (direct["funder"], direct.get("signature"), direct.get("timestamp"),
                         direct.get("amount_sol"), age, edges[-1].get("signature"), fresh,
                         mint, wallet),
                    )
                    traced += 1
            self.db.checkpoint("funding", f"{mint}:{wallet}", "complete",
                               details={"edges": len(edges), "source": evidence_source})
        return traced

    def trace_all_wallets(self, force: bool = False) -> dict[str, int]:
        """Trace each unique early buyer once, then project its funding onto every token."""
        if force:
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM wallet_funding")
                conn.execute(
                    "DELETE FROM wallet_edges WHERE edge_type IN ('FUNDED','UPSTREAM_FUNDER')"
                )
                conn.execute(
                    """UPDATE early_buyers SET direct_funder=NULL,
                       direct_funding_signature=NULL,direct_funding_timestamp=NULL,
                       direct_funding_amount=NULL,wallet_age_at_buy=NULL,
                       first_seen_transaction=NULL,freshness='UNKNOWN'"""
                )
                conn.execute("DELETE FROM checkpoints WHERE task='funding-wallet'")

        wallets = self.db.conn.execute(
            """SELECT * FROM (
                   SELECT e.*,ROW_NUMBER() OVER (
                       PARTITION BY wallet ORDER BY first_buy_timestamp, mint
                   ) AS wallet_rank
                   FROM early_buyers e
               ) WHERE wallet_rank=1 ORDER BY first_buy_timestamp,wallet"""
        ).fetchall()
        stats = {"total": len(wallets), "processed": 0, "resolved": 0, "unresolved": 0}
        for index, row in enumerate(wallets, start=1):
            wallet = row["wallet"]
            if not force and self.db.is_complete("funding-wallet", wallet):
                continue
            edges: list[dict[str, Any]] = []
            try:
                if "helius-rpc.com" in self.settings.rpc_url:
                    edges = self.trace_wallet_helius(wallet, row["first_buy_timestamp"])
                elif row["first_buy_signature"]:
                    edges = self.trace_wallet_rpc(wallet, row["first_buy_signature"],
                                                  row["first_buy_timestamp"])
            except ApiError as exc:
                LOG.warning("RPC funding trace failed for %s: %s", wallet, exc)

            root = edges[-1]["funder"] if edges else None
            token_rows = self.db.conn.execute(
                "SELECT mint,first_buy_timestamp FROM early_buyers WHERE wallet=?",
                (wallet,),
            ).fetchall()
            with self.db.transaction() as conn:
                conn.execute("DELETE FROM wallet_funding WHERE buyer=?", (wallet,))
                for edge in edges:
                    conn.execute(
                        """INSERT OR REPLACE INTO wallet_funding(buyer,funder,depth,signature,
                           timestamp,amount_sol,root_candidate,evidence_source,confidence,raw_json)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (wallet, edge["funder"], edge["depth"], edge.get("signature"),
                         edge.get("timestamp"), edge.get("amount_sol"), root, "helius-transfers",
                         "HIGH" if edge["depth"] == 1 else "MEDIUM", json.dumps(edge)),
                    )
                for token_row in token_rows:
                    mint = token_row["mint"]
                    conn.execute(
                        "DELETE FROM wallet_edges WHERE mint=? AND target=? AND edge_type='FUNDED'",
                        (mint, wallet),
                    )
                    for edge in edges:
                        target = wallet if edge["depth"] == 1 else edges[edge["depth"] - 2]["funder"]
                        conn.execute(
                            """INSERT OR REPLACE INTO wallet_edges(source,target,edge_type,mint,
                               signature,timestamp,amount,metadata_json) VALUES(?,?,?,?,?,?,?,?)""",
                            (edge["funder"], target,
                             "FUNDED" if edge["depth"] == 1 else "UPSTREAM_FUNDER",
                             mint, edge.get("signature") or "", edge.get("timestamp"),
                             edge.get("amount_sol"), json.dumps({"depth": edge["depth"]})),
                        )
                    if edges:
                        direct = edges[0]
                        age = ((token_row["first_buy_timestamp"] - direct["timestamp"]) // 1000
                               if direct.get("timestamp") else None)
                        conn.execute(
                            """UPDATE early_buyers SET direct_funder=?,
                               direct_funding_signature=?,direct_funding_timestamp=?,
                               direct_funding_amount=?,wallet_age_at_buy=?,
                               first_seen_transaction=?,freshness=?
                               WHERE mint=? AND wallet=?""",
                            (direct["funder"], direct.get("signature"), direct.get("timestamp"),
                             direct.get("amount_sol"), age, direct.get("signature"),
                             freshness_class(age), mint, wallet),
                        )
            self.db.checkpoint(
                "funding-wallet", wallet, "complete",
                details={"edges": len(edges), "source": "helius-transfers"},
            )
            stats["processed"] += 1
            stats["resolved" if edges else "unresolved"] += 1
            if index % 50 == 0 or index == len(wallets):
                LOG.info(
                    "Funding progress %s/%s unique wallets; %s resolved this run",
                    index, len(wallets), stats["resolved"],
                )
        return stats

    def score_token(self, mint: str) -> dict[str, Any]:
        rows = [dict(r) for r in self.db.conn.execute(
            "SELECT * FROM early_buyers WHERE mint=? ORDER BY first_buy_timestamp", (mint,)
        )]
        if not rows:
            result = {"status": "UNKNOWN", "score": 0, "confidence": "UNKNOWN",
                      "evidence": ["No early-buyer data available"], "wallets": []}
            self._save_cluster(mint, result)
            return result
        tx_groups = Counter(r["first_buy_signature"] for r in rows if r["first_buy_signature"])
        slot_groups = Counter(r["first_buy_slot"] for r in rows if r["first_buy_slot"] is not None)
        funder_groups = Counter(r["direct_funder"] for r in rows if r["direct_funder"])
        fresh = [r for r in rows if r["freshness"] in ("BRAND_NEW", "VERY_FRESH")]
        same_tx = max(tx_groups.values(), default=1)
        same_slot = max(slot_groups.values(), default=1)
        token_row = self.db.conn.execute(
            "SELECT creator FROM tokens WHERE mint=?", (mint,)
        ).fetchone()
        creator = token_row["creator"] if token_row else None
        suspicious_funders: list[tuple[str, int]] = []
        broad_hub_groups = 0
        for funder, count in funder_groups.items():
            global_stats = self.db.conn.execute(
                """SELECT COUNT(DISTINCT mint) token_count,COUNT(DISTINCT wallet) buyer_count
                   FROM early_buyers WHERE direct_funder=?""",
                (funder,),
            ).fetchone()
            token_count = int(global_stats["token_count"] or 0)
            # Broad funders spanning many unrelated launches are exchange,
            # custody, or trading-service context—not evidence of common control.
            ownership_link = funder in (creator, self.settings.pump_profile) or token_count <= 5
            if ownership_link:
                suspicious_funders.append((funder, count))
            elif count >= 2:
                broad_hub_groups += 1
        shared_funder, shared_count = (
            max(suspicious_funders, key=lambda item: item[1])
            if suspicious_funders else (None, 0)
        )
        evidence: list[str] = []
        score = 0
        if shared_count >= 2:
            score += 30
            evidence.append(f"{shared_count} early wallets share direct funder {shared_funder}")
        if broad_hub_groups:
            evidence.append(
                f"{broad_hub_groups} broad exchange/service-like funding groups excluded from ownership score"
            )
        if same_tx >= 2:
            score += 25
            evidence.append(f"{same_tx} early wallets bought in the same transaction")
        elif same_slot >= 3:
            score += 20
            evidence.append(f"{same_slot} early wallets bought in the same slot")
        if len(fresh) >= 3:
            score += 15
            evidence.append(f"{len(fresh)} early wallets were brand-new or very fresh")
        rounded = Counter(round(r["sol_spent"], 3) for r in rows if r["sol_spent"] is not None)
        similar = max(rounded.values(), default=1)
        if similar >= 3:
            score += 5
            evidence.append(f"{similar} early buys had near-identical SOL sizes (0.001 SOL rounding)")
        pre_call = sum(1 for r in rows if r["timing_class"] == "PRE_CALLOUT")
        if pre_call >= 3 and (same_tx >= 2 or same_slot >= 3):
            score += 10
            evidence.append(f"{pre_call} early wallets bought before the callout")
        if not evidence:
            evidence.append("No multi-signal coordination cluster identified")
        score = min(100, score)
        wallets: set[str] = set()
        for r in rows:
            if (r["first_buy_signature"] and tx_groups[r["first_buy_signature"]] >= 2) or (
                r["first_buy_slot"] is not None and slot_groups[r["first_buy_slot"]] >= 3
            ) or (r["direct_funder"] == shared_funder and shared_count >= 2):
                wallets.add(r["wallet"])
        supply_pct = sum((r["percent_supply_received"] or 0) for r in rows if r["wallet"] in wallets)
        has_strong_ownership_signal = shared_count >= 2 or len(fresh) >= 3
        result = {
            # Timing density alone is never called a bundle. It remains a
            # distinct launch-timing cluster until funding/freshness or another
            # ownership-oriented signal corroborates it.
            "status": "CONFIRMED_BUNDLE" if same_tx >= 2 and shared_count >= 2 else (
                "SUSPECTED_BUNDLE" if score >= 60 and has_strong_ownership_signal else (
                    "COORDINATED_EARLY_BUYERS" if score >= 35
                    else "NO_MULTI_SIGNAL_EVIDENCE")),
            "score": score,
            "confidence": confidence(
                score,
                unknown=sum(1 for r in rows if r.get("direct_funder")) < len(rows) * 0.8,
            ),
            "evidence": evidence,
            "wallets": sorted(wallets),
            "wallet_count": len(wallets),
            "supply_percent": supply_pct,
            "shared_funder": shared_funder if shared_count >= 2 else None,
            "root_funder": None,
            "bundle_timestamp": min((r["first_buy_timestamp"] for r in rows if r["wallet"] in wallets),
                                    default=None),
        }
        self._save_cluster(mint, result)
        return result

    def _save_cluster(self, mint: str, result: dict[str, Any]) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO bundle_clusters(mint,cluster_id,status,score,confidence,
                   wallet_count,supply_percent,shared_funder,root_funder,bundle_timestamp,evidence_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (mint, "launch", result.get("status"), result.get("score"), result.get("confidence"),
                 result.get("wallet_count", 0), result.get("supply_percent"),
                 result.get("shared_funder"), result.get("root_funder"),
                 result.get("bundle_timestamp"), json.dumps({"signals": result.get("evidence", []),
                                                              "wallets": result.get("wallets", [])})),
            )

    def analyze_token(self, mint: str, rpc_wallet_limit: int = 0, force: bool = False) -> dict[str, Any]:
        self.collect_token(mint, force)
        count = self.collect_early_buyers(mint, force)
        traced = self.trace_funding(mint, rpc_wallet_limit, force)
        result = self.score_token(mint)
        self.db.conn.execute("UPDATE tokens SET status='complete',updated_at=? WHERE mint=?",
                             (int(time.time()), mint))
        self.db.conn.commit()
        self.db.checkpoint("analyze-token", mint, "complete",
                           details={"early_buyers": count, "funded_wallets": traced,
                                    "score": result["score"]})
        return result

    def analyze_all(self, rpc_wallet_limit: int = 0, force: bool = False) -> None:
        calls = self.db.conn.execute(
            "SELECT mint,token_name,symbol FROM calls ORDER BY call_timestamp"
        ).fetchall()
        total = len(calls)
        for index, call in enumerate(calls, 1):
            mint = call["mint"]
            label = call["symbol"] or call["token_name"] or mint[:8]
            LOG.info("[%s/%s] %s | Mint: %s", index, total, label, mint)
            if not force and self.db.is_complete("analyze-token", mint):
                LOG.info("[%s/%s] cached complete", index, total)
                continue
            try:
                result = self.analyze_token(mint, rpc_wallet_limit, force)
                LOG.info("[%s/%s] buyers=%s cluster=%s score=%s confidence=%s",
                         index, total,
                         self.db.conn.execute("SELECT COUNT(*) FROM early_buyers WHERE mint=?",
                                              (mint,)).fetchone()[0],
                         result["wallet_count"], result["score"], result["confidence"])
            except Exception as exc:
                LOG.exception("[%s/%s] failed %s: %s", index, total, mint, exc)
                self.db.conn.execute("UPDATE tokens SET status='failed',error=?,updated_at=? WHERE mint=?",
                                     (str(exc)[:2000], int(time.time()), mint))
                self.db.conn.commit()
                self.db.checkpoint("analyze-token", mint, "failed", details={"error": str(exc)})

    def rescore_all(self) -> None:
        for row in self.db.conn.execute("SELECT mint FROM tokens WHERE status='complete'"):
            self.score_token(row["mint"])
