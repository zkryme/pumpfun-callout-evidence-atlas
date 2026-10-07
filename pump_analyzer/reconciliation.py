"""Small, deterministic helpers for position-ledger reconciliation.

The source RPC is intentionally kept outside this module so the accounting rules
can be tested without a network request.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def classify_event(token_delta: float, sol_delta: float, minimum_sol: float = 0.001) -> str:
    """Classify only the economic direction we can observe from wallet deltas."""
    if token_delta > 0 and sol_delta < -minimum_sol:
        return "PURCHASE"
    if token_delta < 0 and sol_delta > minimum_sol:
        return "SALE"
    if token_delta > 0:
        return "TOKEN_IN"
    if token_delta < 0:
        return "TOKEN_OUT"
    return "OTHER"


def reconcile_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Return conservative accounting status for a deduplicated event ledger.

    A cash-flow figure is observable even when inventory or cost basis is not.
    Profit is deliberately omitted unless all token movement can be reconciled.
    """
    rows = list(events)
    purchases = [row for row in rows if row["event_type"] == "PURCHASE"]
    sales = [row for row in rows if row["event_type"] == "SALE"]
    transfers = [row for row in rows if row["event_type"] in {"TOKEN_IN", "TOKEN_OUT"}]
    bought = sum(float(row["token_amount"]) for row in purchases)
    sold = sum(abs(float(row["token_amount"])) for row in sales)
    spent = sum(abs(float(row["sol_delta"])) for row in purchases)
    received = sum(float(row["sol_delta"]) for row in sales)
    net_cash_flow = received - spent
    tolerance = max(1e-9, bought * 1e-9)
    oversold = sold > bought + tolerance
    inbound_transfers = sum(float(row["token_amount"]) for row in transfers if float(row["token_amount"]) > 0)
    if oversold and inbound_transfers >= sold - bought - tolerance:
        status = "INCOMPLETE_TRANSFERRED_INVENTORY"
        limitation = "Inbound token-only transfers explain the extra sold inventory, but the senders' cost basis and control relationship are not reconciled."
    elif oversold:
        status = "INCOMPLETE_OPENING_OR_MISSING_INVENTORY"
        limitation = "Observed sales exceed purchases in this window; opening inventory, transfers, or missing purchases must be reconciled."
    elif transfers:
        status = "INCOMPLETE_TOKEN_TRANSFERS"
        limitation = "Token transfers were observed; cost basis and inventory cannot be established from trade deltas alone."
    else:
        status = "RECONCILED_WITHIN_WINDOW"
        limitation = "Fees remain unavailable in this source snapshot and are not assumed to be zero."
    return {
        "purchase_count": len(purchases), "sale_count": len(sales),
        "transfer_count": len(transfers), "tokens_bought": bought,
        "tokens_sold": sold, "sol_spent": spent, "sol_received": received,
        "observed_net_sol_cash_flow": net_cash_flow,
        "reconciliation_status": status, "limitation": limitation,
        "profit_determined": False,
    }
