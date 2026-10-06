from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
import hashlib
import json
import sqlite3
import time


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS calls (
  call_id TEXT PRIMARY KEY, mint TEXT NOT NULL, token_name TEXT, symbol TEXT,
  call_timestamp INTEGER NOT NULL, source TEXT NOT NULL, source_url TEXT,
  raw_json TEXT NOT NULL, UNIQUE(mint, call_timestamp)
);
CREATE INDEX IF NOT EXISTS idx_calls_mint ON calls(mint);
CREATE INDEX IF NOT EXISTS idx_calls_timestamp ON calls(call_timestamp);

CREATE TABLE IF NOT EXISTS tokens (
  mint TEXT PRIMARY KEY, token_name TEXT, symbol TEXT, creator TEXT,
  launch_timestamp INTEGER, created_signature TEXT, supply REAL, decimals INTEGER,
  bonding_curve TEXT, program TEXT, status TEXT NOT NULL DEFAULT 'pending',
  source TEXT, error TEXT, raw_json TEXT, updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tokens_creator ON tokens(creator);

CREATE TABLE IF NOT EXISTS transactions (
  signature TEXT PRIMARY KEY, slot INTEGER, timestamp INTEGER, fee REAL,
  priority_fee REAL, signer TEXT, raw_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_transactions_slot ON transactions(slot);
CREATE INDEX IF NOT EXISTS idx_transactions_timestamp ON transactions(timestamp);

CREATE TABLE IF NOT EXISTS early_buyers (
  mint TEXT NOT NULL, wallet TEXT NOT NULL, first_buy_signature TEXT,
  first_buy_slot INTEGER, first_buy_timestamp INTEGER, sol_spent REAL,
  tokens_received REAL, percent_supply_received REAL, priority_fee REAL,
  transaction_fee REAL, wallet_age_at_buy INTEGER, first_seen_transaction TEXT,
  direct_funder TEXT, direct_funding_signature TEXT, direct_funding_timestamp INTEGER,
  direct_funding_amount REAL, freshness TEXT DEFAULT 'UNKNOWN',
  seconds_relative_to_call REAL, timing_class TEXT, raw_json TEXT,
  PRIMARY KEY(mint, wallet)
);
CREATE INDEX IF NOT EXISTS idx_early_buyers_wallet ON early_buyers(wallet);
CREATE INDEX IF NOT EXISTS idx_early_buyers_funder ON early_buyers(direct_funder);
CREATE INDEX IF NOT EXISTS idx_early_buyers_time ON early_buyers(first_buy_timestamp);

CREATE TABLE IF NOT EXISTS wallets (
  wallet TEXT PRIMARY KEY, first_activity INTEGER, transaction_count_before_buy INTEGER,
  sol_balance_before_funding REAL, tokens_held_before_buy INTEGER,
  freshness TEXT DEFAULT 'UNKNOWN', label TEXT, wallet_type TEXT,
  raw_json TEXT, updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS wallet_funding (
  buyer TEXT NOT NULL, funder TEXT NOT NULL, depth INTEGER NOT NULL,
  signature TEXT, timestamp INTEGER, amount_sol REAL, root_candidate TEXT,
  evidence_source TEXT, confidence TEXT, raw_json TEXT,
  PRIMARY KEY(buyer, funder, depth, signature)
);
CREATE INDEX IF NOT EXISTS idx_wallet_funding_buyer ON wallet_funding(buyer);
CREATE INDEX IF NOT EXISTS idx_wallet_funding_funder ON wallet_funding(funder);
CREATE INDEX IF NOT EXISTS idx_wallet_funding_root ON wallet_funding(root_candidate);

CREATE TABLE IF NOT EXISTS wallet_edges (
  source TEXT NOT NULL, target TEXT NOT NULL, edge_type TEXT NOT NULL,
  mint TEXT NOT NULL DEFAULT '', signature TEXT NOT NULL DEFAULT '', timestamp INTEGER,
  amount REAL, metadata_json TEXT,
  PRIMARY KEY(source, target, edge_type, mint, signature)
);
CREATE INDEX IF NOT EXISTS idx_wallet_edges_source ON wallet_edges(source);
CREATE INDEX IF NOT EXISTS idx_wallet_edges_target ON wallet_edges(target);

CREATE TABLE IF NOT EXISTS bundle_clusters (
  mint TEXT NOT NULL, cluster_id TEXT NOT NULL, status TEXT, score INTEGER,
  confidence TEXT, wallet_count INTEGER, supply_percent REAL,
  shared_funder TEXT, root_funder TEXT, bundle_timestamp INTEGER,
  evidence_json TEXT, PRIMARY KEY(mint, cluster_id)
);

CREATE TABLE IF NOT EXISTS checkpoints (
  task TEXT NOT NULL, item_key TEXT NOT NULL, status TEXT NOT NULL,
  cursor TEXT, details_json TEXT, updated_at INTEGER NOT NULL,
  PRIMARY KEY(task, item_key)
);

CREATE TABLE IF NOT EXISTS failed_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT, service TEXT, endpoint TEXT,
  params_json TEXT, status_code INTEGER, error TEXT, attempts INTEGER,
  created_at INTEGER NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def checkpoint(self, task: str, item_key: str, status: str, cursor: str | None = None,
                   details: dict[str, Any] | None = None) -> None:
        self.conn.execute(
            """INSERT INTO checkpoints(task,item_key,status,cursor,details_json,updated_at)
               VALUES(?,?,?,?,?,?) ON CONFLICT(task,item_key) DO UPDATE SET
               status=excluded.status,cursor=excluded.cursor,
               details_json=excluded.details_json,updated_at=excluded.updated_at""",
            (task, item_key, status, cursor, json.dumps(details or {}, sort_keys=True), int(time.time())),
        )
        self.conn.commit()

    def is_complete(self, task: str, item_key: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM checkpoints WHERE task=? AND item_key=?", (task, item_key)
        ).fetchone()
        return bool(row and row["status"] == "complete")

    def record_failure(self, service: str, endpoint: str, params: dict[str, Any],
                       status_code: int | None, error: str, attempts: int) -> None:
        self.conn.execute(
            """INSERT INTO failed_requests(service,endpoint,params_json,status_code,error,attempts,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (service, endpoint, json.dumps(params, sort_keys=True), status_code,
             error[:2000], attempts, int(time.time())),
        )
        self.conn.commit()


class JsonCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(service: str, endpoint: str, params: dict[str, Any]) -> str:
        canonical = json.dumps([service, endpoint, params], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def path_for(self, service: str, endpoint: str, params: dict[str, Any]) -> Path:
        digest = self.key(service, endpoint, params)
        safe_service = "".join(c if c.isalnum() or c in "-_" else "_" for c in service)
        directory = self.root / safe_service / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}.json"

    def get(self, service: str, endpoint: str, params: dict[str, Any]) -> Any | None:
        path = self.path_for(service, endpoint, params)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def put(self, service: str, endpoint: str, params: dict[str, Any], value: Any) -> Path:
        path = self.path_for(service, endpoint, params)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return path

