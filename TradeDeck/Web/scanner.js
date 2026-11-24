// TradeDeck live scanner UI layer. Additive: injects a toggle into
// the topbar and an alert drawer; listens to scanner messages from
// the C# ScannerService. Does not touch app.js.

(function () {
    // Designed layout (index.html v3) provides every control; this
    // script only binds behavior. No DOM injection, no inline CSS.

    const saved = JSON.parse(localStorage.getItem("td-risk") || "{}");
    if (saved.acct) document.getElementById("acctSize").value = saved.acct;
    if (saved.pct) document.getElementById("riskPct").value = saved.pct;
    if (saved.cap) document.getElementById("dayCap").value = saved.cap;
    if (saved.open) document.getElementById("maxOpen").value = saved.open;

    const drawer = document.getElementById("alertFeed");
    const countEl = document.getElementById("alertCount");
    let alertCount = 0;
    let running = false;

    function clearEmpty() {
        const e = drawer.querySelector(".feed-empty");
        if (e) e.remove();
    }

    // every tile re-renders the setup panel on click (app.js swaps the chart)
    document.querySelectorAll(".market").forEach(tile => {
        tile.addEventListener("click", () =>
            setTimeout(() => renderStages(tile.dataset.symbol), 50));
    });

    // ---- live setup-panel wiring ----
    const stageCache = {};

    // setup pins are drawn on each coin's own chart by app.js (window.tdPins)
    function drawSetupOnChart(m) {
        if (window.tdPins) window.tdPins(currentSymbol, m);
    }
    window.__tdStageCache = stageCache;

    function setRow(id, on) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = on ? "Done" : "Waiting";
        el.className = on ? "" : "waiting";
        el.style.color = "";
    }

    function setVal(id, v, digits) {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = v == null ? "—"
            : Number(v).toPrecision(digits || 6);
    }

    function renderStages(sym) {
        const m = stageCache[sym + "USDT"];
        const dir = document.getElementById("setupDirection");

        drawSetupOnChart(m);

        if (m && m.openCall) {
            if (dir) dir.textContent = "In trade · " + m.openCall.direction.toLowerCase();
            ["stateLiquidity","stateRaid","stateReturn","stateTrigger",
             "stateDisplacement","stateFvg","stateRetrace"].forEach(id => {
                const el = document.getElementById(id);
                if (el) { el.textContent = "Position open"; el.style.color = "var(--info)"; el.className = ""; }
            });
            setVal("entryPrice", m.openCall.entry);
            setVal("stopPrice", m.openCall.stop);
            setVal("tp1Price", null);
            setVal("tp2Price", m.openCall.tp3);
            const rr0 = document.getElementById("rrValue"); if (rr0) rr0.textContent = "1:3";
            return;
        }

        if (!m || !m.active) {
            for (const id of ["stateLiquidity","stateRaid","stateReturn",
                "stateTrigger","stateDisplacement","stateFvg","stateRetrace"])
                setRow(id, false);
            for (const id of ["entryPrice","stopPrice","tp1Price","tp2Price"])
                setVal(id, null);
            const sz = document.getElementById("positionSize");
            if (sz) sz.textContent = "—";
            const rr = document.getElementById("rrValue");
            if (rr) rr.textContent = "—";
            if (dir) dir.textContent = "—";
            return;
        }

        if (dir) dir.textContent = m.called
            ? m.direction + " · CALLED · " + (m.callResult || "")
            : m.direction;
        setRow("stateLiquidity", m.liquidity);
        setRow("stateRaid", m.raid);
        setRow("stateReturn", m.returned);

        // trigger row: identified != broken. FOUND (yellow) until
        // displacement closes through it, then DONE (green).
        const trigEl = document.getElementById("stateTrigger");
        if (trigEl) {
            if (m.displacement) {
                trigEl.textContent = "Done";
                trigEl.style.color = "";
            } else if (m.trigger) {
                trigEl.textContent = "Found";
                trigEl.style.color = "var(--warn)";
            } else {
                trigEl.textContent = "Waiting";
                trigEl.style.color = "";
            }
            trigEl.className = m.trigger ? "" : "waiting";
        }

        setRow("stateDisplacement", m.displacement);
        setRow("stateFvg", m.fvg);
        setRow("stateRetrace", m.retrace);
        setVal("entryPrice", m.entry);
        setVal("stopPrice", m.stop);
        setVal("tp1Price", m.tp2r);
        setVal("tp2Price", m.tp3r);
        const rr = document.getElementById("rrValue");
        if (rr) rr.textContent = m.entry != null ? "1:3" : "—";
        const sz = document.getElementById("positionSize");
        if (sz) sz.textContent =
            m.units != null ? Number(m.units).toFixed(4) : "—";
    }

    window.tdRenderStages = renderStages;

    // ---- auto-start the scanner on launch ----
    setTimeout(() => {
        if (!running) window.chrome.webview.postMessage(riskConfig());
    }, 2000);

    // speaker icon, far right of the strip: click to mute/unmute voice alerts
    const vb = document.getElementById("voiceBtn");
    if (vb) vb.onclick = () => {
        const muted = vb.textContent === "🔊";
        vb.textContent = muted ? "🔇" : "🔊";
        window.chrome.webview.postMessage({ type: "voice-toggle", muted });
    };

    // replay recent journal into the drawer (rebuilds no longer erase evidence)
    setTimeout(() =>
        window.chrome.webview.postMessage({ type: "replay-request" }), 1500);

    // ---- lower panes: Record / Trades / Alerts live inline under the charts ----
    function showTab(name) {
        document.querySelectorAll(".lower-tabs .tab").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
        document.querySelectorAll(".lower-pane").forEach(p => p.classList.toggle("hidden", p.id !== "pane-" + name));
    }
    document.querySelectorAll(".lower-tabs .tab").forEach(b => b.addEventListener("click", () => {
        showTab(b.dataset.tab);
        if (b.dataset.tab === "record") window.chrome.webview.postMessage({ type: "record-request" });
        if (b.dataset.tab === "trades" || b.dataset.tab === "strategies") window.chrome.webview.postMessage({ type: "exec-record-request" });
        if (b.dataset.tab === "data") document.getElementById("dataBtn").onclick();
        if (b.dataset.tab === "news") document.getElementById("newsBtn").onclick();
        if (b.dataset.tab === "engines" && window.tdEnginesLoad) window.tdEnginesLoad();   // 2026-10-29: Engines tab (engines.js)
    }));
    document.getElementById("recordBtn").onclick = () => {
        showTab("record");
        document.getElementById("recordSummary").textContent = "Grading…";
        window.chrome.webview.postMessage({ type: "record-request" });
    };
    setTimeout(() => window.chrome.webview.postMessage({ type: "exec-record-request" }), 3500);
    // keep the visible pane fresh
    setInterval(() => {
        if (!document.getElementById("pane-record").classList.contains("hidden")) window.chrome.webview.postMessage({ type: "record-request" });
        if (!document.getElementById("pane-trades").classList.contains("hidden") || !document.getElementById("pane-strategies").classList.contains("hidden")) window.chrome.webview.postMessage({ type: "exec-record-request" });
    }, 60000);
    setTimeout(() => window.chrome.webview.postMessage({ type: "record-request" }), 3000);

    // ---- DATA overlay: what the machine is collecting, in plain language ----
    const dat = document.createElement("div");
    dat.id = "dataOverlay";
    dat.className = "pane-inline";
    dat.innerHTML =
        '<div class="overlay-head"><b>Data — what the machine is collecting and why</b>' +
        '<button id="dataClose" class="ghost" style="display:none">Close</button></div>' +
        '<div id="dataCards" style="overflow-y:auto;flex:1;"></div>';
    document.getElementById("pane-data").appendChild(dat);

    const recordBtnEl = document.getElementById("recordBtn");
    recordBtnEl.insertAdjacentHTML("afterend",
        '<button id="dataBtn" class="' + recordBtnEl.className + '">Data</button>');

    document.getElementById("dataBtn").onclick = () => {
        dat.classList.add("open");
        document.getElementById("dataCards").innerHTML =
            '<div class="dim" style="padding:20px;">Counting…</div>';
        window.chrome.webview.postMessage({ type: "data-request" });
    };
    document.getElementById("dataClose").onclick = () => dat.classList.remove("open");

    // ---- NEWS overlay: the intel feed, readable ----
    const nws = document.createElement("div");
    nws.id = "newsOverlay";
    nws.className = "pane-inline";
    nws.innerHTML =
        '<div class="overlay-head"><b>News — exchange announcements, straight from the source</b>' +
        '<button id="newsClose" class="ghost" style="display:none">Close</button></div>' +
        '<div id="newsList" style="overflow-y:auto;flex:1;"></div>';
    document.getElementById("pane-news").appendChild(nws);

    const dataBtnEl = document.getElementById("dataBtn");
    dataBtnEl.insertAdjacentHTML("afterend",
        '<button id="newsBtn" class="' + dataBtnEl.className + '">News</button>');

    document.getElementById("newsBtn").onclick = () => {
        nws.classList.add("open");
        document.getElementById("newsList").innerHTML =
            '<div class="dim" style="padding:20px;">Loading…</div>';
        window.chrome.webview.postMessage({ type: "coverage-request" });
        window.chrome.webview.postMessage({ type: "news-request" });
    };
    document.getElementById("newsClose").onclick = () => nws.classList.remove("open");

    const btn = document.getElementById("scannerBtn");

    function riskConfig() {
        const acct = parseFloat(document.getElementById("acctSize").value) || 10000;
        const pct = parseFloat(document.getElementById("riskPct").value) || 1;
        const cap = parseFloat(document.getElementById("dayCap").value) || 3;
        const open = parseInt(document.getElementById("maxOpen").value) || 2;
        localStorage.setItem("td-risk", JSON.stringify({ acct, pct, cap, open }));
        const risk = acct * pct / 100;
        document.getElementById("scannerRisk").value = risk;
        return { type: "scanner-start", risk, dailyCapR: cap, maxOpen: open };
    }

    btn.addEventListener("click", () => {
        window.chrome.webview.postMessage(
            running ? { type: "scanner-stop" } : riskConfig());
    });

    ["acctSize", "riskPct", "dayCap", "maxOpen"].forEach(id =>
        document.getElementById(id).addEventListener("change", () => {
            if (running) {
                window.chrome.webview.postMessage({ type: "scanner-stop" });
                setTimeout(() => window.chrome.webview.postMessage(riskConfig()), 400);
            }
        }));

    // click any alert card -> jump to that market's chart
    function attachJump(card, marketUsdt) {
        card.style.cursor = "pointer";
        card.title = "Click to open chart";
        card.addEventListener("click", async () => {
            const sym = marketUsdt.replace("USDT", "");
            document.querySelectorAll(".market").forEach(t => {
                t.classList.toggle("active", t.dataset.symbol === sym);
            });
            await changeMarket(sym);
            renderStages(sym);
        });
    }

    function beep() {
        try {
            const ctx = new (window.AudioContext ||
                window.webkitAudioContext)();
            const osc = ctx.createOscillator();
            osc.frequency.value = 880;
            osc.connect(ctx.destination);
            osc.start();
            setTimeout(() => { osc.stop(); ctx.close(); }, 250);
        } catch (e) { /* ignore */ }
    }

    function setMarketStatus(market, text) {
        const short = market.replace("USDT", "");
        const el = document.querySelector(
            '.market[data-symbol="' + short + '"] .market-status');
        if (el) el.textContent = text;
    }

    window.chrome.webview.addEventListener("message", (ev) => {
        const m = ev.data;
        if (!m || !m.type) return;

        if (m.type === "scanner-state") {
            running = m.running;
            btn.textContent = running ? "Scanner on" : "Scanner off";
            btn.classList.toggle("on", running);
            return;
        }

        if (m.type === "scanner-heartbeat") {
            // 2026-10-29 (the user: "why does it say Scanner off?"): after F5 the page starts at "off" and the app's Start() returns early
            // when the scanner is already running, so no scanner-state arrives. A heartbeat only comes from a running scanner.
            if (!running) { running = true; btn.textContent = "Scanner on"; btn.classList.add("on"); }
            return;
        }

        if (m.type === "exec-alert") {
            // 2026-10-25 review #3: the EMERGENCY-CLOSE-FAILED alarm now has a place on the strip (stays until clicked)
            const bar = document.getElementById("asAlert");
            if (bar) { bar.textContent = "\u26A0 " + (m.account ? m.account + " " : "") + m.text; bar.classList.remove("hidden"); }
            return;
        }

        if (m.type === "exec-state") {
            // 2026-10-23 (the user): one account line for every live account, numbers added up
            // 2026-10-25 (the user): click ACCOUNT for a dropdown with each account's equity, P&L, limits, risk and open trades; only LIVE accounts are summed
            const $ = id => document.getElementById(id);
            const money = v => (v < 0 ? "-$" : "$") + Math.abs(v || 0).toLocaleString(undefined, { maximumFractionDigits: 0 });
            const signed = (el, v) => { el.textContent = money(v); el.classList.toggle("pos", v > 0); el.classList.toggle("neg", v < 0); };
            const cls = v => v > 0 ? "pos" : v < 0 ? "neg" : "";
            window.__tdAccts = window.__tdAccts || {};
            window.__tdAccts[m.slot || 1] = m;
            if (!window.__tdDropWired) {
                window.__tdDropWired = true;
                const tog = () => {
                    const d = $("asDrop"), a = $("asAcct"); if (!d) return;
                    if (a) { const r = a.getBoundingClientRect(); d.style.top = (r.bottom + 6) + "px"; d.style.left = Math.max(8, r.left - 60) + "px"; }
                    d.classList.toggle("hidden");
                };
                // 2026-10-27 (the user: "the dropdown does not work"): the whole ACCOUNT item toggles it, not just the small account text
                const item = $("asAcct") && $("asAcct").closest(".as-item");
                if (item) { item.id = "asAcctItem"; item.style.cursor = "pointer"; item.addEventListener("click", e => { e.stopPropagation(); tog(); }); }
                else { if ($("asAcct")) $("asAcct").addEventListener("click", tog); if ($("asCaret")) $("asCaret").addEventListener("click", tog); }
                if ($("asAlert")) $("asAlert").addEventListener("click", () => $("asAlert").classList.add("hidden"));
                document.addEventListener("click", e => { const d = $("asDrop"); if (d && !d.classList.contains("hidden") && !e.target.closest("#asDrop,#asAcctItem,#asAcct,#asCaret")) d.classList.add("hidden"); });
            }
            const all = Object.keys(window.__tdAccts).sort().map(k => window.__tdAccts[k]);
            const shown = all.filter(a => a.mode && a.mode !== "off" && a.equity != null);   // live + dry: listed in the dropdown
            const live = shown.filter(a => a.mode === "live");                                // only real money is added up
            const first = live[0] || shown[0] || m;
            if (first.markets && window.tdSetTraded) window.tdSetTraded(first.markets.split(",").map(x => x.replace("USDT", "")));
            const names = shown.map(a => a.account).filter(Boolean).join(" + ");
            $("asAcct").textContent = (shown.length > 1 ? shown.length + " accounts" : (names || first.account || "not connected")) + (first.markets ? " \u00B7 " + first.markets.replace(/USDT/g, "") : "");
            const md = $("asMode"); const bad = all.find(a => a.status && /error|HALT/i.test(a.status));
            const dry = shown.length - live.length;
            md.textContent = (live.length ? "LIVE" : (first.mode || "off").toUpperCase()) + (live.length && dry ? " +" + dry + " DRY" : "") + (bad ? " \u00B7 " + (bad.account ? bad.account + " " : "") + bad.status : "");
            md.className = "as-item " + (live.length ? "live" : (first.mode || "off"));
            const a2 = $("asAcct2"); if (a2) a2.textContent = "";
            // 2026-10-28 (the user): an order the broker has not filled yet says so, instead of looking like a trade
            const openTxt = a => (a.open || []).map(o => o.Market.replace("USDT", "") + " " + o.Side + (o.fill ? " @" + o.fill + (window.tdOpenPnlText ? window.tdOpenPnlText(o.Market.replace("USDT", "")) : "") : " order waiting, not filled")).join(", ");
            const same = f => live.every(a => a[f] === live[0][f]);
            if (shown.length) {
                const counted = live.length ? live : shown;   // 2026-10-27 (the user): dry accounts are NOT real money; totals add live accounts only (dry shows in the dropdown)
                const sum = f => counted.reduce((t, a) => t + (a[f] || 0), 0);
                $("asEquity").textContent = money(sum("equity"));
                signed($("asToday"), sum("todayPL")); $("asTodayLim").textContent = live.length > 1 && !(same("dailyStop") && same("dailyLimit")) ? "(limits per account \u25BE)" : "(stop -" + money(first.dailyStop) + " \u00B7 limit -" + money(first.dailyLimit) + (live.length > 1 ? " each" : "") + ")";
                signed($("asTotal"), sum("totalPL")); $("asTotalLim").textContent = live.length > 1 && !(same("floorStop") && same("floorLimit")) ? "(limits per account \u25BE)" : "(stop -" + money(first.floorStop) + " \u00B7 limit -" + money(first.floorLimit) + (live.length > 1 ? " each" : "") + ")";
                $("asRisk").textContent = shown.length > 1 ? shown.map(a => money(a.riskDollars) + (a.mode === "dry" ? " dry" : "")).join(" + ") + " (" + first.riskPct + "% each, per account)" : money(first.riskDollars) + " (" + first.riskPct + "%)";
                const opens = first.open || [];
                $("asOpen").textContent = opens.length + "/" + first.maxOpen + (live.length > 1 ? " each" : "") + (opens.length ? " \u00B7 " + openTxt(first) : "");
                // 2026-10-27 (the user): the chart badge adds the same trade across every LIVE account (copy trading): qty summed, fill qty-weighted
                if (window.tdSetOpen) {
                    const comb = {};
                    for (const acc of counted) for (const o of (acc.open || [])) {
                        if (!o || !o.fill) continue;
                        const k = o.Market + "|" + o.Side, q = +(o.qty || 0), c = comb[k] || (comb[k] = { Market: o.Market, Side: o.Side, qty: 0, fillQ: 0, Stop: o.Stop });
                        c.qty += q; c.fillQ += q * +o.fill;
                    }
                    window.tdSetOpen(Object.values(comb).map(c => ({ Market: c.Market, Side: c.Side, qty: c.qty, fill: c.qty ? c.fillQ / c.qty : 0, Stop: c.Stop })));
                }
                if (first.mode === "live" || first.mode === "dry") {
                    const a = $("acctSize"), r = $("riskPct");
                    if (a && Math.abs(parseFloat(a.value) - first.equity) > 1) a.value = Math.round(first.equity);
                    if (r && parseFloat(r.value) !== first.riskPct) r.value = first.riskPct;
                }
            }
            const dd = $("asDrop");
            if (dd) dd.innerHTML = shown.map(a =>
                '<div class="as-row ' + a.mode + '">' +
                '<b>' + (a.account || ("slot " + a.slot)) + '</b><span class="as-tag ' + a.mode + '">' + a.mode.toUpperCase() + '</span>' +
                '<span>Equity <b>' + money(a.equity) + '</b></span>' +
                '<span>Today <b class="' + cls(a.todayPL) + '">' + money(a.todayPL) + '</b><small>(stop -' + money(a.dailyStop) + ' \u00B7 limit -' + money(a.dailyLimit) + ')</small></span>' +
                '<span>Total <b class="' + cls(a.totalPL) + '">' + money(a.totalPL) + '</b><small>(stop -' + money(a.floorStop) + ' \u00B7 limit -' + money(a.floorLimit) + ')</small></span>' +
                '<span>Risk <b>' + money(a.riskDollars) + '</b><small>(' + a.riskPct + '%)</small></span>' +
                '<span>Open <b>' + (a.open || []).length + '/' + a.maxOpen + '</b>' + ((a.open || []).length ? '<small>' + openTxt(a) + '</small>' : '') + '</span>' +
                (a.status && /error|HALT/i.test(a.status) ? '<span class="neg">' + a.status.replace(/v2 filter ON/i, "momentum engine ON") + '</span>' : '') +
                '<span class="as-strat">15m raid → 5m FVG' + (/v2 filter ON/i.test(a.status || "") ? ' + momentum filter' : '') + '</span>' +   // 2026-10-29 (the user): strategy at the far right of the row
                '</div>').join("") || '<div class="as-row off">not connected</div>';
            return;
        }

        if (m.type === "exec-record") {
            const t = document.getElementById("tradesTable");
            const head = "<tr><th>time (UTC)</th><th>event</th><th>strategy</th><th>coin</th><th>fill</th><th>stop</th><th>target</th><th>exit</th><th>how</th><th>R</th><th>$</th><th>qty</th></tr>";
            const sname = id => ((m.strategies || []).find(x => x.id === id) || {}).name || id || "";
            // 2026-10-19: a row with a fill and a stop can open the chart popup (same playout the old Record tab used)
            // a "closed" row has no stop of its own: borrow it from the "protected" row of the same market + fill
            const stopFor = r => r.stop ?? (((m.rows || []).find(x => x.ev === "protected" && x.market === r.market && String(x.fill) === String(r.fill)) || {}).stop);
            // the fill time is the "protected" row's time; a closed row's own time is the exit
            const fillUtcFor = r => r.ev === "protected" ? r.utc : (((m.rows || []).find(x => x.ev === "protected" && x.market === r.market && String(x.fill) === String(r.fill)) || {}).utc || r.utc);
            const canPlay = r => (r.ev === "protected" || r.ev === "closed") && r.fill != null && stopFor(r) != null && r.utc;
            const rowHtml = r =>
                "<tr" + (canPlay(r) ? " class='play-row' title='Click to see this trade on the chart' data-market='" + r.market + "' data-utc='" + fillUtcFor(r) + "' data-fill='" + r.fill + "' data-stop='" + stopFor(r) + "'" : "") + "><td>" + (r.utc || "").replace("T", " ") + "</td><td>" + r.ev + "</td><td>" + sname(r.strategy) + "</td><td>" + (r.market || "").replace("USDT", "") + "</td><td>" + (r.fill ?? "") +
                "</td><td>" + (r.stop ?? "") + "</td><td>" + (r.target ?? "") + "</td><td>" + (r.exit ?? "") + "</td><td>" + (r.how ?? "") +
                "</td><td class='" + (r.r > 0 ? "win" : r.r < 0 ? "loss" : "") + "'>" + (r.r ?? "") + "</td><td class='" + (r.pnl > 0 ? "win" : r.pnl < 0 ? "loss" : "") + "'>" + (r.pnl != null ? "$" + r.pnl : "") + "</td><td>" + (r.qty ?? "") + "</td></tr>";
            t.innerHTML = head + (m.rows || []).map(rowHtml).join("");
            const wirePlayout = table => table.querySelectorAll(".play-row").forEach(tr => {
                tr.style.cursor = "pointer";
                tr.onclick = () => {
                    const fill = Number(tr.dataset.fill), stop = Number(tr.dataset.stop);
                    const epochMs = Date.parse(tr.dataset.utc + ":00Z");   // the fill minute: the chart marks E on the first candle from here and shows 6h before it
                    if (!isFinite(epochMs) || !fill || !stop) return;
                    window.chrome.webview.postMessage({
                        type: "record-open", market: tr.dataset.market, epochMs,
                        direction: stop > fill ? "SHORT" : "LONG", entry: fill, stop, riskPerUnit: Math.abs(fill - stop)
                    });
                };
            });
            wirePlayout(t);
            // Strategies tab: one line per strategy, click for its trades
            window.__execRows = m.rows || []; window.__execStrats = m.strategies || [];
            const st = document.getElementById("stratTable");
            st.innerHTML = "<tr><th>strategy</th><th>trades</th><th>wins</th><th>losses</th><th>win %</th><th>R</th><th>P&amp;L</th><th>open</th></tr>" +
                (m.strategies || []).map(x => "<tr class='strat-row" + (window.__stratPick === x.id ? " active" : "") + "' data-id='" + x.id + "'><td>" + x.name + "</td><td>" + x.Trades + "</td><td>" + x.Wins + "</td><td>" + x.Losses +
                    "</td><td>" + (x.Trades ? Math.round(100 * x.Wins / x.Trades) + "%" : "\u2014") + "</td><td class='" + (x.r > 0 ? "win" : x.r < 0 ? "loss" : "") + "'>" + (x.r > 0 ? "+" : "") + x.r +
                    "R</td><td class='" + (x.pnl > 0 ? "win" : x.pnl < 0 ? "loss" : "") + "'>" + (x.pnl < 0 ? "-" : "") + "$" + Math.abs(x.pnl).toLocaleString() + "</td><td>" + x.Open + "</td></tr>").join("");
            const showStrat = id => {
                window.__stratPick = id;
                st.querySelectorAll(".strat-row").forEach(tr => tr.classList.toggle("active", tr.dataset.id === id));
                const rows = window.__execRows.filter(r => (r.strategy || "raid-fvg") === id);
                document.getElementById("stratTrades").innerHTML = rows.length ? head + rows.map(rowHtml).join("") : "<tr><td class='dim'>no trades yet for " + sname(id) + "</td></tr>";
                wirePlayout(document.getElementById("stratTrades"));
            };
            st.querySelectorAll(".strat-row").forEach(tr => tr.onclick = () => showStrat(tr.dataset.id));
            showStrat(window.__stratPick || ((m.strategies || [])[0] || {}).id);
            // 2026-11-20 (the user): equity/start dropped from this line (redundant with the top bar, and it showed one account only); Rows | Calendar switch + events count
            window.__calRows = (m.rows || []).filter(r => r.ev === "closed" && r.utc);
            document.getElementById("tradesEvents").textContent = (m.rows || []).length + " events";
            renderTradesCal();
            return;
        }

        if (m.type === "alert-history") {
            for (const h of [...m.rows].reverse()) {
                clearEmpty();
                const card = document.createElement("div");
                card.className =
                    "scanner-card history" + (h.status === "FILLED" ? " filled" : "");
                card.innerHTML =
                    "<h4>" + h.status + ": " + h.market + " " + h.direction +
                    ' <span class="dim">' + h.utc + " UTC</span></h4>" +
                    '<div class="row"><span class="dim">ENTRY</span><b>' +
                        h.entry + "</b></div>" +
                    '<div class="row"><span class="dim">STOP</span><b>' +
                        h.stop + "</b></div>";
                attachJump(card, h.market);
                drawer.appendChild(card);
            }
            alertCount = m.rows.length;
            countEl.textContent = alertCount;
            return;
        }

        if (m.type === "record-playout") {
            let po = document.getElementById("playoutOverlay");
            if (!po) {
                po = document.createElement("div");
                po.id = "playoutOverlay";
                po.className = "overlay";
                po.style.zIndex = "90";
                po.innerHTML =
                    '<div class="overlay-head"><b id="playoutTitle"></b>' +
                    '<button id="playoutClose" class="ghost">Close</button></div>' +
                    '<canvas id="playoutCanvas"></canvas>';
                document.body.appendChild(po);
                document.getElementById("playoutClose").onclick =
                    () => po.classList.remove("open");
            }
            po.classList.add("open");
            document.getElementById("playoutTitle").textContent =
                m.title + "   entry " + m.entry + "  stop " + m.stop +
                "  3R " + Number(m.tp3).toPrecision(6);

            const cv = document.getElementById("playoutCanvas");
            cv.width = cv.clientWidth || 1200;
            cv.height = cv.clientHeight || 520;
            const x = cv.getContext("2d");
            const C = m.candles, n = C.length;
            let hi = -1e18, lo = 1e18;
            for (const k of C) { if (k[2] > hi) hi = k[2]; if (k[3] < lo) lo = k[3]; }
            for (const p of [m.entry, m.stop, m.tp3]) { if (p > hi) hi = p; if (p < lo) lo = p; }
            const pad = (hi - lo) * 0.08; hi += pad; lo -= pad;
            const L = 8, R = 100, T = 8, B = 8, W = cv.width - L - R, H = cv.height - T - B;
            const X = i => L + W * (i + 0.5) / n, Y = p => T + H * (hi - p) / (hi - lo);
            const cw = Math.max(2, Math.floor(W / n * 0.6));
            x.clearRect(0, 0, cv.width, cv.height);
            if (m.entryRel >= 0) {
                x.fillStyle = "rgba(210,153,34,.10)";
                x.fillRect(X(m.entryRel), T, L + W - X(m.entryRel), H);
            }
            for (let i = 0; i < n; i++) {
                const [, o, h, l, c] = C[i]; const up = c >= o;
                x.strokeStyle = x.fillStyle = up ? "#2ea043" : "#f85149";
                x.beginPath(); x.moveTo(X(i), Y(h)); x.lineTo(X(i), Y(l)); x.stroke();
                x.fillRect(X(i) - cw / 2, Math.min(Y(o), Y(c)), cw, Math.max(1, Math.abs(Y(c) - Y(o))));
            }
            function hl(p, col, dash, lab) {
                x.strokeStyle = col; x.setLineDash(dash); x.beginPath();
                x.moveTo(L, Y(p)); x.lineTo(L + W, Y(p)); x.stroke(); x.setLineDash([]);
                x.fillStyle = col; x.font = "12px Consolas";
                x.fillText(lab + " " + Number(p).toPrecision(6), L + W + 4, Y(p) + 4);
            }
            hl(m.entry, "#2ea043", [2, 3], "ENTRY");
            hl(m.stop, "#f85149", [], "STOP");
            hl(m.tp3, "#2ea043", [6, 4], "3R");
            if (m.entryRel >= 0) {
                x.fillStyle = "#e6edf3"; x.font = "bold 12px Consolas";
                x.fillText("E", X(m.entryRel) - 4, T + 14);
            }
            return;
        }

        if (m.type === "data-data") {
            const host = document.getElementById("dataCards");
            host.innerHTML = "";
            const grid = document.createElement("div");
            grid.className = "data-grid";
            (m.cards || []).forEach(c => {
                const el = document.createElement("div");
                el.className = "data-card";
                el.title = c.what + "\n\nFeeds: " + c.feeds;   // hover = full story
                el.innerHTML =
                    "<h3>" + c.title + "</h3>" +
                    "<div class='data-hero'>" + (c.read || c.receipt) + "</div>" +
                    (c.read ? "<div class='data-sub'>" + c.receipt + "</div>" : "") +
                    "<div class='data-feeds'>" + c.feeds + "</div>";
                grid.appendChild(el);
            });
            host.appendChild(grid);
            return;
        }

        if (m.type === "record-data") {
            // BY-MARKET DASHBOARD — one card per traded coin
            let grid = document.getElementById("coinGrid");
            if (!grid) {
                grid = document.createElement("div");
                grid.id = "coinGrid";
                grid.className = "coin-grid";
                const summary = document.getElementById("recordSummary");
                summary.parentNode.insertBefore(grid, summary.nextSibling);
            }
            grid.innerHTML = "";
            (m.perCoin || []).forEach(c => {
                const up = c.netR >= 0;
                const card = document.createElement("div");
                card.className = "coin-card";
                card.innerHTML =
                    '<div class="cc-head"><b>' + c.market.replace("USDT", "") + "</b>" +
                    '<span class="cc-tier ' + (c.tier === "PLAYBOOK" ? "pb" : "up") + '">' +
                    (c.tier === "PLAYBOOK" ? "Playbook" : "Unproven") + "</span></div>" +
                    '<div class="cc-net ' + (up ? "pos" : "neg") + '">' +
                    (up ? "+" : "") + c.netR + "R</div>" +
                    '<div class="cc-sub">' + c.wins + "W / " + c.losses + "L · avg " +
                    (c.avgR >= 0 ? "+" : "") + c.avgR + "R</div>";
                grid.appendChild(card);
            });
            document.getElementById("recordSummary").textContent = m.summary;
            const t = document.getElementById("recordTable");
            let html =
                "<tr>" +
                "<th>setup time</th><th>coin</th><th>side</th><th>tier</th>" +
                "<th>entry</th><th>result</th><th>net R</th></tr>";
            for (const r of m.rows) {
                const cls = r.result === "WIN" ? "win" : r.result === "LOSS" ? "loss" : (r.result === "MISSED" || r.result === "SKIPPED") ? "dim" : "open";
                html += "<tr class='rec-row' " +
                    "data-market='" + r.market + "' data-epoch='" + r.epochMs +
                    "' data-dir='" + r.direction + "' data-entry='" + r.entry +
                    "' data-stop='" + r.stop + "' data-risk='" + r.riskPerUnit + "'>" +
                    "<td>" + ((r.sweepTs || r.epochMs)
                        ? new Date(r.sweepTs || r.epochMs).toLocaleString(undefined,
                            { month: "short", day: "numeric", hour: "numeric", minute: "2-digit", hour12: true })
                        : r.utc.replace("T", " ")) + "</td>" +
                    "<td>" + r.market + "</td>" +
                    "<td>" + r.direction + "</td>" +
                    "<td>" + r.tier + "</td>" +
                    "<td>" + r.entry + "</td>" +
                    "<td class='" + cls + "' title='" + (r.reason || "") + "'>" + r.result + (r.reason ? " <span class='dim' style='font-size:0.85em'>(" + r.reason + ")</span>" : "") + "</td>" +
                    "<td>" + (r.netR == null ? "—" : r.netR) + "</td></tr>";
            }
            t.innerHTML = html;
            t.querySelectorAll(".rec-row").forEach(tr => {
                tr.title = "Click to see how this call played out";
                tr.addEventListener("click", () =>
                    window.chrome.webview.postMessage({
                        type: "record-open",
                        market: tr.dataset.market,
                        epochMs: Number(tr.dataset.epoch),
                        direction: tr.dataset.dir,
                        entry: Number(tr.dataset.entry),
                        stop: Number(tr.dataset.stop),
                        riskPerUnit: Number(tr.dataset.risk)
                    }));
            });
            return;
        }

        if (m.type === "recorder") {
            const el = document.getElementById("systemStatus");
            if (!window.__rec) window.__rec = {};
            window.__rec[m.layer] = m.rows;
            const r = window.__rec;
            if (el) el.textContent =
                "System online · recording " +
                ["venues","flow","book","liq","news"].map(k => k + " " + (r[k] ?? "–")).join(" · ");
            return;
        }

        if (m.type === "drawer-replay") {
            (m.items || []).slice().reverse().forEach(it => {
                clearEmpty();
                const card = document.createElement("div");
                card.className = "scanner-card history";
                const when = it.ts ? new Date(it.ts).toLocaleString(undefined,
                    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
                card.innerHTML = '<div class="dim">' +
                    (it.status || "") + " · " + (it.market || "") + " " + (it.direction || "") +
                    (it.entry ? " @ " + it.entry : "") +
                    (it.reason ? " — " + it.reason : "") +
                    " · " + when + "</div>";
                drawer.prepend(card);
            });
            return;
        }

        if (m.type === "news-coverage") {
            window.__coverage = m.lanes || [];
            return;
        }

        if (m.type === "news-feed") {
            const list = document.getElementById("newsList");
            list.innerHTML = "";

            // coverage board first: what's watched vs not built
            if (window.__coverage && window.__coverage.length) {
                const cov = document.createElement("div");
                cov.className = "coverage";
                let html = "<h3>Coverage — what the machine watches (and doesn't yet)</h3>";
                ["CRYPTO", "COMPANY", "MACRO", "GEO"].forEach(lane => {
                    const rows = window.__coverage.filter(c => c.lane === lane);
                    if (!rows.length) return;
                    html += '<div class="cov-lane"><span class="cov-lane-name">' + lane + "</span>" +
                        rows.map(c =>
                            '<span class="cov-item ' + (c.live ? "live" : "off") + '">' +
                            (c.live ? "● " : "○ ") + c.name + "</span>").join("") +
                        "</div>";
                });
                cov.innerHTML = html;
                list.appendChild(cov);
            }
            if (!m.items || !m.items.length) {
                list.innerHTML = '<div class="dim" style="padding:20px;">Nothing stored yet.</div>';
                return;
            }
            m.items.forEach(it => {
                const row = document.createElement("div");
                row.className = "news-row";
                const when = it.ts
                    ? new Date(it.ts).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })
                    : "";
                const badge =
                    it.category === "LISTING" ? '<span class="news-badge up">LISTING</span>' :
                    it.category === "DELISTING" ? '<span class="news-badge down">DELISTING</span>' :
                    it.category === "STAKE" ? '<span class="news-badge hot">13D STAKE</span>' :
                    it.category === "MERGER" ? '<span class="news-badge hot">MERGER</span>' :
                    '<span class="news-badge">' + (it.source || "PRESS") + "</span>";
                row.innerHTML =
                    badge +
                    '<span class="news-title">' + it.title + "</span>" +
                    '<span class="news-when">' + when + "</span>";
                if (it.url) {
                    row.classList.add("click");
                    row.onclick = () =>
                        window.chrome.webview.postMessage({ type: "open-url", url: it.url });
                }
                list.appendChild(row);
            });
            return;
        }

        if (m.type === "action") {
            clearEmpty();
            const card = document.createElement("div");
            card.className = "scanner-card action";
            card.innerHTML =
                '<div class="action-head">' + m.action + " · " + m.symbol + "</div>" +
                '<div class="action-why">' + m.why + "</div>" +
                '<div class="action-status">' + m.status + "</div>";
            drawer.prepend(card);
            return;
        }

        if (m.type === "news") {
            // headlines live in the News tab; the drawer is for
            // act-now events only (calls, listings, SEC filings)
            if (m.category === "PRESS") return;
            clearEmpty();
            const card = document.createElement("div");
            card.className = "scanner-card news";
            const head =
                m.category === "LISTING" ? "🟢 BINANCE LISTING" :
                m.category === "DELISTING" ? "🔴 BINANCE DELISTING" :
                m.category === "STAKE" ? "🟡 SEC 13D — ACTIVIST STAKE" :
                m.category === "MERGER" ? "🟡 SEC S-4 — MERGER FILED" :
                "📰 " + (m.source || "PRESS");
            card.innerHTML =
                '<div class="news-cat">' + head + "</div><div>" + m.title + "</div>";
            drawer.prepend(card);
            return;
        }

        if (m.type === "scanner-note") {
            clearEmpty();
            const card = document.createElement("div");
            card.className = "scanner-card history";
            card.innerHTML = '<div class="dim">' + m.text + "</div>";
            drawer.prepend(card);
            return;
        }

        if (m.type === "positioning") {
            const sym = m.market.replace("USDT", "");
            const tile = document.querySelector('.market[data-symbol="' + sym + '"]');
            if (!tile) return;
            const line = tile.querySelector(".pos-line");
            if (!line) return;
            const cls = m.tag.startsWith("CROWDED") ? "crowded"
                : m.tag.startsWith("LEANING") ? "leaning" : "";
            // plain-English crowd read
            // the user's language up front; model numbers small after
            const crowd =
                m.tag === "CROWDED LONG" ? "Too many betting UP — crash fuel" :
                m.tag === "CROWDED SHORT" ? "Too many betting DOWN — rip fuel" :
                m.tag === "LEANING LONG" ? "More betting up than usual" :
                m.tag === "LEANING SHORT" ? "More betting down than usual" :
                "Nobody trapped. Quiet.";
            const nums = (m.fundingBp >= 0 ? "+" : "") + m.fundingBp + "bp · p" + m.pctile +
                " · OI " + (m.oiChange24h >= 0 ? "+" : "") + m.oiChange24h + "%";
            line.innerHTML =
                '<span class="tag ' + cls + '">' + crowd + "</span>" +
                '<span class="nums"> ' + nums + "</span>";
            line.title =
                "Funding: " + payer + " " + Math.abs(m.fundingBp) + " basis points every 8h to hold. " +
                "That is more extreme than " + m.pctile + "% of this coin's history " +
                "(above 99% = crowd trapped, raids hit hardest).\n" +
                "Leverage (open interest) changed " + (m.oiChange24h >= 0 ? "+" : "") + m.oiChange24h + "% in 24h.";
            return;
        }

        if (m.type === "scanner-stages") {
            stageCache[m.market] = m;
            if (window.tdPins) window.tdPins(m.market.replace("USDT", ""), m);

            // sidebar shows WHERE setups are forming, no clicking:
            // e.g. "SHORT 4/7" on the tile, green when entry-ready.
            const sym = m.market.replace("USDT", "");
            const el = document.querySelector(
                '.market[data-symbol="' + sym + '"] .market-status');

            // market list says only LIVE (bot trades it) or DEMO (paper only); no colours, no stage words
            if (el) {
                const live = (window.__tdTraded || ["BTC", "ETH", "XRP"]).includes(sym);
                el.textContent = live ? "live" : "demo";
                el.style.color = "";
                const tileEl = el.closest(".market"); if (tileEl) delete tileEl.dataset.state;
            }

            if (m.market === currentSymbol + "USDT")
                renderStages(currentSymbol);
            return;
        }

        if (m.type === "scanner-status") {
            const symS = m.market.replace("USDT", "");
            setMarketStatus(m.market, m.status === "ERROR" ? "error" :
                ((window.__tdTraded || ["BTC", "ETH", "XRP"]).includes(symS) ? "live" : "demo"));
            return;
        }

        if (m.type === "scanner-alert") {
            const card = document.createElement("div");
            card.className =
                "scanner-card" + (m.status === "FILLED" ? " filled" : "");

            const tierLine = m.tier && m.tier !== "PLAYBOOK"
                ? '<div class="row"><span class="dim">TIER</span><b style="color:#f85149">' +
                  m.tier + "</b></div>"
                : '<div class="row"><span class="dim">TIER</span><b style="color:#2ea043">PLAYBOOK ✓</b></div>';

            clearEmpty();
            const lockLine = m.locked
                ? '<div class="lock">Locked — ' + m.lockReason + "</div>"
                : "";
            card.innerHTML =
                "<h4>" + m.status + ": " + m.market + " " + m.direction +
                ' <span class="dim">' + m.utc + " UTC</span></h4>" + lockLine +
                '<div class="row"><span class="dim">ENTRY</span><b>' +
                    m.entry + "</b></div>" +
                '<div class="row"><span class="dim">STOP</span><b>' +
                    m.stop + "</b></div>" +
                '<div class="row"><span class="dim">TP 3R</span><b>' +
                    Number(m.tp3).toPrecision(6) + "</b></div>" +
                '<div class="row"><span class="dim">SIZE</span><b>' +
                    Number(m.units).toFixed(4) + " (~$" +
                    Math.round(m.notional) + ")</b></div>" +
                '<div class="row"><span class="dim">disp / fvg</span><b>' +
                    m.dispRatio + "x / " + m.fvgRatio + "x</b></div>" +
                tierLine;

            attachJump(card, m.market);
            drawer.prepend(card);
            alertCount++;
            countEl.textContent = alertCount;
            
            beep();
        }
    });
})();


// ---- Trades tab calendar (2026-11-20, the user): year of months -> month of days, P&L and trade count per day, all accounts summed, local dates
(function () {
    const M = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    const st = { view: "rows", year: new Date().getFullYear(), month: null };
    const key = d => d.getFullYear() + "-" + (d.getMonth() + 1) + "-" + d.getDate();
    const fmt = v => (v < 0 ? "-$" : "+$") + Math.abs(Math.round(v)).toLocaleString();
    const cls = v => v > 0 ? "win" : v < 0 ? "loss" : "";
    const plural = n => n + " trade" + (n > 1 ? "s" : "");
    // % = the day's result over a starting balance. Placeholder value: set it to the account you are testing with.
    const START_BALANCE = 100000;
    const base = d => START_BALANCE;
    const pct = (v, d) => " <span class='cal-pct'>" + (v < 0 ? "" : "+") + (100 * v / base(d)).toFixed(2) + "%</span>";
    // broker history (tools/trades_cal_backfill.py) up to its 'asof', the live executor's closed rows after it
    let hist = null;
    // 2026-11-22: re-read on every Calendar click so a backfill run shows without reloading the app
    const loadHist = () => fetch("lab/trades_cal.json?t=" + Date.now()).then(r => r.ok ? r.json() : null).then(j => { if (j) hist = j; renderTradesCal(); }).catch(() => { });
    loadHist();
    function days() {
        const by = {}, asof = hist ? Date.parse(hist.asof) : 0;
        const add = (ms, pnl, trade) => {
            const d = new Date(ms); if (isNaN(d)) return;
            const k = key(d); const o = by[k] || (by[k] = { pnl: 0, n: 0, d });
            o.pnl += Number(pnl) || 0; if (trade) o.n++;
        };
        if (hist) for (const r of hist.rows) add(Date.parse(r[0]), r[2], r[3] !== 0);
        for (const r of window.__calRows || []) { const ms = Date.parse(r.utc + ":00Z"); if (ms > asof) add(ms, r.pnl, true); }
        return by;
    }
    window.renderTradesCal = function () {
        const box = document.getElementById("tradesCal"); if (!box) return;
        document.getElementById("tradesRowsWrap").classList.toggle("hidden", st.view !== "rows");
        box.classList.toggle("hidden", st.view !== "cal");
        document.querySelectorAll("#tradesSummary .tv-btn").forEach(b => b.classList.toggle("active", b.dataset.view === st.view));
        if (st.view !== "cal") return;
        const by = days(); let h = "";
        if (st.month == null) {
            let yp = 0, yn = 0;
            const cards = M.map((name, mi) => {
                let p = 0, n = 0;
                for (const k in by) { const o = by[k]; if (o.d.getFullYear() === st.year && o.d.getMonth() === mi) { p += o.pnl; n += o.n; } }
                yp += p; yn += n;
                return "<div class='cal-month" + (n ? " has" : "") + "' data-m='" + mi + "'><div class='cal-mname'>" + name + "</div>" +
                    (n ? "<div class='cal-pnl " + cls(p) + "'>" + fmt(p) + pct(p, new Date(st.year, mi + 1, 0)) + "</div><div class='cal-n'>" + plural(n) + "</div>" : "<div class='cal-n'>no trades</div>") + "</div>";
            }).join("");
            h = "<div class='cal-head'><button class='cal-nav' data-y='-1'>◀</button><b>" + st.year + "</b><button class='cal-nav' data-y='1'>▶</button>" +
                "<span class='cal-tot " + cls(yp) + "'>" + (yn ? fmt(yp) + " · " + plural(yn) : "") + "</span></div><div class='cal-year'>" + cards + "</div>";
        } else {
            const first = new Date(st.year, st.month, 1), nd = new Date(st.year, st.month + 1, 0).getDate();
            let cells = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map(w => "<div class='cal-wd'>" + w + "</div>").join("");
            for (let i = 0; i < first.getDay(); i++) cells += "<div class='cal-day empty'></div>";
            let mp = 0, mn = 0;
            for (let dd = 1; dd <= nd; dd++) {
                const o = by[st.year + "-" + (st.month + 1) + "-" + dd];
                if (o) { mp += o.pnl; mn += o.n; }
                cells += "<div class='cal-day" + (o ? " " + cls(o.pnl) + "-bg" : "") + "'><div class='cal-dn'>" + dd + "</div>" +
                    (o ? "<div class='cal-pnl " + cls(o.pnl) + "'>" + fmt(o.pnl) + pct(o.pnl, o.d) + "</div><div class='cal-n'>" + (o.n ? plural(o.n) : "overnight fees") + "</div>" : "") + "</div>";
            }
            h = "<div class='cal-head'><button class='cal-back'>← " + st.year + "</button><button class='cal-nav' data-mm='-1'>◀</button><b>" + M[st.month] + " " + st.year +
                "</b><button class='cal-nav' data-mm='1'>▶</button><span class='cal-tot " + cls(mp) + "'>" + (mn ? fmt(mp) + " · " + plural(mn) : "") + "</span></div><div class='cal-grid'>" + cells + "</div>";
        }
        box.innerHTML = h + "<div class='cal-note'>Closed trades, all accounts added together, by your local date. Includes commissions and overnight fees, so it matches your balance. % = of the starting balances of the accounts live that day.</div>";
        box.querySelectorAll(".cal-month").forEach(e => e.onclick = () => { st.month = +e.dataset.m; renderTradesCal(); });
        box.querySelectorAll(".cal-nav[data-y]").forEach(e => e.onclick = () => { st.year += +e.dataset.y; renderTradesCal(); });
        box.querySelectorAll(".cal-nav[data-mm]").forEach(e => e.onclick = () => {
            st.month += +e.dataset.mm; if (st.month < 0) { st.month = 11; st.year--; } if (st.month > 11) { st.month = 0; st.year++; } renderTradesCal();
        });
        const back = box.querySelector(".cal-back"); if (back) back.onclick = () => { st.month = null; renderTradesCal(); };
    };
    document.addEventListener("click", e => {
        const b = e.target.closest && e.target.closest("#tradesSummary .tv-btn"); if (!b) return;
        st.view = b.dataset.view;
        if (st.view === "cal") { const n = new Date(); st.year = n.getFullYear(); st.month = n.getMonth(); }
        renderTradesCal(); if (st.view === "cal") loadHist();
    });
})();
