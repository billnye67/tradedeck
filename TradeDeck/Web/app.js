// TradeDeck charts v4: one chart per traded coin, side by side, each with its own setup pins.
// Live candles from Hyperliquid (one shared websocket), deep history from the local DB via C#.
// scanner.js draws pins through window.tdPins(symbol, stages) and reads window.tdFocus().

const API_URL = "https://api.hyperliquid.xyz/info";
const WS_URL = "wss://api.hyperliquid.xyz/ws";

const intervalMs = { "5m": 5 * 60 * 1000, "15m": 15 * 60 * 1000, "1h": 60 * 60 * 1000 };
const TRADED_DEFAULT = ["BTC", "ETH", "XRP"];

let currentSymbol = "BTC";      // the focused cell (sidebar setup panel follows it)
let currentInterval = "5m";     // kept for scanner.js compatibility (per-cell intervals below)
const cells = {};               // symbol -> cell
let socket = null, socketVersion = 0;

// ------------------------------------------------------------ chart cell
function makeCell(symbol) {
    const row = document.getElementById("chartRow");
    const el = document.createElement("div");
    el.className = "chart-cell";
    el.dataset.symbol = symbol;
    el.innerHTML =
        '<div class="cell-head"><b class="cell-sym">' + symbol + '</b>' +
        '<span class="cell-pnl"></span>' +
        '<span class="cell-stage"></span>' +
        '<span class="cell-tf"><button data-tf="5m" class="active">5m</button><button data-tf="15m">15m</button><button data-tf="1h">1h</button></span></div>' +
        '<div class="cell-chart"></div>';
    row.appendChild(el);
    const host = el.querySelector(".cell-chart");
    const chart = LightweightCharts.createChart(host, {
        width: host.clientWidth, height: host.clientHeight,
        layout: { background: { type: "solid", color: "#080b10" }, textColor: "#7d8794" },
        grid: { vertLines: { color: "#12171e" }, horzLines: { color: "#12171e" } },
        rightPriceScale: { borderColor: "#1b222c" },
        timeScale: { borderColor: "#1b222c", timeVisible: true, secondsVisible: false }
    });
    const series = chart.addCandlestickSeries({
        upColor: "#16b89c", downColor: "#ef4055", borderUpColor: "#16b89c", borderDownColor: "#ef4055",
        wickUpColor: "#16b89c", wickDownColor: "#ef4055"
    });
    const cell = { symbol, el, host, chart, series, interval: "5m", bars: [], lines: [], histDone: false, histLoading: false, lastHist: 0 };
    cells[symbol] = cell;

    new ResizeObserver(() => chart.applyOptions({ width: host.clientWidth, height: host.clientHeight })).observe(host);

    // deep history on left-edge scroll
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        if (!range || !cell.bars.length || cell.histDone || cell.histLoading) return;
        if (range.from > 80) return;
        if ((range.to - range.from) >= cell.bars.length * 0.9) return;
        if (Date.now() - cell.lastHist < 1000) return;
        cell.lastHist = Date.now();
        if (!window.chrome || !window.chrome.webview) return;
        cell.histLoading = true;
        window.chrome.webview.postMessage({ type: "history-request", market: symbol + "USDT", interval: cell.interval, beforeSec: cell.bars[0].time });
    });

    el.querySelectorAll(".cell-tf button").forEach(b => b.addEventListener("click", async () => {
        el.querySelectorAll(".cell-tf button").forEach(x => x.classList.remove("active"));
        b.classList.add("active");
        cell.interval = b.dataset.tf;
        if (symbol === currentSymbol) currentInterval = cell.interval;
        await loadCell(cell);
        resubscribeAll();
    }));
    el.addEventListener("click", () => focusSymbol(symbol));
    return cell;
}

function setCellStage(cell, text, color) {
    const s = cell.el.querySelector(".cell-stage"); if (s) { s.textContent = text || ""; s.style.color = color || ""; }
}
function setCellStatus(cell, text, color) { /* no status words on the header */ }

async function loadCell(cell) {
    setCellStatus(cell, "loading");
    const endTime = Date.now(), startTime = endTime - intervalMs[cell.interval] * 5000;
    const r = await fetch(API_URL, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: "candleSnapshot", req: { coin: cell.symbol, interval: cell.interval, startTime, endTime } }) });
    if (!r.ok) throw new Error("Hyperliquid HTTP " + r.status);
    const candles = await r.json();
    const bars = candles.map(c => ({ time: Math.floor(c.t / 1000), open: +c.o, high: +c.h, low: +c.l, close: +c.c })).sort((a, b) => a.time - b.time);
    const last = bars.length ? bars[bars.length - 1].close : 1;
    const prec = last >= 100 ? 2 : last >= 1 ? 4 : 6;
    cell.series.applyOptions({ priceFormat: { type: "price", precision: prec, minMove: Math.pow(10, -prec) } });
    cell.series.setData(bars);
    cell.bars = bars; cell.histDone = false; cell.histLoading = false;
    cell.chart.priceScale("right").applyOptions({ autoScale: true });
    // keep the live candle in the MIDDLE of the chart: show ~110 bars with the last one at the centre
    cell.chart.timeScale().applyOptions({ rightOffset: 55 });
    cell.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, bars.length - 55), to: bars.length + 55 });
    setTimeout(() => { cell.chart.priceScale("right").applyOptions({ autoScale: true }); cell.chart.timeScale().scrollToRealTime(); }, 50);
    setCellStatus(cell, cell.interval + " live");
    if (window.__tdStageCache && window.__tdStageCache[cell.symbol + "USDT"]) window.tdPins(cell.symbol, window.__tdStageCache[cell.symbol + "USDT"]);
}

// ------------------------------------------------------------ open-trade P&L badge (2026-10-22, the user)
// scanner.js hands us the executor's open list; each cell shows LONG/SHORT · % · $ · R off its own last candle.
const openTrades = {};   // symbol -> { side, fill, qty, stop }
window.tdSetOpen = function (list) {
    for (const k of Object.keys(openTrades)) delete openTrades[k];
    (list || []).forEach(o => { if (o && o.fill) openTrades[String(o.Market).replace("USDT", "")] = { side: o.Side, fill: +o.fill, qty: +(o.qty || 0), stop: +(o.Stop || 0) }; });
    for (const c of Object.values(cells)) {
        refreshPnl(c);
        if (window.__tdStageCache && window.__tdStageCache[c.symbol + "USDT"]) window.tdPins(c.symbol, window.__tdStageCache[c.symbol + "USDT"]);   // re-label IN TRADE when a fill or close lands
    }
};
function pnlOf(symbol) {
    const t = openTrades[symbol], cell = cells[symbol];
    if (!t || !cell || !cell.bars.length) return null;
    const last = cell.bars[cell.bars.length - 1].close, sign = t.side === "SELL" ? -1 : 1, move = (last - t.fill) * sign;
    const risk = Math.abs(t.fill - t.stop) > t.fill * 0.001 ? Math.abs(t.fill - t.stop) : 0;   // 2026-10-27: no stop known (adopted trade) -> no R shown
    return { side: t.side === "SELL" ? "SHORT" : "LONG", pct: move / t.fill * 100, usd: move * t.qty, r: risk > 0 ? move / risk : null };
}
function pnlText(x) {
    const sg = v => (v >= 0 ? "+" : "-");
    return x.side + " " + sg(x.pct) + Math.abs(x.pct).toFixed(2) + "%" + (x.usd ? " · " + sg(x.usd) + "$" + Math.abs(x.usd).toFixed(0) : "") + (x.r != null ? " · " + sg(x.r) + Math.abs(x.r).toFixed(2) + "R" : "");
}
function refreshPnl(cell) {
    const el = cell.el.querySelector(".cell-pnl"); if (!el) return;
    const x = pnlOf(cell.symbol);
    if (!x) { el.textContent = ""; el.className = "cell-pnl"; return; }
    el.textContent = pnlText(x); el.className = "cell-pnl " + (x.pct >= 0 ? "pos" : "neg");
}
window.tdOpenPnlText = function (symbol) { const x = pnlOf(symbol); return x ? " " + (x.pct >= 0 ? "+" : "-") + Math.abs(x.pct).toFixed(2) + "%" : ""; };

// ------------------------------------------------------------ pins (setup drawn on the chart itself)
window.tdPins = function (symbol, m) {
    const cell = cells[symbol]; if (!cell) return;
    for (const l of cell.lines) { try { cell.series.removePriceLine(l); } catch (e) {} }
    cell.lines = [];
    const add = (price, color, style, title) => {
        if (price == null) return;
        cell.lines.push(cell.series.createPriceLine({ price: Number(price), color, lineWidth: 1, lineStyle: style, axisLabelVisible: true, title }));
    };
    if (m && m.openCall) {
        const o = m.openCall;
        // 2026-11-22 (the user: "says IN TRADE LONG but I'm not in a trade"): the scanner's openCall means the chart touched the entry.
        // Say IN TRADE only when an account actually holds this coin; otherwise the setup hit its entry and no account took it.
        const held = !!openTrades[symbol];
        const word = held ? "IN TRADE " + o.direction : "ENTRY HIT " + o.direction + " · NO ACCOUNT IN";
        add(o.entry, "#58a6ff", 0, held ? "IN TRADE " + o.direction : "ENTRY (not taken)");
        add(o.stop, "#f85149", 0, "STOP");
        add(o.tp3, "#2ea043", 0, "TARGET 3R");
        setCellStage(cell, "15m Liquidity Raid → 5m FVG · " + word, held ? "var(--info)" : "var(--muted)");
        return;
    }
    if (!m || !m.active) { setCellStage(cell, ""); return; }
    add(m.level, "#ef4055", 2, "15m LEVEL");
    add(m.sweepWick, "#ef4055", 0, "SWEEP WICK · STOP");
    add(m.triggerPrice, "#d29922", 2, "5m TRIGGER");
    // entry/target lines only when the scanner has actually journaled this setup (WATCH = order resting, FILLED = called);
    // a setup that reached the FVG stage but was never journaled failed a rule (gap too small, breach too small, FVG too old, muted coin)
    const tradeable = !!m.called;
    if (tradeable) {
        add(m.entry, "#2ea043", 2, "ENTRY (FVG)");
        if (m.tp3r != null) add(m.tp3r, "#2ea043", 0, "TARGET 3R");
    } else if (m.fvg) {
        add(m.entry, "#6b7280", 3, "FVG (not a trade)");
    }
    const steps = ["liquidity", "raid", "returned", "trigger", "displacement", "fvg", "retrace"].filter(k => m[k]).length;
    const tag = tradeable ? (m.retrace ? " · CALLED" : " · ORDER RESTING") : (m.fvg ? " · NOT A TRADE (fails a size/age rule)" : "");
    setCellStage(cell, "15m Liquidity Raid → 5m FVG · " + m.direction + " " + steps + "/7" + tag, tradeable ? (m.retrace ? "var(--up)" : "var(--warn)") : "");
};

// ------------------------------------------------------------ one shared live socket
function resubscribeAll() {
    socketVersion++;
    const myVersion = socketVersion;
    if (socket) { try { socket.close(); } catch (e) {} }
    socket = new WebSocket(WS_URL);
    socket.onopen = () => {
        if (myVersion !== socketVersion) return;
        for (const c of Object.values(cells))
            socket.send(JSON.stringify({ method: "subscribe", subscription: { type: "candle", coin: c.symbol, interval: c.interval } }));
        setConnection("LIVE", true);
    };
    socket.onmessage = ev => {
        if (myVersion !== socketVersion) return;
        const msg = JSON.parse(ev.data); if (msg.channel !== "candle") return;
        const k = msg.data; const cell = cells[k.s]; if (!cell || k.i !== cell.interval) return;
        const bar = { time: Math.floor(k.t / 1000), open: +k.o, high: +k.h, low: +k.l, close: +k.c };
        cell.series.update(bar);
        refreshPnl(cell);
        const lastBar = cell.bars[cell.bars.length - 1];
        if (lastBar && lastBar.time === bar.time) cell.bars[cell.bars.length - 1] = bar; else if (!lastBar || bar.time > lastBar.time) cell.bars.push(bar);
    };
    socket.onerror = () => { if (myVersion === socketVersion) setConnection("ERROR", false); };
    socket.onclose = () => { if (myVersion !== socketVersion) return; setConnection("RECONNECTING", false); setTimeout(() => { if (myVersion === socketVersion) resubscribeAll(); }, 2000); };
}

// deep-history replies routed to the right cell
window.chrome && window.chrome.webview && window.chrome.webview.addEventListener("message", ev => {
    const m = ev.data; if (!m || m.type !== "history-result") return;
    const cell = cells[(m.market || "").replace("USDT", "")]; if (!cell) return;
    cell.histLoading = false;
    if (m.interval !== cell.interval) return;
    if (!m.bars || !m.bars.length) { cell.histDone = true; return; }
    const first = cell.bars[0].time, older = m.bars.filter(b => b.time < first);
    if (!older.length) { cell.histDone = true; return; }
    const merged = older.concat(cell.bars);
    const vr = cell.chart.timeScale().getVisibleLogicalRange();
    cell.series.setData(merged); cell.bars = merged;
    if (vr) cell.chart.timeScale().setVisibleLogicalRange({ from: vr.from + older.length, to: vr.to + older.length });
});

// ------------------------------------------------------------ focus / market tiles
function setConnection(text, connected) {
    document.getElementById("connectionText").textContent = text;
    const dot = document.getElementById("connectionDot");
    dot.style.background = connected ? "#36d67e" : "#59616d"; dot.style.boxShadow = connected ? "0 0 8px #36d67e" : "none";
}
function setDataStatus(text) { const e = document.getElementById("dataStatus"); if (e) e.textContent = text; }

function focusSymbol(symbol) {
    currentSymbol = symbol;
    if (cells[symbol]) currentInterval = cells[symbol].interval;
    document.querySelectorAll(".chart-cell").forEach(c => c.classList.toggle("focused", c.dataset.symbol === symbol));
    document.querySelectorAll(".market").forEach(t => t.classList.toggle("active", t.dataset.symbol === symbol));
    const t1 = document.getElementById("symbolTitle"); if (t1) t1.textContent = symbol;
    const t2 = document.getElementById("selectedMarket"); if (t2) t2.textContent = symbol + " selected";
    if (window.tdRenderStages) window.tdRenderStages(symbol);
}
window.tdFocus = () => currentSymbol;

// the bot tells us which coins it trades; rebuild the row if that set changes
window.tdSetTraded = async function (list) {
    const want = list.filter(Boolean);
    const have = Object.keys(cells).filter(k => !cells[k].el.classList.contains("look"));
    if (want.length === have.length && want.every(s => have.includes(s))) return;
    for (const k of Object.keys(cells)) { try { cells[k].chart.remove(); } catch (e) {} cells[k].el.remove(); delete cells[k]; }
    for (const s of want) makeCell(s);
    for (const c of Object.values(cells)) { try { await loadCell(c); } catch (e) { setCellStatus(c, "error", "var(--down)"); } }
    resubscribeAll();
    focusSymbol(want[0]);
};

// clicking a market that has no chart swaps it into the LAST cell (a look, not a trade)
async function changeMarket(symbol) {
    if (!cells[symbol]) {
        const keys = Object.keys(cells); const victim = cells[keys[keys.length - 1]];
        if (victim) { try { victim.chart.remove(); } catch (e) {} victim.el.remove(); delete cells[victim.symbol]; }
        const c = makeCell(symbol); c.el.classList.add("look");
        try { await loadCell(c); } catch (e) { setCellStatus(c, "error", "var(--down)"); }
        resubscribeAll();
    }
    focusSymbol(symbol);
}
window.changeMarket = changeMarket;

document.querySelectorAll(".market").forEach(t => t.addEventListener("click", () => changeMarket(t.dataset.symbol)));

// ------------------------------------------------------------ boot
(async function boot() {
    const traded = (window.__tdTraded || TRADED_DEFAULT);
    for (const s of traded) makeCell(s);
    for (const c of Object.values(cells)) { try { await loadCell(c); } catch (e) { console.error(e); setCellStatus(c, "error", "var(--down)"); } }
    resubscribeAll();
    focusSymbol(traded[0]);
    setDataStatus("Data: " + traded.join(" · ") + " live");
})();


// splitter: drag to give the charts or the tables more room (remembered)
(function () {
    const app = document.querySelector(".app"), sp = document.getElementById("splitter"); if (!app || !sp) return;
    const saved = parseFloat(localStorage.getItem("td-chart-h")); if (saved) app.style.setProperty("--chartH", saved + "px");
    let drag = null;
    sp.addEventListener("mousedown", e => { drag = { y: e.clientY, h: document.getElementById("chartRow").getBoundingClientRect().height }; e.preventDefault(); });
    window.addEventListener("mousemove", e => { if (!drag) return; const h = Math.max(160, Math.min(window.innerHeight - 220, drag.h + (e.clientY - drag.y))); app.style.setProperty("--chartH", h + "px"); });
    window.addEventListener("mouseup", () => { if (drag) { localStorage.setItem("td-chart-h", parseFloat(getComputedStyle(app).getPropertyValue("--chartH"))); drag = null; } });
})();
