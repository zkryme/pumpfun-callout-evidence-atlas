# Pump.fun wallet due-diligence template

Use this request for each new profile:

> Investigate this Pump.fun profile: `<profile URL or wallet>`. Create a **separate case** in the dashboard. Check every canonical callout, identify the caller's own buys and observed SOL proceeds, screen each token for launch bundles, and test whether bundle wallets have a direct or bounded funding/transfer path back to the caller. Do not merge findings or wallet scores with other cases. Use `supported`, `needs verification`, `incomplete`, and `scoped negative` labels; never call proceeds "profit" without cost basis.

## Standard case workflow

1. Create an isolated case directory, SQLite database, raw cache, exports, and dashboard data file.
2. Pull the complete canonical Pump.fun callout history and record the immutable profile URL, mint, call time, creator, and launch time.
3. For every analysable mint, retrieve the first 50 trades, their Solana slots, timestamps, buyers, and signatures.
4. Identify bundle signals separately:
   - same-slot or tightly clustered launch buys;
   - repeated buyer cohorts across the caller's tokens;
   - common direct non-service funders;
   - creator/caller direct paths.
5. Trace funding only with a bounded, documented rule: first qualifying pre-buy SOL transfer and up to three hops. Label exchange/service-like sources as context, not ownership.
6. Reconstruct the caller's and directly linked wallet's token flow: acquisition, transfers, disposals, and native-SOL/WSOL receipts. Classify this as proceeds unless a cost basis and fees are available.
7. Test links from every suspicious bundle to the caller in this order: direct transfer, common direct funder, bounded upstream ancestry, repeated coordinated timing. Do not infer ownership from a single timing match.
8. Publish case-specific findings, coverage, limitations, Solscan links, and downloadable raw evidence. Keep the UI selector case-scoped.

## Minimum delivery standard

- A wallet is called **linked** only with direct transfer/funding evidence or multiple independently documented coordination signals.
- A token is called **bundled** only with the recorded launch timing/slot evidence and the wallet cohort exported.
- A sale is **observed proceeds**, not profit, until original acquisition cost and fees are reconciled.
- A negative result always states token count, time window, depth, data source, and record caps.
- Every address, mint, and signature displayed in the dashboard links to Solscan.
