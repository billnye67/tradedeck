using System.Net.Http;
using System.Text.Json.Nodes;

namespace TradeDeck;

/// <summary>
/// Forex Midnight (registry 135, R13X): the "anti-ICT" midnight-open momentum model, ported 1:1 from
/// tools/r13_midnight.py day_trade() (15m bars, both sides) traded OPPOSITE (cell T15/NB/OPP).
///   ICT side +1 (bullish order block below the 00:00 NY open): we SELL when price falls to E = OB high + 5 pips.
///   ICT side -1 (bearish order block above the 00:00 NY open): we BUY when price rises to E = OB low - 5 pips.
///   Stop = E -/+ risk, risk = distance from E to the far side of the OB (+1 pip). No target. Flat at 16:55 NY.
///   Entries only inside the London (02-05 NY) and New York (07-10 NY) kill zones; one trade per pair, side and day.
/// Prices: Yahoo Finance 5m (free; within a fraction of a pip of Broker's own FX quotes). Orders go through BrokerExecutor.
/// </summary>
public static class FxMidnightEngine
{
    public sealed record Bar(long OpenMs, double O, double H, double L, double C, DateTime Ny);
    public sealed record Candidate(int IctSide, double Trigger, double Stop, long ObMs, long ValidMs);

    public static readonly (double a, double b)[] KillZones = { (2, 5), (7, 10) };
    public static bool InKillZone(DateTime ny) { double h = ny.Hour + ny.Minute / 60.0; return KillZones.Any(z => h >= z.a && h < z.b); }
    public static double Pip(string pair) => pair.EndsWith("JPY") ? 0.01 : 0.0001;

    /// <summary>
    /// Every order block that is valid for the bar about to trade (index q = number of completed 15m bars), exactly as
    /// day_trade() filters them: validated before q (a later bar broke the OB candle's high/low), E on the right side of
    /// the midnight open, positive risk, and the OB's far side not traded through between validation and q.
    /// Newest validation first, as the backtest sorts them.
    /// </summary>
    public static List<Candidate> Valid(IReadOnlyList<Bar> b, int q, double midnightOpen, int side, double pip)
    {
        var obs = new List<(int j, int k)>();
        for (int k = 0; k < Math.Min(q, b.Count) - 1; k++)
        {
            bool down = side == 1 ? b[k].C < b[k].O : b[k].C > b[k].O;
            if (!down) continue;
            for (int j = k + 1; j < q; j++)
                if (side == 1 ? b[j].H > b[k].H : b[j].L < b[k].L) { obs.Add((j, k)); break; }
        }
        var outList = new List<Candidate>();
        foreach (var (j, k) in obs.OrderByDescending(x => x.j))
        {
            double e = side == 1 ? b[k].H + 5 * pip : b[k].L - 5 * pip;
            if (side == 1 ? e >= midnightOpen : e <= midnightOpen) continue;
            double stopIct = side == 1 ? b[k].L - pip : b[k].H + pip;
            if ((e - stopIct) * side <= 0) continue;
            bool broken = false;
            for (int m = j; m < q; m++) if (side == 1 ? b[m].L < stopIct : b[m].H > stopIct) { broken = true; break; }
            if (broken) continue;
            double risk = Math.Abs(e - stopIct);
            double stop = side == 1 ? e + risk : e - risk;          // traded OPPOSITE: ICT long becomes our short, stop above
            outList.Add(new Candidate(side, e, stop, b[k].OpenMs, b[j].OpenMs));
        }
        return outList;
    }

    /// <summary>Which valid candidate a resting order should sit on: the one price reaches first from where it is now.</summary>
    public static Candidate? Pick(List<Candidate> valid, double lastPrice, double pip, out string why)
    {
        why = "";
        if (valid.Count == 0) { why = "no valid order block"; return null; }
        int side = valid[0].IctSide;
        // ICT +1 -> we SELL as price FALLS to E: E must be at or below price (sell stop). Price already more than 2 pips below E = missed.
        var ahead = valid.Where(c => side == 1 ? c.Trigger <= lastPrice + 2 * pip : c.Trigger >= lastPrice - 2 * pip).ToList();
        if (ahead.Count == 0) { why = "price already through every trigger"; return null; }
        return side == 1 ? ahead.OrderByDescending(c => c.Trigger).First() : ahead.OrderBy(c => c.Trigger).First();
    }

    /// <summary>5m bars (UTC ms) -> 15m bars on the New York clock, from the prior NY day 14:00 through the last COMPLETED 15m bar.</summary>
    public static List<Bar> To15m(IReadOnlyList<(long t, double o, double h, double l, double c)> five, DateTime nyToday, long nowMs)
    {
        var tz = NyZone; var start = nyToday.AddDays(-1).AddHours(14);
        var groups = new SortedDictionary<long, List<(long t, double o, double h, double l, double c)>>();
        foreach (var x in five)
        {
            var ny = TimeZoneInfo.ConvertTimeFromUtc(DateTimeOffset.FromUnixTimeMilliseconds(x.t).UtcDateTime, tz);
            if (ny < start || ny >= nyToday.AddHours(17)) continue;
            long key = x.t - x.t % (15 * 60_000);
            if (key + 15 * 60_000 > nowMs) continue;                     // keep completed 15m bars only
            if (!groups.TryGetValue(key, out var g)) groups[key] = g = new();
            g.Add(x);
        }
        return groups.Select(kv =>
        {
            var g = kv.Value.OrderBy(z => z.t).ToList();
            var ny = TimeZoneInfo.ConvertTimeFromUtc(DateTimeOffset.FromUnixTimeMilliseconds(kv.Key).UtcDateTime, tz);
            return new Bar(kv.Key, g[0].o, g.Max(z => z.h), g.Min(z => z.l), g[^1].c, ny);
        }).ToList();
    }

    public static readonly TimeZoneInfo NyZone = FindNy();
    private static TimeZoneInfo FindNy()
    {
        try { return TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time"); } catch { return TimeZoneInfo.FindSystemTimeZoneById("America/New_York"); }
    }
}

/// <summary>Runs the forex brain once a minute and hands armed setups to the executor. Places nothing itself.</summary>
public sealed class FxMidnightService : IDisposable
{
    private readonly BrokerExecutor _exec;
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private CancellationTokenSource? _cts;
    public string Status { get; private set; } = "off";

    public FxMidnightService(BrokerExecutor exec)
    {
        _exec = exec;
        _http.DefaultRequestHeaders.UserAgent.ParseAdd("Mozilla/5.0");
    }

    public void Start()
    {
        if (_cts != null) return;
        _cts = new CancellationTokenSource();
        _ = Task.Run(() => Loop(_cts.Token));
    }
    public void Stop() { _cts?.Cancel(); _cts = null; }
    public void Dispose() { Stop(); _http.Dispose(); }

    private async Task Loop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try { if (_exec.FxMode != "off") await Tick(); else Status = "off (BROKER_FX_MODE)"; }
            catch (Exception ex) { Status = "error: " + ex.Message; _exec.LogEvent(new { ev = "fx-error", error = ex.Message }); }
            try { await Task.Delay(TimeSpan.FromSeconds(60), ct); } catch { break; }
        }
    }

    private async Task Tick()
    {
        long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        var ny = TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, FxMidnightEngine.NyZone);
        if (ny.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) { Status = "weekend"; return; }
        var today = ny.Date;
        foreach (var pair in _exec.FxMarkets)
        {
            var five = await Fetch(pair);
            if (five.Count == 0) continue;
            var bars = FxMidnightEngine.To15m(five, today, now);
            int i0 = bars.FindIndex(x => x.Ny.Date == today);
            if (i0 < 0) continue;
            double mo = five.Select(x => (x, nyT: TimeZoneInfo.ConvertTimeFromUtc(DateTimeOffset.FromUnixTimeMilliseconds(x.t).UtcDateTime, FxMidnightEngine.NyZone)))
                            .Where(z => z.nyT >= today).OrderBy(z => z.x.t).Select(z => z.x.o).FirstOrDefault();
            if (mo == 0) continue;
            double last = five[^1].c, pip = FxMidnightEngine.Pip(pair);
            bool inKz = FxMidnightEngine.InKillZone(ny);
            double kzEndH = ny.Hour < 5 ? 5 : 10;
            long kzEndMs = new DateTimeOffset(TimeZoneInfo.ConvertTimeToUtc(today.AddHours(kzEndH), FxMidnightEngine.NyZone)).ToUnixTimeMilliseconds();
            foreach (int side in new[] { 1, -1 })
            {
                bool isShort = side == 1;                                  // ICT bullish OB -> we sell
                if (!inKz) { _exec.OnFxDisarm(pair, isShort, "outside kill zone"); continue; }
                if (_exec.FxDoneToday(pair, isShort, today)) continue;
                var valid = FxMidnightEngine.Valid(bars, bars.Count, mo, side, pip);
                var c = FxMidnightEngine.Pick(valid, last, pip, out string why);
                if (c == null) { _exec.OnFxDisarm(pair, isShort, why); continue; }
                _exec.OnFxArm(pair, isShort, c.Trigger, c.Stop, kzEndMs, today, $"{pair}|{(isShort ? "S" : "B")}|{c.ObMs}|{c.ValidMs}", last);
            }
        }
        Status = $"running | {(FxMidnightEngine.InKillZone(ny) ? "in kill zone" : "waiting for kill zone")} | {DateTime.UtcNow:HH:mm} UTC";
    }

    /// <summary>Yahoo Finance 5m candles for the last 2 days, completed bars only.</summary>
    private async Task<List<(long t, double o, double h, double l, double c)>> Fetch(string pair)
    {
        var res = new List<(long, double, double, double, double)>();
        string url = $"https://query1.finance.yahoo.com/v8/finance/chart/{pair}=X?interval=5m&range=2d";
        var j = JsonNode.Parse(await _http.GetStringAsync(url));
        var r = j?["chart"]?["result"]?[0]; var ts = r?["timestamp"]?.AsArray(); var q = r?["indicators"]?["quote"]?[0];
        if (ts == null || q == null) return res;
        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        for (int i = 0; i < ts.Count; i++)
        {
            double? o = (double?)q["open"]?[i], h = (double?)q["high"]?[i], l = (double?)q["low"]?[i], c = (double?)q["close"]?[i];
            if (o == null || h == null || l == null || c == null) continue;
            long t = (long)ts[i]! * 1000;
            if (t + 5 * 60_000 > nowMs) continue;                          // drop the bar still forming
            res.Add((t, o.Value, h.Value, l.Value, c.Value));
        }
        return res;
    }
}
