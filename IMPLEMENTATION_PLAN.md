# Implementation plan

1. Collect and preserve every canonical Pump.fun callout with page-token pagination.
2. Resolve each mint through Pump.fun and, when authorized, Solscan Pro v2.
3. Cursor-page Pump.fun trades back to launch and normalize the first 50 unique buyers.
4. Add transaction-level funding edges through Solscan, with an explicit Solana RPC fallback.
5. Score coordination only from multiple raw signals; keep holder, timing, funding, and recurring-buyer concepts separate.
6. Build cross-token reverse indexes, graphs, resumable checkpoints, and the required exports.
7. Validate on CHONK, then resume through all calls and regenerate the global report.

