from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator
import logging

from .http import ApiError, HttpJsonClient


LOG = logging.getLogger(__name__)
SOLANA_CHAIN_ID = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
NATIVE_SOL_MINT = "So11111111111111111111111111111111111111111"


def timestamp_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value if value > 10_000_000_000 else value * 1000)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


class PumpClient:
    def __init__(self, api: HttpJsonClient, swap_api: HttpJsonClient):
        self.api, self.swap_api = api, swap_api

    def get_all_callouts(self, user_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []
        page_token: str | None = None
        seen: set[tuple[str, int]] = set()
        while True:
            body = self.api.get(
                f"callout/list/{user_id}",
                {"limit": page_size, "sortBy": "TIMESTAMP", "sortOrder": "DESC",
                 "pageToken": page_token},
            )
            page = body.get("callouts") or []
            for call in page:
                key = (call.get("coinMint", ""), int(call.get("createdAt") or 0))
                if key not in seen:
                    seen.add(key)
                    calls.append(call)
            next_token = body.get("nextPageToken")
            if not next_token or next_token == page_token or not page:
                break
            page_token = next_token
        return sorted(calls, key=lambda x: int(x.get("createdAt") or 0))

    def get_coin(self, mint: str) -> dict[str, Any]:
        return self.api.get(f"coins-v3/{mint}", {"includeLiveStreamInfo": "false"})

    def _trade_page(self, mint: str, coin: dict[str, Any], cursor: str | int,
                    limit: int = 100) -> dict[str, Any]:
        params = {
            "limit": limit,
            "cursor": cursor,
            "program": coin.get("program") or "pump",
            "chainId": coin.get("chain_id") or SOLANA_CHAIN_ID,
            "createdTs": coin.get("created_timestamp"),
        }
        return self.swap_api.get(f"v2/coins/{mint}/trades", params)

    def get_launch_trades(self, mint: str, coin: dict[str, Any], unique_buyers: int = 50,
                          initial_window_seconds: int = 1,
                          max_pages_per_window: int = 100) -> list[dict[str, Any]]:
        launch_ms = timestamp_ms(coin.get("created_timestamp"))
        if launch_ms is None:
            raise ValueError(f"Coin {mint} has no creation timestamp")
        # The cursor accepts a wall-clock timestamp. Start immediately after
        # launch and widen only when fewer than the requested unique buyers are
        # present. This avoids walking backward through minutes/hours of later
        # volume on active launches while preserving earliest-first ordering.
        windows = []
        for candidate in (initial_window_seconds, 5, 10, 30, 60, 300, 600, 1800, 7200, 86400):
            if candidate not in windows:
                windows.append(candidate)
        collected: dict[str, dict[str, Any]] = {}
        for window_seconds in windows:
            cursor: str | int = f"9999999999999999999999-{launch_ms + window_seconds * 1000}"
            for _ in range(max_pages_per_window):
                body = self._trade_page(mint, coin, cursor)
                page = body.get("trades") or []
                if not page:
                    break
                oldest = None
                for trade in page:
                    t_ms = timestamp_ms(trade.get("timestamp"))
                    if t_ms is None:
                        continue
                    oldest = t_ms if oldest is None else min(oldest, t_ms)
                    if t_ms >= launch_ms:
                        identity = trade.get("slotIndexId") or trade.get("tx") or repr(trade)
                        collected[str(identity)] = trade
                if oldest is not None and oldest <= launch_ms:
                    break
                pagination = body.get("pagination") or {}
                next_cursor = pagination.get("nextCursor")
                if not pagination.get("hasMore") or not next_cursor or next_cursor == cursor:
                    break
                cursor = next_cursor
            buyers = {t.get("userAddress") for t in collected.values()
                      if t.get("type") == "buy" and t.get("userAddress")}
            if len(buyers) >= unique_buyers:
                break
        return sorted(collected.values(), key=lambda t: (
            timestamp_ms(t.get("timestamp")) or 0, str(t.get("slotIndexId") or "")
        ))


class SolscanClient:
    """Official Solscan Pro v2 wrapper. Authentication is the documented `token` header."""

    def __init__(self, api: HttpJsonClient):
        self.api = api

    @staticmethod
    def _data(body: dict[str, Any]) -> Any:
        if body.get("success") is False:
            raise ApiError("solscan", "response", str(body.get("errors")))
        return body.get("data")

    def check_access(self) -> tuple[bool, str]:
        try:
            self._data(self.api.get("monitor/usage", {}, use_cache=False))
            return True, "Solscan Pro v2 access confirmed"
        except ApiError as exc:
            return False, f"Solscan Pro unavailable ({exc.status}): {exc}"

    def get_token_metadata(self, mint: str) -> dict[str, Any]:
        return self._data(self.api.get("token/meta", {"address": mint})) or {}

    def get_token_transfers(self, mint: str, page: int = 1, page_size: int = 100,
                            sort_order: str = "asc") -> list[dict[str, Any]]:
        return self._data(self.api.get("token/transfer", {
            "address": mint, "page": page, "page_size": page_size,
            "sort_by": "block_time", "sort_order": sort_order,
        })) or []

    def get_account_transactions(self, address: str, before: str | None = None,
                                 limit: int = 50) -> list[dict[str, Any]]:
        return self._data(self.api.get("account/transactions", {
            "address": address, "before": before, "limit": limit,
        })) or []

    def get_account_transfers(self, address: str, page: int = 1, page_size: int = 100,
                              sort_order: str = "desc") -> list[dict[str, Any]]:
        return self._data(self.api.get("account/transfer", {
            "address": address, "page": page, "page_size": page_size,
            "sort_by": "block_time", "sort_order": sort_order,
        })) or []

    def get_transaction(self, signature: str) -> dict[str, Any]:
        return self._data(self.api.get("transaction/detail", {"tx": signature})) or {}

    def get_funded_by(self, address: str) -> dict[str, Any]:
        data = self._data(self.api.get("account/funded-by", {"address": [address]})) or []
        if isinstance(data, list):
            return next((item for item in data if item.get("address") == address), {})
        return data if isinstance(data, dict) else {}

    def get_funding_activities(self, address: str, page: int = 1,
                               page_size: int = 100) -> list[dict[str, Any]]:
        data = self._data(self.api.get("account/funding/activities", {
            "address": address, "page": page, "page_size": page_size,
            "sort_by": "block_time", "sort_order": "asc",
        })) or []
        return data.get("data", []) if isinstance(data, dict) else data


def _is_sol_transfer(item: dict[str, Any]) -> bool:
    token = item.get("token_address") or item.get("token") or item.get("tokenAddress")
    activity = str(item.get("activity_type") or item.get("type") or "").upper()
    return token in (None, "", NATIVE_SOL_MINT, "SOL") or "SOL_TRANSFER" in activity


class SolanaRpcClient:
    def __init__(self, api: HttpJsonClient):
        self.api = api
        self._id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        body = self.api.post("", {"jsonrpc": "2.0", "id": self._id,
                                  "method": method, "params": params})
        if body.get("error"):
            raise ApiError("solana-rpc", method, str(body["error"]))
        return body.get("result")

    def get_signatures_for_address(self, address: str, before: str | None = None,
                                   until: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        options: dict[str, Any] = {"limit": min(1000, limit), "commitment": "finalized"}
        if before:
            options["before"] = before
        if until:
            options["until"] = until
        return self.call("getSignaturesForAddress", [address, options]) or []

    def get_signature_history_before(self, address: str, before: str | None,
                                     max_pages: int = 10,
                                     page_size: int = 1000) -> tuple[list[dict[str, Any]], bool]:
        """Return successful signatures newest-to-oldest and whether history is complete."""
        history: list[dict[str, Any]] = []
        cursor = before
        complete = False
        for _ in range(max(1, max_pages)):
            page = self.get_signatures_for_address(address, before=cursor, limit=page_size)
            if not page:
                complete = True
                break
            history.extend(item for item in page if item.get("err") is None)
            if len(page) < page_size:
                complete = True
                break
            next_cursor = page[-1].get("signature")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return history, complete

    def get_transaction(self, signature: str) -> dict[str, Any] | None:
        return self.call("getTransaction", [signature, {
            "encoding": "jsonParsed", "maxSupportedTransactionVersion": 0,
            "commitment": "finalized",
        }])

    def get_balance(self, address: str) -> int | None:
        result = self.call("getBalance", [address, {"commitment": "finalized"}])
        return result.get("value") if isinstance(result, dict) else None

    def get_first_inbound_sol_transfer(self, address: str,
                                       before_timestamp_ms: int) -> dict[str, Any] | None:
        """Use Helius paid transfer history to return the oldest inbound SOL transfer."""
        result = self.call("getTransfersByAddress", [address, {
            "mint": NATIVE_SOL_MINT,
            "direction": "in",
            "solMode": "merged",
            "sortOrder": "asc",
            "limit": 10,
            "filters": {
                "amount": {"gte": 10_000},
                "blockTime": {"lte": int(before_timestamp_ms / 1000)},
            },
        }]) or {}
        for transfer in result.get("data") or []:
            source = transfer.get("fromUserAccount")
            target = transfer.get("toUserAccount")
            if source and target == address and source != address:
                raw_amount = transfer.get("amount")
                decimals = int(transfer.get("decimals") or 9)
                amount_sol = (float(raw_amount) / (10 ** decimals)
                              if raw_amount is not None else transfer.get("uiAmount"))
                return {
                    "funder": source,
                    "signature": transfer.get("signature"),
                    "timestamp": int(transfer["blockTime"] * 1000)
                    if transfer.get("blockTime") else None,
                    "amount_sol": float(amount_sol) if amount_sol is not None else None,
                    "slot": transfer.get("slot"),
                }
        return None
