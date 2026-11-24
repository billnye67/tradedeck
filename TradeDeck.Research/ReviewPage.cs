using System.Text.Json;

namespace TradeDeck.Research;

/// <summary>
/// Builds a single self-contained HTML file where candidates are
/// drawn as candlestick charts and graded by hand. Grades are
/// exported as JSON via the Export button; nothing is uploaded
/// anywhere.
/// </summary>
public static class ReviewPage
{
    public static string Build(
        string market,
        IReadOnlyList<SweepCandidate> candidates)
    {
        var payload = candidates.Select(c => new
        {
            market = c.Market,
            direction = c.Direction,
            level = c.Level,
            pivotRel = c.PivotIndex - c.ContextStartIndex,
            sweepRel = c.SweepIndex - c.ContextStartIndex,
            pivotUtc = ToUtcText(c.PivotTimestamp),
            sweepUtc = ToUtcText(c.SweepTimestamp),
            candles = c.Context
                .Select(k => new[]
                {
                    (double)k.Timestamp,
                    k.Open,
                    k.High,
                    k.Low,
                    k.Close
                })
                .ToArray()
        });

        string json = JsonSerializer.Serialize(payload);

        return Template
            .Replace("__MARKET__", market)
            .Replace("__DATA__", json);
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
    <title>TradeDeck Sweep Review — __MARKET__</title>
    <style>
      body { background:#0d1117; color:#e6edf3; font-family:Consolas,monospace;
             margin:0; padding:16px; }
      #top { display:flex; gap:16px; align-items:center; flex-wrap:wrap; }
      #info { font-size:14px; }
      #progress { color:#8b949e; }
      canvas { background:#161b22; border:1px solid #30363d; display:block;
               margin:12px 0; }
      button { background:#21262d; color:#e6edf3; border:1px solid #30363d;
               padding:8px 18px; font-family:inherit; font-size:14px;
               cursor:pointer; }
      button:hover { background:#30363d; }
      #real { border-color:#2ea043; }
      #nah { border-color:#f85149; }
      .graded-real { color:#2ea043; }
      .graded-nah { color:#f85149; }
      .graded-unsure { color:#d29922; }
      #export { margin-left:24px; }
      #hint { color:#8b949e; font-size:12px; margin-top:8px; }
      #out { width:100%; height:80px; background:#161b22; color:#e6edf3;
             border:1px solid #30363d; margin-top:8px; display:none; }
    </style>
    </head>
    <body>
    <div id="top">
      <div id="info"></div>
      <div id="progress"></div>
    </div>
    <canvas id="chart" width="1200" height="560"></canvas>
    <div>
      <button id="prev">&#8592; Prev</button>
      <button id="real">REAL (Y)</button>
      <button id="nah">NAH (N)</button>
      <button id="unsure">UNSURE (U)</button>
      <button id="next">Next &#8594;</button>
      <button id="export">EXPORT GRADES</button>
    </div>
    <div id="hint">
      Keys: Y = real raid, N = not one, U = unsure. Grading auto-advances.
      Red line = the level. P marks where it formed, S marks the sweep.
      Export when done and send the JSON back.
    </div>
    <textarea id="out" readonly></textarea>

    <script>
    const DATA = __DATA__;
    const grades = new Array(DATA.length).fill(null);
    let cur = 0;

    const canvas = document.getElementById("chart");
    const ctx = canvas.getContext("2d");

    function draw() {
      const c = DATA[cur];
      const candles = c.candles;
      const n = candles.length;

      let hi = -Infinity, lo = Infinity;
      for (const k of candles) {
        if (k[2] > hi) hi = k[2];
        if (k[3] < lo) lo = k[3];
      }
      if (c.level > hi) hi = c.level;
      if (c.level < lo) lo = c.level;
      const pad = (hi - lo) * 0.08;
      hi += pad; lo -= pad;

      const W = canvas.width, H = canvas.height;
      const left = 10, right = 80, top = 10, bottom = 24;
      const plotW = W - left - right, plotH = H - top - bottom;

      const x = i => left + plotW * (i + 0.5) / n;
      const y = p => top + plotH * (hi - p) / (hi - lo);
      const cw = Math.max(2, Math.floor(plotW / n * 0.6));

      ctx.clearRect(0, 0, W, H);

      // price gridlines
      ctx.strokeStyle = "#21262d";
      ctx.fillStyle = "#8b949e";
      ctx.font = "11px Consolas";
      const steps = 6;
      for (let g = 0; g <= steps; g++) {
        const p = lo + (hi - lo) * g / steps;
        const yy = y(p);
        ctx.beginPath();
        ctx.moveTo(left, yy);
        ctx.lineTo(left + plotW, yy);
        ctx.stroke();
        ctx.fillText(p.toPrecision(6), left + plotW + 6, yy + 4);
      }

      // candles
      for (let i = 0; i < n; i++) {
        const [, o, h, l, cl] = candles[i];
        const up = cl >= o;
        ctx.strokeStyle = up ? "#2ea043" : "#f85149";
        ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath();
        ctx.moveTo(x(i), y(h));
        ctx.lineTo(x(i), y(l));
        ctx.stroke();
        const yo = y(o), yc = y(cl);
        ctx.fillRect(
          x(i) - cw / 2,
          Math.min(yo, yc),
          cw,
          Math.max(1, Math.abs(yc - yo))
        );
      }

      // the level
      ctx.strokeStyle = "#f85149";
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(left, y(c.level));
      ctx.lineTo(left + plotW, y(c.level));
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.lineWidth = 1;

      // pivot + sweep markers
      ctx.fillStyle = "#d29922";
      ctx.font = "bold 14px Consolas";
      const pk = candles[c.pivotRel];
      const sk = candles[c.sweepRel];
      const isHigh = c.direction === "HIGH";
      ctx.fillText("P", x(c.pivotRel) - 4,
        isHigh ? y(pk[2]) - 8 : y(pk[3]) + 18);
      ctx.fillText("S", x(c.sweepRel) - 4,
        isHigh ? y(sk[2]) - 8 : y(sk[3]) + 18);

      const g = grades[cur];
      const ageBars = c.sweepRel - c.pivotRel;
      const ageH = (ageBars / 4).toFixed(1);
      document.getElementById("info").innerHTML =
        `<b>${c.market}</b> 15m &nbsp; ${c.direction} raid &nbsp; ` +
        `level ${c.level} &nbsp; formed ${c.pivotUtc} &nbsp; swept ${c.sweepUtc} ` +
        `&nbsp; age ${ageBars} bars (${ageH}h)` +
        (g ? ` &nbsp; <span class="graded-${g}">[${g.toUpperCase()}]</span>` : "");

      const done = grades.filter(v => v !== null).length;
      document.getElementById("progress").textContent =
        `candidate ${cur + 1} / ${DATA.length} — graded ${done}`;
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
      const rows = DATA.map((c, i) => ({
        market: c.market,
        direction: c.direction,
        level: c.level,
        pivotUtc: c.pivotUtc,
        sweepUtc: c.sweepUtc,
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
      a.download = "sweep-grades-__MARKET__.json";
      a.click();
    }

    document.getElementById("real").onclick = () => grade("real");
    document.getElementById("nah").onclick = () => grade("nah");
    document.getElementById("unsure").onclick = () => grade("unsure");
    document.getElementById("prev").onclick = () => move(-1);
    document.getElementById("next").onclick = () => move(1);
    document.getElementById("export").onclick = exportGrades;

    document.addEventListener("keydown", e => {
      const k = e.key.toLowerCase();
      if (k === "y") grade("real");
      else if (k === "n") grade("nah");
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
