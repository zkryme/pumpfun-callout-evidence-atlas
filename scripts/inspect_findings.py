import sqlite3


PROFILE = "6yVb4pxNwDfr6rovwNnBg3SyKSvDcHGD4WdFPN1JJBqm"
db = sqlite3.connect("analysis.sqlite3")
db.row_factory = sqlite3.Row

queries = {
    "coverage": """SELECT COUNT(DISTINCT wallet) unique_wallets,
        COUNT(DISTINCT CASE WHEN direct_funder IS NOT NULL THEN wallet END) resolved
        FROM early_buyers""",
    "shared_direct_count": """SELECT COUNT(*) n FROM (
        SELECT funder FROM wallet_funding WHERE depth=1 GROUP BY funder
        HAVING COUNT(DISTINCT buyer)>=2)""",
    "shared_upstream_count": """SELECT COUNT(*) n FROM (
        SELECT funder FROM wallet_funding WHERE depth>1 GROUP BY funder
        HAVING COUNT(DISTINCT buyer)>=2)""",
    "caller_links": """SELECT COUNT(DISTINCT wf.buyer) buyers,
        COUNT(DISTINCT eb.mint) tokens FROM wallet_funding wf
        JOIN early_buyers eb ON eb.wallet=wf.buyer WHERE wf.funder=?""",
    "creator_links": """SELECT COUNT(DISTINCT wf.buyer) buyers,
        COUNT(DISTINCT eb.mint) tokens FROM wallet_funding wf
        JOIN early_buyers eb ON eb.wallet=wf.buyer JOIN tokens t ON t.mint=eb.mint
        WHERE wf.funder=t.creator""",
    "creator_is_early_buyer": """SELECT COUNT(DISTINCT eb.wallet) buyers,
        COUNT(DISTINCT eb.mint) tokens FROM early_buyers eb
        JOIN tokens t ON t.mint=eb.mint WHERE eb.wallet=t.creator""",
}

for name, query in queries.items():
    params = (PROFILE,) if "?" in query else ()
    print(name, dict(db.execute(query, params).fetchone()))

print("top_direct")
for row in db.execute("""SELECT wf.funder,COUNT(DISTINCT wf.buyer) buyers,
    COUNT(DISTINCT eb.mint) tokens,
    SUM(CASE WHEN eb.timing_class='PRE_CALLOUT' THEN 1 ELSE 0 END) pre_records
    FROM wallet_funding wf JOIN early_buyers eb ON eb.wallet=wf.buyer
    WHERE wf.depth=1 GROUP BY wf.funder HAVING COUNT(DISTINCT wf.buyer)>=2
    ORDER BY buyers DESC,tokens DESC LIMIT 20"""):
    print(dict(row))

print("top_upstream")
for row in db.execute("""SELECT wf.funder,COUNT(DISTINCT wf.buyer) buyers,
    COUNT(DISTINCT eb.mint) tokens FROM wallet_funding wf
    JOIN early_buyers eb ON eb.wallet=wf.buyer WHERE wf.depth>1
    GROUP BY wf.funder HAVING COUNT(DISTINCT wf.buyer)>=2
    ORDER BY buyers DESC,tokens DESC LIMIT 20"""):
    print(dict(row))

print("caller_detail")
for row in db.execute("""SELECT wf.depth,COUNT(DISTINCT wf.buyer) buyers,
    COUNT(DISTINCT eb.mint) tokens FROM wallet_funding wf
    JOIN early_buyers eb ON eb.wallet=wf.buyer WHERE wf.funder=? GROUP BY wf.depth""", (PROFILE,)):
    print(dict(row))

print("creator_detail")
for row in db.execute("""SELECT eb.mint,t.symbol,wf.depth,
    COUNT(DISTINCT wf.buyer) buyers FROM wallet_funding wf
    JOIN early_buyers eb ON eb.wallet=wf.buyer JOIN tokens t ON t.mint=eb.mint
    WHERE wf.funder=t.creator GROUP BY eb.mint,t.symbol,wf.depth ORDER BY buyers DESC"""):
    print(dict(row))

print("per_token_shared_direct")
for row in db.execute("""SELECT eb.mint,t.symbol,eb.direct_funder,
    COUNT(DISTINCT eb.wallet) buyers,
    SUM(CASE WHEN eb.timing_class='PRE_CALLOUT' THEN 1 ELSE 0 END) pre_buyers,
    MIN(eb.seconds_relative_to_call) first_vs_call,
    MAX(eb.seconds_relative_to_call) last_vs_call
    FROM early_buyers eb JOIN tokens t ON t.mint=eb.mint
    WHERE eb.direct_funder IS NOT NULL
    GROUP BY eb.mint,t.symbol,eb.direct_funder
    HAVING COUNT(DISTINCT eb.wallet)>=2
    ORDER BY buyers DESC LIMIT 40"""):
    print(dict(row))

print("concentrated_direct_hubs")
for row in db.execute("""SELECT wf.funder,COUNT(DISTINCT wf.buyer) buyers,
    COUNT(DISTINCT eb.mint) tokens,ROUND(
      CAST(COUNT(DISTINCT wf.buyer) AS REAL)/COUNT(DISTINCT eb.mint),2
    ) buyers_per_token,GROUP_CONCAT(DISTINCT t.symbol) symbols
    FROM wallet_funding wf JOIN early_buyers eb ON eb.wallet=wf.buyer
    JOIN tokens t ON t.mint=eb.mint WHERE wf.depth=1
    GROUP BY wf.funder HAVING COUNT(DISTINCT wf.buyer)>=3
    ORDER BY buyers_per_token DESC,buyers DESC LIMIT 30"""):
    print(dict(row))
