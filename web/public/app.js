const state = {
  data: null,
  graph: null,
  tokenPage: 1,
  walletPage: 1,
  tokenSort: { key: "call_timestamp", direction: -1 },
  pageSize: 12
};

const formatNumber = new Intl.NumberFormat("en-US");
const fmt = (value, digits = 1) => Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
const escapeHtml = (value = "") => String(value).replace(/[&<>'"]/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
const short = (value, head = 6, tail = 4) => value ? `${value.slice(0, head)}…${value.slice(-tail)}` : "—";
const solscanUrl = (value, type = "account") => `https://solscan.io/${type}/${encodeURIComponent(value)}`;
const solscanLink = (value, label = short(value), type = "account") =>
  `<a class="solscan-link" href="${solscanUrl(value, type)}" target="_blank" rel="noreferrer" title="${escapeHtml(value)}">${escapeHtml(label)} ↗</a>`;
const duration = seconds => {
  const amount = Number(seconds);
  if (!Number.isFinite(amount)) return "—";
  const sign = amount < 0 ? "−" : "";
  const abs = Math.abs(amount);
  if (abs >= 3600) return `${sign}${fmt(abs / 3600, 2)}h`;
  if (abs >= 60) return `${sign}${fmt(abs / 60, 2)}m`;
  return `${sign}${fmt(abs, 2)}s`;
};

async function initialize() {
  try {
    const [analysisResponse, graphResponse] = await Promise.all([
      fetch("/data/analysis.json"), fetch("/data/bubblemap.json")
    ]);
    if (!analysisResponse.ok || !graphResponse.ok) throw new Error("Snapshot files could not be loaded");
    state.data = await analysisResponse.json();
    state.graph = await graphResponse.json();
    hydrateSummary();
    bindControls();
    renderTokens();
    renderWallets();
    initializeNetwork();
  } catch (error) {
    document.querySelector("main").innerHTML = `<section class="section"><h1>Snapshot unavailable.</h1><p>${escapeHtml(error.message)}</p></section>`;
  }
}

function hydrateSummary() {
  const { summary, generated_at: generatedAt, profile } = state.data;
  const timing = summary.timing_counts;
  const buyers = timing.PRE_CALLOUT + timing.CALL_WINDOW + timing.POST_CALLOUT;
  const preRate = timing.PRE_CALLOUT / buyers * 100;
  const setters = {
    calls: summary.calls_total,
    buyers: formatNumber.format(buyers),
    wallets: summary.recurring_buyer_wallets,
    preRate: `${preRate.toFixed(1)}%`,
    bundleTokens: summary.tokens_with_launch_bundle_evidence,
    resolved: formatNumber.format(summary.wallets_with_funding_resolved),
    callerLinks: summary.caller_linked_buyers,
    resolvedFraction: `${formatNumber.format(summary.wallets_with_funding_resolved)} / ${formatNumber.format(summary.unique_early_buyer_wallets)}`,
    suspiciousGroups: summary.suspicious_shared_funding_groups,
    serviceGroups: summary.service_like_shared_funding_groups,
    creatorLinks: summary.creator_linked_buyers,
    pre: formatNumber.format(timing.PRE_CALLOUT),
    window: timing.CALL_WINDOW,
    post: timing.POST_CALLOUT
  };
  Object.entries(setters).forEach(([name, value]) => document.querySelectorAll(`[data-stat="${name}"]`).forEach(node => node.textContent = value));
  document.querySelector("#generated-date").textContent = new Date(generatedAt).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
  document.querySelector("#profile-address").innerHTML = solscanLink(profile, profile);
  document.querySelector("#bar-pre").style.width = `${preRate}%`;
  document.querySelector("#bar-window").style.width = `${timing.CALL_WINDOW / buyers * 100}%`;
  document.querySelector("#bar-post").style.width = `${timing.POST_CALLOUT / buyers * 100}%`;
}

function bindControls() {
  document.querySelector("#token-search").addEventListener("input", () => { state.tokenPage = 1; renderTokens(); });
  document.querySelector("#token-filter").addEventListener("change", () => { state.tokenPage = 1; renderTokens(); });
  document.querySelector("#wallet-search").addEventListener("input", () => { state.walletPage = 1; renderWallets(); });
  document.querySelector("#wallet-filter").addEventListener("change", () => { state.walletPage = 1; renderWallets(); });
  document.querySelector("#tokens-prev").addEventListener("click", () => { state.tokenPage--; renderTokens(); });
  document.querySelector("#tokens-next").addEventListener("click", () => { state.tokenPage++; renderTokens(); });
  document.querySelector("#wallets-prev").addEventListener("click", () => { state.walletPage--; renderWallets(); });
  document.querySelector("#wallets-next").addEventListener("click", () => { state.walletPage++; renderWallets(); });
  document.querySelectorAll("th[data-sort]").forEach(header => header.addEventListener("click", () => {
    const key = header.dataset.sort;
    state.tokenSort = state.tokenSort.key === key ? { key, direction: state.tokenSort.direction * -1 } : { key, direction: -1 };
    renderTokens();
  }));
}

function tokenRows() {
  const search = document.querySelector("#token-search").value.trim().toLowerCase();
  const filter = document.querySelector("#token-filter").value;
  return state.data.calls
    .map(item => ({ ...item, secondsToCall: (item.call_timestamp - item.launch_timestamp) / 1000 }))
    .filter(item => filter === "all" || item.bundle_status === filter)
    .filter(item => !search || [item.symbol, item.token_name, item.mint].some(value => String(value || "").toLowerCase().includes(search)))
    .sort((a, b) => {
      const av = a[state.tokenSort.key] ?? "";
      const bv = b[state.tokenSort.key] ?? "";
      return (typeof av === "string" ? av.localeCompare(bv) : av - bv) * state.tokenSort.direction;
    });
}

function renderTokens() {
  const rows = tokenRows();
  const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.tokenPage = Math.min(state.tokenPage, pages);
  const visible = rows.slice((state.tokenPage - 1) * state.pageSize, state.tokenPage * state.pageSize);
  document.querySelector("#token-table").innerHTML = visible.map(item => `
    <tr>
      <td class="token-cell"><strong>${solscanLink(item.mint, item.symbol || "Unknown", "token")}</strong><small>${solscanLink(item.mint, item.token_name || short(item.mint), "token")}</small></td>
      <td class="mono">${duration(item.secondsToCall)}</td>
      <td class="mono">${formatNumber.format(item.wallet_count || 0)}</td>
      <td class="mono">${fmt(item.supply_percent || 0, 3)}%</td>
      <td class="mono">${item.score ?? "—"}</td>
      <td><span class="assessment ${item.bundle_status === "NO_MULTI_SIGNAL_EVIDENCE" ? "clear" : ""}">${assessmentLabel(item.bundle_status)}</span></td>
    </tr>`).join("");
  document.querySelector("#token-count").textContent = `${rows.length} result${rows.length === 1 ? "" : "s"}`;
  document.querySelector("#tokens-page").textContent = `Page ${state.tokenPage} of ${pages}`;
  document.querySelector("#tokens-prev").disabled = state.tokenPage === 1;
  document.querySelector("#tokens-next").disabled = state.tokenPage === pages;
}

function assessmentLabel(status) {
  return ({
    CONFIRMED_BUNDLE: "CONFIRMED BUNDLE",
    SUSPECTED_BUNDLE: "SUSPECTED BUNDLE",
    COORDINATED_EARLY_BUYERS: "TIMING CLUSTER",
    NO_MULTI_SIGNAL_EVIDENCE: "NO MULTI-SIGNAL"
  })[status] || "UNKNOWN";
}

function walletRows() {
  const search = document.querySelector("#wallet-search").value.trim().toLowerCase();
  const minimum = Number(document.querySelector("#wallet-filter").value);
  return state.data.recurring_buyers
    .filter(item => item.token_count >= minimum)
    .filter(item => !search || item.wallet.toLowerCase().includes(search))
    .sort((a, b) => b.token_count - a.token_count || a.avg_seconds_after_launch - b.avg_seconds_after_launch);
}

function renderWallets() {
  const rows = walletRows();
  const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.walletPage = Math.min(state.walletPage, pages);
  const visible = rows.slice((state.walletPage - 1) * state.pageSize, state.walletPage * state.pageSize);
  document.querySelector("#wallet-table").innerHTML = visible.map(item => `
    <tr>
      <td><span class="wallet-address">${solscanLink(item.wallet, short(item.wallet, 10, 8))}</span></td>
      <td class="mono">${item.token_count}</td>
      <td class="mono">${duration(item.avg_seconds_after_launch)}</td>
      <td class="mono">${item.avg_seconds_after_call < 0 ? `${duration(item.avg_seconds_after_call)} before` : `${duration(item.avg_seconds_after_call)} after`}</td>
      <td class="mono">${item.pre_call_count}</td>
    </tr>`).join("");
  document.querySelector("#wallet-count").textContent = `${formatNumber.format(rows.length)} results`;
  document.querySelector("#wallets-page").textContent = `Page ${state.walletPage} of ${pages}`;
  document.querySelector("#wallets-prev").disabled = state.walletPage === 1;
  document.querySelector("#wallets-next").disabled = state.walletPage === pages;
}

function initializeNetwork() {
  const select = document.querySelector("#network-token");
  const tokens = [...new Map(state.graph.groups.map(group => [group.mint, group.token])).entries()]
    .sort((a, b) => a[1].localeCompare(b[1]));
  select.insertAdjacentHTML("beforeend", tokens.map(([mint, label]) =>
    `<option value="${escapeHtml(mint)}">${escapeHtml(label)}</option>`).join(""));
  select.addEventListener("change", renderNetwork);
  document.querySelector("#network-services").addEventListener("change", renderNetwork);
  document.querySelector("#network-reset").addEventListener("click", () => {
    select.value = "all";
    document.querySelector("#network-services").checked = false;
    renderNetwork();
  });
  renderNetwork();
}

function renderNetwork() {
  const selectedMint = document.querySelector("#network-token").value;
  const includeServices = document.querySelector("#network-services").checked;
  let groups = state.graph.groups
    .filter(group => includeServices || !group.service_like)
    .sort((a, b) => b.pre_call_count - a.pre_call_count || b.buyer_count - a.buyer_count);
  if (selectedMint !== "all") {
    groups = groups.filter(group => group.mint === selectedMint);
  } else {
    const concentrated = groups.filter(group => !group.service_like).slice(0, 10);
    const services = includeServices ? groups.filter(group => group.service_like).slice(0, 3) : [];
    groups = [...concentrated, ...services];
  }
  const allBuyerWallets = new Set(groups.flatMap(group => String(group.wallets || "").split(";").filter(Boolean)));
  document.querySelector("#network-summary").textContent = groups.length
    ? `${groups.length} funding path${groups.length === 1 ? "" : "s"} shown · ${allBuyerWallets.size} unique early-buyer wallets · every identifier opens in Solscan`
    : "No funding path matches the current filter.";

  document.querySelector("#relationship-map").innerHTML = groups.map(group => {
    const wallets = String(group.wallets || "").split(";").filter(Boolean);
    const visibleWallets = wallets.slice(0, selectedMint === "all" ? 6 : 12);
    const fundingEdges = state.graph.edges.filter(edge => edge.type === "FUNDED" && edge.mint === group.mint && edge.source === group.funder);
    const fundingTotal = fundingEdges.reduce((sum, edge) => sum + Number(edge.amount || 0), 0);
    const firstTiming = Number(group.first_seconds_relative_to_call);
    const lastTiming = Number(group.last_seconds_relative_to_call);
    const timingText = Number.isFinite(firstTiming) && Number.isFinite(lastTiming)
      ? `${duration(Math.abs(firstTiming))}–${duration(Math.abs(lastTiming))} before call`
      : "Pre-call timing unavailable";
    return `
      <article class="path-row ${group.service_like ? "service-path" : ""}">
        <div class="path-node path-origin">
          <span>${group.service_like ? "SERVICE-LIKE ORIGIN" : "CONCENTRATED FUNDER"}</span>
          <strong>${solscanLink(group.funder, short(group.funder, 10, 8))}</strong>
          <small>${group.global_token_count} called token${group.global_token_count === 1 ? "" : "s"} in dataset</small>
        </div>
        <div class="path-connector"><i></i><span>funded${fundingTotal ? ` · ${fmt(fundingTotal, 4)} SOL` : ""}</span><b>→</b></div>
        <div class="path-buyers">
          <span class="path-label">${wallets.length} EARLY BUYER${wallets.length === 1 ? "" : "S"}</span>
          <div>${visibleWallets.map(wallet => solscanLink(wallet, short(wallet, 6, 4))).join("")}${wallets.length > visibleWallets.length ? `<span class="more-buyers">+${wallets.length - visibleWallets.length} more</span>` : ""}</div>
          <small>${group.pre_call_count} pre-call · ${timingText}</small>
        </div>
        <div class="path-connector"><i></i><span>bought</span><b>→</b></div>
        <div class="path-node path-token">
          <span>CALLED TOKEN</span>
          <strong>${solscanLink(group.mint, group.token || short(group.mint), "token")}</strong>
          <small>${short(group.mint, 8, 6)}</small>
        </div>
      </article>`;
  }).join("") || `<div class="path-empty">No matching funding paths.</div>`;
}

initialize();
