namespace TradeDeck.Research;

/// <summary>
/// LEVEL DEFINITION — ABLATION STUDY (GPT audit spec).
/// 2020-2023 = discovery, 2024-2026 = validation (it informed
/// feature selection, so it is NOT a virgin holdout for this rule).
///
/// Rows: OLD 8h | RAW permissive | P | D | A | P+D | P+A | D+A | P+D+A
/// Metrics per era: n, NET exp, total NET R, PF, max DD.
/// Breadth: per-year and per-market NET for RAW vs OLD.
/// Correlations: Spearman between prominence, displacement, age.
/// Robustness: single-feature floors at neighboring thresholds.
/// Nothing here changes the playbook. Run: dotnet run levelablate
/// </summary>
public static class LevelAblation
{
    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;
    private const int AtrLen = 20;
    private const int PivotStrength = 3;

    private sealed record R(
        string Market, long SweepTs, double NetR,
        double P, double D, double A, bool Available);

    public static void Run(
        string source,
        string[] markets,
        Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        int origProm = SweepDetector.ProminenceLookback;
        int origExp = SweepDetector.MaxSweepDelay;

        SweepDetector.ProminenceLookback = 32; SweepDetector.MaxSweepDelay = 96;
        var old = Collect(reader, source, markets, checklist);

        SweepDetector.ProminenceLookback = 8; SweepDetector.MaxSweepDelay = 672;
        var raw = Collect(reader, source, markets, checklist);

        SweepDetector.ProminenceLookback = origProm; SweepDetector.MaxSweepDelay = origExp;

        int notAvail = raw.Count(r => !r.Available);
        Console.WriteLine();
        Console.WriteLine($"LOOKAHEAD AUDIT: {notAvail} of {raw.Count:N0} raw rows had feature bars not closed before the sweep (should be 0).");

        // ---- ablation table ----
        double p0 = 1.79, d0 = 2.00, a0 = 11;

        var variants = new (string name, Func<R, bool> keep, List<R> src)[]
        {
            ("OLD 8h rule", _ => true, old),
            ("RAW permissive", _ => true, raw),
            ("P only", r => r.P >= p0, raw),
            ("D only", r => r.D >= d0, raw),
            ("A only", r => r.A >= a0, raw),
            ("P+D", r => r.P >= p0 && r.D >= d0, raw),
            ("P+A", r => r.P >= p0 && r.A >= a0, raw),
            ("D+A", r => r.D >= d0 && r.A >= a0, raw),
            ("P+D+A", r => r.P >= p0 && r.D >= d0 && r.A >= a0, raw),
        };

        Console.WriteLine();
        Console.WriteLine("ABLATION (discovery 2020-23 | validation 2024-26)");
        Console.WriteLine(
            $"{"rule",-15} | {"n",6} {"exp",7} {"total",8} {"PF",5} {"maxDD",7} | {"n",6} {"exp",7} {"total",8} {"PF",5} {"maxDD",7}");
        Console.WriteLine(new string('-', 100));

        foreach (var (name, keep, src) in variants)
        {
            var rows = src.Where(r => r.Available && keep(r)).ToList();
            var l = rows.Where(r => r.SweepTs < eraSplit).ToList();
            var e = rows.Where(r => r.SweepTs >= eraSplit).ToList();
            Console.WriteLine($"{name,-15} | {Stats(l)} | {Stats(e)}");
        }

        // ---- breadth: per year, RAW vs OLD ----
        Console.WriteLine();
        Console.WriteLine("BREADTH BY YEAR (NET total R): OLD vs RAW");
        var years = raw.Concat(old).Select(r => Year(r.SweepTs)).Distinct().OrderBy(y => y);
        foreach (int y in years)
        {
            var o = old.Where(r => Year(r.SweepTs) == y).ToList();
            var w = raw.Where(r => r.Available && Year(r.SweepTs) == y).ToList();
            Console.WriteLine(
                $"   {y} | OLD n {o.Count,4} {o.Sum(r => r.NetR),7:F1}R | RAW n {w.Count,4} {w.Sum(r => r.NetR),7:F1}R");
        }

        Console.WriteLine();
        Console.WriteLine("BREADTH BY MARKET (NET total R): OLD vs RAW");
        foreach (string m in markets)
        {
            var o = old.Where(r => r.Market == m).ToList();
            var w = raw.Where(r => r.Available && r.Market == m).ToList();
            Console.WriteLine(
                $"   {m,-8} | OLD n {o.Count,4} {o.Sum(r => r.NetR),7:F1}R | RAW n {w.Count,4} {w.Sum(r => r.NetR),7:F1}R");
        }

        // ---- correlations (Spearman) among P, D, A on discovery era ----
        var disc = raw.Where(r => r.Available && r.SweepTs < eraSplit).ToList();
        Console.WriteLine();
        Console.WriteLine("SPEARMAN CORRELATIONS (discovery era)");
        Console.WriteLine($"   P~D {Spearman(disc.Select(r => r.P), disc.Select(r => r.D)):F2} | " +
                          $"P~A {Spearman(disc.Select(r => r.P), disc.Select(r => r.A)):F2} | " +
                          $"D~A {Spearman(disc.Select(r => r.D), disc.Select(r => r.A)):F2}");

        // ---- threshold neighborhood robustness (single features) ----
        Console.WriteLine();
        Console.WriteLine("ROBUSTNESS — single-feature floors at neighboring thresholds (exp per era)");
        Neighborhood("prominence >=", new[] { 1.5, 1.8, 2.1 }, raw, eraSplit, r => r.P);
        Neighborhood("displacement >=", new[] { 1.7, 2.0, 2.3 }, raw, eraSplit, r => r.D);
        Neighborhood("age >=", new[] { 8.0, 11.0, 14.0 }, raw, eraSplit, r => r.A);

        Console.WriteLine();
        Console.WriteLine("GPT bar before playbook change: breadth across years+markets, plateau not spike,");
        Console.WriteLine("combination adds beyond simpler rule, total R/PF/DD don't degrade, walk-forward, then live.");
    }

    private static void Neighborhood(
        string label, double[] ts, List<R> raw, long eraSplit, Func<R, double> f)
    {
        foreach (double t in ts)
        {
            var rows = raw.Where(r => r.Available && f(r) >= t).ToList();
            var l = rows.Where(r => r.SweepTs < eraSplit).ToList();
            var e = rows.Where(r => r.SweepTs >= eraSplit).ToList();
            Console.WriteLine(
                $"   {label} {t,5:F2} | disc n {l.Count,5} exp {Exp(l),6:F3} | val n {e.Count,5} exp {Exp(e),6:F3}");
        }
    }

    private static string Stats(List<R> rows)
    {
        if (rows.Count == 0) return $"{0,6} {0,7:F3} {0,8:F1} {0,5:F2} {0,7:F1}";

        double total = rows.Sum(r => r.NetR);
        double gw = rows.Where(r => r.NetR > 0).Sum(r => r.NetR);
        double gl = Math.Abs(rows.Where(r => r.NetR < 0).Sum(r => r.NetR));
        double pf = gl > 0 ? gw / gl : 0;

        double peak = 0, dd = 0, cum = 0;
        foreach (var r in rows.OrderBy(r => r.SweepTs))
        {
            cum += r.NetR;
            if (cum > peak) peak = cum;
            if (peak - cum > dd) dd = peak - cum;
        }

        return $"{rows.Count,6:N0} {total / rows.Count,7:F3} {total,8:F1} {pf,5:F2} {dd,7:F1}";
    }

    private static double Exp(List<R> r) => r.Count > 0 ? r.Average(x => x.NetR) : 0;

    private static int Year(long ms) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ms).Year;

    private static double Spearman(IEnumerable<double> xs, IEnumerable<double> ys)
    {
        var x = xs.ToArray(); var y = ys.ToArray();
        int n = x.Length; if (n < 3) return 0;
        var rx = Rank(x); var ry = Rank(y);
        double mx = rx.Average(), my = ry.Average();
        double num = 0, dx = 0, dy = 0;
        for (int i = 0; i < n; i++)
        {
            num += (rx[i] - mx) * (ry[i] - my);
            dx += (rx[i] - mx) * (rx[i] - mx);
            dy += (ry[i] - my) * (ry[i] - my);
        }
        return dx > 0 && dy > 0 ? num / Math.Sqrt(dx * dy) : 0;
    }

    private static double[] Rank(double[] v)
    {
        var idx = Enumerable.Range(0, v.Length).OrderBy(i => v[i]).ToArray();
        var rank = new double[v.Length];
        int i0 = 0;
        while (i0 < idx.Length)
        {
            int j = i0;
            while (j + 1 < idx.Length && v[idx[j + 1]] == v[idx[i0]]) j++;
            double avg = (i0 + j) / 2.0 + 1;
            for (int k = i0; k <= j; k++) rank[idx[k]] = avg;
            i0 = j + 1;
        }
        return rank;
    }

    private static List<R> Collect(
        HistoryReader reader, string source, string[] markets,
        Func<FullSetup, bool> checklist)
    {
        var rows = new List<R>();

        foreach (string market in markets)
        {
            var c15 = reader.GetAllCandles(source, market, "15m");
            var c5 = reader.GetAllCandles(source, market, "5m");
            if (c15.Count == 0 || c5.Count == 0) continue;

            double[] atr = Atr(c15, AtrLen);

            foreach (var raid in SweepDetector.FindCandidates(market, c15))
            {
                FullSetup s = SetupDetector.Build(raid, c5);
                if (s.Stage != SetupStage.Complete || s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;

                int p = raid.PivotIndex;
                if (p < 8 || p + 8 >= c15.Count) continue;
                double a = atr[p]; if (a <= 0) continue;

                bool isHigh = raid.Direction == "HIGH";
                double L = raid.Level;

                int breach = -1;
                for (int i = p + 1; i < c15.Count; i++)
                {
                    bool b = isHigh ? c15[i].High > L : c15[i].Low < L;
                    if (b) { breach = i; break; }
                }
                if (breach < 0) continue;

                // availability: right-side prominence bars (p+1..p+8) must
                // have CLOSED before the sweep bar. breach >= p+9 guarantees it.
                bool available = breach >= p + 9;

                double prom = isHigh
                    ? Math.Min(L - Rng(c15, p - 8, p - 1).min, L - Rng(c15, p + 1, p + 8).min) / a
                    : Math.Min(Rng(c15, p - 8, p - 1).max - L, Rng(c15, p + 1, p + 8).max - L) / a;

                var away = Rng(c15, p + PivotStrength, breach - 1);
                double disp = isHigh ? (L - away.min) / a : (away.max - L) / a;

                rows.Add(new R(market, raid.SweepTimestamp, Walk(s, c5),
                    prom, disp, breach - p, available));
            }
        }
        return rows;
    }

    private static (double min, double max) Rng(IReadOnlyList<ResearchCandle> c, int from, int to)
    {
        double mn = double.MaxValue, mx = double.MinValue;
        from = Math.Max(0, from); to = Math.Min(c.Count - 1, to);
        if (to < from) return (0, 0);
        for (int i = from; i <= to; i++) { mn = Math.Min(mn, c[i].Low); mx = Math.Max(mx, c[i].High); }
        return (mn, mx);
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

    private static double Walk(FullSetup s, IReadOnlyList<ResearchCandle> c5)
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
            if (stopped) return -1.0 - (MakerFee + TakerFee) * entry / risk;
            bool hit = isShort ? c5[i].Low <= target : c5[i].High >= target;
            if (hit) return TargetR - (MakerFee + MakerFee) * entry / risk;
        }
        double exitR = isShort ? (entry - c5[last].Close) / risk : (c5[last].Close - entry) / risk;
        return exitR - (MakerFee + TakerFee) * entry / risk;
    }
}
