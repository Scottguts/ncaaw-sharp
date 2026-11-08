"""Static dashboard — docs/ for GitHub Pages, styled after the NCAAW/MLB
React model apps (dark JetBrains-Mono terminal aesthetic).

Three tabs (client-side, no framework):
  PREDICTIONS — every game: matchup card, pitching-style stat grid (model
                score / win% / total / fair spread vs book), EDGE glow when
                a bet fired, date navigation.
  BEST BETS   — fired cards with star sizing + live status (pending/win/loss).
  BET HISTORY — P&L summary tiles (net units, ROI, W-L-P, CLV) + every bet
                ever logged, newest first.

Rebuilt + pushed by every daily-bets / line-watch / closing-snapshot run;
index.html auto-refreshes every 5 minutes. Deterministic output.
"""

from __future__ import annotations

import csv
import html
import json
import math
import sys
from pathlib import Path

from ncaaw_data_scraper import DATA_ROOT
from ncaaw_model import SPREAD_SIGMA


def _win_prob_home(margin_home: float) -> float:
    return 0.5 * (1.0 + math.erf(margin_home / SPREAD_SIGMA / math.sqrt(2.0)))

DOCS = Path(__file__).resolve().parent / "docs"

C = {  # palette lifted from the React app
    "bg": "#0a0f1a", "surface": "#111827", "hover": "#1a2236",
    "border": "#1e293b", "text": "#e2e8f0", "mut": "#8899aa", "dim": "#556677",
    "accent": "#f97316", "green": "#22c55e", "red": "#ef4444",
    "blue": "#3b82f6", "yellow": "#eab308",
}

STYLE = f"""
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700;800&display=swap');
*{{box-sizing:border-box}}
body{{font-family:'JetBrains Mono','SF Mono',monospace;background:{C['bg']};color:{C['text']};
margin:0;font-size:13px;min-height:100vh}}
a{{color:{C['blue']};text-decoration:none}}
.header{{background:linear-gradient(135deg,{C['surface']} 0%,#0d1525 100%);
border-bottom:1px solid {C['border']};padding:16px 20px;display:flex;align-items:center;
justify-content:space-between;flex-wrap:wrap;gap:8px}}
.title{{font-size:18px;font-weight:800;letter-spacing:-0.02em;
background:linear-gradient(135deg,{C['accent']} 0%,#fbbf24 100%);
-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
.subtitle{{font-size:11px;color:{C['mut']};margin-top:2px}}
.badge{{display:inline-flex;align-items:center;padding:2px 8px;border-radius:6px;
font-size:11px;font-weight:700}}
.b-green{{color:{C['green']};background:{C['green']}18;border:1px solid {C['green']}25}}
.b-red{{color:{C['red']};background:{C['red']}18;border:1px solid {C['red']}25}}
.b-grey{{color:{C['mut']};background:{C['mut']}18;border:1px solid {C['mut']}25}}
.b-yellow{{color:{C['yellow']};background:{C['yellow']}18;border:1px solid {C['yellow']}25}}
.b-accent{{color:{C['accent']};background:{C['accent']}18;border:1px solid {C['accent']}25}}
.tabs{{display:flex;gap:2px;padding:8px 20px;background:{C['surface']};
border-bottom:1px solid {C['border']};position:sticky;top:0;z-index:5}}
.tab{{padding:8px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;
border:none;background:transparent;color:{C['mut']};font-family:inherit}}
.tab.active{{background:{C['accent']};color:#fff}}
.wrap{{padding:20px;max-width:1100px;margin:0 auto}}
.card{{background:{C['surface']};border:1px solid {C['border']};border-radius:10px;
padding:16px;margin-bottom:12px}}
.card.edge{{border:1px solid {C['accent']}40;box-shadow:0 0 20px {C['accent']}22}}
.matchup{{font-size:15px;font-weight:800;letter-spacing:-0.01em}}
.muted{{font-size:11px;color:{C['mut']}}} .dim{{font-size:10px;color:{C['dim']}}}
.grid4{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:12px 0}}
.stat{{padding:8px 10px;background:{C['bg']};border-radius:6px;text-align:center}}
.stat .l{{font-size:10px;color:{C['mut']};margin-bottom:3px}}
.stat .v{{font-size:15px;font-weight:800}}
.stat .s{{font-size:10px;color:{C['dim']}}}
.play{{flex:1;min-width:200px;padding:10px 12px;border-radius:8px;margin-top:8px}}
.play-g{{background:{C['green']}12;border:1px solid {C['green']}30}}
.play-b{{background:{C['blue']}12;border:1px solid {C['blue']}30}}
.play-head{{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}}
.stars{{font-size:14px;font-weight:800;color:{C['yellow']}}}
.datenav{{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}}
.navbtn{{padding:8px 16px;border-radius:6px;border:1px solid {C['border']};color:{C['mut']};
background:transparent;font-size:14px;font-weight:700}}
.datetitle{{font-size:16px;font-weight:700}}
.pnl{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:16px}}
.pnl .card{{text-align:center;padding:12px;margin:0}}
.pnl .l{{font-size:10px;color:{C['mut']}}} .pnl .v{{font-size:20px;font-weight:800}}
.betrow{{display:flex;justify-content:space-between;align-items:center;padding:12px;
background:{C['surface']};border:1px solid {C['border']};border-radius:10px;margin-bottom:8px}}
.g{{color:{C['green']}}} .r{{color:{C['red']}}} .y{{color:{C['yellow']}}} .bl{{color:{C['blue']}}}
.empty{{text-align:center;color:{C['mut']};padding:40px}}
.search{{width:100%;background:{C['bg']};border:1px solid {C['border']};border-radius:8px;
padding:10px 14px;color:{C['text']};font-family:inherit;font-size:13px;outline:none;
margin-bottom:14px}}
.search:focus{{border-color:{C['accent']}66}}
.gamecard{{cursor:pointer}}
.chev{{color:{C['dim']};font-size:14px;margin-right:6px}}
.details{{margin-top:12px;border-top:1px solid {C['border']};padding-top:12px}}
.details p{{font-size:12px;color:{C['mut']};line-height:1.55;margin:.4rem 0}}
.details b{{color:{C['text']}}}
.statrow td{{font-variant-numeric:tabular-nums}}
.better{{color:{C['green']};font-weight:700}}
.lrow{{display:flex;gap:8px;flex-wrap:wrap;margin:.5rem 0}}
.lrow input,.lrow select{{background:{C['bg']};border:1px solid {C['border']};
border-radius:6px;padding:8px 10px;color:{C['text']};font-family:inherit;font-size:13px}}
.btn{{background:{C['accent']};border:none;border-radius:6px;padding:8px 16px;
color:#fff;font-family:inherit;font-weight:700;font-size:12px;cursor:pointer}}
.btn.sec{{background:{C['hover']};border:1px solid {C['border']}}}
.verdict{{border-radius:8px;padding:10px 12px;margin:.5rem 0;font-size:13px}}
.v-go{{background:{C['green']}18;border:1px solid {C['green']}40}}
.v-no{{background:{C['hover']};border:1px solid {C['border']}}}
.chat{{display:flex;flex-direction:column;gap:8px;margin-top:8px}}
.msg{{border-radius:10px;padding:10px 12px;font-size:13px;line-height:1.5;white-space:pre-wrap}}
.msg.user{{background:{C['accent']}22;border:1px solid {C['accent']}35;align-self:flex-end;max-width:85%}}
.msg.ai{{background:{C['hover']};border:1px solid {C['border']};max-width:95%}}
.chatbar{{display:flex;gap:8px;margin-top:8px}}
.chatbar textarea{{flex:1;background:{C['bg']};border:1px solid {C['border']};border-radius:8px;
padding:10px;color:{C['text']};font-family:inherit;font-size:13px;resize:vertical;min-height:44px}}
.msg .who{{font-size:9px;letter-spacing:.08em;color:{C['dim']};margin-bottom:4px;font-weight:700}}
.chead{{font-weight:800;color:{C['text']};margin:.5rem 0 .2rem;font-size:13px}}
.cbul{{padding-left:6px;margin:.15rem 0}}
.cverdict{{border-radius:8px;padding:8px 12px;margin:.6rem 0 .2rem;font-weight:700;font-size:13px}}
.cv-bet{{background:{C['green']}18;border:1px solid {C['green']}45;color:{C['green']}}}
.cv-pass{{background:{C['hover']};border:1px solid {C['border']};color:{C['mut']}}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{padding:8px 6px;text-align:left;font-size:10px;font-weight:700;color:{C['mut']};
text-transform:uppercase;border-bottom:2px solid {C['border']}}}
td{{padding:6px;border-bottom:1px solid {C['border']}30}}
.lc:empty{{display:none}}
.lc-live{{color:{C['red']};background:{C['red']}18;border:1px solid {C['red']}30;
animation:pulse 2s infinite}}
@keyframes pulse{{50%{{opacity:.55}}}}
.card{{transition:border-color .15s, box-shadow .15s}}
.gamecard:hover{{border-color:{C['accent']}50}}
.btn{{transition:filter .1s}} .btn:hover{{filter:brightness(1.15)}}
.tab{{transition:background .12s,color .12s}}
::selection{{background:{C['accent']}55}}
*{{scrollbar-width:thin;scrollbar-color:{C['border']} transparent}}
::-webkit-scrollbar{{width:8px;height:8px}}
::-webkit-scrollbar-thumb{{background:{C['border']};border-radius:99px}}
details.xtra{{margin-top:16px}} details.xtra summary{{color:{C['dim']};cursor:pointer;
font-size:12px;padding:8px 0}}
details.xtra div{{color:{C['dim']};font-size:12px;padding:3px 0 3px 14px}}
@media(max-width:700px){{.grid4{{grid-template-columns:repeat(2,1fr)}}
.pnl{{grid-template-columns:repeat(2,1fr)}}
.tab{{padding:8px 9px;font-size:11px}}
.wrap{{padding:14px 10px}}
.header{{padding:12px 14px}}}}
"""

TABS_JS = """
function showTab(id){
  document.querySelectorAll('.pane').forEach(p=>p.style.display='none');
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.getElementById('pane-'+id).style.display='block';
  document.getElementById('tab-'+id).classList.add('active');
  if(history.replaceState)history.replaceState(null,'','#'+id);
}
function toggleGame(id){
  const d=document.getElementById('det-'+id);
  const c=document.getElementById('chev-'+id);
  const open=d.style.display!=='none';
  d.style.display=open?'none':'block';
  if(c)c.textContent=open?'▸':'▾';
}
function searchGames(q){
  q=q.trim().toLowerCase();
  document.querySelectorAll('.gamecard').forEach(el=>{
    el.style.display=(!q||el.dataset.search.includes(q))?'':'none';
  });
  document.querySelectorAll('.secthead').forEach(el=>{el.style.display=q?'none':'';});
}
window.addEventListener('DOMContentLoaded',()=>{
  const h=(location.hash||'#predictions').slice(1);
  showTab(['predictions','bets','history','finals','live','chat'].includes(h)?h:'predictions');
  if(h==='live'||h==='chat'){liveRefresh();renderKeyState();}
  loadChat();
  annotateLive();
  // in-app browsers (Discord, Messages, Instagram...) block cross-site
  // fetches and wipe localStorage — warn loudly
  const ua=navigator.userAgent||'';
  if(/Discord|FBAN|FBAV|Instagram|Line\\/|wv\\)|WebView/i.test(ua)){
    const w=document.createElement('div');
    w.style.cssText='background:#ef444422;border:1px solid #ef444460;border-radius:8px;padding:10px 14px;margin:10px 20px;font-size:13px';
    w.innerHTML='⚠️ <b>You are in an in-app browser</b> — live scores, the copilot, and saved keys will not work here. Tap the ··· menu and choose <b>Open in Browser / Safari</b>.';
    document.body.prepend(w);
  }
});
// live score chips on the prediction cards (one free ESPN call per page load)
async function annotateLive(){
  if(!document.querySelector('.lc'))return;
  try{
    const today=new Date().toLocaleDateString('en-CA',{timeZone:'America/New_York'}).replaceAll('-','');
    const r=await fetch('https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard?dates='+today+'&limit=500&groups=50');
    const d=await r.json();
    (d.events||[]).forEach(ev=>{
      const el=document.getElementById('lc-'+ev.id);
      if(!el)return;
      const st=ev.status||{}; const state=(st.type||{}).state;
      const sides={}; (ev.competitions[0].competitors||[]).forEach(c=>sides[c.homeAway]=c);
      const sc=`${sides.away?sides.away.score:'?'}–${sides.home?sides.home.score:'?'}`;
      if(state==='in'){el.textContent=`🔴 ${sc} Q${st.period||''} ${st.displayClock||''}`;el.classList.add('lc-live');}
      else if(state==='post'){el.textContent=`FINAL ${sc}`;}
    });
  }catch(e){}
}
function fetchHint(e){
  return (e&&e.message?e.message:'failed')+
    ' — if you opened this link from Discord or Messages, tap ··· and "Open in Browser"; '+
    'ad-blockers/VPNs can also block api calls.';
}

// ═══ LIVE TAB — client-side line checker ═══
const ESPN='https://site.api.espn.com/apis/site/v2/sports/basketball/womens-college-basketball/scoreboard';
let LIVEGAMES={};
function phi(x){ // Abramowitz-Stegun erf approx
  const t=1/(1+0.3275911*Math.abs(x/Math.SQRT2));
  const e=1-(((((1.061405429*t-1.453152027)*t)+1.421413741)*t-0.284496736)*t+0.254829592)*t*Math.exp(-(x*x)/2);
  return x>=0?0.5*(1+e):0.5*(1-e);
}
function edgePct(p,am){const dec=am>0?am/100+1:100/(-am)+1;return p*dec-1;}
function fmtOdds(a){return a>0?'+'+a:''+a;}
async function liveRefresh(){
  const sel=document.getElementById('livesel');
  if(!sel)return;
  sel.innerHTML='<option>loading…</option>';
  const today=new Date().toLocaleDateString('en-CA',{timeZone:'America/New_York'}).replaceAll('-','');
  try{
    const r=await fetch(ESPN+'?dates='+today+'&limit=500&groups=50');
    const d=await r.json();
    LIVEGAMES={};
    (d.events||[]).forEach(ev=>{
      const id=String(ev.id); const model=window.SLATE[id];
      if(!model)return;
      const comp=ev.competitions[0];
      const sides={}; comp.competitors.forEach(c=>sides[c.homeAway]=c);
      const st=ev.status||{}; const state=(st.type||{}).state;
      LIVEGAMES[id]={id,name:ev.name,state,period:st.period||0,clock:st.displayClock||'',
        hs:+(sides.home.score||0),as:+(sides.away.score||0),model};
    });
    const inplay=Object.values(LIVEGAMES).filter(g=>g.state==='in');
    const pre=Object.values(LIVEGAMES).filter(g=>g.state==='pre');
    sel.innerHTML='';
    [...inplay,...pre].forEach(g=>{
      const tag=g.state==='in'?`LIVE ${g.as}-${g.hs} Q${g.period} ${g.clock}`:'pregame';
      sel.insertAdjacentHTML('beforeend',`<option value='${g.id}'>${g.name} — ${tag}</option>`);
    });
    if(!sel.options.length)sel.innerHTML='<option>no graded games today</option>';
    document.getElementById('livestatus').textContent=
      `${inplay.length} live · ${pre.length} upcoming · refreshed ${new Date().toLocaleTimeString()}`;
  }catch(e){sel.innerHTML='<option>ESPN fetch failed — retry</option>';document.getElementById('livestatus').textContent=fetchHint(e);}
}
function minutesLeft(g){
  const parts=(g.clock||'0:00').split(':');
  const cm=(+parts[0]||0)+((+parts[1]||0)/60);
  if(g.period<=0)return 40;
  if(g.period<=4)return (4-g.period)*10+cm;
  return cm;
}
function liveVerdict(){
  const id=document.getElementById('livesel').value;
  const g=LIVEGAMES[id]; const out=document.getElementById('liveout');
  if(!g||!g.model){out.innerHTML="<div class='verdict v-no'>pick a game</div>";return;}
  const sp=parseFloat(document.getElementById('livesp').value);
  const tot=parseFloat(document.getElementById('livetot').value);
  const price=parseInt(document.getElementById('livepr').value)||-110;
  const f=g.state==='pre'?1:Math.max(0,Math.min(1,minutesLeft(g)/40));
  const m=g.model;
  const em=(g.hs-g.as)+m.m*f, et=(g.hs+g.as)+m.t*f;
  const ss=(m.ss||12)*Math.sqrt(Math.max(f,0.05)), st=(m.st||16)*Math.sqrt(Math.max(f,0.05));
  let rows=[`<div class='muted'>projected finish: home margin ${em>0?'+':''}${em.toFixed(1)}, total ${et.toFixed(1)} · ${(f*100).toFixed(0)}% of game left</div>`];
  let checks=[];
  if(!isNaN(sp)){
    const ph=phi((em+sp)/ss);
    checks.push({label:`HOME ${sp>0?'+':''}${sp}`,p:ph});
    checks.push({label:`AWAY ${-sp>0?'+':''}${-sp}`,p:1-ph});
  }
  if(!isNaN(tot)){
    const po=phi((et-tot)/st);
    checks.push({label:`OVER ${tot}`,p:po});
    checks.push({label:`UNDER ${tot}`,p:1-po});
  }
  if(g.state==='in'&&f<0.1){
    out.innerHTML=rows.join('')+"<div class='verdict v-no'>PASS — under 4 game-minutes left, projection unreliable</div>";return;
  }
  let best=null;
  checks.forEach(c=>{c.edge=edgePct(c.p,price);
    rows.push(`<div class='muted'>${c.label}: fair ${(c.p*100).toFixed(1)}% · edge ${(c.edge*100).toFixed(1)}%</div>`);
    if(c.edge>=0.035&&c.edge<=0.12&&(!best||c.edge>best.edge))best=c;});
  const stale=checks.some(c=>c.edge>0.12);
  if(best)rows.push(`<div class='verdict v-go'>✅ VIABLE — ${best.label} (${fmtOdds(price)}): fair ${(best.p*100).toFixed(1)}%, edge ${(best.edge*100).toFixed(1)}% <span class='muted'>· live advisory, separate from pregame cards</span></div>`);
  else if(stale)rows.push("<div class='verdict v-no'>⚠️ PASS — biggest 'edge' exceeds the 12% sanity reject. Re-check the line you entered.</div>");
  else if(checks.length)rows.push("<div class='verdict v-no'>PASS — nothing clears the 3.5% live edge bar.</div>");
  else rows.push("<div class='verdict v-no'>enter a live spread and/or total</div>");
  out.innerHTML=rows.join('');
}
function openLive(id){showTab('live');liveRefresh().then(()=>{
  const sel=document.getElementById('livesel');
  if(sel&&[...sel.options].some(o=>o.value===id))sel.value=id;});}

// ═══ MANUAL TRIGGERS — fire the pipeline from your phone ═══
// Needs a fine-grained GitHub PAT (repo: ncaaw-sharp, Actions: write),
// stored in localStorage only — same trust model as the AI key.
function ghToken(){return localStorage.getItem('gh_pat')||'';}
function saveGh(){
  const v=document.getElementById('ghpat').value.trim();
  if(v)localStorage.setItem('gh_pat',v);else localStorage.removeItem('gh_pat');
  renderKeyState();
}
function clearGh(){localStorage.removeItem('gh_pat');renderKeyState();}
async function runWorkflow(file,label){
  const t=ghToken();
  const out=document.getElementById('runstatus');
  if(!t){out.innerHTML="<span class='r'>set a GitHub token first (or use the Actions page link)</span>";return;}
  out.textContent='dispatching '+label+'…';
  try{
    const r=await fetch(`https://api.github.com/repos/Scottguts/ncaaw-sharp/actions/workflows/${file}/dispatches`,{
      method:'POST',
      headers:{'authorization':'Bearer '+t,'accept':'application/vnd.github+json',
               'content-type':'application/json'},
      body:JSON.stringify({ref:'main'})});
    if(r.status===204){
      out.innerHTML=`<span class='g'>✅ ${label} dispatched ${new Date().toLocaleTimeString()} — results hit Discord + this page in ~3 min</span>`;
    }else{
      const d=await r.json().catch(()=>({}));
      out.innerHTML=`<span class='r'>failed (${r.status}): ${(d.message||'').slice(0,120)}</span>`;
    }
  }catch(e){out.innerHTML="<span class='r'>request failed: "+fetchHint(e)+"</span>";}
}

// ═══ AI CHAT — Claude, direct from the browser (key stays on this device) ═══
let CHAT=[];
let PENDING_IMGS=[];
function addImageFile(file){
  if(!file||!file.type.startsWith('image/'))return;
  const img=new Image();
  img.onload=()=>{
    const MAX=1400;
    let w=img.width,h=img.height;
    if(Math.max(w,h)>MAX){const s=MAX/Math.max(w,h);w=Math.round(w*s);h=Math.round(h*s);}
    const c=document.createElement('canvas');c.width=w;c.height=h;
    c.getContext('2d').drawImage(img,0,0,w,h);
    const url=c.toDataURL('image/jpeg',0.85);
    PENDING_IMGS.push({media_type:'image/jpeg',data:url.split(',')[1],url});
    renderImgPreview();
    URL.revokeObjectURL(img.src);
  };
  img.src=URL.createObjectURL(file);
}
function renderImgPreview(){
  const el=document.getElementById('imgpreview');
  el.innerHTML=PENDING_IMGS.map((im,i)=>
    `<span style='position:relative;display:inline-block;margin:4px'>`+
    `<img src='${im.url}' style='height:64px;border-radius:6px;border:1px solid #30363d'>`+
    `<a onclick='PENDING_IMGS.splice(${i},1);renderImgPreview()' `+
    `style='position:absolute;top:-6px;right:-6px;background:#ef4444;color:#fff;`+
    `border-radius:99px;width:18px;height:18px;text-align:center;line-height:18px;`+
    `font-size:11px;cursor:pointer'>×</a></span>`).join('');
}
function aiKey(){return localStorage.getItem('anthropic_key')||'';}
function saveKey(){
  const v=document.getElementById('aikey').value.trim();
  if(v){localStorage.setItem('anthropic_key',v);}else{localStorage.removeItem('anthropic_key');}
  renderKeyState();
}
function renderKeyState(){
  const has=!!aiKey();
  document.getElementById('keyform').style.display=has?'none':'flex';
  document.getElementById('keyset').style.display=has?'block':'none';
  const gh=!!ghToken();
  const gf=document.getElementById('ghform'), gs=document.getElementById('ghset');
  if(gf){gf.style.display=gh?'none':'flex';gs.style.display=gh?'block':'none';}
}
function clearKey(){localStorage.removeItem('anthropic_key');renderKeyState();}
function chatContext(){
  const lines=[];
  const id=(document.getElementById('livesel')||{}).value;
  const src=Object.keys(LIVEGAMES).length?Object.values(LIVEGAMES)
    :Object.entries(SLATE).map(([k,m])=>({id:k,name:(m.aw||'?')+' @ '+(m.hm||'?'),state:'pre',model:m}));
  src.forEach(g=>{
    const m=g.model;
    const live=g.state==='in'?` LIVE ${g.as}-${g.hs} Q${g.period} ${g.clock} (${(minutesLeft(g)/40*100).toFixed(0)}% left)`:
      g.state==='post'?` FINAL ${g.as}-${g.hs}`:' pregame';
    const rank=(m.ra?` #${m.ra} away`:'' )+(m.rh?` #${m.rh} home`:'');
    const bk=(m.bk_sp!==undefined?` | book: home ${m.bk_sp>0?'+':''}${m.bk_sp}`:'')+
             (m.bk_t!==undefined?` O/U ${m.bk_t}`:'');
    const extra=(m.card?` | FIRED: ${m.card}`:'')+(m.inj?` | INJ: ${m.inj}`:'');
    lines.push(`${g.name}:${live}${rank} | model: ${m.aw||'away'} ${m.as_||'?'}-${m.hs_||'?'} ${m.hm||'home'} (home margin ${m.m>0?'+':''}${m.m}, total ${m.t}, sigma ${m.ss||12}/${m.st||16})${bk}${extra}${id===g.id?' [USER SELECTED THIS GAME]':''}`);
  });
  return (window.BETCTX?window.BETCTX+'\\n':'')+lines.join('\\n')+
    `\\n(as of ${new Date().toLocaleTimeString()} ET-ish, user local)`;
}
async function sendChat(){
  const box=document.getElementById('aimsg');
  const text=box.value.trim();
  if(!text&&!PENDING_IMGS.length)return;
  const key=aiKey(); if(!key){alert('Set your Anthropic API key first.');return;}
  box.value='';
  if(PENDING_IMGS.length){
    const blocks=PENDING_IMGS.map(im=>({type:'image',
      source:{type:'base64',media_type:im.media_type,data:im.data},_url:im.url}));
    blocks.push({type:'text',text:text||'What do you see — any viable plays in this screenshot? Check the numbers against the model.'});
    CHAT.push({role:'user',content:blocks});
    PENDING_IMGS=[];renderImgPreview();
  }else{
    CHAT.push({role:'user',content:text});
  }
  renderChat(); renderChat('…thinking');
  if(!Object.keys(LIVEGAMES).length){try{await liveRefresh();}catch(e){}}
  const system=`You are the betting copilot inside NCAAW Sharp — a market-anchored women's college basketball model built by your user, Scott. `+
`VOICE: talk like a sharp, honest friend watching the games with him — plain English, short sentences, contractions, no jargon walls. `+
`Lead with your answer in one plain sentence. Back it with 2-3 short points, weaving the key numbers in naturally `+
`("the model makes this more like -9, so laying -12.5 means paying for three points that aren't there"). `+
`Do all math silently — never show formulas, sigma, or Phi unless he asks how you got there. `+
`Round everything sensibly (fair 54%, not 0.5417). Use team names, never event ids. `+
`ALWAYS end with exactly one line formatted: "Verdict: BET 0.5u — <pick>" or "Verdict: PASS". `+
`DISCIPLINE (non-negotiable): edges come from small disagreements with the market; most spots are passes and saying PASS is good advice; never suggest loosening gates or chasing. `+
`Sizing (1u = 1% bankroll): ~5% edge = 1.5u max; ~3.5% = 1u; ~2% = 0.5u; never exceed 6u/day across 5 bets. `+
`Live reference math (do silently): proj final home margin = current margin + model_margin x fraction_left; proj total = current total + model_total x fraction_left; `+
`uncertainty shrinks with sqrt(fraction_left); viable live edge band 3.5%-12%; a bigger "edge" than 12% usually means a misread line — say that like a human ("that number looks off, double-check it"). `+
`It's opening week: ratings are thin, the model runs hot on mismatches — be humble about big spreads and say so plainly. `+
`If a sportsbook screenshot is attached: read every visible line, match to the board below, and verdict each one in a short list. `+
`PENDING BETS below are already placed — talk about how they're tracking or hedges, never re-recommend them. `+
`Today's board (model numbers are pregame full-game rates):\\n`+chatContext();
  const msgs=CHAT.slice(-12).map((m,i,arr)=>{
    if(typeof m.content==='string')return m;
    const recent=i>=arr.length-4;   // only resend images from the last 2 exchanges
    if(!recent&&m.content.some(b=>b.type==='image')){
      const t=m.content.filter(b=>b.type==='text').map(b=>b.text).join(' ');
      return {role:m.role,content:'[earlier screenshot omitted] '+t};
    }
    return {role:m.role,content:m.content.map(b=>
      b.type==='image'?{type:'image',source:b.source}:{type:'text',text:b.text})};
  });
  try{
    const r=await fetch('https://api.anthropic.com/v1/messages',{
      method:'POST',
      headers:{'content-type':'application/json','x-api-key':key,
        'anthropic-version':'2023-06-01','anthropic-dangerous-direct-browser-access':'true'},
      body:JSON.stringify({model:'claude-sonnet-4-6',max_tokens:1800,stream:true,
        system:system,messages:msgs})});
    if(!r.ok){
      const d=await r.json().catch(()=>({}));
      CHAT.push({role:'assistant',content:'error: '+JSON.stringify(d.error||d).slice(0,300)});
      renderChat();saveChat();return;
    }
    CHAT.push({role:'assistant',content:''});
    const reader=r.body.getReader();
    const dec=new TextDecoder();
    let buf='';
    while(true){
      const {done,value}=await reader.read();
      if(done)break;
      buf+=dec.decode(value,{stream:true});
      const lines=buf.split('\\n'); buf=lines.pop();
      for(const ln of lines){
        if(!ln.startsWith('data: '))continue;
        try{
          const ev=JSON.parse(ln.slice(6));
          if(ev.type==='content_block_delta'&&ev.delta&&ev.delta.text){
            CHAT[CHAT.length-1].content+=ev.delta.text;
            renderChat();
          }
        }catch(e){}
      }
    }
  }catch(e){CHAT.push({role:'assistant',content:'request failed: '+fetchHint(e)});}
  renderChat();saveChat();
}
function saveChat(){
  try{
    const slim=CHAT.slice(-30).map(m=>typeof m.content==='string'?m:
      {role:m.role,content:'[screenshot] '+m.content.filter(b=>b.type==='text').map(b=>b.text).join(' ')});
    localStorage.setItem('chat_hist',JSON.stringify(slim));
  }catch(e){}
}
function loadChat(){
  try{CHAT=JSON.parse(localStorage.getItem('chat_hist')||'[]');}catch(e){CHAT=[];}
  if(CHAT.length)renderChat();
}
function clearChat(){CHAT=[];localStorage.removeItem('chat_hist');renderChat();}
function quick(t){document.getElementById('aimsg').value=t;sendChat();}
function fmtMsg(t){
  let h=t.replace(/&/g,'&amp;').replace(/</g,'&lt;');
  h=h.replace(/\\*\\*(.+?)\\*\\*/g,'<b>$1</b>');
  h=h.replace(/^#{1,3}\\s*(.+)$/gm,'<div class="chead">$1</div>');
  h=h.replace(/^(Verdict:?\\s*)(.+)$/gmi,(m,a,b)=>{
    const pass=/\\bpass\\b/i.test(b);
    return `<div class="cverdict ${pass?'cv-pass':'cv-bet'}">${pass?'🛑':'🎯'} ${b}</div>`;});
  h=h.replace(/^[-•*]\\s+(.+)$/gm,'<div class="cbul">•&nbsp; $1</div>');
  return h;
}
function renderChat(pending){
  const el=document.getElementById('chatlog');
  el.innerHTML=CHAT.map(m=>{
    const who=m.role==='user'?'YOU':'COPILOT';
    let body;
    if(typeof m.content==='string'){body=fmtMsg(m.content);}
    else{
      body=m.content.map(b=>b.type==='image'
        ?`<img src='${b._url||''}' style='max-height:120px;border-radius:8px;display:block;margin:4px 0'>`
        :fmtMsg(b.text||'')).join('');
    }
    return `<div class='msg ${m.role==='user'?'user':'ai'}'><div class='who'>${who}</div>${body}</div>`;
  }).join('')+(pending?`<div class='msg ai muted'>${pending}</div>`:'');
  el.scrollTop=el.scrollHeight;
}
"""


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _price(p) -> str:
    try:
        p = int(float(p))
    except (TypeError, ValueError):
        return "—"
    return f"+{p}" if p > 0 else str(p)


def _stars(units) -> str:
    try:
        u = float(units)
    except (TypeError, ValueError):
        return ""
    return "★★★" if u >= 1.5 else "★★" if u >= 1.0 else "★" if u > 0 else ""


def _read_bets() -> list[dict]:
    log = DATA_ROOT / "bet_log.csv"
    if not log.exists():
        return []
    with log.open(newline="") as f:
        return list(csv.DictReader(f))


def _pnl(bets: list[dict]) -> dict:
    w = l = p = 0
    net = risked = 0.0
    clvs = []
    for b in bets:
        st = b.get("status")
        try:
            u = float(b.get("units_risked") or 0)
            pl = float(b.get("units_pl") or 0)
        except ValueError:
            u = pl = 0.0
        if st == "win":
            w += 1; net += pl; risked += u
        elif st == "loss":
            l += 1; net += pl; risked += u
        elif st == "push":
            p += 1
        try:
            clvs.append(float(b["clv_pct"]))
        except (KeyError, ValueError, TypeError):
            pass
    return {"w": w, "l": l, "p": p, "net": net, "risked": risked,
            "roi": (net / risked * 100) if risked else 0.0,
            "clv": (sum(clvs) / len(clvs)) if clvs else None,
            "pending": sum(1 for b in bets if b.get("status") == "pending")}


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------

def _team_stats_ctx(day: Path) -> dict:
    """espn_event_id -> {'home': torvik stats, 'away': torvik stats} for the
    advanced-metrics panel. Joined from the day's slate.json + teams.json."""
    try:
        slate = json.loads((day / "slate.json").read_text())
        teams = json.loads((day / "teams.json").read_text())["teams"]
    except (OSError, ValueError, KeyError):
        return {}
    ctx = {}
    for g in slate.get("games", []):
        ctx[g["espn_event_id"]] = {
            "home": teams.get(g["home"]["canon"]),
            "away": teams.get(g["away"]["canon"]),
        }
    return ctx


_STAT_ROWS = [
    ("T-Rank", "tRank", False), ("Record", "record", None),
    ("adjOE", "adjOE", True), ("adjDE", "adjDE", False),
    ("Tempo", "tempo", None), ("eFG%", "eFG", True), ("eFG% def", "eFGD", False),
    ("TOV%", "tor", False), ("TOV% forced", "torD", True),
    ("ORB%", "orb", True), ("DRB%", "drb", False),
    ("FT rate", "ftr", True), ("FT rate def", "ftrD", False),
]


def _details_html(g: dict, stats: dict | None) -> str:
    """Advanced metrics table + generated reasoning for one game."""
    m = g.get("model") or {}
    mk = g.get("market") or {}
    ts = stats or {}
    th, ta = ts.get("home"), ts.get("away")
    away_nm = g.get("away_short") or "Away"
    home_nm = g.get("home_short") or "Home"
    parts = []

    # four-factor stats are raw this-season numbers — meaningless at 0 games
    # (Torvik fills placeholders like eFG 0.0 / def 200.0)
    _FACTOR_KEYS = {"eFG", "eFGD", "tor", "torD", "orb", "drb", "ftr", "ftrD"}
    a_played = bool(ta and ta.get("games"))
    h_played = bool(th and th.get("games"))

    def _fmt(v, key, played):
        if key in _FACTOR_KEYS and not played:
            return None
        if isinstance(v, float):
            return f"{v:.1f}"
        return v

    if th and ta:
        rows = []
        for label, key, higher_better in _STAT_ROWS:
            av, hv = _fmt(ta.get(key), key, a_played), _fmt(th.get(key), key, h_played)
            ac = hc = ""
            rav, rhv = ta.get(key), th.get(key)
            if (higher_better is not None and av is not None and hv is not None
                    and isinstance(rav, (int, float)) and isinstance(rhv, (int, float))):
                if (rav > rhv) == higher_better and rav != rhv:
                    ac = " class='better'"
                elif rav != rhv:
                    hc = " class='better'"
            rows.append(f"<tr class='statrow'><td{ac}>{esc(av if av is not None else '—')}</td>"
                        f"<td style='color:{C['mut']}'>{esc(label)}</td>"
                        f"<td{hc} style='text-align:right'>{esc(hv if hv is not None else '—')}</td></tr>")
        parts.append(
            f"<table style='max-width:420px'><tr><th>{esc(away_nm)}</th>"
            f"<th style='text-align:center'>TORVIK</th>"
            f"<th style='text-align:right'>{esc(home_nm)}</th></tr>"
            + "".join(rows) + "</table>")

    # ── generated reasoning ──
    paras = []
    if th and ta and m:
        net_a = ta["adjOE"] - ta["adjDE"]
        net_h = th["adjOE"] - th["adjDE"]
        edge_team = home_nm if net_h > net_a else away_nm
        paras.append(
            f"<b>Efficiency:</b> {esc(away_nm)} nets {net_a:+.1f} pts/100 poss "
            f"(O {ta['adjOE']:.1f} / D {ta['adjDE']:.1f}) vs {esc(home_nm)} "
            f"{net_h:+.1f} (O {th['adjOE']:.1f} / D {th['adjDE']:.1f}) — a "
            f"{abs(net_h - net_a):.1f}-point gap favoring {esc(edge_team)} before "
            f"venue. At ~{m.get('proj_poss', 70):.0f} projected possessions that "
            f"drives most of the {abs(m.get('margin_home', 0)):.1f}-point model margin.")
        if a_played and h_played:   # factor stats are placeholders at 0 games
            ff = []
            shoot = (ta["eFG"] - th["eFGD"]) - (th["eFG"] - ta["eFGD"])
            if abs(shoot) >= 3:
                ff.append(f"shooting leans {esc(away_nm if shoot > 0 else home_nm)} "
                          f"({abs(shoot):.1f} eFG pts of matchup edge)")
            to = (th["torD"] - ta["tor"]) - (ta["torD"] - th["tor"])
            if abs(to) >= 3:
                ff.append(f"turnover battle leans {esc(away_nm if to > 0 else home_nm)}")
            glass = (ta["orb"] - (100 - th["drb"])) - (th["orb"] - (100 - ta["drb"]))
            if abs(glass) >= 3:
                ff.append(f"offensive glass leans {esc(away_nm if glass > 0 else home_nm)}")
            if ff:
                paras.append("<b>Four factors:</b> " + "; ".join(ff) + ".")
        paras.append(
            f"<b>Pace:</b> {esc(away_nm)} plays at {ta['tempo']:.0f} poss/40, "
            f"{esc(home_nm)} at {th['tempo']:.0f} → ~{m.get('proj_poss', 0):.0f} "
            f"possessions, model total {m.get('total', 0):.1f}.")
    if g.get("injury_notes"):
        paras.append("<b>Injuries:</b> " + esc("; ".join(g["injury_notes"])) + ".")
    anch = g.get("anchored") or {}
    if mk.get("spread_home") is not None and m:
        dis = -m["margin_home"] - mk["spread_home"]
        paras.append(
            f"<b>Market:</b> book has home {mk['spread_home']:+g} "
            f"({esc(mk.get('spread_src') or '')}); model says {-m['margin_home']:+.1f} "
            f"(Δ {dis:+.1f}). Anchored fair margin {anch.get('margin_home', '—')} "
            f"(shift capped at ±1.5)"
            + (f"; fair total {anch.get('total')} vs book {mk.get('total'):g}."
               if mk.get("total") is not None and anch.get("total") is not None else "."))
    sig = g.get("sigma") or {}
    if sig:
        paras.append(f"<b>Uncertainty:</b> σ spread {sig.get('spread')} / total "
                     f"{sig.get('total')} (widened until both teams reach ~10 games; "
                     f"currently {m.get('games_away', '?')} and {m.get('games_home', '?')} played).")
    for c in g.get("cards") or []:
        paras.append(f"<b class='g'>FIRED:</b> {esc(c['bet_label'])} "
                     f"{_price(c['price_american'])} @ {esc(c['book'])} — edge "
                     f"{c['edge']:.1%}, conf {c['confidence']}/10, {c['unit_size']}u.")
    for c in (g.get("shadow") or [])[:3]:
        paras.append(f"<b class='y'>shadow:</b> {esc(c['bet_label'])} "
                     f"{_price(c['price_american'])} — edge {c['edge']:.1%} "
                     f"(paper tier, below live gates).")
    if g.get("pass_reasons") and not (g.get("cards")):
        paras.append(f"<b>Verdict:</b> PASS — {esc(g['pass_reasons'][0])}.")
    parts.append("".join(f"<p>{p}</p>" for p in paras))
    # jump to the Live tab with this game preselected (in-app, no GitHub)
    parts.append(
        f"<p><a class='badge b-accent' "
        f"onclick='event.stopPropagation();openLive(\"{esc(g['espn_event_id'])}\")'>"
        f"📡 CHECK LIVE LINE</a> <span class='dim'>opens the Live tab with this "
        f"game selected — instant in-browser verdict</span></p>")
    return "".join(parts)


def render_predictions(grades: dict, dates: list[str], this_date: str,
                       bet_idx: dict, stats_ctx: dict | None = None) -> str:
    i = dates.index(this_date)
    prev_link = (f"<a class='navbtn' href='{dates[i + 1]}.html'>‹</a>"
                 if i + 1 < len(dates) else "<span class='navbtn'>‹</span>")
    next_link = (f"<a class='navbtn' href='{dates[i - 1]}.html'>›</a>"
                 if i > 0 else "<span class='navbtn'>›</span>")
    out = [f"<div class='datenav'>{prev_link}"
           f"<div class='datetitle'>📅 {this_date}</div>{next_link}</div>",
           "<input class='search' type='search' placeholder='🔍 Search teams…' "
           "oninput='searchGames(this.value)'>"]
    if not grades.get("odds_available"):
        out.append("<div class='card' style='border-color:#ef444460'>"
                   "<span class='r'><b>DATA OUTAGE</b></span> — odds unavailable "
                   "on the last run.</div>")
    def _best_rank(g):
        m = g.get("model") or {}
        ranks = [r for r in (m.get("rank_home"), m.get("rank_away"))
                 if isinstance(r, int)]
        return min(ranks) if ranks else 999

    # Ranked matchups (top-25 on either side) float to the top by best rank;
    # the rest follow by tip time.
    games = sorted(grades.get("games") or [],
                   key=lambda x: (0 if _best_rank(x) <= 25 else 1,
                                  _best_rank(x) if _best_rank(x) <= 25 else 0,
                                  x.get("start") or "", x.get("matchup") or ""))
    if not games:
        out.append("<div class='card empty'>No D-I games on this date.</div>")
    carded = {c["espn_event_id"]: c for c in (grades.get("cards") or [])}

    def _ranked_matchup(g) -> str:
        m = g.get("model") or {}
        parts = (g.get("matchup") or " @ ").split(" @ ")
        if len(parts) != 2:
            return esc(g.get("matchup"))
        away, home = parts
        ra, rh = m.get("rank_away"), m.get("rank_home")
        if isinstance(ra, int) and ra <= 25:
            away = f"<span class='y'>#{ra}</span> {esc(away)}"
        else:
            away = esc(away)
        if isinstance(rh, int) and rh <= 25:
            home = f"<span class='y'>#{rh}</span> {esc(home)}"
        else:
            home = esc(home)
        return f"{away} <span class='dim'>@</span> {home}"

    # non-modeled games (non-D-I exhibition opponents) collapse into a
    # footnote instead of cluttering the board with 20+ dead cards
    unmodeled = [g for g in games if not g.get("model")]
    games = [g for g in games if g.get("model")]

    ranked_header_done = others_header_done = False
    for g in games:
        if _best_rank(g) <= 25 and not ranked_header_done:
            out.append("<div class='muted secthead' style='margin:10px 0 6px'>"
                       "🏆 RANKED MATCHUPS</div>")
            ranked_header_done = True
        elif _best_rank(g) > 25 and ranked_header_done and not others_header_done:
            out.append("<div class='muted secthead' style='margin:18px 0 6px'>"
                       "— rest of the slate —</div>")
            others_header_done = True
        m = g.get("model")
        mk = g.get("market") or {}
        card = carded.get(g["espn_event_id"])
        bet = bet_idx.get((str(g["espn_event_id"]), card["market"], card["side"])) if card else None
        tip = (g.get("start") or "")[11:16]
        head = (f"<div style='display:flex;justify-content:space-between;align-items:flex-start;gap:6px'>"
                f"<div><div class='matchup'>{_ranked_matchup(g)}</div>"
                f"<div class='muted'>{tip} UTC"
                + (" · neutral site" if g.get("neutral_site") else "")
                + "</div></div><div style='text-align:right'>"
                + ("<span class='badge b-accent'>⚡ EDGE</span> " if card else "")
                + f"<span class='badge b-grey lc' id='lc-{esc(g['espn_event_id'])}'></span>"
                + "</div></div>")
        if m:
            away_nm = g.get("away_short") or (g.get("matchup") or " @ ").split(" @ ")[0]
            home_nm = g.get("home_short") or (g.get("matchup") or " @ ").split(" @ ")[-1]
            home_wins = m["margin_home"] > 0
            p_home = _win_prob_home(m["margin_home"])
            away_cls = "" if home_wins else " g"
            home_cls = " g" if home_wins else ""
            fav_nm = home_nm if home_wins else away_nm
            model_line = f"{esc(fav_nm)} {-abs(m['margin_home']):.1f}"
            # total lean vs book (blue), spread disagreement vs book (gold)
            if mk.get("total") is not None:
                lean = m["total"] - mk["total"]
                total_sub = (f"Book {mk['total']:g} · <span class='bl'>lean "
                             f"{'OVER' if lean > 0 else 'UNDER'} {abs(lean):.1f}</span>")
            else:
                total_sub = "Book: no line yet"
            if mk.get("spread_home") is not None:
                dis = -m["margin_home"] - mk["spread_home"]
                dis_html = (f"<span class='y'>Δ {dis:+.1f} vs book {mk['spread_home']:+g}</span>"
                            if abs(dis) >= 2 else f"Book {mk['spread_home']:+g}")
            else:
                dis_html = "Book: no line yet"
            grid = f"""<div class='grid4'>
<div class='stat'><div class='l'>{esc(away_nm.upper())}</div><div class='v{away_cls}'>{m['away_score']:.0f}</div><div class='s'>win {1 - p_home:.0%}</div></div>
<div class='stat'><div class='l'>{esc(home_nm.upper())}</div><div class='v{home_cls}'>{m['home_score']:.0f}</div><div class='s'>win {p_home:.0%}</div></div>
<div class='stat'><div class='l'>PROJ TOTAL</div><div class='v bl'>{m['total']:.1f}</div><div class='s'>{total_sub}</div></div>
<div class='stat'><div class='l'>MODEL LINE</div><div class='v'>{esc(model_line)}</div><div class='s'>{dis_html}</div></div>
</div>"""
        else:
            grid = ("<div class='muted' style='margin:10px 0'>No Torvik data "
                    "(non-D-I opponent) — not graded.</div>")
        play = ""
        if card:
            status = (bet or {}).get("status", "pending")
            badge = {"win": "<span class='badge b-green'>WIN</span>",
                     "loss": "<span class='badge b-red'>LOSS</span>",
                     "push": "<span class='badge b-yellow'>PUSH</span>"}.get(
                status, "<span class='badge b-grey'>PENDING</span>")
            cls = "play-b" if card["market"] == "total" else "play-g"
            clr = "bl" if card["market"] == "total" else "g"
            play = (f"<div class='play {cls}'><div class='play-head'>"
                    f"<span class='{clr}' style='font-size:12px;font-weight:800'>"
                    f"{esc(card['bet_label'])} {_price(card['price_american'])} "
                    f"({esc(card['book'])})</span>"
                    f"<span class='stars'>{_stars(card['unit_size'])}</span></div>"
                    f"<div class='muted'>Edge +{card['edge']:.1%} · fair "
                    f"{card['fair_prob']:.1%} ({_price(card['fair_american'])}) · "
                    f"conf {card['confidence']}/10 · {card['unit_size']}u {badge}</div></div>")
        elif m and (g.get("pass_reasons")):
            play = f"<div class='dim'>PASS — {esc(g['pass_reasons'][0])}</div>"
        eid = esc(g["espn_event_id"])
        search_key = esc((g.get("matchup") or "").lower())
        details = _details_html(g, (stats_ctx or {}).get(g["espn_event_id"]))
        head = head.replace("<div class='matchup'>",
                            f"<div class='matchup'><span class='chev' id='chev-{eid}'>▸</span>", 1)
        out.append(
            f"<div class='card gamecard{' edge' if card else ''}' "
            f"data-search='{search_key}' onclick='toggleGame(\"{eid}\")'>"
            f"{head}{grid}{play}"
            f"<div class='details' id='det-{eid}' style='display:none'>{details}</div></div>")
    if unmodeled:
        rows = "".join(f"<div>{esc(x.get('matchup'))}</div>" for x in unmodeled)
        out.append(f"<details class='xtra'><summary>{len(unmodeled)} exhibition / "
                   f"non-D-I games (no ratings — not graded)</summary>{rows}</details>")
    return "".join(out)


def render_best_bets(dates: list[str], bet_idx: dict) -> str:
    out = []
    shown = 0
    for d in dates[:14]:   # last two weeks of cards
        gp = DATA_ROOT / d / "grades.json"
        if not gp.exists():
            continue
        grades = json.loads(gp.read_text())
        cards = grades.get("cards") or []
        if not cards:
            continue
        out.append(f"<div class='muted' style='margin:14px 0 6px'>— {d} —</div>")
        for c in cards:
            bet = bet_idx.get((str(c["espn_event_id"]), c["market"], c["side"])) or {}
            status = bet.get("status", "pending")
            badge = {"win": "<span class='badge b-green'>✅ WIN</span>",
                     "loss": "<span class='badge b-red'>❌ LOSS</span>",
                     "push": "<span class='badge b-yellow'>➖ PUSH</span>"}.get(
                status, "<span class='badge b-grey'>🕐 PENDING</span>")
            pl = bet.get("units_pl")
            pl_txt = ""
            if pl not in (None, "") and status in ("win", "loss"):
                try:
                    pl_txt = f" <b class='{'g' if float(pl) >= 0 else 'r'}'>{float(pl):+.2f}u</b>"
                except ValueError:
                    pass
            out.append(
                f"<div class='card edge'><div class='play-head'>"
                f"<span class='matchup'>{esc(c['bet_label'])} "
                f"{_price(c['price_american'])} <span class='muted'>({esc(c['book'])})</span></span>"
                f"<span class='stars'>{_stars(c['unit_size'])}</span></div>"
                f"<div class='muted'>{esc(c['matchup'])}</div>"
                f"<div class='muted' style='margin-top:6px'>edge <b class='g'>+{c['edge']:.1%}</b> · "
                f"fair {c['fair_prob']:.1%} ({_price(c['fair_american'])}) · "
                f"conf {c['confidence']}/10 · <b>{c['unit_size']}u</b> {esc(c['risk'])} "
                f"· {badge}{pl_txt}</div>"
                f"<div class='dim' style='margin-top:4px'>{esc('; '.join(c.get('reasoning') or []))}</div></div>")
            shown += 1
    if not shown:
        out.append("<div class='card empty'>No bets fired yet. The gates are "
                   "strict by design — line-watch checks every 2h as books "
                   "post lines.</div>")
    return "".join(out)


def render_finals(day: Path, dates: list[str], this_date: str) -> str:
    fp = day / "finals.json"
    if not fp.exists():
        # point at the most recent date that has finals
        for d in dates:
            if d < this_date and (DATA_ROOT / d / "finals.json").exists():
                return (f"<div class='card empty'>No finals for {this_date} yet — "
                        f"the report generates the morning after games complete.<br><br>"
                        f"<a href='{d}.html#finals'>→ see {d} finals</a></div>")
        return ("<div class='card empty'>No finals yet — the report generates "
                "the morning after games complete.</div>")
    rep = json.loads(fp.read_text())
    s = rep["summary"]
    fired = s.get("fired") or {}
    tiles = [
        ("GAMES", str(s["n"]), ""),
        ("SPREAD MAE", f"{s['spread_mae']:.1f}", ""),
        ("TOTAL MAE", f"{s['total_mae']:.1f}", ""),
        ("SIDE %", f"{s['su_pct']:.0f}%", "g" if s["su_pct"] >= 70 else ""),
        ("CARDS", f"{fired.get('w', 0)}-{fired.get('l', 0)}-{fired.get('p', 0)} "
                  f"({fired.get('units', 0):+.2f}u)",
         "g" if fired.get("units", 0) > 0 else "r" if fired.get("units", 0) < 0 else ""),
    ]
    out = ["<div class='pnl'>" + "".join(
        f"<div class='card'><div class='l'>{t}</div><div class='v {c}'>{v}</div></div>"
        for t, v, c in tiles) + "</div>",
        "<p class='muted'>Sorted best → worst spread call. Green ≤5 pts off, "
        "gold ≤12, red >12. This table is what the weekly retune learns from.</p>",
        "<table><tr><th>game</th><th>model</th><th>actual</th>"
        "<th>spread err</th><th>total err</th><th>side</th></tr>"]
    for r in rep["games"]:
        se, te = abs(r["spread_err"]), abs(r["total_err"])
        se_c = "g" if se <= 5 else "y" if se <= 12 else "r"
        te_c = "g" if te <= 6 else "y" if te <= 14 else "r"
        star = " ⚡" if r.get("had_card") else ""
        out.append(
            f"<tr><td>{esc(r['away_short'])} @ {esc(r['home_short'])}{star}</td>"
            f"<td>{r['model_away']:.0f}–{r['model_home']:.0f}</td>"
            f"<td><b>{r['act_away']}–{r['act_home']}</b></td>"
            f"<td class='{se_c}'>{r['spread_err']:+.1f}</td>"
            f"<td class='{te_c}'>{r['total_err']:+.1f}</td>"
            f"<td>{'✓' if r['side_right'] else '✗'}</td></tr>")
    out.append("</table>")
    return "".join(out)


def render_history(bets: list[dict]) -> str:
    pnl = _pnl(bets)
    net_cls = "g" if pnl["net"] >= 0 else "r"
    tiles = [
        ("NET P&L", f"{pnl['net']:+.2f}u", net_cls),
        ("ROI", f"{pnl['roi']:+.1f}%", net_cls),
        ("WINS", str(pnl["w"]), "g"),
        ("LOSSES", str(pnl["l"]), "r"),
        ("PUSH / PEND", f"{pnl['p']} / {pnl['pending']}", "y"),
    ]
    out = ["<div class='pnl'>" + "".join(
        f"<div class='card'><div class='l'>{t}</div><div class='v {c}'>{v}</div></div>"
        for t, v, c in tiles) + "</div>"]
    if pnl["clv"] is not None:
        out.append(f"<div class='muted' style='margin-bottom:12px'>avg CLV "
                   f"{pnl['clv']:+.2f}pp vs close</div>")
    if not bets:
        out.append("<div class='card empty'>No bets logged yet.</div>")
        return "".join(out)
    for b in sorted(bets, key=lambda r: -(int(r.get("bet_id") or 0))):
        status = b.get("status", "pending")
        badge = {"win": "b-green", "loss": "b-red", "push": "b-yellow"}.get(status, "b-grey")
        line = b.get("line")
        label = (f"{esc(b.get('matchup'))} — {esc(b.get('market'))} "
                 f"{esc(b.get('side'))}" + (f" {line}" if line not in (None, "") else ""))
        pl = b.get("units_pl")
        try:
            pl_txt = f"<b class='{'g' if float(pl) >= 0 else 'r'}'>{float(pl):+.2f}u</b>" \
                if pl not in (None, "") and status != "pending" else ""
        except ValueError:
            pl_txt = ""
        out.append(
            f"<div class='betrow'><div><div style='font-weight:700;font-size:13px'>{label}</div>"
            f"<div class='muted'>{esc(b.get('date'))} · {_price(b.get('price'))} @ "
            f"{esc(b.get('book'))} · {esc(b.get('units_risked'))}u · "
            f"edge {esc(b.get('edge_pred'))} · conf {esc(b.get('confidence'))}/10</div></div>"
            f"<div style='display:flex;gap:8px;align-items:center'>{pl_txt}"
            f"<span class='badge {badge}'>{status.upper()}</span></div></div>")
    return "".join(out)


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

def _page(this_date: str, grades: dict, dates: list[str], bets: list[dict],
          bet_idx: dict, is_index: bool, stats_ctx: dict | None = None) -> str:
    pnl = _pnl(bets)
    n_cards = len(grades.get("cards") or [])
    pnl_badge = ""
    if pnl["risked"] > 0:
        cls = "b-green" if pnl["net"] >= 0 else "b-red"
        pnl_badge = (f"<span class='badge {cls}'>{pnl['net']:+.2f}u "
                     f"({pnl['roi']:+.1f}% ROI)</span>")
    refresh = "<meta http-equiv='refresh' content='300'>" if is_index else ""
    # compact per-game model data for the Live tab / chat context
    slate_js = {}
    for g in grades.get("games") or []:
        m = g.get("model")
        if not m:
            continue
        sig = g.get("sigma") or {}
        mk = g.get("market") or {}
        entry = {
            "m": m["margin_home"], "t": m["total"],
            "ss": sig.get("spread"), "st": sig.get("total"),
            "aw": g.get("away_short"), "hm": g.get("home_short"),
            "as_": m.get("away_score"), "hs_": m.get("home_score"),
        }
        if mk.get("spread_home") is not None:
            entry["bk_sp"] = mk["spread_home"]
        if mk.get("total") is not None:
            entry["bk_t"] = mk["total"]
        ra, rh = m.get("rank_away"), m.get("rank_home")
        if isinstance(ra, int) and ra <= 50:
            entry["ra"] = ra
        if isinstance(rh, int) and rh <= 50:
            entry["rh"] = rh
        if g.get("cards"):
            c = g["cards"][0]
            entry["card"] = (f"{c['bet_label']} {c['price_american']:+d} "
                             f"@{c['book']} edge {c['edge']:.1%} {c['unit_size']}u")
        if g.get("injury_notes"):
            entry["inj"] = "; ".join(g["injury_notes"])[:160]
        slate_js[str(g["espn_event_id"])] = entry
    slate_json = json.dumps(slate_js, sort_keys=True)

    # global context for the copilot: pending bets, record, recent accuracy
    ctx_bits = []
    pend = [b for b in bets if b.get("status") == "pending"]
    if pend:
        ctx_bits.append("PENDING BETS: " + "; ".join(
            f"{b.get('matchup')} {b.get('market')} {b.get('side')} "
            f"{b.get('line') or ''} {b.get('price')} ({b.get('units_risked')}u, "
            f"edge {b.get('edge_pred')})" for b in pend[:10]))
    if pnl["risked"] > 0:
        ctx_bits.append(f"SEASON RECORD: {pnl['w']}-{pnl['l']}-{pnl['p']}, "
                        f"{pnl['net']:+.2f}u ({pnl['roi']:+.1f}% ROI)")
    for d in dates:
        frep = DATA_ROOT / d / "finals.json"
        if d < this_date and frep.exists():
            s = json.loads(frep.read_text())["summary"]
            ctx_bits.append(f"MODEL ACCURACY {d}: {s['n']} games, spread MAE "
                            f"{s['spread_mae']}, total MAE {s['total_mae']}, "
                            f"sides {s['su_pct']}%")
            break
    chat_ctx_json = json.dumps(" | ".join(ctx_bits))
    return f"""<!doctype html><html><head><meta charset='utf-8'>{refresh}
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='theme-color' content='#0a0f1a'>
<link rel='manifest' href='manifest.json'>
<link rel='icon' href='icon.svg' type='image/svg+xml'>
<link rel='apple-touch-icon' href='apple-touch-icon.png'>
<meta name='apple-mobile-web-app-capable' content='yes'>
<meta name='apple-mobile-web-app-status-bar-style' content='black-translucent'>
<title>NCAAW SHARP — {esc(this_date)}</title><style>{STYLE}</style></head><body>
<div class='header'><div>
<div class='title'>🏀 NCAAW SHARP</div>
<div class='subtitle'>Tuned Torvik Model · Market-Anchored · ML + Spread + Totals ·
updated {esc((grades.get('generated_at') or '')[:16].replace('T', ' '))}Z</div></div>
<div style='display:flex;align-items:center;gap:12px;flex-wrap:wrap'>
<span class='badge b-green'>{n_cards} PLAY{'S' if n_cards != 1 else ''}</span>
{pnl_badge}
<a class='badge b-accent' target='_blank' rel='noopener'
href='https://github.com/Scottguts/ncaaw-sharp/issues/new?title=LINE%3A%20Away%20%40%20Home&body=spread_home%3D-6.5%0Atotal%3D141.5%0A%23%20optional%3A%20ml_home%3D-250%20ml_away%3D%2B200%20spread_price%3D-110%20date%3D{esc(this_date)}'>➕ LATE LINE</a>
<a class='badge b-red' target='_blank' rel='noopener'
href='https://github.com/Scottguts/ncaaw-sharp/issues/new?title=INJ%3A%20Team%20Name&body=player%3A%20Star%20Guard%0Appg%3A%2018.5%0Astatus%3A%20OUT%0A%23%20status%3A%20OUT%20%7C%20GTD%20%7C%20PROB%20%7C%20CLEAR%20(removes%20team)%0A%23%20optional%3A%20replacement%3A%200.45%2C%20starter%3A%20true'>🏥 INJURY</a></div></div>
<div class='tabs'>
<button class='tab' id='tab-predictions' onclick="showTab('predictions')">🎯 Predictions</button>
<button class='tab' id='tab-bets' onclick="showTab('bets')">⚡ Best Bets</button>
<button class='tab' id='tab-live' onclick="showTab('live');liveRefresh();renderKeyState()">📡 Live</button>
<button class='tab' id='tab-chat' onclick="showTab('chat');renderKeyState();if(!Object.keys(LIVEGAMES).length)liveRefresh()">🤖 Copilot</button>
<button class='tab' id='tab-finals' onclick="showTab('finals')">🏁 Finals</button>
<button class='tab' id='tab-history' onclick="showTab('history')">💰 Bet History</button>
</div>
<div class='wrap'>
<div class='pane' id='pane-predictions'>{render_predictions(grades, dates, this_date, bet_idx, stats_ctx)}</div>
<div class='pane' id='pane-bets' style='display:none'>{render_best_bets(dates, bet_idx)}</div>
<div class='pane' id='pane-live' style='display:none'>
<h2>🚀 Run the pipeline now</h2>
<p class='muted'>GitHub's scheduler drops runs — these buttons fire it on demand from any
device. Needs a fine-grained GitHub token (repo: ncaaw-sharp, Actions read+write),
stored only in this browser. No token? Use the
<a href='https://github.com/Scottguts/ncaaw-sharp/actions/workflows/line-watch.yml'
target='_blank' rel='noopener'>Actions page</a> → Run workflow.</p>
<div id='ghform' class='lrow'>
<input id='ghpat' type='password' placeholder='GitHub fine-grained token (github_pat_…)' style='flex:1'>
<button class='btn' onclick='saveGh()'>save</button>
</div>
<div id='ghset' style='display:none'>
<span class='badge b-green'>token set</span>
<button class='btn sec' onclick='clearGh()' style='margin-left:8px'>remove</button>
</div>
<div class='lrow'>
<button class='btn' onclick='runWorkflow("line-watch.yml","line sweep")'>🔄 SWEEP LINES NOW</button>
<button class='btn sec' onclick='runWorkflow("daily-bets.yml","daily card")'>📋 RUN DAILY CARD</button>
</div>
<div id='runstatus' class='muted'></div>
<h2 style='margin-top:24px'>📡 Live line checker</h2>
<p class='muted'>Scores and clock pull straight from ESPN in your browser; the verdict is
computed instantly with the model's rates. Advisory only — separate from pregame cards.</p>
<div class='lrow'>
<select id='livesel' style='flex:1;min-width:240px'><option>open this tab to load…</option></select>
<button class='btn sec' onclick='liveRefresh()'>↻ refresh</button>
</div>
<div class='lrow'>
<input id='livesp' type='number' step='0.5' placeholder='live spread (home, e.g. -6.5)' style='flex:1'>
<input id='livetot' type='number' step='0.5' placeholder='live total (e.g. 134.5)' style='flex:1'>
<input id='livepr' type='number' step='5' placeholder='price (-110)' style='width:110px'>
<button class='btn' onclick='liveVerdict()'>CHECK</button>
</div>
<div id='livestatus' class='dim'></div>
<div id='liveout'></div>
<p class='muted' style='margin-top:18px'>Want to talk a spot through instead?
Head to the <a onclick="showTab('chat');renderKeyState()">🤖 Copilot</a> tab.</p>
</div>
<div class='pane' id='pane-chat' style='display:none'>
<h2>🤖 Betting copilot</h2>
<p class='muted'>Your second opinion — it knows today's board, the model's numbers, live
scores, and your pending bets. <b>📷 snap your book's lines</b> and it reads every
number. Key stays on this device.</p>
<div id='keyform' class='lrow'>
<input id='aikey' type='password' placeholder='Anthropic API key (sk-ant-…)' style='flex:1'>
<button class='btn' onclick='saveKey()'>save</button>
</div>
<div id='keyset' style='display:none'>
<span class='badge b-green'>key set</span>
<button class='btn sec' onclick='clearKey()' style='margin-left:8px'>remove key</button>
</div>
<div class='lrow'>
<button class='btn sec' onclick='quick("Scan the whole board: which games have the biggest model-vs-book gaps right now, and is anything worth a bet under the gates?")'>🔍 scan board</button>
<button class='btn sec' onclick='quick("What is live right now and how do the live scores compare to what the model projected?")'>📡 what&#39;s live</button>
<button class='btn sec' onclick='quick("Review my pending bets: current state of each game, how they are tracking, and any hedge worth considering.")'>🎫 my bets</button>
<button class='btn sec' onclick='clearChat()'>🗑 clear</button>
</div>
<div class='chat'><div id='chatlog'></div></div>
<div id='imgpreview'></div>
<div class='chatbar'>
<input id='imgfile' type='file' accept='image/*' multiple style='display:none'
onchange='[...this.files].forEach(addImageFile);this.value=""'>
<button class='btn sec' title='attach screenshot' style='font-size:18px;min-width:52px'
onclick='document.getElementById("imgfile").click()'>📷</button>
<textarea id='aimsg' placeholder='ask about a spot — or 📷 snap your book&#39;s live lines'
onkeydown='if(event.key==="Enter"&&!event.shiftKey){{event.preventDefault();sendChat();}}'
onpaste='[...(event.clipboardData||{{}}).items||[]].forEach(it=>{{if(it.type&&it.type.startsWith("image/"))addImageFile(it.getAsFile());}})'></textarea>
<button class='btn' onclick='sendChat()'>send</button>
</div>
</div>
<div class='pane' id='pane-finals' style='display:none'>{render_finals(DATA_ROOT / this_date, dates, this_date)}</div>
<div class='pane' id='pane-history' style='display:none'>{render_history(bets)}</div>
</div>
<div class='wrap dim' style='padding-top:0'>Gates: 1.8–2.5% min edge · 6% max (stale-line reject)
· conf ≥6/10 · 5 cards / 6u per day · early-season sigma widening. NO PLAYS is a correct day.</div>
<script>window.SLATE={slate_json};window.BETCTX={chat_ctx_json};{TABS_JS}</script></body></html>"""


def build() -> int:
    DOCS.mkdir(exist_ok=True)
    days = sorted((p for p in DATA_ROOT.iterdir()
                   if p.is_dir() and (p / "grades.json").exists()),
                  key=lambda p: p.name, reverse=True)
    dates = [p.name for p in days]
    bets = _read_bets()
    bet_idx = {(str(b["espn_event_id"]), b["market"], b["side"]): b for b in bets}

    for i, day in enumerate(days):
        grades = json.loads((day / "grades.json").read_text())
        page = _page(day.name, grades, dates, bets, bet_idx, is_index=(i == 0),
                     stats_ctx=_team_stats_ctx(day))
        (DOCS / f"{day.name}.html").write_text(page)
        (DOCS / f"{day.name}.json").write_text(
            json.dumps(grades, indent=2, sort_keys=True, default=str))
        if i == 0:
            (DOCS / "index.html").write_text(page)
    # legacy redirects
    for name in ("record.html", "archive.html"):
        (DOCS / name).write_text(
            "<!doctype html><meta http-equiv='refresh' content='0;url=index.html#history'>")
    print(f"[site] built {len(days)} slate page(s) + app dashboard")
    return 0


if __name__ == "__main__":
    sys.exit(build())
