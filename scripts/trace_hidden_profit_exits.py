from __future__ import annotations

import argparse
import csv
import json
import os
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
CALLER = "6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm"
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEFAULT_WALLETS = (
    "9gpdgZTNxvSrbiBcFM8aWdCUBvQrcprQR4TmsJLU2M9L",
    "Bi4rd5FH5bYEN8scZ7wevxNZyNmKHdaBcvewdPFxYdLt",
    "EqiFgyNw6kgrmYstWyrP8VjKhka7XEmKTZzHSmwpr1Zb",
    "14onpqnhGahiwPK3PDyr1vrVNNvk6Bdasps15DQxvYuC",
    "Fzz2amRoCCpEvxtwdurs8qLVLSdrd3dcraJVpNjE4rp4",
    "FRbUNvGxYNC1eFngpn7AD3f14aKKTJVC6zSMtvj2dyCS",
    "89HbgWduLwoxcofWpmn1EiF9wEdpgkNDEyPjzZ72mkDi",
    "3LUfv2u5yzsDtUzPdsSJ7ygPBuqwfycMkjpNreRR2Yww",
)


def load_dotenv(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'") )


def parse_timestamp(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso_timestamp(value: int | None) -> str:
    if value is None:
        return ""
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


class Helius:
    def __init__(self, api_key: str, requests_per_second: float = 8.0) -> None:
        self.url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
        self.cache_dir = ROOT / "data" / "cache" / "hidden_profit_exits"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.minimum_interval = 1.0 / max(1.0, requests_per_second)
        self.last_request = 0.0
        self.request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        cache_key = json.dumps([method, params], sort_keys=True, separators=(",", ":"))
        import hashlib

        path = self.cache_dir / f"{hashlib.sha256(cache_key.encode()).hexdigest()}.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        self.request_id += 1
        payload = json.dumps({
            "jsonrpc": "2.0", "id": self.request_id,
            "method": method, "params": params,
        }).encode()
        last_error: Exception | None = None
        for attempt in range(5):
            wait = self.minimum_interval - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            try:
                request = Request(
                    self.url, data=payload, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                with urlopen(request, timeout=60) as response:
                    body = json.loads(response.read().decode())
                self.last_request = time.monotonic()
                if body.get("error"):
                    raise RuntimeError(f"{method}: {body['error']}")
                result = body.get("result")
                path.write_text(json.dumps(result), encoding="utf-8")
                return result
            except (HTTPError, URLError, TimeoutError, RuntimeError) as exc:
                last_error = exc
                time.sleep(min(8, 2 ** attempt))
        raise RuntimeError(f"Helius request failed: {last_error}")

    def transfers(self, address: str, *, mint: str | None = None,
                  direction: str | None = None, counterparty: str | None = None,
                  start: int | None = None, end: int | None = None,
                  minimum_raw_amount: int | None = None) -> list[dict[str, Any]]:
        options: dict[str, Any] = {"sortOrder": "asc", "limit": 100, "solMode": "merged"}
        if mint:
            options["mint"] = mint
        if direction:
            options["direction"] = direction
        if counterparty:
            options["with"] = counterparty
        filters: dict[str, Any] = {}
        if start is not None or end is not None:
            filters["blockTime"] = {
                key: value for key, value in (("gte", start), ("lte", end))
                if value is not None
            }
        if minimum_raw_amount is not None:
            if not mint:
                raise ValueError("amount filters require a mint")
            filters["amount"] = {"gte": minimum_raw_amount}
        if filters:
            options["filters"] = filters
        result = self.call("getTransfersByAddress", [address, options]) or {}
        return result.get("data") or []

    def transfers_page(self, address: str, *, pagination_token: str | None = None,
                       mint: str | None = None, start: int | None = None,
                       end: int | None = None, sort_order: str = "asc") -> tuple[list[dict[str, Any]], str | None]:
        """Read one Helius transfer page with its explicit continuation token."""
        options: dict[str, Any] = {"sortOrder": sort_order, "limit": 100, "solMode": "merged"}
        if pagination_token:
            options["paginationToken"] = pagination_token
        if mint:
            options["mint"] = mint
        if start is not None or end is not None:
            options["filters"] = {"blockTime": {key: value for key, value in
                                  (("gte", start), ("lte", end)) if value is not None}}
        result = self.call("getTransfersByAddress", [address, options]) or {}
        return result.get("data") or [], result.get("paginationToken")

    def transaction(self, signature: str) -> dict[str, Any] | None:
        return self.call("getTransaction", [signature, {
            "encoding": "jsonParsed", "commitment": "finalized",
            "maxSupportedTransactionVersion": 1,
        }])


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def pubkey(item: Any) -> str:
    return item.get("pubkey", "") if isinstance(item, dict) else str(item)


def token_amounts(meta: dict[str, Any], owner: str, field: str) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for balance in meta.get(field) or []:
        if balance.get("owner") != owner:
            continue
        amount = ((balance.get("uiTokenAmount") or {}).get("amount"))
        if amount is not None:
            totals[balance.get("mint", "")] += int(amount)
    return totals


def transaction_deltas(tx: dict[str, Any], owner: str) -> tuple[float, dict[str, int]]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    keys = [pubkey(item) for item in message.get("accountKeys") or []]
    meta = tx.get("meta") or {}
    sol_delta = 0.0
    if owner in keys:
        index = keys.index(owner)
        pre = meta.get("preBalances") or []
        post = meta.get("postBalances") or []
        if index < len(pre) and index < len(post):
            sol_delta = (int(post[index]) - int(pre[index])) / 1_000_000_000
    before = token_amounts(meta, owner, "preTokenBalances")
    after = token_amounts(meta, owner, "postTokenBalances")
    deltas = {mint: after.get(mint, 0) - before.get(mint, 0)
              for mint in set(before) | set(after)}
    return sol_delta, deltas


def direct_system_transfers(tx: dict[str, Any], source: str) -> list[tuple[str, float]]:
    message = ((tx.get("transaction") or {}).get("message") or {})
    output: list[tuple[str, float]] = []
    for instruction in message.get("instructions") or []:
        if instruction.get("program") != "system":
            continue
        parsed = instruction.get("parsed") or {}
        if parsed.get("type") not in {"transfer", "transferWithSeed"}:
            continue
        info = parsed.get("info") or {}
        if info.get("source") != source:
            continue
        destination = info.get("destination")
        lamports = info.get("lamports")
        if destination and lamports is not None:
            output.append((destination, int(lamports) / 1_000_000_000))
    return output


def caller_interactions(api: Helius, destination: str, minimum_sol: float = 0.05) -> int:
    """Count economically meaningful caller↔destination SOL transfers, excluding dust/tips."""
    return len(api.transfers(
        CALLER, mint=SOL_MINT, counterparty=destination,
        minimum_raw_amount=int(minimum_sol * 1_000_000_000),
    ))


def destination_fan_in(api: Helius, destination: str) -> tuple[int, int]:
    records = api.transfers(destination, mint=SOL_MINT, direction="in")
    sources = {item.get("fromUserAccount") for item in records if item.get("fromUserAccount")}
    return len(records), len(sources)


def main() -> None:
    parser = argparse.ArgumentParser(description="Trace post-call exits and cleanup destinations.")
    parser.add_argument("--wallet", action="append", dest="wallets")
    parser.add_argument("--exit-hours", type=float, default=6.0)
    parser.add_argument("--cleanup-hours", type=float, default=24.0)
    parser.add_argument("--minimum-cleanup-sol", type=float, default=0.05)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    parser.add_argument("--output-prefix", default="hidden_wallet")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("HELIUS_API_KEY", "").strip()
    if not key:
        raise SystemExit("HELIUS_API_KEY is required in .env")
    api = Helius(key, args.requests_per_second)
    wallets = set(args.wallets or DEFAULT_WALLETS)
    calls = {row["mint"]: row for row in read_csv(ROOT / "output" / "calls.csv")}
    buys = [row for row in read_csv(ROOT / "output" / "early_buyers.csv")
            if row["wallet"] in wallets and float(row["seconds_relative_to_call"]) < 0]

    sale_rows: list[dict[str, Any]] = []
    sale_tx_cache: dict[str, dict[str, Any] | None] = {}
    for position, buy in enumerate(buys, start=1):
        call = calls.get(buy["mint"])
        if not call:
            continue
        call_time = parse_timestamp(call["call_timestamp"])
        end_time = call_time + int(args.exit_hours * 3600)
        print(f"[{position}/{len(buys)}] exits {call['symbol']} {buy['wallet'][:8]}", flush=True)
        exits = api.transfers(
            buy["wallet"], mint=buy["mint"], direction="out",
            start=call_time, end=end_time,
        )
        signatures = list(dict.fromkeys(item["signature"] for item in exits))
        for signature in signatures:
            tx = sale_tx_cache.setdefault(signature, api.transaction(signature))
            if not tx or not tx.get("blockTime"):
                continue
            sol_delta, token_deltas = transaction_deltas(tx, buy["wallet"])
            decimals = next((int(item.get("decimals") or 0) for item in exits
                             if item.get("signature") == signature), 0)
            token_delta = token_deltas.get(buy["mint"], 0) / (10 ** decimals)
            usdc_delta = token_deltas.get(USDC_MINT, 0) / 1_000_000
            wsol_delta = token_deltas.get(SOL_MINT, 0) / 1_000_000_000
            realized = sol_delta > 0.005 or usdc_delta > 0.01 or wsol_delta > 0.005
            if token_delta >= 0 or not realized:
                continue
            sale_rows.append({
                "wallet": buy["wallet"], "mint": buy["mint"],
                "token": call["token_name"], "symbol": call["symbol"],
                "call_timestamp": call["call_timestamp"],
                "buy_timestamp": buy["first_buy_timestamp"],
                "buy_sol": float(buy["sol_spent"] or 0),
                "sale_timestamp": iso_timestamp(tx["blockTime"]),
                "seconds_after_call": tx["blockTime"] - call_time,
                "tokens_disposed": -token_delta,
                "net_sol_change": sol_delta,
                "net_usdc_change": usdc_delta,
                "net_wsol_change": wsol_delta,
                "sale_signature": signature,
            })
            break

    cleanup_rows: list[dict[str, Any]] = []
    cleanup_tx_cache: dict[str, dict[str, Any] | None] = {}
    seen_cleanup: set[tuple[str, str, str]] = set()
    for position, sale in enumerate(sale_rows, start=1):
        sale_time = parse_timestamp(sale["sale_timestamp"])
        print(f"[{position}/{len(sale_rows)}] cleanup {sale['symbol']} {sale['wallet'][:8]}", flush=True)
        transfers = api.transfers(
            sale["wallet"], mint=SOL_MINT, direction="out", start=sale_time,
            end=sale_time + int(args.cleanup_hours * 3600),
            minimum_raw_amount=int(args.minimum_cleanup_sol * 1_000_000_000),
        )
        for record in transfers:
            signature = record.get("signature", "")
            tx = cleanup_tx_cache.setdefault(signature, api.transaction(signature))
            if not tx:
                continue
            for destination, amount in direct_system_transfers(tx, sale["wallet"]):
                key_tuple = (sale["wallet"], signature, destination)
                if key_tuple in seen_cleanup or amount < args.minimum_cleanup_sol:
                    continue
                seen_cleanup.add(key_tuple)
                cleanup_rows.append({
                    "wallet": sale["wallet"], "mint": sale["mint"],
                    "symbol": sale["symbol"], "sale_signature": sale["sale_signature"],
                    "sale_timestamp": sale["sale_timestamp"],
                    "destination": destination, "amount_sol": amount,
                    "cleanup_timestamp": iso_timestamp(tx.get("blockTime")),
                    "seconds_after_sale": (tx.get("blockTime") or sale_time) - sale_time,
                    "cleanup_signature": signature,
                })

    destination_counts = Counter(row["destination"] for row in cleanup_rows)
    wallet_counts: dict[str, set[str]] = defaultdict(set)
    for row in cleanup_rows:
        wallet_counts[row["destination"]].add(row["wallet"])
    destination_cache: dict[str, tuple[int, int, int]] = {}
    for destination in destination_counts:
        print(f"classify {destination[:8]}", flush=True)
        interactions = caller_interactions(api, destination)
        inbound_records, unique_sources = destination_fan_in(api, destination)
        destination_cache[destination] = (interactions, inbound_records, unique_sources)
    for row in cleanup_rows:
        interactions, inbound_records, unique_sources = destination_cache[row["destination"]]
        row.update({
            "destination_event_count": destination_counts[row["destination"]],
            "destination_hidden_wallet_count": len(wallet_counts[row["destination"]]),
            "caller_interaction_count": interactions,
            "destination_latest_inbound_records": inbound_records,
            "destination_latest_unique_sources": unique_sources,
            "service_like": unique_sources >= 20,
            "caller_link_class": (
                "DIRECT_TO_CALLER" if row["destination"] == CALLER else
                "CALLER_SHARED_DESTINATION" if interactions else
                "NO_CALLER_LINK"
            ),
        })

    sale_fields = [
        "wallet", "mint", "token", "symbol", "call_timestamp", "buy_timestamp",
        "buy_sol", "sale_timestamp", "seconds_after_call", "tokens_disposed",
        "net_sol_change", "net_usdc_change", "net_wsol_change", "sale_signature",
    ]
    cleanup_fields = [
        "wallet", "mint", "symbol", "sale_signature", "sale_timestamp",
        "destination", "amount_sol", "cleanup_timestamp", "seconds_after_sale",
        "cleanup_signature", "destination_event_count", "destination_hidden_wallet_count",
        "caller_interaction_count", "destination_latest_inbound_records",
        "destination_latest_unique_sources", "service_like", "caller_link_class",
    ]
    write_csv(ROOT / "output" / f"{args.output_prefix}_sales.csv", sale_rows, sale_fields)
    write_csv(ROOT / "output" / f"{args.output_prefix}_cleanup.csv", cleanup_rows, cleanup_fields)
    print(f"Verified sales: {len(sale_rows)}")
    print(f"Cleanup transfers: {len(cleanup_rows)}")
    print(f"Caller-linked cleanup transfers: {sum(row['caller_link_class'] != 'NO_CALLER_LINK' for row in cleanup_rows)}")


if __name__ == "__main__":
    main()
