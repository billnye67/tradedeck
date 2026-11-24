using System.IO;
using System.Net.Http;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TradeDeck;

/// <summary>
/// The in-app RECORD: reads the vault journal, grades any
/// ungraded FILLED calls against what price actually did on
/// Hyperliquid (the venue the calls were made on), writes the
/// grades back, and hands the rows to the UI. Same rules as the
/// backtest: stop touched = loss, maker/maker wins, maker/taker
/// losses, 40h window then exit at close.
/// </summary>
public static class AppJournal
{
    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;
    private const long FiveMs = 5 * 60 * 1000;

    public static string VaultFolder =>
        Path.GetFullPath(Path.Combine(
            AppContext.BaseDirectory,
            "..", "..", "..", "..", "reviews", "vault"));

    public static async Task<string> GetRecordJsonAsync(HttpClient http)
    {
        string path = Path.Combine(VaultFolder, "journal.jsonl");

        if (!File.Exists(path))
            return JsonSerializer.Serialize(new
            {
                type = "record-data",
                rows = Array.Empty<object>(),
                summary = "No calls journaled yet — leave the scanner running."
            });

        var rows = File.ReadAllLines(path)
            .Where(l => !string.IsNullOrWhiteSpace(l))
            .Select(l => JsonNode.Parse(l)!.AsObject())
            .ToList();

        // Dedupe: app restarts used to re-journal the same setup.
        // One trade = one row: market|direction|sweepTs|status,
        // earliest kept. (Cleans historical duplicates too.)
        rows = rows
            .GroupBy(r =>
                $"{(string?)r["market"]}|{(string?)r["direction"]}|" +
                $"{(long?)r["sweepTs"] ?? (long?)r["epochMs"]}|{(string?)r["status"]}")
            .Select(g => g.OrderBy(r => (long?)r["epochMs"] ?? 0).First())
            .OrderBy(r => (long?)r["epochMs"] ?? 0)
            .ToList();

        // grade anything ungraded
        foreach (JsonObject row in rows)
        {
            if ((string?)row["status"] != "FILLED") continue;
            if (row.ContainsKey("outcomeR")) continue;

            try { await Grade(http, row); }
            catch { /* still open or fetch failed; next open works */ }
        }

        File.WriteAllLines(path, rows.Select(r => r.ToJsonString()));

        var filled = rows
            .Where(r => (string?)r["status"] == "FILLED")
            .ToList();

        var graded = filled
            .Where(r => r.ContainsKey("outcomeR"))
            .ToList();

        int wins = graded.Count(r => (double)r["outcomeR"]! > 0);
        double net = graded.Sum(r => (double?)r["netR"] ?? 0);

        // live playbook coins first (what the account trades); demo coins are paper-only and shown apart
        var liveSet = new HashSet<string> { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
        var gLive = graded.Where(r => liveSet.Contains((string?)r["market"] ?? "")).ToList();
        var gDemo = graded.Where(r => !liveSet.Contains((string?)r["market"] ?? "")).ToList();
        int winsLive = gLive.Count(r => (double)r["outcomeR"]! > 0);
        double netLive = gLive.Sum(r => (double?)r["netR"] ?? 0), netDemo = gDemo.Sum(r => (double?)r["netR"] ?? 0);

        string summary =
            $"LIVE COINS BTC/ETH/XRP: {gLive.Count} graded | {winsLive} won / {gLive.Count - winsLive} lost | net {netLive:+0.0;-0.0}R | backtest promise ~+0.107R per call" +
            $"   ·   demo coins (paper only): {gDemo.Count} graded, net {netDemo:+0.0;-0.0}R   ·   all: {filled.Count} calls, {graded.Count} graded, net {net:+0.0;-0.0}R";

        // a MISSED row that matches an actually-CALLED trade is a
        // repair-era double-write; the call is the truth, hide the ghost
        var calledSig = rows
            .Where(r => (string?)r["status"] == "FILLED")
            .Select(Sig)
            .ToHashSet();

        var outRows = rows
            .Where(r => (string?)r["status"] is "FILLED" or "MISSED" or "SUPPRESSED")
            .Where(r => (string?)r["status"] == "FILLED" || !calledSig.Contains(Sig(r)))
            .OrderByDescending(r => (long?)r["epochMs"] ?? 0)
            .Select(r => new
            {
                utc = ((string?)r["utcIso"] ?? "")[..Math.Min(16, ((string?)r["utcIso"] ?? "").Length)],
                epochMs = (long?)r["epochMs"] ?? 0,
                sweepTs = (long?)r["sweepTs"],
                reason = (string?)r["reason"],
                riskPerUnit = (double?)r["riskPerUnit"] ?? 0,
                market = (string?)r["market"],
                direction = (string?)r["direction"],
                tier = ShortTier((string?)r["tier"]),
                entry = (double?)r["entry"],
                stop = (double?)r["stop"],
                result = (string?)r["status"] == "MISSED" ? "MISSED"
                    : (string?)r["status"] == "SUPPRESSED" ? "SKIPPED"
                    : !r.ContainsKey("outcomeR") ? "OPEN"
                    : (double)r["outcomeR"]! > 0 ? "WIN" : "LOSS",
                netR = r.ContainsKey("netR")
                    ? Math.Round((double)r["netR"]!, 2)
                    : (double?)null
            });

        var perCoin = outRows
            .Where(r => r.netR != null)
            .GroupBy(r => r.market)
            .Select(g => new
            {
                market = g.Key,
                tier = g.First().tier,
                n = g.Count(),
                wins = g.Count(x => (x.netR ?? 0) > 0),
                losses = g.Count(x => (x.netR ?? 0) <= 0),
                netR = Math.Round(g.Sum(x => x.netR ?? 0), 2),
                avgR = Math.Round(g.Average(x => x.netR ?? 0), 2)
            })
            .OrderByDescending(x => x.netR)
            .ToList();

        return JsonSerializer.Serialize(new
        {
            type = "record-data",
            perCoin,
            rows = outRows,
            summary
        });
    }

    /// <summary>
    /// A3: render one call's playout — candles from before the
    /// call through the outcome, with entry/stop/3R target drawn —
    /// to an HTML file in the vault, and return its path.
    /// </summary>
    public static async Task<string> BuildPlayoutAsync(
        HttpClient http,
        string market,
        long epochMs,
        string direction,
        double entry,
        double stop,
        double risk)
    {
        bool isShort = direction == "SHORT";
        string coin = market.EndsWith("USDT") ? market[..^4] : market;

        long from = epochMs - 6L * 3600 * 1000;
        long to = Math.Min(
            DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
            epochMs + (long)WalkWindow * FiveMs);

        var body = JsonSerializer.Serialize(new
        {
            type = "candleSnapshot",
            req = new { coin, interval = "5m", startTime = from, endTime = to }
        });

        using var content = new StringContent(
            body, System.Text.Encoding.UTF8, "application/json");

        using var resp = await http.PostAsync(
            "https://api.hyperliquid.xyz/info", content);

        resp.EnsureSuccessStatusCode();

        await using var stream = await resp.Content.ReadAsStreamAsync();
        using var doc = await JsonDocument.ParseAsync(stream);

        var candles = new List<double[]>();
        int entryRel = -1;

        foreach (JsonElement k in doc.RootElement.EnumerateArray())
        {
            long t = k.GetProperty("t").GetInt64();

            candles.Add(new[]
            {
                (double)t,
                P(k.GetProperty("o")), P(k.GetProperty("h")),
                P(k.GetProperty("l")), P(k.GetProperty("c"))
            });

            if (entryRel < 0 && t >= epochMs) entryRel = candles.Count - 1;
        }

        double tp3 = isShort ? entry - 3 * risk : entry + 3 * risk;

        var data = new
        {
            title = $"{market} {direction} — called {DateTimeOffset.FromUnixTimeMilliseconds(epochMs).UtcDateTime:yyyy-MM-dd HH:mm} UTC",
            entry, stop, tp3, entryRel, candles
        };

        string dataJson = JsonSerializer.Serialize(data);

        // keep a vault copy for the archive
        try
        {
            Directory.CreateDirectory(VaultFolder);

            string path = Path.Combine(
                VaultFolder,
                $"playout-{DateTimeOffset.FromUnixTimeMilliseconds(epochMs).UtcDateTime:yyyyMMdd-HHmm}-{market}.html");

            File.WriteAllText(path, PlayoutTemplate.Replace("__DATA__", dataJson));
        }
        catch { }

        // in-app render: the UI draws this itself
        return JsonSerializer.Serialize(new
        {
            type = "record-playout",
            title = data.title,
            entry, stop, tp3, entryRel, candles
        });
    }

    private const string PlayoutTemplate =
    """
    <!DOCTYPE html><html><head><meta charset="utf-8">
    <style>body{background:#0d1117;color:#e6edf3;font-family:Consolas,monospace;margin:12px}
    canvas{background:#161b22;border:1px solid #30363d}</style></head><body>
    <div id="t"></div><canvas id="c" width="1300" height="560"></canvas>
    <script>
    const D=__DATA__;const cv=document.getElementById("c"),x=cv.getContext("2d");
    document.getElementById("t").textContent=D.title+"   entry "+D.entry+"  stop "+D.stop+"  3R "+D.tp3.toPrecision(6);
    const n=D.candles.length;let hi=-1e18,lo=1e18;
    for(const k of D.candles){if(k[2]>hi)hi=k[2];if(k[3]<lo)lo=k[3];}
    for(const p of[D.entry,D.stop,D.tp3]){if(p>hi)hi=p;if(p<lo)lo=p;}
    const pad=(hi-lo)*.08;hi+=pad;lo-=pad;
    const L=8,R=90,T=8,B=8,W=cv.width-L-R,H=cv.height-T-B;
    const X=i=>L+W*(i+.5)/n,Y=p=>T+H*(hi-p)/(hi-lo);
    const cw=Math.max(2,Math.floor(W/n*.6));
    if(D.entryRel>=0){x.fillStyle="rgba(210,153,34,.10)";x.fillRect(X(D.entryRel),T,L+W-X(D.entryRel),H);}
    for(let i=0;i<n;i++){const[,o,h,l,c]=D.candles[i];const up=c>=o;
      x.strokeStyle=x.fillStyle=up?"#2ea043":"#f85149";
      x.beginPath();x.moveTo(X(i),Y(h));x.lineTo(X(i),Y(l));x.stroke();
      x.fillRect(X(i)-cw/2,Math.min(Y(o),Y(c)),cw,Math.max(1,Math.abs(Y(c)-Y(o))));}
    function hl(p,c,d,lab){x.strokeStyle=c;x.setLineDash(d);x.beginPath();x.moveTo(L,Y(p));x.lineTo(L+W,Y(p));x.stroke();x.setLineDash([]);
      x.fillStyle=c;x.font="12px Consolas";x.fillText(lab+" "+p.toPrecision(6),L+W+4,Y(p)+4);}
    hl(D.entry,"#2ea043",[2,3],"ENTRY");hl(D.stop,"#f85149",[],"STOP");hl(D.tp3,"#2ea043",[6,4],"3R");
    if(D.entryRel>=0){x.fillStyle="#e6edf3";x.font="bold 12px Consolas";x.fillText("E",X(D.entryRel)-4,T+14);}
    </script></body></html>
    """;

    /// <summary>Recent journal rows for drawer replay on launch, so
    /// rebuilds stop erasing the evidence.</summary>
    public static string RecentAlertsJson(int n)
    {
        var items = new List<object>();
        try
        {
            string path = Path.Combine(VaultFolder, "journal.jsonl");
            if (File.Exists(path))
            {
                items = File.ReadAllLines(path)
                    .Where(l => !string.IsNullOrWhiteSpace(l))
                    .Select(l => { try { return JsonNode.Parse(l)!.AsObject(); } catch { return null; } })
                    .Where(r => r != null).Select(r => r!)
                    .OrderByDescending(r => (long?)r["epochMs"] ?? 0)
                    .Take(n)
                    .Select(r => (object)new
                    {
                        status = (string?)r["status"],
                        market = (string?)r["market"],
                        direction = (string?)r["direction"],
                        entry = (double?)r["entry"],
                        reason = (string?)r["reason"],
                        ts = (long?)r["epochMs"] ?? 0
                    })
                    .ToList();
            }
        }
        catch { }
        return JsonSerializer.Serialize(new { type = "drawer-replay", items });
    }

    /// <summary>Was this exact setup already called, and how did it go?</summary>
    public static (bool called, string result) CallInfo(string market, string direction, long sweepTs)
    {
        try
        {
            string path = Path.Combine(VaultFolder, "journal.jsonl");
            if (!File.Exists(path)) return (false, "");

            var rows = File.ReadAllLines(path)
                .Where(l => !string.IsNullOrWhiteSpace(l))
                .Select(l => { try { return JsonNode.Parse(l)!.AsObject(); } catch { return null; } })
                .Where(r => r != null).Select(r => r!)
                .Where(r => (string?)r["market"] == market &&
                            (string?)r["direction"] == direction &&
                            (long?)r["sweepTs"] == sweepTs &&
                            ((string?)r["status"] == "FILLED" || (string?)r["status"] == "WATCH"))
                .ToList();

            if (rows.Count == 0) return (false, "");

            var graded = rows.FirstOrDefault(r => r.ContainsKey("outcomeR"));
            if (graded == null)
                return (true, rows.Any(r => (string?)r["status"] == "FILLED") ? "OPEN" : "WATCHING");

            double net = (double?)graded["netR"] ?? 0;
            return (true, net > 0 ? $"WIN +{net:F2}R" : $"LOSS {net:F2}R");
        }
        catch { return (false, ""); }
    }

    /// <summary>Append an arbitrary row (e.g. SUPPRESSED) to the journal.</summary>
    public static void Append(object row)
    {
        try
        {
            Directory.CreateDirectory(VaultFolder);
            File.AppendAllText(
                Path.Combine(VaultFolder, "journal.jsonl"),
                JsonSerializer.Serialize(row) + Environment.NewLine);
        }
        catch { }
    }

    /// <summary>The open FILLED call on a market (entry/stop/direction), if any.</summary>
    public static (bool open, string direction, double entry, double stop, double risk) OpenCall(string market)
    {
        try
        {
            string path = Path.Combine(VaultFolder, "journal.jsonl");
            if (!File.Exists(path)) return (false, "", 0, 0, 0);

            var r = OpenFilled(market)
                .OrderByDescending(x => (long?)x["epochMs"] ?? 0)
                .FirstOrDefault();

            if (r == null) return (false, "", 0, 0, 0);

            return (true,
                (string?)r["direction"] ?? "",
                (double?)r["entry"] ?? 0,
                (double?)r["stop"] ?? 0,
                (double?)r["riskPerUnit"] ?? 0);
        }
        catch { return (false, "", 0, 0, 0); }
    }

    /// <summary>Signature = the trade itself; duplicate journal rows of
    /// the same trade share it, so a graded duplicate closes them all.
    /// Entry identifies the trade; the stop is EXCLUDED because restart
    /// re-detection can recompute it a tick differently, and that tiny
    /// wobble was creating phantom "open" trades that outlived grading
    /// (stale chart lines, inflated open counts, SKIPPED twins).</summary>
    private static string Sig(JsonObject r) =>
        $"{(string?)r["market"]}|{(string?)r["direction"]}|{(double?)r["entry"]:G9}";

    /// <summary>True once the FILLED call for this market+sweep has an outcome.</summary>
    public static bool IsGraded(string market, long sweepTs)
    {
        try
        {
            string path = Path.Combine(VaultFolder, "journal.jsonl");
            if (!File.Exists(path)) return false;
            return File.ReadAllLines(path)
                .Where(l => !string.IsNullOrWhiteSpace(l))
                .Select(l => { try { return JsonNode.Parse(l)!.AsObject(); } catch { return null; } })
                .Any(r => r != null && (string?)r["status"] == "FILLED" && (string?)r["market"] == market &&
                          (long?)r["sweepTs"] == sweepTs && r.ContainsKey("outcomeR"));
        }
        catch { return false; }
    }

    private static List<JsonObject> OpenFilled(string? market = null)
    {
        string path = Path.Combine(VaultFolder, "journal.jsonl");
        if (!File.Exists(path)) return new();

        var rows = File.ReadAllLines(path)
            .Where(l => !string.IsNullOrWhiteSpace(l))
            .Select(l => { try { return JsonNode.Parse(l)!.AsObject(); } catch { return null; } })
            .Where(r => r != null).Select(r => r!)
            .Where(r => (string?)r["status"] == "FILLED" &&
                        (market == null || (string?)r["market"] == market))
            .ToList();

        var graded = rows.Where(r => r.ContainsKey("outcomeR")).Select(Sig).ToHashSet();

        // 72h hard expiry: the strategy time-stops at 48h, so any row
        // still ungraded past 72h is a stale artifact, not a position.
        long expiry = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - 72L * 3_600_000;

        return rows
            .Where(r => !r.ContainsKey("outcomeR") && !graded.Contains(Sig(r)))
            .Where(r => ((long?)r["epochMs"] ?? 0) > expiry)
            .GroupBy(Sig)
            .Select(g => g.OrderBy(r => (long?)r["epochMs"] ?? 0).First())
            .ToList();
    }

    /// <summary>2026-10-19: the broker is the source of truth for a fill. When the live
    /// executor gets a protected position for a sweep the scanner never journaled as
    /// FILLED (it declared the setup dead after the real fill — BTC 2026-10-19 08:17, "FVG
    /// inverted" at 08:21 — or suppressed it on the cap — XRP 2026-10-06), write the FILLED
    /// row here so the chart pins, the record, the open-call cap and the grader all see
    /// the trade. Idempotent: one FILLED row per market+sweep.</summary>
    public static void EnsureFilledFromBroker(string market, bool isShort, double entry, double stop, long sweepTs, double riskPerUnit, double fill)
    {
        try
        {
            string path = Path.Combine(VaultFolder, "journal.jsonl");
            if (File.Exists(path))
            {
                bool have = File.ReadAllLines(path)
                    .Where(l => !string.IsNullOrWhiteSpace(l))
                    .Select(l => { try { return JsonNode.Parse(l)!.AsObject(); } catch { return null; } })
                    .Any(r => r != null && (string?)r["status"] == "FILLED" && (string?)r["market"] == market && (long?)r["sweepTs"] == sweepTs);
                if (have) return;
            }
            Append(new
            {
                epochMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                sweepTs,
                utcIso = DateTime.UtcNow.ToString("O"),
                market,
                status = "FILLED",
                direction = isShort ? "SHORT" : "LONG",
                entry,
                stop,
                riskPerUnit,
                fill,
                tier = "PLAYBOOK",
                source = "broker-fill"
            });
        }
        catch { }
    }

    /// <summary>Per-market position rule: is a FILLED call still open here?</summary>
    public static bool HasOpenCall(string market)
    {
        try { return OpenFilled(market).Count > 0; }
        catch { return false; }
    }

    /// <summary>A7: today's realized R (UTC day) and open call count.</summary>
    public static (double todayNetR, int openCalls) GetRiskState()
    {
        try
        {
            string path = Path.Combine(VaultFolder, "journal.jsonl");
            if (!File.Exists(path)) return (0, 0);

            long dayStart = new DateTimeOffset(
                DateTime.UtcNow.Date, TimeSpan.Zero).ToUnixTimeMilliseconds();

            var rows = File.ReadAllLines(path)
                .Where(l => !string.IsNullOrWhiteSpace(l))
                .Select(l => JsonNode.Parse(l)!.AsObject())
                .Where(r => (string?)r["status"] == "FILLED")
                .GroupBy(Sig)
                .Select(g => g.OrderByDescending(r => r.ContainsKey("outcomeR"))
                              .ThenBy(r => (long?)r["epochMs"] ?? 0).First())
                .ToList();

            double today = rows
                .Where(r => r.ContainsKey("netR") &&
                            ((long?)r["gradedUtcMs"] ?? (long?)r["epochMs"] ?? 0) >= dayStart)
                .Sum(r => (double?)r["netR"] ?? 0);

            int open = OpenFilled().Count;

            return (today, open);
        }
        catch { return (0, 0); }
    }

    private static string ShortTier(string? tier) =>
        tier == null ? "" :
        tier.StartsWith("PLAYBOOK") ? "PLAYBOOK" :
        tier.StartsWith("NEGATIVE") ? "NEG-EDGE" : "UNPROVEN";

    private static async Task Grade(HttpClient http, JsonObject row)
    {
        string market = (string)row["market"]!;
        bool isShort = (string)row["direction"]! == "SHORT";

        double entry = (double)row["entry"]!;
        double stop = (double)row["stop"]!;
        double risk = (double)row["riskPerUnit"]!;
        long fromMs = (long)row["epochMs"]!;

        double target = isShort
            ? entry - TargetR * risk
            : entry + TargetR * risk;

        string coin = market.EndsWith("USDT") ? market[..^4] : market;

        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        long lastClosed = ((nowMs / FiveMs) * FiveMs) - FiveMs;

        var body = JsonSerializer.Serialize(new
        {
            type = "candleSnapshot",
            req = new
            {
                coin,
                interval = "5m",
                startTime = fromMs,
                endTime = nowMs
            }
        });

        using var content = new StringContent(
            body, System.Text.Encoding.UTF8, "application/json");

        using var resp = await http.PostAsync(
            "https://api.hyperliquid.xyz/info", content);

        resp.EnsureSuccessStatusCode();

        await using var stream = await resp.Content.ReadAsStreamAsync();
        using var doc = await JsonDocument.ParseAsync(stream);

        int walked = 0;
        double lastClose = entry;
        bool resolved = false;

        foreach (JsonElement k in doc.RootElement.EnumerateArray())
        {
            long open = k.GetProperty("t").GetInt64();
            if (open > lastClosed) continue;

            walked++;
            if (walked > WalkWindow) break;

            double high = P(k.GetProperty("h"));
            double low = P(k.GetProperty("l"));
            lastClose = P(k.GetProperty("c"));

            bool stopped = isShort ? high >= stop : low <= stop;
            bool hit = isShort ? low <= target : high >= target;

            if (stopped)
            {
                Write(row, -1.0, MakerFee + TakerFee, entry, risk);
                resolved = true;
                break;
            }

            if (hit)
            {
                Write(row, TargetR, MakerFee + MakerFee, entry, risk);
                resolved = true;
                break;
            }
        }

        if (!resolved && walked >= WalkWindow)
        {
            double exitR = isShort
                ? (entry - lastClose) / risk
                : (lastClose - entry) / risk;

            Write(row, exitR, MakerFee + TakerFee, entry, risk);
        }
        // fewer than WalkWindow candles and no hit = still open: no grade yet
    }

    private static void Write(
        JsonObject row,
        double resultR,
        double fee,
        double entry,
        double risk)
    {
        double feeR = fee * entry / risk;

        row["outcomeR"] = resultR;
        row["netR"] = resultR - feeR;
        row["gradedUtc"] = DateTime.UtcNow.ToString("O");
    }

    private static double P(JsonElement e) =>
        double.Parse(
            e.GetString()!,
            System.Globalization.CultureInfo.InvariantCulture);
}
