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
  const svg = document.querySelector("#bubblemap");
  const selectedMint = document.querySelector("#network-token").value;
  const includeServices = document.querySelector("#network-services").checked;
  let groups = state.graph.groups.filter(group => includeServices || !group.service_like);
  if (selectedMint !== "all") {
    groups = groups.filter(group => group.mint === selectedMint);
  } else {
    const concentrated = groups.filter(group => !group.service_like).slice(0, 12);
    const services = includeServices ? groups.filter(group => group.service_like).slice(0, 4) : [];
    groups = [...concentrated, ...services];
  }
  const groupKeys = new Set(groups.map(group => `${group.mint}|${group.funder}`));
  const fundedEdges = state.graph.edges.filter(edge => edge.type === "FUNDED" && groupKeys.has(`${edge.mint}|${edge.source}`));
  const buyers = new Set(fundedEdges.map(edge => edge.target));
  const boughtEdges = state.graph.edges.filter(edge => edge.type === "BOUGHT" && buyers.has(edge.source) && groups.some(group => group.mint === edge.target));
  const edges = [...fundedEdges, ...boughtEdges];
  const nodeIds = new Set(edges.flatMap(edge => [edge.source, edge.target]));
  const nodes = state.graph.nodes.filter(node => nodeIds.has(node.id)).map(node => ({ ...node }));
  layoutGraph(nodes, edges);
  const byId = new Map(nodes.map(node => [node.id, node]));
  svg.innerHTML = [
    ...edges.map(edge => {
      const source = byId.get(edge.source), target = byId.get(edge.target);
      if (!source || !target) return "";
      return `<line class="edge ${edge.pre_call ? "pre-call" : ""}" x1="${source.x}" y1="${source.y}" x2="${target.x}" y2="${target.y}"></line>`;
    }),
    ...nodes.map(node => {
      const radius = node.type === "TOKEN" ? 14 : node.type === "FUNDER" || node.type === "SERVICE" ? 11 : 4;
      const label = node.type === "BUYER" ? "" : `<text x="${node.x + radius + 4}" y="${node.y + 4}">${escapeHtml(node.label)}</text>`;
      return `<g data-node="${escapeHtml(node.id)}"><circle class="node ${node.type}" cx="${node.x}" cy="${node.y}" r="${radius}"><title>${escapeHtml(node.label)} · ${node.type}</title></circle>${label}</g>`;
    })
  ].join("");
  svg.querySelectorAll("g[data-node]").forEach(element => element.addEventListener("click", () => {
    showNodeDetail(byId.get(element.dataset.node), edges, groups);
  }));
  const detail = document.querySelector("#network-detail");
  detail.querySelector("h3").textContent = selectedMint === "all" ? "Strongest concentrated clusters" : (groups[0]?.token || "No matching cluster");
  detail.querySelector(":scope > p").textContent = groups.length
    ? `${groups.length} shared-funding group${groups.length === 1 ? "" : "s"}; ${buyers.size} linked buyer wallets shown. Click a node for details.`
    : "No shared-funding group matches the current filter.";
}

function layoutGraph(nodes, edges) {
  const width = 900, height = 620;
  const hash = value => [...value].reduce((total, char) => (total * 31 + char.charCodeAt(0)) >>> 0, 7);
  nodes.forEach(node => {
    const seed = hash(node.id);
    node.x = 80 + seed % 740;
    node.y = 70 + Math.floor(seed / 997) % 480;
    node.vx = 0; node.vy = 0;
  });
  const byId = new Map(nodes.map(node => [node.id, node]));
  for (let tick = 0; tick < 140; tick++) {
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        const distance2 = Math.max(80, dx * dx + dy * dy);
        const force = 220 / distance2;
        a.vx += dx * force; a.vy += dy * force;
        b.vx -= dx * force; b.vy -= dy * force;
      }
    }
    edges.forEach(edge => {
      const a = byId.get(edge.source), b = byId.get(edge.target);
      if (!a || !b) return;
      const dx = b.x - a.x, dy = b.y - a.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const desired = edge.type === "FUNDED" ? 62 : 82;
      const force = (distance - desired) * .004;
      a.vx += dx / distance * force; a.vy += dy / distance * force;
      b.vx -= dx / distance * force; b.vy -= dy / distance * force;
    });
    nodes.forEach(node => {
      node.vx += (width / 2 - node.x) * .0007;
      node.vy += (height / 2 - node.y) * .0007;
      node.x = Math.max(25, Math.min(width - 130, node.x + node.vx));
      node.y = Math.max(25, Math.min(height - 25, node.y + node.vy));
      node.vx *= .82; node.vy *= .82;
    });
  }
}

function showNodeDetail(node, edges, groups) {
  if (!node) return;
  const connected = new Set(edges.filter(edge => edge.source === node.id || edge.target === node.id)
    .flatMap(edge => [edge.source, edge.target]).filter(id => id !== node.id));
  const relatedGroups = groups.filter(group => group.mint === node.id || group.funder === node.id ||
    edges.some(edge => edge.source === node.id && edge.mint === group.mint));
  const detail = document.querySelector("#network-detail");
  detail.querySelector(".badge").textContent = node.type;
  const linkType = node.type === "TOKEN" ? "token" : "account";
  detail.querySelector("h3").innerHTML = solscanLink(node.id, node.label || short(node.id, 10, 8), linkType);
  detail.querySelector(":scope > p").innerHTML = node.type === "SERVICE"
    ? `Broad exchange/service origin. Shown as funding context but excluded from common-ownership scoring. ${solscanLink(node.id, short(node.id, 10, 8), linkType)}`
    : `${connected.size} visible connection${connected.size === 1 ? "" : "s"}. ${relatedGroups.length} related funding group${relatedGroups.length === 1 ? "" : "s"}. ${solscanLink(node.id, short(node.id, 10, 8), linkType)}`;
}

initialize();
