"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { TrendingUp, TrendingDown, Target, Database, CheckCircle2, XCircle, DollarSign, Award } from "lucide-react";

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
    ", " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: true }) + " IST";
}
function fmtTime(dt) {
  if (!dt) return "—";
  const d = new Date(dt);
  return d.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) +
    ", " + d.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: true }) + " IST";
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

const ScoreBadge = ({ v }) => {
  const s = v ?? 0;
  const color = s >= 70 ? "#16a34a" : s >= 40 ? "#f59e0b" : "#dc2626";
  const bg    = s >= 70 ? "#e7f8ef" : s >= 40 ? "#fef3e2" : "#fdecec";
  return (
    <span style={{
      display:"inline-block", minWidth:34, textAlign:"center", padding:"3px 10px",
      borderRadius:6, fontSize:13, fontWeight:700, color, background:bg, border:`1px solid ${color}33`,
    }}>{s.toFixed(0)}</span>
  );
};

const SummaryCard = ({ label, badge, badgeBg, badgeColor, value, valueColor, bg, Icon }) => (
  <div style={{
    background:bg, borderRadius:16, padding:"18px 20px", border:"3px solid #fff",
    boxShadow:"0 1px 4px rgba(0,0,0,0.05)", position:"relative", overflow:"hidden", minHeight:96,
  }}>
    <div style={{ display:"flex", alignItems:"center", gap:8, flexWrap:"wrap" }}>
      <span style={{ fontSize:11, fontWeight:800, letterSpacing:"0.06em", color:"#374151", textTransform:"uppercase" }}>{label}</span>
      <span style={{
        fontSize:10, fontWeight:700, padding:"2px 9px", borderRadius:999,
        background:badgeBg, color:badgeColor, whiteSpace:"nowrap",
      }}>{badge}</span>
    </div>
    <div style={{ fontSize:30, fontWeight:800, color:valueColor, marginTop:8 }}>{value}</div>
    <Icon size={30} strokeWidth={2} style={{ position:"absolute", right:16, bottom:14, opacity:0.5, color:valueColor }}/>
  </div>
);

// Free-form Risk:Reward entry — sits alongside the 1:2 / 2:4 presets so any
// ratio can be backtested, not just the two built-in ones. Uncontrolled
// inputs keyed on `value` so clicking a preset button resets them to match,
// without fighting the parent's controlled rrMode state on every keystroke.
const RRCustomInput = ({ value, onChange, disabled }) => {
  // value can also be the "swing" sentinel (no colon) when Swing SL/TP mode
  // is active — fall back to a default pair so these inputs stay controlled
  // (defined) instead of flipping to undefined and tripping React's warning.
  const [riskDefault, rewardDefault] = value === "swing" ? ["1", "2"] : value.split(":");
  const [risk, setRisk] = useState(riskDefault);
  const [reward, setReward] = useState(rewardDefault);

  // Reset the fields to match whenever a preset button changes `value`
  // externally — otherwise a stale typed value would linger after a preset click.
  useEffect(() => { setRisk(riskDefault); setReward(rewardDefault); }, [riskDefault, rewardDefault]);

  const apply = () => {
    const r = parseFloat(risk), w = parseFloat(reward);
    if (r > 0 && w > 0) onChange(`${r}:${w}`);
  };

  // Same look as the 1:2 / 2:4 preset buttons — plain bordered box, not a
  // standout colored pill, so the custom entry reads as part of the same set.
  const inputStyle = {
    width:44, padding:"5px 6px", borderRadius:7, border:"1px solid #e5e7eb",
    fontSize:12, fontWeight:600, textAlign:"center", color:"#6b7280",
    outline:"none", background:"#fff",
  };

  return (
    <div style={{ marginLeft:"auto", display:"flex", alignItems:"center", gap:6 }}>
      <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase", whiteSpace:"nowrap" }}>
        Customize Risk &amp; Reward
      </span>
      <input
        type="number" step="0.5" min="0.1" disabled={disabled} value={risk} title="Risk %"
        onChange={e => setRisk(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") apply(); }}
        style={inputStyle}
      />
      <span style={{ color:"#9ca3af", fontSize:12 }}>:</span>
      <input
        type="number" step="0.5" min="0.1" disabled={disabled} value={reward} title="Reward %"
        onChange={e => setReward(e.target.value)}
        onKeyDown={e => { if (e.key === "Enter") apply(); }}
        style={inputStyle}
      />
      <button onClick={apply} disabled={disabled} style={{
        padding:"5px 12px", borderRadius:7, fontSize:12, fontWeight:700,
        cursor: disabled ? "not-allowed" : "pointer",
        border:"1px solid #e5e7eb", background:"#fff", color:"#6b7280",
      }}>Apply</button>
    </div>
  );
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

// Most recent confirmed swing low/high strictly before `uptoIdx` (the last
// fully-closed candle at entry time) — a pivot at index i only counts once
// `strength` candles exist on BOTH sides with a higher low (or lower high),
// and all of those confirming candles must also be <= uptoIdx so this never
// looks at data that wouldn't exist yet at the moment of entry.
function findRecentSwingLow(candles, uptoIdx, strength = 2, maxLookback = 100) {
  const minIdx = Math.max(strength, uptoIdx - maxLookback);
  for (let i = uptoIdx - strength; i >= minIdx; i--) {
    let isPivot = true;
    for (let k = 1; k <= strength; k++) {
      if (candles[i].low >= candles[i - k].low || candles[i].low >= candles[i + k].low) { isPivot = false; break; }
    }
    if (isPivot) return candles[i].low;
  }
  return null;
}
function findRecentSwingHigh(candles, uptoIdx, strength = 2, maxLookback = 100) {
  const minIdx = Math.max(strength, uptoIdx - maxLookback);
  for (let i = uptoIdx - strength; i >= minIdx; i--) {
    let isPivot = true;
    for (let k = 1; k <= strength; k++) {
      if (candles[i].high <= candles[i - k].high || candles[i].high <= candles[i + k].high) { isPivot = false; break; }
    }
    if (isPivot) return candles[i].high;
  }
  return null;
}

// ─── Backtest using DB signals (exact same signals as scanner page) ───────────
// Instead of recalculating EMA crossovers on the frontend (which can differ
// from the backend due to logic version mismatches), we fetch the signals
// directly from the DB and use them as-is. Only the SL/TP simulation runs
// on the frontend using the candle OHLC data.
function runBacktestFromSignals(candles, dbSignals, rrMode, windowDays, candleMs = 3_600_000, timeframe = '1h', capital = 1000) {
  if (!candles || candles.length === 0 || !dbSignals) return [];

  // rrMode is any "risk:reward" string, e.g. "1:2", "2:4", a custom
  // user-entered ratio like "1.5:3", or the sentinel "swing" — meaning SL is
  // derived from market structure (nearest swing low/high) instead of a
  // fixed percentage, with TP always set to double that risk distance.
  const isSwing = rrMode === "swing";
  const [riskPct, rewardPct] = isSwing ? [null, null] : rrMode.split(":").map(Number);
  const SWING_BUFFER = 0.001; // 0.1% beyond the swing point, so SL sits "just" past it rather than exactly on it
  const SWING_FALLBACK_PCT = 1; // used only if no swing point is found in the lookback window

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

  function simulateTradeFromIdx(type, entryPrice, startCandleIdx, sigCandleIdx) {
    let stopLoss, targetPrice;
    if (isSwing) {
      if (type === "BUY") {
        const swingLow = findRecentSwingLow(candles, sigCandleIdx);
        stopLoss = swingLow != null ? swingLow * (1 - SWING_BUFFER) : entryPrice * (1 - SWING_FALLBACK_PCT / 100);
        const risk = entryPrice - stopLoss;
        targetPrice = entryPrice + risk; // TP = same distance as SL (1:1)
      } else {
        const swingHigh = findRecentSwingHigh(candles, sigCandleIdx);
        stopLoss = swingHigh != null ? swingHigh * (1 + SWING_BUFFER) : entryPrice * (1 + SWING_FALLBACK_PCT / 100);
        const risk = stopLoss - entryPrice;
        targetPrice = entryPrice - risk; // TP = same distance as SL (1:1)
      }
    } else if (type === "BUY") {
      stopLoss    = entryPrice * (1 - riskPct  / 100);
      targetPrice = entryPrice * (1 + rewardPct / 100);
    } else {
      stopLoss    = entryPrice * (1 + riskPct  / 100);
      targetPrice = entryPrice * (1 - rewardPct / 100);
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

    // Entry = the NEXT candle's open price/time — the signal candle has
    // already closed by the time you could realistically act on it, so the
    // earliest honest fill is the following candle's open, not a price
    // inside (or the close of) the candle that already happened.
    // If that next candle doesn't exist yet (signal fired on the most
    // recent candle available), skip this signal entirely until it's
    // picked up on a later refresh once that candle exists.
    const entryCandleIdx = sigCandleIdx + 1;
    if (entryCandleIdx >= candles.length) continue;

    const entryTimeMs = candles[entryCandleIdx].openTimeMs;
    if (entryTimeMs > Date.now()) continue;

    const entryPrice  = candles[entryCandleIdx].open;
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
      prev.gainDollar     = (capital * gainPct) / 100;
      prev._exitTimeMs    = forceTime;
      openTradeRef        = null;
    }

    // Simulate this trade — walk forward starting at the entry candle itself
    const sim = simulateTradeFromIdx(type, entryPrice, entryCandleIdx, sigCandleIdx);
    const { stopLoss, targetPrice, exitPrice, exitTimeMs, exitReason } = sim;

    if (!exitPrice) {
      const row = {
        date: fmtDate(entryTimeMs), timeFrame: timeframe.toUpperCase(), symbol: symLabel,
        tradeSignal: type, signalTime, entryTime, entryPrice, stopLoss, targetPrice,
        entryClose: null, entryCloseTime: null, exitReason: "Open", duration: "—",
        result: "OPEN", gainPct: null, gainAmount: null, gainDollar: null,
        _entryTimeMs: entryTimeMs, _exitTimeMs: null,
      };
      trades.push(row);
      openTradeRef = row;
      continue;
    }

    const gainPct    = type === "BUY" ? ((exitPrice - entryPrice) / entryPrice) * 100 : ((entryPrice - exitPrice) / entryPrice) * 100;
    const gainAmount = type === "BUY" ? exitPrice - entryPrice : entryPrice - exitPrice;
    const gainDollar = (capital * gainPct) / 100;
    const result     = exitReason === "Target Hit" ? "WIN" : "LOSS";
    const durationMs = exitTimeMs - entryTimeMs;
    const dh         = Math.floor(durationMs / 3_600_000);
    const dm         = Math.floor((durationMs % 3_600_000) / 60000);
    const duration   = dh >= 24 ? `${Math.floor(dh/24)}d ${dh%24}h` : `${dh}h ${dm}m`;

    trades.push({
      date: fmtDate(entryTimeMs), timeFrame: timeframe.toUpperCase(), symbol: symLabel,
      tradeSignal: type, signalTime, entryTime, entryPrice, stopLoss, targetPrice,
      entryClose: exitPrice, entryCloseTime: fmtDateTime(exitTimeMs), exitReason,
      duration, result, gainPct, gainAmount, gainDollar,
      _entryTimeMs: entryTimeMs, _exitTimeMs: exitTimeMs,
    });
    openTradeRef = null;
  }

  return trades;
}


// Toggle to false to hide the "Swing SL/TP" button from both backtest pages
// without removing the feature — flip back to true to bring it back.
const SHOW_SWING_BUTTON = false;

const SIGS  = ["All","BUY","SELL"];
const BT_PERIOD_DAYS = { day: 1, week: 7, month: 30 };
const SCOLS = [
  {k:"rank",     l:"#",            s:true},
  {k:"symbol",   l:"Symbol",       s:true},
  {k:"ema_trend",l:"EMA Trend",    s:true},
  {k:"score",    l:"Score",        s:true,  r:true},
  {k:"price",    l:"Price",        s:true,  r:true},
  {k:"change_1h",l:"1H %",         s:true,  r:true},
  {k:"change_24h",l:"24H %",       s:true,  r:true},
  {k:"volume_24h",l:"Volume (24H)",s:true,  r:true},
  {k:"last_signal",l:"Last Signal",s:true},
  {k:"signal_time",l:"Signal Time",s:true},
  {k:"details",  l:"Details",      s:false},
];

function ScannerPage({ market, setMarket, onDetails, onBacktest, onScreenerBacktest }) {
  const [rows, setRows] = useState([]);
  const [status, setStatus] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [sig, setSig] = useState("All");
  const [trend, setTrend] = useState("All");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState({ k:"signal_time", dir:"desc" });
  const [modal, setModal] = useState(null);
  const [updated, setUpdated] = useState(null);
  const [nowTick, setNowTick] = useState(null);
  const intRef = useRef(null);

  useEffect(() => {
    const id = setInterval(() => setNowTick(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const scannedAgoSec = (updated && nowTick) ? Math.max(0, Math.floor((nowTick - updated.getTime()) / 1000)) : null;

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
      if (trend !== "All" && r.ema_trend !== trend) return false;
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

  const bullishCount = rows.filter(r => r.ema_trend === "Bullish").length;
  const bearishCount = rows.filter(r => r.ema_trend === "Bearish").length;
  const scoredRows = rows.filter(r => r.last_signal);
  const avgScore = scoredRows.length
    ? Math.round(scoredRows.reduce((s, r) => s + (r.score || 0), 0) / scoredRows.length)
    : 0;

  return (
    <div style={{ fontFamily:"'Inter',system-ui,sans-serif", background:"#f5f6f8", minHeight:"100vh", width:"100%", color:"#111827" }}>
      {/* Header */}
      <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between", padding:"20px 24px 16px", flexWrap:"wrap", gap:12 }}>
        <div>
          <div style={{ fontSize:26, fontWeight:800, letterSpacing:"-0.02em" }}>EMA SCANNER</div>
          <div style={{ fontSize:12, color:"#9ca3af", fontWeight:600, marginTop:2, letterSpacing:"0.02em" }}>
            TRIPLE EMA STRATEGY 7 › 25 › 99 · {market === "spot" ? "Spot" : "USDT Futures"}
          </div>
        </div>
        <div style={{ display:"flex", alignItems:"center", gap:14, flexWrap:"wrap" }}>
          <MarketToggle market={market} setMarket={setMarket} />
          {status && (
            <span style={{ display:"flex", alignItems:"center", gap:6, fontSize:12, color:"#6b7280", fontWeight:500 }}>
              <span style={{ width:7, height:7, borderRadius:"50%", background:status.status==="ready"?"#22c55e":"#f59e0b", display:"inline-block" }}/>
              {status.status === "ready"
                ? (scannedAgoSec != null ? `Scanned ${scannedAgoSec}s ago` : "Live")
                : "Initializing"}
            </span>
          )}
          <button onClick={fetchData} title="Refresh" style={{
            width:32, height:32, borderRadius:8, border:"1px solid #e5e7eb", background:"#fff",
            fontSize:15, color:"#374151", cursor:"pointer", display:"flex", alignItems:"center", justifyContent:"center",
          }}>↻</button>
        </div>
      </div>

      {/* Summary cards */}
      <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))", gap:16, padding:"0 24px 16px" }}>
        <SummaryCard
          label="Bullish" badge="7›25›99" badgeBg="#bbf1d3" badgeColor="#166534"
          value={bullishCount} valueColor="#16a34a" bg="#e7f8ef" Icon={TrendingUp}
        />
        <SummaryCard
          label="Bearish" badge="99›25›7" badgeBg="#fbcfd1" badgeColor="#991b1b"
          value={bearishCount} valueColor="#dc2626" bg="#fdecec" Icon={TrendingDown}
        />
        <SummaryCard
          label="Avg Score" badge="AVG" badgeBg="#111827" badgeColor="#fff"
          value={avgScore} valueColor="#111827" bg="#e7e7fb" Icon={Target}
        />
        <SummaryCard
          label="Stored" badge="TOTAL" badgeBg="#fcd9a8" badgeColor="#9a5b13"
          value={status?.symbols_tracked ?? rows.length} valueColor="#f59e0b" bg="#fdf1e2" Icon={Database}
        />
      </div>

      {/* Search */}
      <div style={{ padding:"0 24px 12px" }}>
        <div style={{ position:"relative", maxWidth:260 }}>
          <span style={{ position:"absolute", left:14, top:"50%", transform:"translateY(-50%)", color:"#9ca3af", fontSize:13 }}>⌕</span>
          <input placeholder="Search coins…" value={search} onChange={e=>setSearch(e.target.value)} style={{
            width:"100%", padding:"9px 14px 9px 32px", borderRadius:10, border:"1px solid #e5e7eb", fontSize:13,
            color:"#374151", outline:"none", background:"#fff", boxSizing:"border-box",
          }}/>
        </div>
        <div style={{ marginTop:8, fontSize:12, color:"#9ca3af" }}>
          <strong style={{ color:"#374151" }}>{filtered.length}</strong> entries
          {status && <> · Signals today: <strong style={{ color:"#374151" }}>{status.signals_today}</strong> · Uptime: <strong style={{ color:"#374151" }}>{Math.floor(status.uptime_seconds/60)}m</strong></>}
        </div>
      </div>

      {/* Filters */}
      <div style={{ padding:"16px 24px", display:"flex", alignItems:"center", gap:16, flexWrap:"wrap", borderTop:"1px solid #e5e7eb", borderBottom:"1px solid #e5e7eb" }}>
        <div style={{ display:"flex", gap:4 }}>
          {["All","Bullish","Bearish"].map(t => (
            <button key={t} onClick={()=>setTrend(t)} style={{
              padding:"6px 16px", borderRadius:8, fontSize:12, fontWeight:700, cursor:"pointer",
              border: trend===t ? "1px solid #111827" : "1px solid #e5e7eb",
              background: trend===t ? "#111827" : "#fff",
              color: trend===t ? "#fff" : "#6b7280", textTransform:"uppercase",
            }}>{t}</button>
          ))}
        </div>
        <div style={{ width:1, height:20, background:"#e5e7eb" }}/>
        <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Signal</span>
        <div style={{ display:"flex", gap:4 }}>
          {SIGS.map(s => (
            <button key={s} onClick={()=>setSig(s)} style={{
              padding:"5px 13px", borderRadius:7, fontSize:12, fontWeight:600, cursor:"pointer",
              border:sig===s?"1.5px solid #f59e0b":"1px solid #e5e7eb", background:sig===s?"#fff7ed":"#fff",
              color:sig===s?"#f59e0b":"#6b7280",
            }}>{s}</button>
          ))}
        </div>
        <div style={{ marginLeft:"auto", display:"flex", alignItems:"center", gap:8, flexWrap:"wrap" }}>
          <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Sort</span>
          {[["signal_time","Time"],["score","Score"],["volume_24h","Vol"],["change_24h","24H %"]].map(([k,l]) => (
            <button key={k} onClick={()=>toggleSort(k)} style={{
              padding:"5px 13px", borderRadius:7, fontSize:12, fontWeight:600, cursor:"pointer",
              border:sort.k===k?"1.5px solid #6366f1":"1px solid #e5e7eb", background:sort.k===k?"#eef2ff":"#fff",
              color:sort.k===k?"#6366f1":"#6b7280",
            }}>{l}{sort.k===k ? (sort.dir==="asc"?" ↑":" ↓") : ""}</button>
          ))}
          <div style={{ width:1, height:20, background:"#e5e7eb" }}/>
          <button onClick={onScreenerBacktest} style={{
            padding:"6px 16px", borderRadius:8, border:"1px solid #6366f1",
            background:"transparent", color:"#6366f1", fontSize:12, fontWeight:700, cursor:"pointer",
          }}>Backtest</button>
        </div>
      </div>

      {/* Table */}
      <div style={{ margin:"16px 24px 24px", background:"#fff", borderRadius:14, border:"1px solid #e5e7eb", overflow:"hidden" }}>
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
                <tr><td colSpan={11} style={{ padding:48, textAlign:"center", color:"#9ca3af", fontSize:13 }}>No coins match your filters.</td></tr>
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
                    <td style={{ padding:"11px 14px", textAlign:"right" }}><ScoreBadge v={row.score}/></td>
                    <td style={{ padding:"11px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", fontWeight:500 }}>{fmtPrice(row.price)}</td>
                    <td style={{ padding:"11px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums" }}><ChgCell v={row.change_1h}/></td>
                    <td style={{ padding:"11px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums" }}><ChgCell v={row.change_24h}/></td>
                    <td style={{ padding:"11px 14px", textAlign:"right", color:"#374151", fontVariantNumeric:"tabular-nums" }}>{fmtVol(row.volume_24h)}</td>
                    <td style={{ padding:"11px 14px" }}><SigBadge s={row.last_signal}/></td>
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
                {l:"Score", v:<ScoreBadge v={modal.score}/>},
                {l:"Price", v:`$${fmtPrice(modal.price)}`},
                {l:"1H Change", v:<ChgCell v={modal.change_1h}/>},
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

// ─── Screener Backtest Page (aggregate Day/Week/Month WIN/LOSS/OPEN) ─────────
const BT_PERIOD_LABELS = [["day","Day"],["week","Week"],["month","Month"]];

function ScreenerBacktestPage({ market, onBack }) {
  const [btPeriod, setBtPeriod] = useState("day");
  const [btRrMode, setBtRrMode] = useState("1:2");
  const [btCapital, setBtCapital] = useState(1000);
  const [btStats, setBtStats] = useState(null);
  const [btLoading, setBtLoading] = useState(true);
  const [btError, setBtError] = useState(null);
  const [trades, setTrades] = useState([]);
  const [sort, setSort] = useState({ k:"_entryTimeMs", dir:"desc" });
  const [exporting, setExporting] = useState(false);
  const fetchIdRef = useRef(0);

  // Aggregate Day/Week/Month backtest — how many of ALL 1H signals in this
  // window resolved as WIN/LOSS, or are still OPEN. Reuses the same
  // signal+candle-driven SL/TP simulation as the per-coin Backtest page
  // (runBacktestFromSignals) — never recomputed on the backend, per this
  // app's "signals from DB, SL/TP simulation on the frontend" convention.
  const fetchBacktestStats = useCallback(async () => {
    // Guards against out-of-order responses: if the period/RR mode changes
    // again before this request lands (e.g. Day -> Month clicked quickly),
    // a slower older request must not be allowed to clobber the newer one's
    // state once it resolves.
    const requestId = ++fetchIdRef.current;
    setBtLoading(true);
    setBtError(null);
    try {
      const days = BT_PERIOD_DAYS[btPeriod];
      // Only fetch as many candles as this window actually needs (plus a
      // buffer for the SL/TP walk-forward after entry) instead of always
      // pulling the full 1500-candle history — cuts payload size a lot for
      // Day/Week, and meaningfully for Month too.
      const candleLimit = Math.min(1500, days * 24 + 200);
      const sigRes = await fetch(`${API_BASE}/signals?market=${market}&interval=1h&days=${days}&limit=10000`);
      if (!sigRes.ok) throw new Error(`API ${sigRes.status}`);
      const rawSignals = await sigRes.json();

      // The backend occasionally stores the same crossover twice (near-identical
      // cross_time a few hundred ms apart, from separate scan runs) — collapse
      // those down to one signal per symbol/type/minute before simulating, same
      // as the Details/Backtest pages already do.
      const bySymbol = new Map();
      const seenKeys = new Set();
      for (const s of rawSignals) {
        const crossTimeMs = new Date(s.cross_time).getTime();
        const dedupeKey = `${s.symbol}|${s.signal_type}|${Math.round(crossTimeMs / 60000)}`;
        if (seenKeys.has(dedupeKey)) continue;
        seenKeys.add(dedupeKey);
        if (!bySymbol.has(s.symbol)) bySymbol.set(s.symbol, []);
        bySymbol.get(s.symbol).push({
          type: s.signal_type, crossPrice: s.cross_price, crossTimeMs,
        });
      }

      const perSymbol = await Promise.all([...bySymbol.entries()].map(async ([symbol, sigs]) => {
        const res = await fetch(`${API_BASE}/candles/${symbol}?interval=1h&market=${market}&limit=${candleLimit}`);
        if (!res.ok) return [];
        const rawCandles = await res.json();
        const candles = rawCandles.map(r => ({
          symbol, openTimeMs: r[0],
          open: parseFloat(r[1]), high: parseFloat(r[2]), low: parseFloat(r[3]), close: parseFloat(r[4]),
        }));
        // runBacktestFromSignals requires oldest->newest — the API returns
        // signals newest-first, so this must be sorted before simulating
        // (every other caller of this function already does this).
        const sortedSigs = [...sigs].sort((a, b) => a.crossTimeMs - b.crossTimeMs);
        return runBacktestFromSignals(candles, sortedSigs, btRrMode, 3650, CANDLE_MS_MAP["1h"], "1h", btCapital);
      }));

      if (fetchIdRef.current !== requestId) return; // a newer request has since superseded this one

      const allTrades = perSymbol.flat();
      const won    = allTrades.filter(t => t.result === "WIN").length;
      const lost   = allTrades.filter(t => t.result === "LOSS").length;
      const closed = allTrades.filter(t => t.result === "WIN" || t.result === "LOSS");
      const pnl    = closed.reduce((sum, t) => sum + t.gainPct, 0);
      const pnlDollar = closed.reduce((sum, t) => sum + t.gainDollar, 0);
      const winRate = closed.length > 0 ? (won / closed.length) * 100 : null;
      setBtStats({ won, lost, pnl, pnlDollar, winRate, total: allTrades.length });
      setTrades(allTrades);
    } catch (e) {
      if (fetchIdRef.current === requestId) {
        setBtStats(null);
        setTrades([]);
        setBtError(e.message);
      }
    } finally {
      if (fetchIdRef.current === requestId) setBtLoading(false);
    }
  }, [btPeriod, btRrMode, btCapital, market]);

  useEffect(() => { fetchBacktestStats(); }, [fetchBacktestStats]);

  const toggleSort = k => setSort(s => s.k===k ? {k, dir:s.dir==="asc"?"desc":"asc"} : {k, dir:"desc"});
  const sortedTrades = [...trades].sort((a, b) => {
    const dir = sort.dir === "asc" ? 1 : -1;
    const av = a[sort.k], bv = b[sort.k];
    if (av == null && bv == null) return 0;
    if (av == null) return 1; if (bv == null) return -1;
    return typeof av === "string" ? dir*av.localeCompare(bv) : dir*(av-bv);
  });

  // Export CSV — re-runs the same signal+candle simulation for Day, Week,
  // AND Month (independent of whichever period is currently selected on
  // screen) and downloads one combined file with a Period column, so you
  // get all three windows in a single export rather than just what's shown.
  const exportAllPeriods = useCallback(async () => {
    setExporting(true);
    try {
      const allRows = [];
      for (const period of ["day", "week", "month"]) {
        const days = BT_PERIOD_DAYS[period];
        const candleLimit = Math.min(1500, days * 24 + 200);
        const sigRes = await fetch(`${API_BASE}/signals?market=${market}&interval=1h&days=${days}&limit=10000`);
        if (!sigRes.ok) continue;
        const rawSignals = await sigRes.json();

        // Same near-duplicate collapse as the on-screen stats — otherwise a
        // signal the backend stored twice would double-count in the export.
        const bySymbol = new Map();
        const seenKeys = new Set();
        for (const s of rawSignals) {
          const crossTimeMs = new Date(s.cross_time).getTime();
          const dedupeKey = `${s.symbol}|${s.signal_type}|${Math.round(crossTimeMs / 60000)}`;
          if (seenKeys.has(dedupeKey)) continue;
          seenKeys.add(dedupeKey);
          if (!bySymbol.has(s.symbol)) bySymbol.set(s.symbol, []);
          bySymbol.get(s.symbol).push({
            type: s.signal_type, crossPrice: s.cross_price, crossTimeMs,
          });
        }

        const perSymbol = await Promise.all([...bySymbol.entries()].map(async ([symbol, sigs]) => {
          const res = await fetch(`${API_BASE}/candles/${symbol}?interval=1h&market=${market}&limit=${candleLimit}`);
          if (!res.ok) return [];
          const rawCandles = await res.json();
          const candles = rawCandles.map(r => ({
            symbol, openTimeMs: r[0],
            open: parseFloat(r[1]), high: parseFloat(r[2]), low: parseFloat(r[3]), close: parseFloat(r[4]),
          }));
          const sortedSigs = [...sigs].sort((a, b) => a.crossTimeMs - b.crossTimeMs);
          return runBacktestFromSignals(candles, sortedSigs, btRrMode, 3650, CANDLE_MS_MAP["1h"], "1h", btCapital);
        }));

        for (const t of perSymbol.flat()) allRows.push({ period, ...t });
      }

      const header = [
        "Period","Symbol","Signal Type","Signal Time","Entry Time","Entry Price",
        "Stop Loss","Take Profit","Exit Time","Exit Price","Exit Reason",
        "Duration","PnL %","PnL Amount","PnL ($)","Result",
      ];
      const csvEscape = v => `"${String(v ?? "").replace(/"/g, '""')}"`;
      const lines = [header.join(",")];
      for (const t of allRows) {
        const open = t.result === "OPEN";
        lines.push([
          t.period.toUpperCase(), t.symbol, t.tradeSignal, t.signalTime || "", t.entryTime,
          t.entryPrice, t.stopLoss, t.targetPrice,
          open ? "Still running" : t.entryCloseTime,
          open ? "" : t.entryClose,
          t.exitReason, t.duration,
          open ? "" : t.gainPct.toFixed(2),
          open ? "" : t.gainAmount,
          open ? "" : t.gainDollar.toFixed(2),
          t.result,
        ].map(csvEscape).join(","));
      }

      const blob = new Blob([lines.join("\n")], { type: "text/csv;charset=utf-8;" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `backtest_${market}_${btRrMode.replace(":","-")}.csv`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (e) {
      setBtError(`Export failed: ${e.message}`);
    } finally {
      setExporting(false);
    }
  }, [market, btRrMode, btCapital]);

  return (
    <div style={{ fontFamily:"'Inter',system-ui,sans-serif", background:"#f5f6f8", minHeight:"100vh", width:"100%", color:"#111827" }}>
      {/* Header */}
      <div style={{ display:"flex", alignItems:"center", gap:10, padding:"13px 24px", borderBottom:"1px solid #e8eaed", flexWrap:"wrap" }}>
        <button onClick={onBack} style={{
          display:"flex", alignItems:"center", gap:5, padding:"5px 12px",
          borderRadius:7, border:"1px solid #e5e7eb", background:"transparent",
          fontSize:12, fontWeight:600, color:"#374151", cursor:"pointer",
        }}>← Back</button>
        <span style={{ color:"#d1d5db", fontSize:14 }}>›</span>
        <span style={{ fontWeight:800, fontSize:17 }}>Backtest Summary</span>
        <span style={{ color:"#9ca3af", fontSize:12 }}>All coins · {market === "spot" ? "Spot" : "USDT Futures"} · 1H signals</span>
        <button onClick={exportAllPeriods} disabled={exporting} style={{
          marginLeft:"auto", display:"flex", alignItems:"center", gap:6,
          padding:"7px 16px", borderRadius:8, border:"none",
          background:"#111827", color:"#fff", fontSize:12, fontWeight:700,
          cursor: exporting ? "not-allowed" : "pointer", opacity: exporting ? 0.6 : 1,
        }}>{exporting ? "Exporting…" : "⭳ Export"}</button>
      </div>

      {/* Controls + cards */}
      <div style={{ padding:"20px 24px" }}>
        <div style={{ display:"flex", alignItems:"center", gap:8, marginBottom:16, flexWrap:"wrap" }}>
          <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Period</span>
          <div style={{ display:"flex", gap:4 }}>
            {BT_PERIOD_LABELS.map(([k,l]) => (
              <button key={k} disabled={btLoading} onClick={()=>setBtPeriod(k)} style={{
                padding:"5px 14px", borderRadius:7, fontSize:12, fontWeight:700,
                cursor: btLoading ? "not-allowed" : "pointer", opacity: btLoading && btPeriod!==k ? 0.5 : 1,
                border: btPeriod===k ? "1px solid #111827" : "1px solid #e5e7eb",
                background: btPeriod===k ? "#111827" : "#fff",
                color: btPeriod===k ? "#fff" : "#6b7280",
              }}>{l}</button>
            ))}
          </div>
          <div style={{ width:1, height:20, background:"#e5e7eb" }}/>
          <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>RR</span>
          <div style={{ display:"flex", gap:4 }}>
            {RR_MODES.map(m => (
              <button key={m} disabled={btLoading} onClick={()=>setBtRrMode(m)} style={{
                padding:"5px 14px", borderRadius:7, fontSize:12, fontWeight:700,
                cursor: btLoading ? "not-allowed" : "pointer", opacity: btLoading && btRrMode!==m ? 0.5 : 1,
                border: btRrMode===m ? "1.5px solid #f59e0b" : "1px solid #e5e7eb",
                background: btRrMode===m ? "#fff7ed" : "#fff",
                color: btRrMode===m ? "#f59e0b" : "#6b7280",
              }}>{m}</button>
            ))}
            {SHOW_SWING_BUTTON && (
              <button disabled={btLoading} onClick={()=>setBtRrMode("swing")} title="SL = just past the nearest swing low/high before entry, TP = same distance" style={{
                padding:"5px 14px", borderRadius:7, fontSize:12, fontWeight:700,
                cursor: btLoading ? "not-allowed" : "pointer", opacity: btLoading && btRrMode!=="swing" ? 0.5 : 1,
                border: btRrMode==="swing" ? "1.5px solid #f59e0b" : "1px solid #e5e7eb",
                background: btRrMode==="swing" ? "#fff7ed" : "#fff",
                color: btRrMode==="swing" ? "#f59e0b" : "#6b7280",
              }}>Swing SL/TP</button>
            )}
          </div>
          <div style={{ width:1, height:20, background:"#e5e7eb" }}/>
          <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase" }}>Capital</span>
          <div style={{ display:"flex", alignItems:"center", gap:4 }}>
            <span style={{ color:"#9ca3af", fontSize:12 }}>$</span>
            <input
              type="number" min="1" step="100" disabled={btLoading} defaultValue={btCapital}
              onBlur={e => { const v = parseFloat(e.target.value); if (v > 0) setBtCapital(v); }}
              onKeyDown={e => { if (e.key === "Enter") e.target.blur(); }}
              style={{ width:80, padding:"4px 6px", borderRadius:6, border:"1px solid #e5e7eb", fontSize:12 }}
            />
          </div>
          {btLoading && <span style={{ fontSize:11, color:"#9ca3af" }}>Loading…</span>}
          <RRCustomInput value={btRrMode} onChange={setBtRrMode} disabled={btLoading}/>
        </div>

        {btError ? (
          <div style={{ padding:48, textAlign:"center", color:"#dc2626", fontSize:14, background:"#fff", borderRadius:14, border:"1px solid #e5e7eb" }}>
            <div style={{ fontSize:22, marginBottom:8 }}>⚠</div>
            Could not load backtest stats: <code style={{ fontSize:12 }}>{btError}</code>
          </div>
        ) : (
          <div style={{ display:"grid", gridTemplateColumns:"repeat(auto-fit,minmax(220px,1fr))", gap:16 }}>
            <SummaryCard
              label="Won" badge="WIN" badgeBg="#bbf1d3" badgeColor="#166534"
              value={btStats?.won ?? "—"} valueColor="#16a34a" bg="#e7f8ef" Icon={CheckCircle2}
            />
            <SummaryCard
              label="Loss" badge="LOSS" badgeBg="#fbcfd1" badgeColor="#991b1b"
              value={btStats?.lost ?? "—"} valueColor="#dc2626" bg="#fdecec" Icon={XCircle}
            />
            <SummaryCard
              label="PnL ($)" badge={btStats && btStats.pnlDollar >= 0 ? "PROFIT" : "LOSS"}
              badgeBg={btStats && btStats.pnlDollar >= 0 ? "#bfe3fb" : "#fbcfd1"}
              badgeColor={btStats && btStats.pnlDollar >= 0 ? "#075985" : "#991b1b"}
              value={btStats ? `${btStats.pnlDollar >= 0 ? "+" : ""}$${btStats.pnlDollar.toFixed(2)}` : "—"}
              valueColor={btStats && btStats.pnlDollar >= 0 ? "#0891b2" : "#dc2626"} bg="#e6f6fd" Icon={DollarSign}
            />
            <SummaryCard
              label="Win Rate" badge={btStats && btStats.winRate >= 50 ? "GOOD" : "LOW"}
              badgeBg={btStats && btStats.winRate >= 50 ? "#bbf1d3" : "#fbcfd1"}
              badgeColor={btStats && btStats.winRate >= 50 ? "#166534" : "#991b1b"}
              value={btStats && btStats.winRate != null ? `${btStats.winRate.toFixed(1)}%` : "—"}
              valueColor={btStats && btStats.winRate >= 50 ? "#16a34a" : "#dc2626"} bg="#fdf1e2" Icon={Award}
            />
          </div>
        )}

        {/* All trades in this window */}
        {!btError && (
          <div style={{ marginTop:20, background:"#fff", borderRadius:14, border:"1px solid #e5e7eb", overflow:"hidden" }}>
            <div style={{ overflowX:"auto" }}>
              {btLoading ? (
                <div style={{ padding:60, textAlign:"center", color:"#9ca3af", fontSize:13 }}>Loading trades…</div>
              ) : sortedTrades.length === 0 ? (
                <div style={{ padding:60, textAlign:"center", color:"#9ca3af", fontSize:14 }}>
                  No signals in this window.
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
                      const win    = t.result === "WIN";
                      const open   = t.result === "OPEN";
                      const forced = t.exitReason === "Closed by new signal";
                      const buy    = t.tradeSignal === "BUY";
                      return (
                        <tr key={i}
                          style={{ borderBottom:"1px solid #f3f4f6", background:i%2===0?"#fff":"#fafafa" }}
                          onMouseEnter={e=>e.currentTarget.style.background="#f5f3ff"}
                          onMouseLeave={e=>e.currentTarget.style.background=i%2===0?"#fff":"#fafafa"}
                        >
                          <td style={{ padding:"10px 14px", fontWeight:700 }}>
                            <span style={{ color:"#f59e0b" }}>{t.symbol.replace("/USDT","")}</span>
                            <span style={{ color:"#9ca3af", fontSize:10 }}>/USDT</span>
                          </td>
                          <td style={{ padding:"10px 14px" }}>
                            <span style={{
                              display:"inline-block", padding:"2px 8px", borderRadius:4, fontSize:11,
                              fontWeight:700, background:buy?"#dcfce7":"#fee2e2", color:buy?"#15803d":"#b91c1c"
                            }}>{t.tradeSignal}</span>
                          </td>
                          <td style={{ padding:"10px 14px", color:"#374151", whiteSpace:"nowrap", fontSize:11 }}>{t.signalTime || "—"}</td>
                          <td style={{ padding:"10px 14px", color:"#374151", whiteSpace:"nowrap", fontSize:11 }}>{t.entryTime}</td>
                          <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", fontWeight:600, color:"#111827" }}>{fmtPrice(t.entryPrice)}</td>
                          <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", color:"#dc2626" }}>{fmtPrice(t.stopLoss)}</td>
                          <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", color:"#16a34a" }}>{fmtPrice(t.targetPrice)}</td>
                          <td style={{ padding:"10px 14px", color:"#374151", whiteSpace:"nowrap", fontSize:11 }}>
                            {open ? <span style={{ color:"#9ca3af" }}>Still running</span> : t.entryCloseTime}
                          </td>
                          <td style={{ padding:"10px 14px", textAlign:"right", fontVariantNumeric:"tabular-nums", fontWeight:500 }}>
                            {open ? <span style={{ color:"#9ca3af" }}>—</span> : fmtPrice(t.entryClose)}
                          </td>
                          <td style={{ padding:"10px 14px" }}>
                            <span style={{
                              fontSize:11, fontWeight:500,
                              color: open ? "#0891b2" : forced ? "#6366f1" : t.exitReason==="Target Hit" ? "#15803d" : "#b91c1c"
                            }}>{t.exitReason}</span>
                          </td>
                          <td style={{ padding:"10px 14px", color:"#6b7280", whiteSpace:"nowrap" }}>{t.duration}</td>
                          <td style={{ padding:"10px 14px", textAlign:"right", fontWeight:700, fontVariantNumeric:"tabular-nums",
                            color: open ? "#9ca3af" : t.gainPct>=0?"#16a34a":"#dc2626"
                          }}>
                            {open ? "—" : `${t.gainPct>=0?"+":""}${t.gainPct.toFixed(2)}%`}
                          </td>
                          <td style={{ padding:"10px 14px", textAlign:"right", fontWeight:600, fontVariantNumeric:"tabular-nums",
                            color: open ? "#9ca3af" : t.gainAmount>=0?"#16a34a":"#dc2626"
                          }}>
                            {open ? "—" : `${t.gainAmount>=0?"+":""}${fmtPrice(t.gainAmount)}`}
                          </td>
                          <td style={{ padding:"10px 14px", textAlign:"right", fontWeight:700, fontVariantNumeric:"tabular-nums",
                            color: open ? "#9ca3af" : t.gainDollar>=0?"#16a34a":"#dc2626"
                          }}>
                            {open ? "—" : `${t.gainDollar>=0?"+":""}$${t.gainDollar.toFixed(2)}`}
                          </td>
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
          </div>
        )}
      </div>
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
  {k:"gainDollar",    l:"PnL ($)",        r:true},
  {k:"result",        l:"Result"},
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

  // Auto-refresh every 20s so an OPEN crossover picks up a live SL/TP hit
  // (fresh candle data) without needing to leave and re-open this page.
  useEffect(() => {
    const id = setInterval(fetchSignals, 20000);
    return () => clearInterval(id);
  }, [fetchSignals]);

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
          {l:"Score",        v:<ScoreBadge v={row?.score}/>},
          {l:"Price",        v:`$${fmtPrice(row?.price)}`},
          {l:"1H Change",    v:<ChgCell v={row?.change_1h ?? 0}/>},
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
  const [capital, setCapital]     = useState(1000);
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
      setTrades(runBacktestFromSignals(candles, dbSignals, rrMode, window, CANDLE_MS_MAP[timeframe], timeframe, capital));
    }
  }, [candles, dbSignals, rrMode, window, timeframe, capital]);

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
        <span style={{ color:"#9ca3af", fontSize:13 }}>Backtest · Last {window} days · IST · EMA 7/25/99 · {timeframe.toUpperCase()} · Next candle open entry</span>
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
        {SHOW_SWING_BUTTON && (
          <button onClick={()=>setRrMode("swing")} title="SL = just past the nearest swing low/high before entry, TP = same distance" style={{
            padding:"4px 14px", borderRadius:6, fontSize:12, fontWeight:600, cursor:"pointer",
            border:rrMode==="swing"?"1.5px solid #f59e0b":"1px solid #e5e7eb",
            background:"transparent", color:rrMode==="swing"?"#f59e0b":"#6b7280",
          }}>Swing SL/TP</button>
        )}
        <span style={{ fontSize:11, color:"#9ca3af", fontWeight:700, letterSpacing:"0.06em", textTransform:"uppercase", marginLeft:8 }}>Capital</span>
        <div style={{ display:"flex", alignItems:"center", gap:4 }}>
          <span style={{ color:"#9ca3af", fontSize:12 }}>$</span>
          <input
            type="number" min="1" step="100" defaultValue={capital}
            onBlur={e => { const v = parseFloat(e.target.value); if (v > 0) setCapital(v); }}
            onKeyDown={e => { if (e.key === "Enter") e.target.blur(); }}
            style={{ width:80, padding:"4px 6px", borderRadius:6, border:"1px solid #e5e7eb", fontSize:12 }}
          />
        </div>
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
        <RRCustomInput value={rrMode} onChange={setRrMode}/>
      </div>

      {/* Stat cards */}
      {!loading && !error && trades && (
        <div style={{ display:"grid", gridTemplateColumns:"repeat(8,1fr)", gap:12, padding:"16px 24px", borderBottom:"1px solid #e8eaed" }}>
          <StatCard label="Total Trades" value={allTradesCount}/>
          <StatCard label="Open"         value={openCount} color="#0891b2"/>
          <StatCard label="Wins"         value={wins}   color="#16a34a"/>
          <StatCard label="Losses"       value={losses} color="#dc2626"/>
          <StatCard label="Win Rate"     value={winRate ? `${winRate}%` : "—"} color="#f59e0b"/>
          <StatCard label="Total P&L"    value={closedCount>0?`${totalPnl>=0?"+":""}${totalPnl.toFixed(2)}%`:"—"} color={totalPnl>=0?"#16a34a":"#dc2626"}/>
          <StatCard label="RR Mode"      value={rrMode === "swing" ? "Swing SL/TP" : rrMode} color="#6366f1"/>
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
                    {/* PnL ($) */}
                    <td style={{ padding:"10px 14px", textAlign:"right", fontWeight:700, fontVariantNumeric:"tabular-nums",
                      color: open ? "#9ca3af" : t.gainDollar>=0?"#16a34a":"#dc2626"
                    }}>
                      {open ? "—" : `${t.gainDollar>=0?"+":""}$${t.gainDollar.toFixed(2)}`}
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

  const goScreenerBacktest = useCallback(() => {
    setPage("screener-backtest");
  }, []);

  // Returning from Details always lands back on the scanner, which remounts
  // ScannerPage and restarts its auto-refresh polling.
  const goBackFromDetails = useCallback(() => {
    setPage("scanner");
  }, []);

  // Returning from Backtest lands wherever it was opened from.
  const goBackFromBacktest = useCallback(() => {
    setPage(backtestFrom);
  }, [backtestFrom]);

  if (page === "backtest")           return <BacktestPage scanRow={scanRow} initialMarket={market} onBack={goBackFromBacktest}/>;
  if (page === "details")            return <DetailsPage row={detailsRow} market={market} onBack={goBackFromDetails} onBacktest={goBacktest}/>;
  if (page === "screener-backtest")  return <ScreenerBacktestPage market={market} onBack={goBackFromDetails}/>;
  return <ScannerPage market={market} setMarket={setMarket} onDetails={goDetails} onBacktest={goBacktest} onScreenerBacktest={goScreenerBacktest}/>;
}
