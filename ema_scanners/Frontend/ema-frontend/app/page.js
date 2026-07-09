"use client";

import { useState, useEffect, useCallback, useRef } from "react";

// ─── Config ───────────────────────────────────────────────────────────────────
const API_BASE = process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000/api";

const MARKETS = ["futures", "spot"];

// ─── Formatting ───────────────────────────────────────────────────────────────
function fmtPrice(p, sym) {
  if (p == null) return "—";
  if (p >= 1000)  return p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (p >= 1)     return p.toFixed(4);
  return p.toFixed(6);
}
function fmtVol(v) {
  if (v == null) return "—";
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(2)}K`;
  return `$${v.toFixed(2)}`;
}
function fmtDate(ms) {
  const d = new Date(ms);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}
function fmtDateTime(ms) {
  const d = new Date(ms);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) +
    ", " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }) + " IST";
}
function fmtTime(dt) {
  if (!dt) return "—";
  const d = new Date(dt);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) +
    ", " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }) + " IST";
}
function symCategory(sym) {
  const s = sym.replace("USDT", "");
  if (s === "BTC") return "Bitcoin";
  const stables = ["USDC","BUSD","DAI","FDUSD","TUSD","USDE","USDP"];
  if (stables.includes(s)) return "Stables";
  const memes = ["DOGE","SHIB","PEPE","FLOKI","BONK","WIF","MEME","BOME","NEIRO","MOODENG"];
  if (memes.includes(s)) return "Memes";
  return "Alts";
}
function fmtSym(sym) {
  return { base: sym.replace("USDT",""), quote: "/USDT" };
}

// ─── Shared components ───────────────────────────────────────────────────────
const TH = ({ children, right, onClick, sorted, dir }) => (
  <th onClick={onClick} style={{
    padding: "10px 14px", fontSize: 11, fontWeight: 700, letterSpacing: "0.07em",
    textTransform: "uppercase", color: "#9ca3af", textAlign: right ? "right" : "left",
    whiteSpace: "nowrap", borderBottom: "1px solid #e8eaed", background: "#f8f9fb",
    cursor: onClick ? "pointer" : "default", userSelect: "none",
  }}>
    {children}{sorted ? <span style={{ marginLeft: 3, opacity: 0.6 }}>{dir === "asc" ? "↑" : "↓"}</span> : null}
  </th>
);

const Trend = ({ t }) => {
  const bull = t === "Bullish", neu = t === "Neutral";
  return <span style={{ display:"flex", alignItems:"center", gap:4 }}>
    {!neu && <span style={{ fontSize:10, color: bull?"#22c55e":"#ef4444" }}>{bull?"▲":"▼"}</span>}
    <span style={{ fontWeight:600, fontSize:13, color: neu?"#9ca3af":bull?"#16a34a":"#dc2626" }}>{t}</span>
  </span>;
};

const SigBadge = ({ s }) => {
  if (!s) return <span style={{ color:"#d1d5db" }}>—</span>;
  return <span style={{
    display:"inline-block", padding:"3px 10px", borderRadius:5, fontSize:11,
    fontWeight:700, letterSpacing:"0.04em",
    background: s==="BUY"?"#16a34a":"#dc2626", color:"#fff"
  }}>{s}</span>;
};

const ChgCell = ({ v }) => (
  <span style={{ color: v>=0?"#16a34a":"#dc2626", fontWeight:500 }}>
    {v>=0?"+":""}{v.toFixed(2)}%
  </span>
);

const MarketToggle = ({ market, setMarket }) => (
  <div style={{ display:"flex", gap:4 }}>
    {MARKETS.map(m => (
      <button key={m} onClick={()=>setMarket(m)} style={{
        padding:"4px 12px", borderRadius:6, fontSize:12, fontWeight:600, cursor:"pointer",
        border:market===m?"1.5px solid #16a34a":"1px solid #e5e7eb", background:"transparent",
        color:market===m?"#16a34a":"#6b7280", textTransform:"capitalize",
      }}>{m}</button>
    ))}
  </div>
);

// ─── Backtest using DB signals (exact same signals as scanner page) ───────────
// Instead of recalculating EMA crossovers on the frontend (which can differ
// from the backend due to logic version mismatches), we fetch the signals
// directly from the DB and use them as-is. Only the SL/TP simulation runs
// on the frontend using the candle OHLC data.
function runBacktestFromSignals(candles, dbSignals, rrMode, windowDays, candleMs = 3_600_000, timeframe = '1h') {
  if (!candles || candles.length === 0 || !dbSignals) return [];

  const [riskPct, rewardPct] = rrMode === "1:2" ? [1, 2] : [2, 4];

  // Window cutoff — relative to last candle
  const windowMs     = windowDays * 24 * 3_600_000;
  const lastCandleMs = candles[candles.length - 1].openTimeMs;
  const cutoffMs     = lastCandleMs - windowMs;

  // Filter signals to window
  const windowSignals = dbSignals.filter(s => s.crossTimeMs >= cutoffMs);

  if (windowSignals.length === 0) return [];

  const symbol   = candles[0]?.symbol || "";
  const symLabel = symbol.replace("USDT", "") + "/USDT";

  function interpolateHitTime(candleOpenMs, open, high, low, level, hitType) {
    let t;
    if (hitType === "high") t = high !== open ? (level - open) / (high - open) : 0.5;
    else                    t = open !== low  ? (open - level) / (open - low)  : 0.5;
    t = Math.max(0, Math.min(1, t));
    return candleOpenMs + t * candleMs;
  }

  function simulateTradeFromIdx(type, entryPrice, startCandleIdx, rPct, rwPct) {
    let stopLoss, targetPrice;
    if (type === "BUY") {
      stopLoss    = entryPrice * (1 - rPct  / 100);
      targetPrice = entryPrice * (1 + rwPct / 100);
    } else {
      stopLoss    = entryPrice * (1 + rPct  / 100);
      targetPrice = entryPrice * (1 - rwPct / 100);
    }
    let exitPrice = null, exitTimeMs = null, exitReason = null;
    for (let j = startCandleIdx; j < candles.length; j++) {
      const c = candles[j];
      if (type === "BUY") {
        if (c.low  <= stopLoss)    { exitPrice = stopLoss;    exitTimeMs = interpolateHitTime(c.openTimeMs, c.open, c.high, c.low, stopLoss,    "low");  exitReason = "Stop Loss Hit"; break; }
        if (c.high >= targetPrice) { exitPrice = targetPrice; exitTimeMs = interpolateHitTime(c.openTimeMs, c.open, c.high, c.low, targetPrice, "high"); exitReason = "Target Hit";   break; }
      } else {
        if (c.high >= stopLoss)    { exitPrice = stopLoss;    exitTimeMs = interpolateHitTime(c.openTimeMs, c.open, c.high, c.low, stopLoss,    "high"); exitReason = "Stop Loss Hit"; break; }
        if (c.low  <= targetPrice) { exitPrice = targetPrice; exitTimeMs = interpolateHitTime(c.openTimeMs, c.open, c.high, c.low, targetPrice, "low");  exitReason = "Target Hit";   break; }
      }
    }
    return { stopLoss, targetPrice, exitPrice, exitTimeMs, exitReason };
  }

  const trades    = [];
  let openTradeRef = null;

  for (const sig of windowSignals) {
    const { type, crossPrice, crossTimeMs } = sig;

    // Find the signal candle by matching openTimeMs to cross_time (rounded to candle open)
    // cross_time from DB is the EMA cross time — find the candle that contains it
    let sigCandleIdx = -1;
    for (let i = 0; i < candles.length; i++) {
      if (candles[i].openTimeMs <= crossTimeMs && crossTimeMs < candles[i].openTimeMs + candleMs) {
        sigCandleIdx = i;
        break;
      }
    }
    // Fallback: use closest candle before crossTimeMs
    if (sigCandleIdx === -1) {
      for (let i = candles.length - 1; i >= 0; i--) {
        if (candles[i].openTimeMs <= crossTimeMs) { sigCandleIdx = i; break; }
      }
    }
    if (sigCandleIdx === -1) continue;  // signal candle not in fetched data

    // Entry = signal candle close price, entry time = candle close time.
    // If that candle hasn't actually closed yet, its "close" is just a live,
    // still-changing price snapshot — not a real entry — so skip this signal
    // entirely until the candle finishes (it'll be picked up on a later refresh).
    const entryTimeMs = candles[sigCandleIdx].openTimeMs + candleMs;
    if (entryTimeMs > Date.now()) continue;

    const entryPrice  = candles[sigCandleIdx].close;
    const signalTime  = fmtDateTime(crossTimeMs);
    const entryTime   = fmtDateTime(entryTimeMs);

    // If previous trade is still open → force close at this entry price
    if (openTradeRef !== null) {
      const prev       = openTradeRef;
      const forceExit  = entryPrice;
      const forceTime  = entryTimeMs;
      const gainPct    = prev.tradeSignal === "BUY"
        ? ((forceExit - prev.entryPrice) / prev.entryPrice) * 100
        : ((prev.entryPrice - forceExit) / prev.entryPrice) * 100;
      const gainAmount = prev.tradeSignal === "BUY"
        ? forceExit - prev.entryPrice : prev.entryPrice - forceExit;
      const durationMs = forceTime - prev._entryTimeMs;
      const dh = Math.floor(durationMs / 3_600_000);
      const dm = Math.floor((durationMs % 3_600_000) / 60000);
      prev.entryClose     = forceExit;
      prev.entryCloseTime = fmtDateTime(forceTime);
      prev.exitReason     = "Closed by new signal";
      prev.duration       = dh >= 24 ? `${Math.floor(dh/24)}d ${dh%24}h` : `${dh}h ${dm}m`;
      prev.result         = gainPct >= 0 ? "WIN" : "LOSS";
      prev.gainPct        = gainPct;
      prev.gainAmount     = gainAmount;
      prev._exitTimeMs    = forceTime;
      openTradeRef        = null;
    }

    // Simulate this trade — walk from candle AFTER signal candle
    const sim = simulateTradeFromIdx(type, entryPrice, sigCandleIdx + 1, riskPct, rewardPct);
    const { stopLoss, targetPrice, exitPrice, exitTimeMs, exitReason } = sim;

    if (!exitPrice) {
      const row = {
        date: fmtDate(entryTimeMs), timeFrame: timeframe.toUpperCase(), symbol: symLabel,
        tradeSignal: type, signalTime, entryTime, entryPrice, stopLoss, targetPrice,
        entryClose: null, entryCloseTime: null, exitReason: "Open", duration: "—",
        result: "OPEN", gainPct: null, gainAmount: null,
        _entryTimeMs: entryTimeMs, _exitTimeMs: null,
      };
      trades.push(row);
      openTradeRef = row;
      continue;
    }

    const gainPct    = type === "BUY" ? ((exitPrice - entryPrice) / entryPrice) * 100 : ((entryPrice - exitPrice) / entryPrice) * 100;
    const gainAmount = type === "BUY" ? exitPrice - entryPrice : entryPrice - exitPrice;
    const result     = exitReason === "Target Hit" ? "WIN" : "LOSS";
    const durationMs = exitTimeMs - entryTimeMs;
    const dh         = Math.floor(durationMs / 3_600_000);
    const dm         = Math.floor((durationMs % 3_600_000) / 60000);
    const duration   = dh >= 24 ? `${Math.floor(dh/24)}d ${dh%24}h` : `${dh}h ${dm}m`;

    trades.push({
      date: fmtDate(entryTimeMs), timeFrame: timeframe.toUpperCase(), symbol: symLabel,
      tradeSignal: type, signalTime, entryTime, entryPrice, stopLoss, targetPrice,
      entryClose: exitPrice, entryCloseTime: fmtDateTime(exitTimeMs), exitReason,
      duration, result, gainPct, gainAmount,
      _entryTimeMs: entryTimeMs, _exitTimeMs: exitTimeMs,
    });
    openTradeRef = null;
  }

  return trades;
}


const CATS  = ["All","Bitcoin","Alts","Memes","Stables"];
const SIGS  = ["All","BUY","SELL"];
const SCOLS = [
  {k:"rank",     l:"#",            s:true},
  {k:"symbol",   l:"Symbol",       s:true},
  {k:"ema_trend",l:"EMA Trend",    s:true},
  {k:"price",    l:"Price",        s:true,  r:true},
  {k:"change_24h",l:"24H Change",  s:true,  r:true},
  {k:"volume_24h",l:"Volume (24H)",s:true,  r:true},
  {k:"last_signal",l:"Last Signal",s:true},
  {k:"cross_price",l:"Cross Price",s:false, r:true},
  {k:"signal_time",l:"Signal Time",s:true},
  {k:"details",  l:"Details",      s:false},
];

function ScannerPage({ market, setMarket, onDetails, onBacktest }) {
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [cat, setCat] = useState("All");
  const [sig, setSig] = useState("All");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState({ k:"signal_time", dir:"desc" });
  const [modal, setModal] = useState(null);
  const [updated, setUpdated] = useState(null);
  const intRef = useRef(null);

  const fetchData = useCallback(async () => {
    try {
      const p = new URLSearchParams({ limit:500, market });
      if (sig !== "All") p.set("signal", sig);
      const [sr, str] = await Promise.all([
        fetch(`${API_BASE}/scanner?${p}`),
        fetch(`${API_BASE}/status?market=${market}`),
      ]);
      if (!sr.ok) throw new Error(`API ${sr.status}`);
      const sd = await sr.json();
      const std = str.ok ? await str.json() : null;
      setRows(sd.data || []);
      setStatus(std);
      setUpdated(new Date());
      setError(null);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }, [sig, market]);

  useEffect(() => {
    setLoading(true);
    fetchData();
    intRef.current = setInterval(fetchData, 30000);
    return () => clearInterval(intRef.current);
  }, [fetchData]);

  const filtered = rows
    .filter(r => {
      if (cat !== "All" && symCategory(r.symbol) !== cat) return false;
      if (search && !r.symbol.toLowerCase().includes(search.toLowerCase())) return false;
      return true;
    })
    .sort((a, b) => {
      const dir = sort.dir === "asc" ? 1 : -1;
      const av = a[sort.k], bv = b[sort.k];
      if (av == null && bv == null) return 0;
      if (av == null) return 1; if (bv == null) return -1;
      return typeof av === "string" ? dir*av.localeCompare(bv) : dir*(av-bv);
    });

  const toggleSort = k => setSort(s => s.k===k ? {k, dir:s.dir==="asc"?"desc":"asc"} : {k, dir:"desc"});

  return (
    <div style={{ fontFamily:"'Inter',system-ui,sans-serif", background:"#fff", minHeight:"100vh", width:"100%", color:"#111827" }}>
      {/* Header */}
      <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", padding:"14px 24px", borderBottom:"1px solid #e8eaed" }}>
        <div style={{ display:"flex", alignItems:"center", gap:10 }}>
          <div style={{ width:36, height:36, borderRadius:8, background:"#f59e0b", display:"flex", alignItems:"center", justifyContent:"center", fontSize:18, color:"#fff", fontWeight:700 }}>⚡</div>
          <div>
            <div style={{ fontSize:17, fontWeight:700, letterSpacing:"-0.01em" }}>EMA Scanner</div>
            <div style={{ fontSize:11, color:"#9ca3af" }}>Binance {market === "spot" ? "Spot" : "USDT Futures"} · EMA 7/25/99</div>
          </div>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:16 }}>
          <MarketToggle market={market} setMarket={setMarket} />
          {updated && <span style={{ fontSize:11, color:"#9ca3af" }}>Updated {updated.toLocaleTimeString("en-US",{hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false})}</span>}
          <button onClick={fetchData} style={{ padding:"6px 14px", borderRadius:8, border:"1px solid #e5e7eb", background:"transparent", fontSize:12, color:"#374151", cursor:"pointer", fontWeight:500 }}>↻ Refresh</button>
        </div>
      </div>

      {/* Status bar */}
      {status && (
        <div style={{ display:"flex", gap:24, padding:"9px 24px", background:"#f8f9fb", borderBottom:"1px solid #e8eaed", fontSize:12, color:"#6b7280", alignItems:"center" }}>
          <span style={{ display:"flex", alignItems:"center", gap:6 }}>
            <span style={{ width:7, height:7, borderRadius:"50%", background:status.status==="ready"?"#22c55e":"#f59e0b", display:"inline-block" }}/>
            {status.status === "ready" ? "Live" : "Initializing"}
          </span>
          <span>Tracking <strong>{status.symbols_tracked}</strong> symbols</span>
          <span>Signals today: <strong>{status.signals_today}</strong></span>
          <span>Uptime: <strong>{Math.floor(status.uptime_seconds/60)}m</strong></span>
        </div>
      )}

      {/* Filters */}
      <div style={{ padding:"12px 24px", borderBottom:"1px solid #e8eaed" }}>
        <div style={{ display:"flex", alignItems:"center", gap:12, flexWrap:"wrap" }}>
          <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Category</span>
          <div style={{ display:"flex", gap:4 }}>
            {CATS.map(c => (
              <button key={c} onClick={()=>setCat(c)} style={{
                padding:"4px 12px", borderRadius:6, fontSize:12, fontWeight:500, cursor:"pointer",
                border:cat===c?"1.5px solid #f59e0b":"1px solid #e5e7eb", background:"transparent",
                color:cat===c?"#f59e0b":"#6b7280",
              }}>{c}</button>
            ))}
          </div>
          <div style={{ width:1, height:20, background:"#e5e7eb" }}/>
          <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Signal</span>
          <div style={{ display:"flex", gap:4 }}>
            {SIGS.map(s => (
              <button key={s} onClick={()=>setSig(s)} style={{
                padding:"4px 12px", borderRadius:6, fontSize:12, fontWeight:500, cursor:"pointer",
                border:sig===s?"1.5px solid #f59e0b":"1px solid #e5e7eb", background:"transparent",
                color:sig===s?"#f59e0b":"#6b7280",
              }}>{s}</button>
            ))}
          </div>
          <div style={{ marginLeft:"auto" }}>
            <input placeholder="Search…" value={search} onChange={e=>setSearch(e.target.value)} style={{
              padding:"6px 12px", borderRadius:8, border:"1px solid #e5e7eb", fontSize:13,
              color:"#374151", outline:"none", width:180, background:"#fafafa",
            }}/>
          </div>
        </div>
        <div style={{ marginTop:8, fontSize:12, color:"#9ca3af" }}>
          Showing <strong style={{ color:"#374151" }}>{filtered.length}</strong> coins
        </div>
      </div>

      {/* Table */}
      <div style={{ overflowX:"auto" }}>
        {error ? (
          <div style={{ padding:48, textAlign:"center", color:"#dc2626", fontSize:14 }}>
            <div style={{ fontSize:22, marginBottom:8 }}>⚠</div>
            Could not reach backend: <code style={{ fontSize:12 }}>{error}</code>
            <div style={{ marginTop:6, color:"#9ca3af", fontSize:12 }}>Make sure FastAPI is running at <code>{API_BASE}</code></div>
          </div>
        ) : loading ? (
          <div style={{ padding:60, textAlign:"center", color:"#9ca3af", fontSize:13 }}>Loading scanner data…</div>
        ) : (
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
            <thead>
              <tr>
                {SCOLS.map(col => (
                  <TH key={col.k} right={col.r} onClick={col.s?()=>toggleSort(col.k):null}
                    sorted={sort.k===col.k} dir={sort.dir}>{col.l}</TH>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.length === 0 ? (
                <tr><td colSpan={10} style={{ padding:48, textAlign:"center", color:"#9ca3af", fontSize:13 }}>No coins match your filters.</td></tr>
              ) : filtered.map((row, i) => {
                const { base, quote } = fmtSym(row.symbol);
                return (
                  <tr key={row.symbol}
                    style={{ borderBottom:"1px solid #f3f4f6", background:i%2===0?"#fff":"#fafafa", transition:"background 0.1s" }}
                    onMouseEnter={e=>e.currentTarget.style.background="#f0f9ff"}
                    onMouseLeave={e=>e.currentTarget.style.background=i%2===0?"#fff":"#fafafa"}
                  >
                    <td style={{ padding:"11px 14px", color:"#9ca3af", fontWeight:500 }}>{i + 1}</td>
                    <td style={{ padding:"11px 14px", fontWeight:700, cursor:"pointer" }} onClick={()=>setModal(row)}>
                      <span style={{ color:"#f59e0b" }}>{base}</span>
                      <span style={{ color:"#9ca3af", fontSize:11 }}>{quote}</span>
                    </td>
                    <td style={{ padding:"11px 14px" }}><Trend t={row.ema_trend}/></td>
                    <td style={{ padding:"11px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", fontWeight:500 }}>{fmtPrice(row.price)}</td>
                    <td style={{ padding:"11px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums" }}><ChgCell v={row.change_24h}/></td>
                    <td style={{ padding:"11px 14px", textAlign:"right", color:"#374151", fontVariantNumeric:"tabular-nums" }}>{fmtVol(row.volume_24h)}</td>
                    <td style={{ padding:"11px 14px" }}><SigBadge s={row.last_signal}/></td>
                    <td style={{ padding:"11px 14px", textAlign:"right", color:"#6b7280", fontVariantNumeric:"tabular-nums" }}>{row.cross_price ? fmtPrice(row.cross_price) : "—"}</td>
                    <td style={{ padding:"11px 14px", color:"#6b7280", whiteSpace:"nowrap", fontSize:12 }}>{row.signal_time ? fmtTime(row.signal_time) : "—"}</td>
                    <td style={{ padding:"11px 14px" }}>
                      <button
                        onClick={() => onDetails(row)}
                        style={{
                          padding:"4px 12px", borderRadius:6, border:"1px solid #6366f1",
                          background:"transparent", color:"#6366f1", fontSize:11, fontWeight:600,
                          cursor:"pointer", whiteSpace:"nowrap", transition:"all 0.15s",
                        }}
                        onMouseEnter={e=>{e.target.style.background="#6366f1";e.target.style.color="#fff";}}
                        onMouseLeave={e=>{e.target.style.background="transparent";e.target.style.color="#6366f1";}}
                      >
                        Details
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Detail modal */}
      {modal && (
        <div onClick={()=>setModal(null)} style={{ position:"fixed", inset:0, background:"rgba(0,0,0,0.35)", display:"flex", alignItems:"center", justifyContent:"center", zIndex:1000 }}>
          <div onClick={e=>e.stopPropagation()} style={{ background:"#fff", borderRadius:14, padding:28, width:440, border:"1px solid #e5e7eb" }}>
            <div style={{ display:"flex", justifyContent:"space-between", alignItems:"flex-start", marginBottom:20 }}>
              <div>
                <div style={{ fontSize:22, fontWeight:700 }}>
                  <span style={{ color:"#f59e0b" }}>{fmtSym(modal.symbol).base}</span>
                  <span style={{ color:"#9ca3af", fontSize:14 }}>/USDT</span>
                </div>
                <Trend t={modal.ema_trend}/>
              </div>
              <button onClick={()=>setModal(null)} style={{ background:"none", border:"none", cursor:"pointer", fontSize:20, color:"#9ca3af" }}>×</button>
            </div>
            <div style={{ display:"grid", gridTemplateColumns:"1fr 1fr", gap:12, marginBottom:16 }}>
              {[
                {l:"Price", v:`$${fmtPrice(modal.price)}`},
                {l:"24H Change", v:<ChgCell v={modal.change_24h}/>},
                {l:"Volume (24H)", v:fmtVol(modal.volume_24h)},
                {l:"Last Signal", v:<SigBadge s={modal.last_signal}/>},
                {l:"Cross Price", v:modal.cross_price?`$${fmtPrice(modal.cross_price)}`:"—"},
                {l:"Signal Time", v:modal.signal_time?fmtTime(modal.signal_time):"—"},
              ].map(item => (
                <div key={item.l} style={{ background:"#f9fafb", borderRadius:8, padding:"10px 14px" }}>
                  <div style={{ fontSize:11, color:"#9ca3af", fontWeight:600, textTransform:"uppercase", letterSpacing:"0.06em", marginBottom:4 }}>{item.l}</div>
                  <div style={{ fontSize:14, fontWeight:500, color:"#111827" }}>{item.v}</div>
                </div>
              ))}
            </div>
            {(modal.ema_7||modal.ema_25||modal.ema_99) && (
              <div style={{ background:"#f9fafb", borderRadius:8, padding:"10px 14px" }}>
                <div style={{ fontSize:11, color:"#9ca3af", fontWeight:600, textTransform:"uppercase", letterSpacing:"0.06em", marginBottom:8 }}>EMA Values</div>
                <div style={{ display:"flex", gap:16 }}>
                  {[["EMA 7",modal.ema_7],["EMA 25",modal.ema_25],["EMA 99",modal.ema_99]].map(([l,v])=>(
                    <div key={l}><div style={{ fontSize:11, color:"#9ca3af" }}>{l}</div><div style={{ fontSize:13, fontWeight:600, color:"#374151" }}>{v?fmtPrice(v):"—"}</div></div>
                  ))}
                </div>
              </div>
            )}
            <button onClick={()=>{setModal(null);onBacktest(modal);}} style={{
              marginTop:16, width:"100%", padding:"9px 0", borderRadius:8,
              border:"1px solid #6366f1", background:"transparent", color:"#6366f1",
              fontSize:13, fontWeight:600, cursor:"pointer",
            }}>Run Backtest for {fmtSym(modal.symbol).base}/USDT</button>
          </div>
        </div>
      )}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
        input:focus{border-color:#f59e0b!important;box-shadow:0 0 0 3px rgba(245,158,11,.12);outline:none;}
      `}</style>
    </div>
  );
}

// ─── Details Page (coin details + full crossover history) ────────────────────
const ALL_TIMEFRAMES = ["1h", "2h", "4h", "6h"];
const CROSS_COLS = [
  {k:"signal_type", l:"Type"},
  {k:"interval",    l:"Timeframe"},
  {k:"cross_time",  l:"Signal Time"},
  {k:"cross_price", l:"Cross Price", r:true},
  {k:"result",      l:"Result"},
];

const ResultBadge = ({ result }) => {
  if (result === "WIN")  return <span style={{ display:"inline-block", padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:700, background:"#dcfce7", color:"#15803d" }}>WIN</span>;
  if (result === "LOSS") return <span style={{ display:"inline-block", padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:700, background:"#fee2e2", color:"#b91c1c" }}>LOSS</span>;
  if (result === "OPEN") return <span style={{ display:"inline-block", padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:700, background:"#e0f2fe", color:"#0369a1" }}>OPEN</span>;
  return <span style={{ color:"#d1d5db" }}>—</span>;
};

const CROSS_HISTORY_DAYS = 30;

function DetailsPage({ row, market, onBack, onBacktest }) {
  const [signals, setSignals] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  const symbol = row?.symbol || "";
  const { base } = fmtSym(symbol);

  const fetchSignals = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const perInterval = await Promise.all(ALL_TIMEFRAMES.map(async tf => {
        const res = await fetch(`${API_BASE}/signals?symbol=${symbol}&interval=${tf}&market=${market}&days=${CROSS_HISTORY_DAYS}&limit=500`);
        if (!res.ok) throw new Error(`API ${res.status}`);
        const data = await res.json();

        // The backend occasionally stores the same crossover twice (near-identical
        // cross_time a few hundred ms apart, from separate scan runs) — collapse
        // those down to one row per type/minute before displaying.
        const seen = new Map();
        for (const s of data) {
          const crossTimeMs = new Date(s.cross_time).getTime();
          const dedupeKey = `${s.signal_type}|${Math.round(crossTimeMs / 60000)}`;
          if (!seen.has(dedupeKey)) seen.set(dedupeKey, { ...s, interval: tf, crossTimeMs });
        }
        const dedupedSignals = [...seen.values()];
        if (dedupedSignals.length === 0) return dedupedSignals;

        // Simulate each signal's SL/TP outcome (RR 1:2, same default as the
        // Backtest page) against candle data so we can show won/lost/open.
        const limit = LIMIT_MAP[tf] || 1500;
        const candleRes = await fetch(`${API_BASE}/candles/${symbol}?interval=${tf}&market=${market}&limit=${limit}`);
        if (!candleRes.ok) return dedupedSignals.map(s => ({ ...s, result: null }));
        const rawCandles = await candleRes.json();
        const candles = rawCandles.map(r => ({
          symbol, openTimeMs: r[0],
          open: parseFloat(r[1]), high: parseFloat(r[2]), low: parseFloat(r[3]), close: parseFloat(r[4]),
        }));
        const simSignals = [...dedupedSignals]
          .sort((a, b) => a.crossTimeMs - b.crossTimeMs)
          .map(s => ({ type: s.signal_type, crossPrice: s.cross_price, crossTimeMs: s.crossTimeMs }));
        // windowDays is set far larger than any signal age so nothing gets filtered out here —
        // the only real limit is how far back the fetched candles reach.
        const trades = runBacktestFromSignals(candles, simSignals, "1:2", 3650, CANDLE_MS_MAP[tf], tf);
        const resultByKey = new Map(trades.map(t => [`${t.tradeSignal}|${t.signalTime}`, t.result]));

        return dedupedSignals.map(s => ({
          ...s,
          result: resultByKey.get(`${s.signal_type}|${fmtDateTime(s.crossTimeMs)}`) ?? null,
        }));
      }));

      const merged = perInterval.flat().sort((a, b) => b.crossTimeMs - a.crossTimeMs);
      setSignals(merged);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [symbol, market]);

  useEffect(() => { fetchSignals(); }, [fetchSignals]);

  return (
    <div style={{ fontFamily:"'Inter',system-ui,sans-serif", background:"#fff", minHeight:"100vh", width:"100%", color:"#111827" }}>
      {/* Header */}
      <div style={{ display:"flex", alignItems:"center", gap:10, padding:"13px 24px", borderBottom:"1px solid #e8eaed", flexWrap:"wrap" }}>
        <button onClick={onBack} style={{
          display:"flex", alignItems:"center", gap:5, padding:"5px 12px",
          borderRadius:7, border:"1px solid #e5e7eb", background:"transparent",
          fontSize:12, fontWeight:600, color:"#374151", cursor:"pointer",
        }}>← Back</button>
        <span style={{ color:"#d1d5db", fontSize:14 }}>›</span>
        <span style={{ fontWeight:700, color:"#f59e0b", fontSize:15 }}>{base}</span>
        <span style={{ color:"#9ca3af", fontSize:14, fontWeight:400 }}>/USDT</span>
        <Trend t={row?.ema_trend}/>
      </div>

      {/* Detail cards */}
      <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(140px,1fr))", gap:12, padding:"16px 24px", borderBottom:"1px solid #e8eaed" }}>
        {[
          {l:"Price",        v:`$${fmtPrice(row?.price)}`},
          {l:"24H Change",   v:<ChgCell v={row?.change_24h ?? 0}/>},
          {l:"Volume (24H)", v:fmtVol(row?.volume_24h)},
          {l:"Last Signal",  v:<SigBadge s={row?.last_signal}/>},
          {l:"Cross Price",  v: row?.cross_price ? `$${fmtPrice(row.cross_price)}` : "—"},
          {l:"Signal Time",  v: row?.signal_time ? fmtTime(row.signal_time) : "—"},
        ].map(item => (
          <div key={item.l} style={{ background:"#f9fafb", borderRadius:8, padding:"10px 14px" }}>
            <div style={{ fontSize:11, color:"#9ca3af", fontWeight:600, textTransform:"uppercase", letterSpacing:"0.06em", marginBottom:4 }}>{item.l}</div>
            <div style={{ fontSize:14, fontWeight:500, color:"#111827" }}>{item.v}</div>
          </div>
        ))}
      </div>

      {/* Crossover history */}
      <div style={{ padding:"14px 24px", display:"flex", alignItems:"baseline", gap:8, flexWrap:"wrap" }}>
        <span style={{ fontSize:13, fontWeight:700 }}>All Crossovers</span>
        <span style={{ fontSize:12, color:"#9ca3af" }}>Showing last {CROSS_HISTORY_DAYS} days</span>
        <button onClick={() => onBacktest(row)} style={{
          marginLeft:"auto", padding:"6px 16px", borderRadius:8, border:"1px solid #6366f1",
          background:"transparent", color:"#6366f1", fontSize:12, fontWeight:700, cursor:"pointer",
        }}>Backtest</button>
      </div>

      <div style={{ overflowX:"auto" }}>
        {error ? (
          <div style={{ padding:48, textAlign:"center", color:"#dc2626", fontSize:14 }}>
            <div style={{ fontSize:22, marginBottom:8 }}>⚠</div>
            Could not load crossovers: <code style={{ fontSize:12 }}>{error}</code>
          </div>
        ) : loading ? (
          <div style={{ padding:60, textAlign:"center", color:"#9ca3af", fontSize:13 }}>Loading crossover history…</div>
        ) : !signals || signals.length === 0 ? (
          <div style={{ padding:60, textAlign:"center", color:"#9ca3af", fontSize:14 }}>
            No crossovers found for {base}/USDT.
          </div>
        ) : (
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:13 }}>
            <thead>
              <tr>
                {CROSS_COLS.map(col => (
                  <TH key={col.k} right={col.r}>{col.l}</TH>
                ))}
              </tr>
            </thead>
            <tbody>
              {signals.map((s, i) => (
                <tr key={i} style={{ borderBottom:"1px solid #f3f4f6", background:i%2===0?"#fff":"#fafafa" }}>
                  <td style={{ padding:"10px 14px" }}><SigBadge s={s.signal_type}/></td>
                  <td style={{ padding:"10px 14px", color:"#6b7280", fontWeight:600 }}>{s.interval.toUpperCase()}</td>
                  <td style={{ padding:"10px 14px", color:"#374151", whiteSpace:"nowrap" }}>{fmtTime(s.cross_time)}</td>
                  <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums" }}>{fmtPrice(s.cross_price)}</td>
                  <td style={{ padding:"10px 14px" }}><ResultBadge result={s.result}/></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ─── Backtest Page ────────────────────────────────────────────────────────────
const RR_MODES   = ["1:2", "2:4"];
const WIN_DAYS   = [7, 14, 30];
const TIMEFRAMES = ["1h", "2h", "4h", "6h"];
const CANDLE_MS_MAP = {"1h":3_600_000,"2h":7_200_000,"4h":14_400_000,"6h":21_600_000};
const LIMIT_MAP     = {"1h":1500,"2h":900,"4h":570,"6h":450};

function StatCard({ label, value, color }) {
  return (
    <div style={{ background:"#f8f9fb", borderRadius:10, padding:"14px 18px", border:"1px solid #e8eaed", minWidth:110 }}>
      <div style={{ fontSize:10, color:"#9ca3af", fontWeight:700, letterSpacing:"0.07em", textTransform:"uppercase", marginBottom:6 }}>{label}</div>
      <div style={{ fontSize:22, fontWeight:700, color: color||"#111827" }}>{value}</div>
    </div>
  );
}

function BacktestPage({ scanRow, initialMarket, onBack }) {
  const market = initialMarket || "futures";
  const [rrMode, setRrMode]       = useState("1:2");
  const [window, setWindow]       = useState(7);
  const [timeframe, setTimeframe] = useState("1h");
  const [candles, setCandles]     = useState(null);
  const [dbSignals, setDbSignals] = useState(null);  // signals fetched from DB
  const [trades, setTrades]       = useState(null);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [reloading, setReloading] = useState(false);
  const [sort, setSort]           = useState({ k:"_entryTimeMs", dir:"desc" });

  const symbol = scanRow?.symbol || "BTCUSDT";
  const { base } = fmtSym(symbol);
  const fetchIdRef = useRef(0);

  // Fetch candles + signals from DB together
  const fetchData = useCallback(async () => {
    // Guards against out-of-order responses: if the symbol/timeframe/market
    // changes again before this request lands, a slower older request must
    // not be allowed to clobber the newer one's state once it resolves.
    const requestId = ++fetchIdRef.current;
    setReloading(true);
    setError(null);
    try {
      const limit = LIMIT_MAP[timeframe] || 1500;
      const [candleRes, signalRes] = await Promise.all([
        fetch(`${API_BASE}/candles/${symbol}?interval=${timeframe}&market=${market}&limit=${limit}`),
        fetch(`${API_BASE}/signals?symbol=${symbol}&interval=${timeframe}&market=${market}&days=30&limit=500`),
      ]);
      if (!candleRes.ok) {
        const detail = await candleRes.json().catch(() => ({}));
        throw new Error(detail?.detail || `Candles API ${candleRes.status}`);
      }
      const rawCandles = await candleRes.json();
      const parsedCandles = rawCandles.map(r => ({
        symbol,
        openTimeMs: r[0],
        open:   parseFloat(r[1]),
        high:   parseFloat(r[2]),
        low:    parseFloat(r[3]),
        close:  parseFloat(r[4]),
        volume: parseFloat(r[5]),
      }));

      // DB signals — sorted oldest first for simulation
      let rawSignals = [];
      if (signalRes.ok) {
        rawSignals = await signalRes.json();
      }
      // cross_time from API is ISO string, convert to ms
      // The backend occasionally stores the same crossover twice (near-identical
      // cross_time a few hundred ms apart, from separate scan runs) — collapse
      // those down to one signal per type/minute before simulating trades.
      const seenSignals = new Map();
      for (const s of rawSignals) {
        const crossTimeMs = new Date(s.cross_time).getTime();
        const dedupeKey = `${s.signal_type}|${Math.round(crossTimeMs / 60000)}`;
        if (!seenSignals.has(dedupeKey)) {
          seenSignals.set(dedupeKey, {
            type:        s.signal_type,           // "BUY" | "SELL"
            crossPrice:  s.cross_price,
            crossTimeMs,
            ema7:  s.ema_7, ema25: s.ema_25, ema99: s.ema_99,
          });
        }
      }
      // Sort oldest → newest for simulation walkthrough
      const parsedSignals = [...seenSignals.values()].sort((a, b) => a.crossTimeMs - b.crossTimeMs);

      if (fetchIdRef.current !== requestId) return; // a newer request has since superseded this one
      setCandles(parsedCandles);
      setDbSignals(parsedSignals);
    } catch(e) {
      if (fetchIdRef.current === requestId) setError(e.message);
    } finally {
      if (fetchIdRef.current === requestId) {
        setLoading(false);
        setReloading(false);
      }
    }
  }, [symbol, timeframe, market]);

  useEffect(() => {
    setLoading(true);
    setCandles(null);
    setDbSignals(null);
    setTrades(null);
    fetchData();
  }, [fetchData]);

  // Auto-refresh every 20s so OPEN trades pick up SL/TP hits from fresh
  // candle data without needing a manual Reload click.
  useEffect(() => {
    const id = setInterval(fetchData, 20000);
    return () => clearInterval(id);
  }, [fetchData]);

  // Re-run simulation whenever candles, signals, rrMode, or window changes.
  // Every timeframe now stores its own signals in the DB using the identical
  // detection condition — so all of them use the same DB-signal simulation
  // path, no client-side EMA recomputation.
  useEffect(() => {
    if (candles && dbSignals) {
      setTrades(runBacktestFromSignals(candles, dbSignals, rrMode, window, CANDLE_MS_MAP[timeframe], timeframe));
    }
  }, [candles, dbSignals, rrMode, window, timeframe]);

  const sortedTrades = trades ? [...trades].sort((a,b)=>{
    const dir = sort.dir==="asc"?1:-1;
    const av=a[sort.k], bv=b[sort.k];
    if (av==null&&bv==null) return 0;
    if (av==null) return 1; if (bv==null) return -1;
    return typeof av==="string"?dir*av.localeCompare(bv):dir*(av-bv);
  }) : [];

  // Stats: exclude OPEN and SKIP from wins/losses/P&L — only closed real trades count
  const closedTrades   = trades ? trades.filter(t => t.result === "WIN" || t.result === "LOSS") : [];
  const openTrades     = trades ? trades.filter(t => t.result === "OPEN") : [];
  const wins           = closedTrades.filter(t=>t.result==="WIN").length;
  const losses         = closedTrades.filter(t=>t.result==="LOSS").length;
  const closedCount    = closedTrades.length;
  const winRate        = closedCount > 0 ? ((wins/closedCount)*100).toFixed(1) : null;
  const totalPnl       = closedTrades.reduce((sum,t)=>sum+t.gainPct,0);
  const openCount      = openTrades.length;
  const allTradesCount = trades ? trades.length : 0;

  const toggleSort = k => setSort(s => s.k===k?{k,dir:s.dir==="asc"?"desc":"asc"}:{k,dir:"desc"});

  const BCOLS = [
    {k:"symbol",        l:"Symbol"},
    {k:"tradeSignal",   l:"Signal Type"},
    {k:"signalTime",    l:"Signal Time"},
    {k:"_entryTimeMs",  l:"Entry Time"},
    {k:"entryPrice",    l:"Entry Price",    r:true},
    {k:"stopLoss",      l:"Stop Loss",      r:true},
    {k:"targetPrice",   l:"Take Profit",    r:true},
    {k:"entryCloseTime",l:"Exit Time"},
    {k:"entryClose",    l:"Exit Price",     r:true},
    {k:"exitReason",    l:"Exit Reason"},
    {k:"duration",      l:"Duration"},
    {k:"gainPct",       l:"PnL %",          r:true},
    {k:"gainAmount",    l:"PnL Amount",     r:true},
    {k:"result",        l:"Result"},
  ];

  return (
    <div style={{ fontFamily:"'Inter',system-ui,sans-serif", background:"#fff", minHeight:"100vh", width:"100%", color:"#111827" }}>

      {/* Header */}
      <div style={{ display:"flex", alignItems:"center", gap:10, padding:"13px 24px", borderBottom:"1px solid #e8eaed", background:"#fff", flexWrap:"wrap" }}>
        <button onClick={onBack} style={{
          display:"flex", alignItems:"center", gap:5, padding:"5px 12px",
          borderRadius:7, border:"1px solid #e5e7eb", background:"transparent",
          fontSize:12, fontWeight:600, color:"#374151", cursor:"pointer",
        }}>← Back</button>
        <span style={{ color:"#d1d5db", fontSize:14 }}>›</span>
        <span style={{ fontWeight:700, color:"#f59e0b", fontSize:15 }}>{base}</span>
        <span style={{ color:"#9ca3af", fontSize:14, fontWeight:400 }}>/USDT</span>
        <span style={{ color:"#9ca3af", fontSize:13 }}>Backtest · Last {window} days · IST · EMA 7/25/99 · {timeframe.toUpperCase()} · Signal candle close entry</span>
        <div style={{ marginLeft:"auto", display:"flex", alignItems:"center", gap:12 }}>
          <button onClick={fetchData} disabled={reloading} style={{
            display:"flex", alignItems:"center", gap:5,
            padding:"5px 14px", borderRadius:7, border:"1px solid #e5e7eb",
            background:"transparent", fontSize:12, fontWeight:600,
            color: reloading?"#9ca3af":"#374151", cursor:"pointer",
          }}>↻ {reloading?"Loading…":"Reload"}</button>
        </div>
      </div>

      {/* Controls */}
      <div style={{ padding:"12px 24px", borderBottom:"1px solid #e8eaed", display:"flex", alignItems:"center", gap:16, flexWrap:"wrap" }}>
        <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Risk : Reward</span>
        {RR_MODES.map(m => (
          <button key={m} onClick={()=>setRrMode(m)} style={{
            padding:"4px 14px", borderRadius:6, fontSize:12, fontWeight:600, cursor:"pointer",
            border:rrMode===m?"1.5px solid #f59e0b":"1px solid #e5e7eb",
            background:"transparent", color:rrMode===m?"#f59e0b":"#6b7280",
          }}>RR {m}</button>
        ))}
        <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase", marginLeft:8 }}>Window</span>
        {WIN_DAYS.map(d => (
          <button key={d} onClick={()=>setWindow(d)} style={{
            padding:"4px 14px", borderRadius:6, fontSize:12, fontWeight:600, cursor:"pointer",
            border:window===d?"1.5px solid #f59e0b":"1px solid #e5e7eb",
            background:"transparent", color:window===d?"#f59e0b":"#6b7280",
          }}>{d}d</button>
        ))}
        <div style={{ width:1, height:20, background:"#e5e7eb", marginLeft:8 }}/>
        <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Timeframe</span>
        {TIMEFRAMES.map(tf => (
          <button key={tf} onClick={()=>setTimeframe(tf)} style={{
            padding:"4px 14px", borderRadius:6, fontSize:12, fontWeight:600, cursor:"pointer",
            border:timeframe===tf?"1.5px solid #6366f1":"1px solid #e5e7eb",
            background:"transparent", color:timeframe===tf?"#6366f1":"#6b7280",
            transition:"all 0.15s",
          }}>{tf.toUpperCase()}</button>
        ))}
      </div>

      {/* Stat cards */}
      {!loading && !error && trades && (
        <div style={{ display:"flex", gap:12, padding:"16px 24px", borderBottom:"1px solid #e8eaed", flexWrap:"wrap" }}>
          <StatCard label="Total Trades" value={allTradesCount}/>
          <StatCard label="Open"         value={openCount} color="#0891b2"/>
          <StatCard label="Wins"         value={wins}   color="#16a34a"/>
          <StatCard label="Losses"       value={losses} color="#dc2626"/>
          <StatCard label="Win Rate"     value={winRate ? `${winRate}%` : "—"} color="#f59e0b"/>
          <StatCard label="Total P&L"    value={closedCount>0?`${totalPnl>=0?"+":""}${totalPnl.toFixed(2)}%`:"—"} color={totalPnl>=0?"#16a34a":"#dc2626"}/>
          <StatCard label="RR Mode"      value={rrMode} color="#6366f1"/>
          <StatCard label="Timeframe"    value={timeframe.toUpperCase()} color="#0891b2"/>
        </div>
      )}

      {/* Table */}
      <div style={{ overflowX:"auto" }}>
        {error ? (
          <div style={{ padding:48, textAlign:"center", color:"#dc2626", fontSize:14 }}>
            <div style={{ fontSize:22, marginBottom:8 }}>⚠</div>
            Failed to fetch candle data: <code style={{ fontSize:12 }}>{error}</code>
            <div style={{ marginTop:6, color:"#9ca3af", fontSize:12 }}>Check your backend server is running at {API_BASE}</div>
          </div>
        ) : loading ? (
          <div style={{ padding:60, textAlign:"center", color:"#9ca3af", fontSize:13 }}>Loading candle data from database…</div>
        ) : sortedTrades.length === 0 ? (
          <div style={{ padding:60, textAlign:"center", color:"#9ca3af", fontSize:14 }}>
            <div style={{ fontSize:24, marginBottom:8 }}>📊</div>
            No completed trades in last {window} days for {base}/USDT.
            <div style={{ marginTop:6, fontSize:12 }}>Try extending the window or adjusting the RR mode.</div>
          </div>
        ) : (
          <table style={{ width:"100%", borderCollapse:"collapse", fontSize:12 }}>
            <thead>
              <tr>
                {BCOLS.map(col => (
                  <TH key={col.k} right={col.r} onClick={()=>toggleSort(col.k)} sorted={sort.k===col.k} dir={sort.dir}>{col.l}</TH>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedTrades.map((t, i) => {
                const win     = t.result === "WIN";
                const open    = t.result === "OPEN";
                const forced  = t.exitReason === "Closed by new signal";
                const buy     = t.tradeSignal === "BUY";
                return (
                  <tr key={i}
                    style={{ borderBottom:"1px solid #f3f4f6", background:i%2===0?"#fff":"#fafafa" }}
                    onMouseEnter={e=>e.currentTarget.style.background="#f5f3ff"}
                    onMouseLeave={e=>e.currentTarget.style.background=i%2===0?"#fff":"#fafafa"}
                  >
                    {/* Symbol */}
                    <td style={{ padding:"10px 14px", fontWeight:700 }}>
                      <span style={{ color:"#f59e0b" }}>{t.symbol.replace("/USDT","")}</span>
                      <span style={{ color:"#9ca3af", fontSize:10 }}>/USDT</span>
                    </td>
                    {/* Signal Type */}
                    <td style={{ padding:"10px 14px" }}>
                      <span style={{
                        display:"inline-block", padding:"2px 8px", borderRadius:4, fontSize:11,
                        fontWeight:700, background:buy?"#dcfce7":"#fee2e2", color:buy?"#15803d":"#b91c1c"
                      }}>{t.tradeSignal}</span>
                    </td>
                    {/* Signal Time */}
                    <td style={{ padding:"10px 14px", color:"#374151", whiteSpace:"nowrap", fontSize:11 }}>{t.signalTime || "—"}</td>
                    {/* Entry Time */}
                    <td style={{ padding:"10px 14px", color:"#374151", whiteSpace:"nowrap", fontSize:11 }}>{t.entryTime}</td>
                    {/* Entry Price */}
                    <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", fontWeight:600, color:"#111827" }}>{fmtPrice(t.entryPrice)}</td>
                    {/* Stop Loss */}
                    <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", color:"#dc2626" }}>{fmtPrice(t.stopLoss)}</td>
                    {/* Take Profit */}
                    <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", color:"#16a34a" }}>{fmtPrice(t.targetPrice)}</td>
                    {/* Exit Time */}
                    <td style={{ padding:"10px 14px", color:"#374151", whiteSpace:"nowrap", fontSize:11 }}>
                      {open ? <span style={{ color:"#9ca3af" }}>Still running</span> : t.entryCloseTime}
                    </td>
                    {/* Exit Price */}
                    <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", fontWeight:500 }}>
                      {open ? <span style={{ color:"#9ca3af" }}>—</span> : fmtPrice(t.entryClose)}
                    </td>
                    {/* Exit Reason */}
                    <td style={{ padding:"10px 14px" }}>
                      <span style={{
                        fontSize:11, fontWeight:500,
                        color: open ? "#0891b2" : forced ? "#6366f1" : t.exitReason==="Target Hit" ? "#15803d" : "#b91c1c"
                      }}>{t.exitReason}</span>
                    </td>
                    {/* Duration */}
                    <td style={{ padding:"10px 14px", color:"#6b7280", whiteSpace:"nowrap" }}>{t.duration}</td>
                    {/* PnL % */}
                    <td style={{ padding:"10px 14px", textAlign:"right", fontWeight:700, fontVariantNumeric:"tabular-nums",
                      color: open ? "#9ca3af" : t.gainPct>=0?"#16a34a":"#dc2626"
                    }}>
                      {open ? "—" : `${t.gainPct>=0?"+":""}${t.gainPct.toFixed(2)}%`}
                    </td>
                    {/* PnL Amount */}
                    <td style={{ padding:"10px 14px", textAlign:"right", fontWeight:600, fontVariantNumeric:"tabular-nums",
                      color: open ? "#9ca3af" : t.gainAmount>=0?"#16a34a":"#dc2626"
                    }}>
                      {open ? "—" : `${t.gainAmount>=0?"+":""}${fmtPrice(t.gainAmount)}`}
                    </td>
                    {/* Result */}
                    <td style={{ padding:"10px 14px" }}>
                      <span style={{
                        display:"inline-block", padding:"2px 8px", borderRadius:4, fontSize:11, fontWeight:700,
                        background: open ? "#e0f2fe" : forced ? "#ede9fe" : win ? "#dcfce7" : "#fee2e2",
                        color:      open ? "#0369a1" : forced ? "#6d28d9" : win ? "#15803d" : "#b91c1c"
                      }}>{t.result}</span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      `}</style>
    </div>
  );
}

// ─── App router ───────────────────────────────────────────────────────────────
export default function App() {
  const [page, setPage]           = useState("scanner");
  const [market, setMarket]       = useState("futures");
  const [scanRow, setScanRow]     = useState(null);
  const [detailsRow, setDetailsRow] = useState(null);
  // Where Backtest was opened from, so its Back button returns there instead
  // of always dropping to the scanner (e.g. Details -> Backtest -> Back should
  // land back on Details, not the scanner).
  const [backtestFrom, setBacktestFrom] = useState("scanner");

  const goDetails = useCallback(row => {
    setDetailsRow(row);
    setPage("details");
  }, []);

  const goBacktest = useCallback(row => {
    setScanRow(row);
    setBacktestFrom(page);
    setPage("backtest");
  }, [page]);

  // Returning from Details always lands back on the scanner, which remounts
  // ScannerPage and restarts its auto-refresh polling.
  const goBackFromDetails = useCallback(() => {
    setPage("scanner");
  }, []);

  // Returning from Backtest lands wherever it was opened from.
  const goBackFromBacktest = useCallback(() => {
    setPage(backtestFrom);
  }, [backtestFrom]);

  if (page === "backtest") return <BacktestPage scanRow={scanRow} initialMarket={market} onBack={goBackFromBacktest}/>;
  if (page === "details")  return <DetailsPage row={detailsRow} market={market} onBack={goBackFromDetails} onBacktest={goBacktest}/>;
  return <ScannerPage market={market} setMarket={setMarket} onDetails={goDetails} onBacktest={goBacktest}/>;
}
