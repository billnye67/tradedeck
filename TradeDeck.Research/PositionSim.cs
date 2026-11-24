using System.IO;
namespace TradeDeck.Research;

/// <summary>
/// EXECUTABLE SIMULATION — one open position per market.
/// The independent-trade backtest let overlapping setups on the
/// same coin all count. A one-way account can't do that. This
/// replays Strategy #1 chronologically per market: while a
/// position is open (entry -> TP/SL/window exit), every new setup
/// on that market is IGNORED (same or opposite direction), per the
/// frozen PER-MARKET POSITION RULE (GPT audit). No close-and-reverse.
///
/// Reports suppressed counts (same/opposite) and n / exp / total /
/// PF / max DD per era for INDEPENDENT vs EXECUTABLE.
/// Run: dotnet run sim
/// </summary>
public static class PositionSim
{
    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    // Per-side costs. Crypto default = Binance maker 1.5bp / taker 4.5bp (= Broker 3+3 round trip).
    // TD_MAKER_BP / TD_TAKER_BP override (e.g. forex: 0.4bp commission + half-spread each side).
    private static readonly double MakerFee = double.TryParse(Environment.GetEnvironmentVariable("TD_MAKER_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var mk) ? mk / 10_000.0 : 0.00015;
    private static readonly double TakerFee = double.TryParse(Environment.GetEnvironmentVariable("TD_TAKER_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var tk) ? tk / 10_000.0 : 0.00045;
    private static readonly double SlipBp = double.TryParse(Environment.GetEnvironmentVariable("TD_SLIP_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var sl) ? sl / 10_000.0 : 0.0;
    private static readonly double SwapBp = double.TryParse(Environment.GetEnvironmentVariable("TD_SWAP_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var sw) ? sw / 10_000.0 : 0.0;
    // TD_SLIP_MODE=stops: entry is a resting LIMIT at the retrace price and the target is a LIMIT, so only
    // the stop (a market fill) slips. Default: every execution slips.
    private static readonly bool SlipStopsOnly = Environment.GetEnvironmentVariable("TD_SLIP_MODE") == "stops";
    private static double Extra(IReadOnlyList<ResearchCandle> c5, int entryIdx, int exitIdx, bool stopped) =>
        (SlipStopsOnly ? (stopped ? SlipBp : 0.0) : 2 * SlipBp) + SwapBp * (c5[exitIdx].Timestamp / 86_400_000L - c5[entryIdx].Timestamp / 86_400_000L);

    private sealed record Tr(string Market, long SweepTs, long EntryTs, long ExitTs, int EntryIdx, int ExitIdx,
        bool IsShort, double NetR, double Fvg, double Breach, double LevelAway, double CrowdAligned,
        double EntryPrice, bool Aligned)
    {
        public double Score { get; set; }
    }

    public static void Run(string source, string[] markets, Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        var independent = new List<Tr>();
        var executable = new List<Tr>();
        int supSame = 0, supOpp = 0;

        string dbPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "TradeDeck", "Data", "history.sqlite");
        using var db = new Microsoft.Data.Sqlite.SqliteConnection($"Data Source={dbPath}");
        db.Open();

        foreach (string market in markets)
        {
            var c15 = reader.GetAllCandles(source, market, "15m");
            var c5 = reader.GetAllCandles(source, market, "5m");
            if (c15.Count == 0 || c5.Count == 0) continue;

            double[] atr = Atr(c15, 20);

            // daily closes for 30d trend alignment (F4)
            var dayClose = new Dictionary<long, double>();
            {
                long cdD = -1; double clD = 0;
                foreach (var k in c15)
                {
                    long dd = k.Timestamp / 86_400_000L;
                    if (dd != cdD) { if (cdD >= 0) dayClose[cdD] = clD; cdD = dd; }
                    clD = k.Close;
                }
                if (cdD >= 0) dayClose[cdD] = clD;
            }
            var funding = LoadFunding(db, market);
            var learnRates = funding.Where(f => f.ts < eraSplit).Select(f => f.rate).OrderBy(x => x).ToList();

            var trades = new List<Tr>();

            foreach (var raid in SweepDetector.FindCandidates(market, c15))
            {
                FullSetup s = SetupDetector.Build(raid, c5);
                if (s.Stage != SetupStage.Complete || s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;

                bool isShort = s.Raid.Direction == "HIGH";
                double breach = Math.Abs(s.SweepExtreme - raid.Level) / raid.Level * 100;

                int p = raid.PivotIndex;
                int firstBreach = -1;
                for (int i = p + 1; i < c15.Count; i++)
                {
                    bool b = isShort ? c15[i].High > raid.Level : c15[i].Low < raid.Level;
                    if (b) { firstBreach = i; break; }
                }
                double levelAway = firstBreach > 0 && p < atr.Length
                    ? SweepDetector.DisplacementAwayAtr(c15, atr, p, firstBreach, raid.Level, isShort) : 0;

                double aligned0 = 50;
                if (learnRates.Count > 300)
                {
                    int fi = LastIndexBefore(funding, raid.SweepTimestamp);
                    if (fi >= 0)
                    {
                        int below = LowerBound(learnRates, funding[fi].rate);
                        double pct = 100.0 * below / learnRates.Count;
                        aligned0 = isShort ? pct : 100 - pct;
                    }
                }

                bool aligned = false;
                {
                    long d0 = raid.SweepTimestamp / 86_400_000L;
                    if (dayClose.TryGetValue(d0 - 1, out double nowC) &&
                        dayClose.TryGetValue(d0 - 31, out double oldC))
                        aligned = (nowC > oldC) == (raid.Direction == "LOW");
                }

                (double r, int exitIdx) = Walk(s, c5);
                trades.Add(new Tr(market, raid.SweepTimestamp,
                    c5[s.EntryIndex5m].Timestamp, c5[exitIdx].Timestamp,
                    s.EntryIndex5m, exitIdx,
                    isShort, r, s.FvgSizeRatio, breach, levelAway, aligned0,
                    s.EntryPrice, aligned));
            }

            trades = trades.OrderBy(t => t.EntryIdx).ToList();
            independent.AddRange(trades);

            // chronological: one open position at a time
            int busyUntil = -1;
            bool busyShort = false;

            foreach (var t in trades)
            {
                if (t.EntryIdx <= busyUntil)
                {
                    if (t.IsShort == busyShort) supSame++; else supOpp++;
                    continue;
                }

                executable.Add(t);
                busyUntil = t.ExitIdx;
                busyShort = t.IsShort;
            }

            Console.WriteLine($"{market}: {trades.Count:N0} independent -> " +
                              $"{executable.Count(e => e.Market == market):N0} executable");
        }

        // ---- COMPOSITE QUALITY SCORE (learn-era percentile ranks, averaged) ----
        var learnAll = independent.Where(t => t.SweepTs < eraSplit).ToList();
        var fvgS = learnAll.Select(t => t.Fvg).OrderBy(x => x).ToList();
        var brS = learnAll.Select(t => t.Breach).OrderBy(x => x).ToList();
        var awS = learnAll.Select(t => t.LevelAway).OrderBy(x => x).ToList();
        var crS = learnAll.Select(t => t.CrowdAligned).OrderBy(x => x).ToList();
        double Pct(List<double> s, double v) => s.Count == 0 ? 50 : 100.0 * LowerBound(s, v) / s.Count;
        foreach (var t in independent)
            t.Score = (Pct(fvgS, t.Fvg) + Pct(brS, t.Breach) + Pct(awS, t.LevelAway) + Pct(crS, t.CrowdAligned)) / 4.0;

        // ---- GLOBAL portfolio models: one account, 2 slots, correlation-aware ----
        var global2 = Portfolio(independent, maxOpen: 2, maxSameDir: 2, minFvg: 0);
        if (Environment.GetEnvironmentVariable("TD_DUMP") is string dumpPath && dumpPath.Length > 0)
        {
            File.WriteAllLines(dumpPath, new[] { "market,entryTs,exitTs,netR,aligned" }.Concat(
                global2.OrderBy(t => t.EntryTs).Select(t => $"{t.Market},{t.EntryTs},{t.ExitTs},{t.NetR:F4},{(t.Aligned ? 1 : 0)}")));
            File.WriteAllLines(dumpPath.Replace(".csv", "-pyr.csv"), new[] { "market,entryTs,exitTs,netR,aligned" }.Concat(
                PortfolioPyramid(independent, 2).OrderBy(t => t.EntryTs).Select(t => $"{t.Market},{t.EntryTs},{t.ExitTs},{t.NetR:F4},{(t.Aligned ? 1 : 0)}")));
        }
        var global2Dir1 = Portfolio(independent, maxOpen: 2, maxSameDir: 1, minFvg: 0);
        var global2Q = Portfolio(independent, maxOpen: 2, maxSameDir: 2, minFvg: 0.60);   // quality bar (frozen)
        var global3 = Portfolio(independent, maxOpen: 3, maxSameDir: 2, minFvg: 0);

        Console.WriteLine();
        Console.WriteLine($"SUPPRESSED (per-coin rule): {supSame:N0} same-direction, {supOpp:N0} opposite-direction");
        Console.WriteLine();
        Console.WriteLine($"{"model",-24} | {"LEARN n",7} {"exp",7} {"total",8} {"PF",5} {"maxDD",7} | {"EXAM n",7} {"exp",7} {"total",8} {"PF",5} {"maxDD",7}");
        Console.WriteLine(new string('-', 108));
        Row("INDEPENDENT (fantasy)", independent, eraSplit);
        Row("PER-COIN 1 open", executable, eraSplit);
        Row("GLOBAL 2 open", global2, eraSplit);
        Row("GLOBAL 2, max 1 per dir", global2Dir1, eraSplit);
        Row("GLOBAL 2 + FVG>=0.6 bar", global2Q, eraSplit);
        Row("GLOBAL 3 open", global3, eraSplit);
        Console.WriteLine();
        Console.WriteLine("QUALITY BAR ROBUSTNESS — gap-only bar at neighbors");
        foreach (double bar in new[] { 0.45, 0.50, 0.60, 0.70, 0.80 })
            Row($"GLOBAL 2 + FVG>={bar:F2}", Portfolio(independent, 2, 2, bar), eraSplit);

        Console.WriteLine();
        Console.WriteLine("F4 SLOT RANKING — when signals land in the same hour, WITH-30d-trend takes the slot");
        Row("GLOBAL 2 first-come", global2, eraSplit);
        Row("GLOBAL 2 RANKED", PortfolioRanked(independent, 2, 2), eraSplit);
        Row("GLOBAL 2 ALIGNED-ONLY", Portfolio(independent.Where(t => t.Aligned).ToList(), 2, 2, 0), eraSplit);

        Console.WriteLine();
        Console.WriteLine("PYRAMIDING — take the 2nd same-direction signal on a market while the 1st is IN PROFIT");
        Row("GLOBAL 2 (no pyramid)", global2, eraSplit);
        Row("GLOBAL 2 + PYRAMID", PortfolioPyramid(independent, 2), eraSplit);
        Row("GLOBAL 3 + PYRAMID", PortfolioPyramid(independent, 3), eraSplit);

        Console.WriteLine();
        Console.WriteLine("COMPOSITE 'clean setup' SCORE — level-away + breach + gap + crowd (avg percentile)");
        foreach (double bar in new[] { 30, 40, 50, 60, 70 })
            Row($"GLOBAL 2 + score>={bar:F0}", PortfolioScore(independent, 2, 2, bar), eraSplit);
        Console.WriteLine();
        Console.WriteLine("GLOBAL rows = one account, correlation-aware. The one you can live with becomes the promise line.");
    }

    /// <summary>First-come replay, except signals arriving within the same
    /// UTC hour are admitted with-trend first (F4 ranking).</summary>
    private static List<Tr> PortfolioRanked(List<Tr> all, int maxOpen, int maxSameDir)
    {
        var taken = new List<Tr>(); var open = new List<Tr>();

        foreach (var batch in all.OrderBy(x => x.EntryTs).GroupBy(x => x.EntryTs / 3_600_000L).OrderBy(g => g.Key))
        {
            foreach (var t in batch.OrderByDescending(x => x.Aligned).ThenBy(x => x.EntryTs))
            {
                open.RemoveAll(o => o.ExitTs <= t.EntryTs);
                if (open.Count >= maxOpen) continue;
                if (open.Count(o => o.IsShort == t.IsShort) >= maxSameDir) continue;
                if (open.Any(o => o.Market == t.Market)) continue;
                open.Add(t); taken.Add(t);
            }
        }
        return taken;
    }

    /// <summary>Global slots; a same-direction ADD on the same market is
    /// allowed (max 2 units/market) if the first unit is currently in
    /// profit at the add signal's entry price. Opposite still blocked.</summary>
    private static List<Tr> PortfolioPyramid(List<Tr> all, int maxOpen)
    {
        var taken = new List<Tr>(); var open = new List<Tr>();

        foreach (var t in all.OrderBy(x => x.EntryTs))
        {
            open.RemoveAll(o => o.ExitTs <= t.EntryTs);
            if (open.Count >= maxOpen) continue;

            var sameMkt = open.Where(o => o.Market == t.Market).ToList();

            if (sameMkt.Count > 0)
            {
                if (sameMkt.Count >= 2) continue;                    // max 2 units
                var first = sameMkt[0];
                if (first.IsShort != t.IsShort) continue;            // opposite blocked

                // "in profit": price has moved from first entry toward
                // profit by the time the add triggers. Entry prices are
                // on the same market, so compare directly.
                bool inProfit = first.IsShort
                    ? t.EntryPrice < first.EntryPrice
                    : t.EntryPrice > first.EntryPrice;

                if (!inProfit) continue;
            }

            open.Add(t); taken.Add(t);
        }

        return taken;
    }

    private static List<Tr> PortfolioScore(List<Tr> all, int maxOpen, int maxSameDir, double minScore)
    {
        var taken = new List<Tr>(); var open = new List<Tr>();
        foreach (var t in all.OrderBy(x => x.EntryTs))
        {
            open.RemoveAll(o => o.ExitTs <= t.EntryTs);
            if (t.Score < minScore) continue;
            if (open.Count >= maxOpen) continue;
            if (open.Count(o => o.IsShort == t.IsShort) >= maxSameDir) continue;
            if (open.Any(o => o.Market == t.Market)) continue;
            open.Add(t); taken.Add(t);
        }
        return taken;
    }

    private static List<(long ts, double rate)> LoadFunding(Microsoft.Data.Sqlite.SqliteConnection db, string market)
    {
        var list = new List<(long, double)>();
        using var cmd = db.CreateCommand();
        cmd.CommandText = "SELECT ts, rate FROM funding_rates WHERE source='binance_um' AND market=$m ORDER BY ts;";
        cmd.Parameters.AddWithValue("$m", market);
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add((r.GetInt64(0), r.GetDouble(1)));
        return list;
    }

    private static int LastIndexBefore(List<(long ts, double rate)> f, long ts)
    {
        int lo = 0, hi = f.Count - 1, ans = -1;
        while (lo <= hi) { int mid = (lo + hi) / 2; if (f[mid].ts < ts) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
        return ans;
    }

    private static int LowerBound(List<double> sorted, double v)
    {
        int lo = 0, hi = sorted.Count;
        while (lo < hi) { int mid = (lo + hi) / 2; if (sorted[mid] < v) lo = mid + 1; else hi = mid; }
        return lo;
    }

    private static double[] Atr(IReadOnlyList<ResearchCandle> c, int len)
    {
        var atr = new double[c.Count]; var trs = new double[c.Count]; double sum = 0;
        for (int i = 1; i < c.Count; i++)
        {
            double tr = Math.Max(c[i].High - c[i].Low,
                Math.Max(Math.Abs(c[i].High - c[i - 1].Close), Math.Abs(c[i].Low - c[i - 1].Close)));
            trs[i] = tr; sum += tr;
            if (i > len) sum -= trs[i - len];
            if (i >= len) atr[i] = sum / len;
        }
        return atr;
    }

    /// <summary>Chronological one-account replay across ALL markets.</summary>
    private static List<Tr> Portfolio(List<Tr> all, int maxOpen, int maxSameDir, double minFvg)
    {
        var taken = new List<Tr>();
        var open = new List<Tr>();

        foreach (var t in all.OrderBy(x => x.EntryTs))
        {
            open.RemoveAll(o => o.ExitTs <= t.EntryTs);

            if (t.Fvg < minFvg) continue;
            if (open.Count >= maxOpen) continue;
            if (open.Count(o => o.IsShort == t.IsShort) >= maxSameDir) continue;
            if (open.Any(o => o.Market == t.Market)) continue;

            open.Add(t);
            taken.Add(t);
        }

        return taken;
    }

    private static void Row(string name, List<Tr> all, long eraSplit)
    {
        var l = all.Where(t => t.SweepTs < eraSplit).OrderBy(t => t.SweepTs).ToList();
        var e = all.Where(t => t.SweepTs >= eraSplit).OrderBy(t => t.SweepTs).ToList();
        Console.WriteLine($"{name,-24} | {Stats(l)} | {Stats(e)}");
    }

    private static string Stats(List<Tr> rows)
    {
        if (rows.Count == 0) return $"{0,7} {0,7:F3} {0,8:F1} {0,5:F2} {0,7:F1}";
        double total = rows.Sum(r => r.NetR);
        double gw = rows.Where(r => r.NetR > 0).Sum(r => r.NetR);
        double gl = Math.Abs(rows.Where(r => r.NetR < 0).Sum(r => r.NetR));
        double pf = gl > 0 ? gw / gl : 0;
        double peak = 0, dd = 0, cum = 0;
        foreach (var r in rows) { cum += r.NetR; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
        return $"{rows.Count,7:N0} {total / rows.Count,7:F3} {total,8:F1} {pf,5:F2} {dd,7:F1}";
    }

    private static (double r, int exitIdx) Walk(FullSetup s, IReadOnlyList<ResearchCandle> c5)
    {
        bool isShort = s.Raid.Direction == "HIGH";
        double entry = s.EntryPrice, stop = s.StopLoss, risk = s.RiskPerUnit;
        double target = isShort ? entry - TargetR * risk : entry + TargetR * risk;
        int limit = Math.Min(s.EntryIndex5m + WalkWindow, c5.Count - 1);
        int last = s.EntryIndex5m;

        for (int i = s.EntryIndex5m; i <= limit; i++)
        {
            last = i;
            bool stopped = isShort ? c5[i].High >= stop : c5[i].Low <= stop;
            if (stopped) return (-1.0 - (MakerFee + TakerFee + Extra(c5, s.EntryIndex5m, i, true)) * entry / risk, i);
            bool hit = isShort ? c5[i].Low <= target : c5[i].High >= target;
            if (hit) return (TargetR - (MakerFee + MakerFee + Extra(c5, s.EntryIndex5m, i, false)) * entry / risk, i);
        }

        double exitR = isShort ? (entry - c5[last].Close) / risk : (c5[last].Close - entry) / risk;
        return (exitR - (MakerFee + TakerFee + Extra(c5, s.EntryIndex5m, last, true)) * entry / risk, last);
    }
}
