const solscan=(type,value)=>`https://solscan.io/${type}/${value}`;
const short=value=>value?`${value.slice(0,6)}…${value.slice(-4)}`:'—';
const link=(value,type='account',label=short(value))=>value?`<a href="${solscan(type,value)}" target="_blank" rel="noreferrer">${label} ↗</a>`:'—';
const cases={caller:{label:'Original four-wallet case',path:'/data/analysis.json'},eqbq:{label:'EqBQhoU…64qvv',path:'/data/cases/EqBQhoU88ty3a3aDphDRCBuiRirjUsFSFi4UHGY64qvv.json'},case215:{label:'215nhcA…EgQjP',path:'/data/cases/215nhcAHjQQGgwpQSJQ7zR26etbjjtVdW74NLzwEgQjP.json'}};
let data,active='ALL';
function badge(value){return `<span class="badge ${value}">${value.replaceAll('_',' ')}</span>`}
function renderStandard(){
 document.querySelector('.hero h1').textContent=`Case: ${short(data.profile)}`;
 document.querySelector('.hero>p:last-child').innerHTML=`Independent profile investigation for ${link(data.profile)}. No wallet cluster, score, or conclusion is shared with another selected case.`;
 ['#findings','#evidence','#positions','#methods'].forEach(x=>document.querySelector(x).hidden=true);
 const view=document.querySelector('#case-overview'),s=data.summary;view.hidden=false;
 document.querySelector('#case-summary').innerHTML=[['canonical calls',s.calls_total],['tokens fully analyzed',s.tokens_analyzed],['bundle evidence',s.tokens_with_launch_bundle_evidence],['timing-only clusters',s.tokens_with_timing_only_launch_clusters],['caller-linked buyers',s.caller_linked_buyers],['funding paths resolved',`${s.wallets_with_funding_resolved} / ${s.unique_early_buyer_wallets}`]].map(([k,v])=>`<article><span>${k}</span><strong>${v}</strong></article>`).join('');
 document.querySelector('#case-bundles').innerHTML=(data.suspected_bundles||[]).map(row=>{const state=row.bundle_status==='BUNDLED'?'SUPPORTED_FINDING':row.bundle_status==='UNKNOWN'?'INCOMPLETE':'SCOPED_NEGATIVE';return `<tr><td>${link(row.mint,'token',row.token||short(row.mint))}</td><td>${badge(state)}<br><small>${row.confidence||'pending'}</small></td><td>${row.evidence||'Trade and funding scan pending or incomplete.'}</td><td>${row.shared_funder?link(row.shared_funder):'No supported link in this case yet.'}</td></tr>`}).join('');
}
function renderOriginal(){
 document.querySelector('#generated').textContent=new Date(data.generated_at).toLocaleDateString();
 const findings=()=>{const rows=data.investigation.findings.filter(x=>active==='ALL'||x.status===active);document.querySelector('#findings-body').innerHTML=rows.map(x=>`<tr><td><strong>${x.finding}</strong></td><td>${x.evidence}</td><td>${badge(x.status)}</td><td>${x.limitations}</td></tr>`).join('')};
 document.querySelectorAll('[data-filter]').forEach(button=>button.onclick=()=>{active=button.dataset.filter;document.querySelectorAll('[data-filter]').forEach(x=>x.classList.toggle('active',x===button));findings()});findings();
 const ledger=data.investigation.position_ledger;document.querySelector('#positions-body').innerHTML=data.investigation.position_analysis.map(position=>`<tr><td>${link(position.mint,'token',position.symbol)}</td><td class="num">${position.purchase_count}</td><td class="num">${position.sale_count}</td><td class="num">${position.transfer_count}</td><td class="num">${Number(position.observed_net_sol_cash_flow).toFixed(6)} SOL</td><td>${badge(position.reconciliation_status.startsWith('RECONCILED')?'SUPPORTED_FINDING':'INCOMPLETE')}</td><td>${ledger.filter(x=>x.mint===position.mint).length} events</td></tr>`).join('');
 const exact=data.investigation.same_slot_execution.filter(x=>x.wallets_same_slot===4).length,card=document.querySelector('.cards article:nth-child(2)');card.querySelector('strong').textContent=`${exact} four-wallet same-slot buys`;
 document.querySelector('#coverage').innerHTML=Object.entries(data.investigation.coverage).map(([k,v])=>`<article><span>${k.replaceAll('_',' ')}</span><p>${v}</p></article>`).join('');
}
async function init(){const requested=new URLSearchParams(location.search).get('case')||'caller',id=cases[requested]?requested:'caller',picker=document.querySelector('#case-select');picker.innerHTML=Object.entries(cases).map(([key,entry])=>`<option value="${key}">${entry.label}</option>`).join('');picker.value=id;picker.onchange=()=>location.search=`?case=${picker.value}`;const response=await fetch(cases[id].path);if(!response.ok)throw Error('case snapshot is not published yet');data=await response.json();if(data.investigation)renderOriginal();else renderStandard()}
init().catch(error=>document.body.innerHTML=`<main><p>Snapshot unavailable: ${error.message}</p></main>`);
