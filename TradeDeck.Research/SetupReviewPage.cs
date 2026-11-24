using System.Text.Json;

namespace TradeDeck.Research;

/// <summary>
/// Review page for COMPLETE Strategy #1 setups: 5m chart with the
/// 15m level, sweep wick, trigger, displacement, FVG zone, entry
/// and stop all drawn. Grading = "would you have marked it the
/// same way": Y / N / U, exported as JSON.
/// </summary>
public static class SetupReviewPage
{
    public static string BuildJson(
        string market,
        IReadOnlyList<FullSetup> setups,
        IReadOnlyList<ResearchCandle> c5)
    {
        return JsonSerializer.Serialize(
            Payload(setups, c5),
            new JsonSerializerOptions { WriteIndented = false }
        );
    }

    public static string Build(
        string market,
        IReadOnlyList<FullSetup> setups,
        IReadOnlyList<ResearchCandle> c5)
    {
        string json = JsonSerializer.Serialize(Payload(setups, c5));

        return Template
            .Replace("__MARKET__", market)
            .Replace("__DATA__", json);
    }

    private static IEnumerable<object> Payload(
        IReadOnlyList<FullSetup> setups,
        IReadOnlyList<ResearchCandle> c5)
    {
        return setups.Select(object (s) =>
        {
            var slice = new List<double[]>();

            for (int i = s.ChartStart5m; i <= s.ChartEnd5m; i++)
            {
                slice.Add(new[]
                {
                    (double)c5[i].Timestamp,
                    c5[i].Open, c5[i].High, c5[i].Low, c5[i].Close
                });
            }

            var slice15 = s.Raid.Context
                .Select(k => new[]
                {
                    (double)k.Timestamp,
                    k.Open, k.High, k.Low, k.Close
                })
                .ToArray();

            return new
            {
                market = s.Raid.Market,
                direction = s.Raid.Direction == "HIGH" ? "SHORT" : "LONG",
                level = s.Raid.Level,
                sweepExtreme = s.SweepExtreme,
                trigger = s.TriggerPrice,
                fvgProximal = s.FvgProximal,
                fvgDistal = s.FvgDistal,
                entry = s.EntryPrice,
                stop = s.StopLoss,
                chop = s.ChopCrossings,
                dispRatio = Math.Round(s.DisplacementRatio, 2),
                fvgRatio = Math.Round(s.FvgSizeRatio, 2),
                sweepUtc = ToUtcText(s.Raid.SweepTimestamp),
                extremeRel = s.ExtremeIndex5m - s.ChartStart5m,
                triggerRel = s.TriggerIndex5m - s.ChartStart5m,
                dispRel = s.DisplacementIndex5m - s.ChartStart5m,
                fvgRel = s.FvgMiddleIndex5m - s.ChartStart5m,
                entryRel = s.EntryIndex5m - s.ChartStart5m,
                pivotRel15 = s.Raid.PivotIndex - s.Raid.ContextStartIndex,
                sweepRel15 = s.Raid.SweepIndex - s.Raid.ContextStartIndex,
                candles15 = slice15,
                candles = slice
            };
        });
    }

    private static string ToUtcText(long unixMs) =>
        DateTimeOffset
            .FromUnixTimeMilliseconds(unixMs)
            .ToString("yyyy-MM-dd HH:mm 'UTC'");

    private const string Template =
    """
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8">
    <title>TradeDeck Full Setups — __MARKET__</title>
    <style>
      body { background:#0d1117; color:#e6edf3; font-family:Consolas,monospace;
             margin:0; padding:16px; }
      #top { display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
      #info { font-size:13px; }
      #progress { color:#8b949e; }
      canvas { background:#161b22; border:1px solid #30363d; display:block;
               margin:12px 0; }
      button { background:#21262d; color:#e6edf3; border:1px solid #30363d;
               padding:8px 18px; font-family:inherit; font-size:14px;
               cursor:pointer; }
      button:hover { background:#30363d; }
      #legend { color:#8b949e; font-size:12px; margin-top:8px; }
      #out { width:100%; height:80px; background:#161b22; color:#e6edf3;
             border:1px solid #30363d; margin-top:8px; display:none; }
    </style>
    </head>
    <body>
    <div id="top">
      <div id="info"></div>
      <div id="progress"></div>
    </div>
    <div style="color:#8b949e;font-size:12px;">15m — the raid (P = level formed, S = sweep)</div>
    <canvas id="chart15" width="1300" height="230"></canvas>
    <div style="color:#8b949e;font-size:12px;">5m — the setup (T / D / FVG / E)</div>
    <canvas id="chart" width="1300" height="470"></canvas>
    <div>
      <button id="prev">&#8592; Prev</button>
      <button id="yes">MARKED RIGHT (Y)</button>
      <button id="no">MARKED WRONG (N)</button>
      <button id="unsure">UNSURE (U)</button>
      <button id="next">Next &#8594;</button>
      <button id="export">EXPORT</button>
    </div>
    <div id="legend">
      RED dashed = 15m level &nbsp; ORANGE = trigger &nbsp; PURPLE box = FVG &nbsp;
      GREEN = entry &nbsp; RED solid = stop (sweep wick) &nbsp;
      T = trigger candle &nbsp; D = displacement &nbsp; E = entry candle.
      Grade whether the machine marked the SAME trigger/FVG/entry you would have.
    </div>
    <textarea id="out" readonly></textarea>

    <script>
    const DATA = __DATA__;
    const grades = new Array(DATA.length).fill(null);
    let cur = 0;

    const canvas = document.getElementById("chart");
    const ctx = canvas.getContext("2d");
    const canvas15 = document.getElementById("chart15");
    const ctx15 = canvas15.getContext("2d");

    function draw15() {
      const s = DATA[cur];
      const candles = s.candles15;
      const n = candles.length;

      let hi = -Infinity, lo = Infinity;
      for (const k of candles) {
        if (k[2] > hi) hi = k[2];
        if (k[3] < lo) lo = k[3];
      }
      if (s.level > hi) hi = s.level;
      if (s.level < lo) lo = s.level;
      if (s.stop > hi) hi = s.stop;
      if (s.stop < lo) lo = s.stop;
      const pad = (hi - lo) * 0.08;
      hi += pad; lo -= pad;

      const W = canvas15.width, H = canvas15.height;
      const left = 10, right = 86, top = 6, bottom = 6;
      const plotW = W - left - right, plotH = H - top - bottom;

      const x = i => left + plotW * (i + 0.5) / n;
      const y = p => top + plotH * (hi - p) / (hi - lo);
      const cw = Math.max(1, Math.floor(plotW / n * 0.6));

      ctx15.clearRect(0, 0, W, H);

      for (let i = 0; i < n; i++) {
        const [, o, h, l, cl] = candles[i];
        const up = cl >= o;
        ctx15.strokeStyle = up ? "#2ea043" : "#f85149";
        ctx15.fillStyle = ctx15.strokeStyle;
        ctx15.beginPath();
        ctx15.moveTo(x(i), y(h));
        ctx15.lineTo(x(i), y(l));
        ctx15.stroke();
        ctx15.fillRect(x(i) - cw / 2, Math.min(y(o), y(cl)), cw,
                       Math.max(1, Math.abs(y(cl) - y(o))));
      }

      ctx15.strokeStyle = "#f85149";
      ctx15.setLineDash([6, 4]);
      ctx15.lineWidth = 1.5;
      ctx15.beginPath();
      ctx15.moveTo(left, y(s.level));
      ctx15.lineTo(left + plotW, y(s.level));
      ctx15.stroke();
      ctx15.setLineDash([]);
      ctx15.lineWidth = 1;

      ctx15.strokeStyle = "#f85149";
      ctx15.beginPath();
      ctx15.moveTo(left, y(s.stop));
      ctx15.lineTo(left + plotW, y(s.stop));
      ctx15.stroke();

      ctx15.fillStyle = "#d29922";
      ctx15.font = "bold 13px Consolas";
      const isHighRaid = s.direction === "SHORT";
      function mark15(rel, label) {
        if (rel < 0 || rel >= n) return;
        const k = candles[rel];
        ctx15.fillText(label, x(rel) - 4,
          isHighRaid ? y(k[2]) - 6 : y(k[3]) + 14);
      }
      mark15(s.pivotRel15, "P");
      mark15(s.sweepRel15, "S");

      ctx15.fillStyle = "#8b949e";
      ctx15.font = "11px Consolas";
      for (let g = 0; g <= 3; g++) {
        const p = lo + (hi - lo) * g / 3;
        ctx15.fillText(p.toPrecision(6), left + plotW + 6, y(p) + 4);
      }
    }

    function draw() {
      const s = DATA[cur];
      const candles = s.candles;
      const n = candles.length;

      let hi = -Infinity, lo = Infinity;
      for (const k of candles) {
        if (k[2] > hi) hi = k[2];
        if (k[3] < lo) lo = k[3];
      }
      for (const p of [s.level, s.stop, s.trigger, s.fvgDistal])
        { if (p > hi) hi = p; if (p < lo) lo = p; }
      const pad = (hi - lo) * 0.08;
      hi += pad; lo -= pad;

      const W = canvas.width, H = canvas.height;
      const left = 10, right = 86, top = 10, bottom = 24;
      const plotW = W - left - right, plotH = H - top - bottom;

      const x = i => left + plotW * (i + 0.5) / n;
      const y = p => top + plotH * (hi - p) / (hi - lo);
      const cw = Math.max(2, Math.floor(plotW / n * 0.6));

      ctx.clearRect(0, 0, W, H);

      ctx.strokeStyle = "#21262d";
      ctx.fillStyle = "#8b949e";
      ctx.font = "11px Consolas";
      for (let g = 0; g <= 6; g++) {
        const p = lo + (hi - lo) * g / 6;
        ctx.beginPath();
        ctx.moveTo(left, y(p));
        ctx.lineTo(left + plotW, y(p));
        ctx.stroke();
        ctx.fillText(p.toPrecision(6), left + plotW + 6, y(p) + 4);
      }

      // FVG zone first so candles draw over it
      ctx.fillStyle = "rgba(163,113,247,0.25)";
      const fy1 = y(Math.max(s.fvgProximal, s.fvgDistal));
      const fy2 = y(Math.min(s.fvgProximal, s.fvgDistal));
      ctx.fillRect(x(Math.max(0, s.fvgRel - 1)), fy1,
                   plotW - x(Math.max(0, s.fvgRel - 1)) + left, fy2 - fy1);

      for (let i = 0; i < n; i++) {
        const [, o, h, l, cl] = candles[i];
        const up = cl >= o;
        ctx.strokeStyle = up ? "#2ea043" : "#f85149";
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath();
        ctx.moveTo(x(i), y(h));
        ctx.lineTo(x(i), y(l));
        ctx.stroke();
        ctx.fillRect(x(i) - cw / 2, Math.min(y(o), y(cl)), cw,
                     Math.max(1, Math.abs(y(cl) - y(o))));
      }

      function hline(p, color, dash) {
        ctx.strokeStyle = color;
        ctx.setLineDash(dash);
        ctx.lineWidth = 1.5;
        ctx.beginPath();
        ctx.moveTo(left, y(p));
        ctx.lineTo(left + plotW, y(p));
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineWidth = 1;
      }

      hline(s.level, "#f85149", [6, 4]);     // 15m level
      hline(s.trigger, "#d29922", [2, 3]);   // trigger
      hline(s.entry, "#2ea043", [2, 3]);     // entry
      hline(s.stop, "#f85149", []);          // stop

      ctx.fillStyle = "#e6edf3";
      ctx.font = "bold 13px Consolas";
      const isShortSetup = s.direction === "SHORT";
      function mark(rel, label, below) {
        if (rel < 0 || rel >= n) return;
        const k = candles[rel];
        if (below) ctx.fillText(label, x(rel) - 4, y(k[3]) + 16);
        else ctx.fillText(label, x(rel) - 4, y(k[2]) - 8);
      }
      // T marks a LOW for shorts (below the candle), a HIGH for longs.
      mark(s.triggerRel, "T", isShortSetup);
      mark(s.dispRel, "D", !isShortSetup);
      mark(s.entryRel, "E", !isShortSetup);

      const risk = Math.abs(s.stop - s.entry);
      const g = grades[cur];
      document.getElementById("info").innerHTML =
        `<b>${s.market}</b> ${s.direction} &nbsp; level ${s.level} &nbsp; ` +
        `swept ${s.sweepUtc} &nbsp; trigger ${s.trigger} &nbsp; ` +
        `entry ${s.entry} &nbsp; stop ${s.stop} &nbsp; ` +
        `risk/unit ${risk.toPrecision(4)} &nbsp; chop ${s.chop} ` +
        `&nbsp; disp ${s.dispRatio}x &nbsp; fvg ${s.fvgRatio}x` +
        (g ? ` &nbsp; [${g.toUpperCase()}]` : "");

      const done = grades.filter(v => v !== null).length;
      document.getElementById("progress").textContent =
        `setup ${cur + 1} / ${DATA.length} — graded ${done}`;

      draw15();
    }

    function grade(v) {
      grades[cur] = v;
      if (cur < DATA.length - 1) cur++;
      draw();
    }

    function move(d) {
      cur = Math.min(DATA.length - 1, Math.max(0, cur + d));
      draw();
    }

    function exportGrades() {
      const rows = DATA.map((s, i) => ({
        market: s.market,
        direction: s.direction,
        level: s.level,
        sweepUtc: s.sweepUtc,
        trigger: s.trigger,
        entry: s.entry,
        stop: s.stop,
        chop: s.chop,
        grade: grades[i]
      }));
      const text = JSON.stringify(rows, null, 2);
      const out = document.getElementById("out");
      out.style.display = "block";
      out.value = text;
      out.select();
      const blob = new Blob([text], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "setup-grades-__MARKET__.json";
      a.click();
    }

    document.getElementById("yes").onclick = () => grade("yes");
    document.getElementById("no").onclick = () => grade("no");
    document.getElementById("unsure").onclick = () => grade("unsure");
    document.getElementById("prev").onclick = () => move(-1);
    document.getElementById("next").onclick = () => move(1);
    document.getElementById("export").onclick = exportGrades;

    document.addEventListener("keydown", e => {
      const k = e.key.toLowerCase();
      if (k === "y") grade("yes");
      else if (k === "n") grade("no");
      else if (k === "u") grade("unsure");
      else if (k === "arrowleft") move(-1);
      else if (k === "arrowright") move(1);
    });

    draw();
    </script>
    </body>
    </html>
    """;
}
