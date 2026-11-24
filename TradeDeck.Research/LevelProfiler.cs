namespace TradeDeck.Research;

/// <summary>
/// LEVEL PROFILER — "what does a meaningful liquidity level look
/// like?" answered by data, not by a time window.
///
/// Candidate generation is deliberately PERMISSIVE (2h prominence,
/// long expiry) so the profiler sees the small levels the user's eye
/// counts as well as the big ones. For every raided level that
/// produced a checklist-passing trade, seven structural features
/// are computed using only information available BEFORE the sweep
/// (GPT's spec):
///   1 prominenceATR         how much the level stands out (±8 bars)
///   2 displacementAwayATR   how far price left it before returning
///   3 ageBars               untested lifetime until first breach
///   4 equalCount_0.20ATR    stacked equal highs/lows (liquidity)
///   5 nearbySwingCount      competing pivots within ±0.5 ATR / 32 bars
///   6 threeBarRejectionATR  how hard price rejected at formation
///   7 relativeVolume        formation volume vs prior-20 median
///
/// Output per feature: 5 quantile buckets x {learn, exam} with
/// n / NET exp / win%, plus winner-vs-loser medians. No thresholds
/// are chosen here. Run: dotnet run levelprofile
/// </summary>
public static class LevelProfiler
{
    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;
    private const int AtrLen = 20;
    private const int PivotStrength = 3;   // same as SweepDetector

    private sealed record Row(
        long SweepTs,
        double NetR,
        double Prominence,
        double DisplacementAway,
        double AgeBars,
        double EqualCount,
        double NearbySwings,
        double Rejection3,
        double RelVolume
    );

    public static void Run(
        string source,
        string[] markets,
        Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();

        // permissive candidate net
        int origProm = SweepDetector.ProminenceLookback;
        int origExp = SweepDetector.MaxSweepDelay;
        SweepDetector.ProminenceLookback = 8;
        SweepDetector.MaxSweepDelay = 672;

        var rows = new List<Row>();

        foreach (string market in markets)
        {
            var c15 = reader.GetAllCandles(source, market, "15m");
            var c5 = reader.GetAllCandles(source, market, "5m");

            if (c15.Count == 0 || c5.Count == 0) continue;

            double[] atr = Atr(c15, AtrLen);
            double[] vol = c15.Select(c => c.Volume).ToArray();

            // all confirmed swing highs/lows for clustering & isolation
            var swingHighs = new List<(int idx, double px)>();
            var swingLows = new List<(int idx, double px)>();

            for (int i = PivotStrength; i < c15.Count - PivotStrength; i++)
            {
                if (IsPivot(c15, i, true)) swingHighs.Add((i, c15[i].High));
                if (IsPivot(c15, i, false)) swingLows.Add((i, c15[i].Low));
            }

            foreach (var raid in SweepDetector.FindCandidates(market, c15))
            {
                FullSetup s = SetupDetector.Build(raid, c5);

                if (s.Stage != SetupStage.Complete) continue;
                if (s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;

                int p = raid.PivotIndex;
                if (p < 8 || p + 8 >= c15.Count) continue;

                double a = atr[p];
                if (a <= 0) continue;

                bool isHigh = raid.Direction == "HIGH";
                double L = raid.Level;

                // first breach bar after pivot
                int breach = -1;
                for (int i = p + 1; i < c15.Count; i++)
                {
                    bool b = isHigh ? c15[i].High > L : c15[i].Low < L;
                    if (b) { breach = i; break; }
                }
                if (breach < 0) continue;

                // 1 prominence
                double prom;
                if (isHigh)
                {
                    double leftLow = Range(c15, p - 8, p - 1).min;
                    double rightLow = Range(c15, p + 1, p + 8).min;
                    prom = Math.Min(L - leftLow, L - rightLow) / a;
                }
                else
                {
                    double leftHigh = Range(c15, p - 8, p - 1).max;
                    double rightHigh = Range(c15, p + 1, p + 8).max;
                    prom = Math.Min(leftHigh - L, rightHigh - L) / a;
                }

                // 2 displacement away (pivot confirm -> first breach)
                var away = Range(c15, p + PivotStrength, breach - 1);
                double dispAway = isHigh
                    ? (L - away.min) / a
                    : (away.max - L) / a;

                // 3 age
                double age = breach - p;

                // 4 equal-level clustering (other swings before breach)
                var others = (isHigh ? swingHighs : swingLows)
                    .Where(sw => sw.idx != p && sw.idx < breach)
                    .ToList();

                double eq = others.Count(sw => Math.Abs(sw.px - L) <= 0.20 * a);

                // 5 nearby competing swings within ±0.5 ATR, prior 32 bars
                double nearby = others.Count(sw =>
                    sw.idx >= p - 32 && sw.idx <= p &&
                    Math.Abs(sw.px - L) <= 0.50 * a);

                // 6 three-bar rejection
                var next3 = Range(c15, p + 1, p + 3);
                double rej = isHigh
                    ? (L - next3.min) / a
                    : (next3.max - L) / a;

                // 7 relative volume
                double medVol = Median(vol.Skip(Math.Max(0, p - 20)).Take(Math.Min(20, p)));
                double relVol = medVol > 0 ? vol[p] / medVol : 0;

                rows.Add(new Row(
                    raid.SweepTimestamp,
                    Walk(s, c5),
                    prom, dispAway, age, eq, nearby, rej, relVol));
            }

            Console.WriteLine($"{market}: {rows.Count:N0} rows so far");
        }

        SweepDetector.ProminenceLookback = origProm;
        SweepDetector.MaxSweepDelay = origExp;

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        var learn = rows.Where(r => r.SweepTs < eraSplit).ToList();
        var exam = rows.Where(r => r.SweepTs >= eraSplit).ToList();

        Console.WriteLine();
        Console.WriteLine($"LEVEL PROFILE — permissive net: {rows.Count:N0} trades " +
                          $"(learn {learn.Count:N0} / exam {exam.Count:N0})");
        Console.WriteLine($"baseline NET exp: learn {Exp(learn):F3}R | exam {Exp(exam):F3}R");

        Feature("1 prominenceATR", learn, exam, r => r.Prominence);
        Feature("2 displacementAwayATR", learn, exam, r => r.DisplacementAway);
        Feature("3 ageBars (15m)", learn, exam, r => r.AgeBars);
        Feature("4 equalCount 0.20ATR", learn, exam, r => r.EqualCount);
        Feature("5 nearbySwingCount", learn, exam, r => r.NearbySwings);
        Feature("6 threeBarRejectionATR", learn, exam, r => r.Rejection3);
        Feature("7 relativeVolume", learn, exam, r => r.RelVolume);

        Console.WriteLine();
        Console.WriteLine("read: a feature matters if its buckets separate the SAME way in BOTH eras.");
        Console.WriteLine("no thresholds chosen here — next step is combination testing + exam.");
    }

    private static void Feature(
        string name,
        List<Row> learn,
        List<Row> exam,
        Func<Row, double> f)
    {
        Console.WriteLine();
        Console.WriteLine($"-- {name}");
        Console.WriteLine(
            $"   {"bucket",-6} | {"LEARN n",7} {"NET",7} {"win%",6} | {"EXAM n",7} {"NET",7} {"win%",6}");

        // quantile edges from LEARN only (no peeking at exam)
        var sorted = learn.Select(f).OrderBy(x => x).ToList();
        if (sorted.Count < 25) { Console.WriteLine("   too few rows"); return; }

        double Q(double q) => sorted[(int)Math.Clamp(q * (sorted.Count - 1), 0, sorted.Count - 1)];
        double[] edges = { Q(0.2), Q(0.4), Q(0.6), Q(0.8) };

        int Bucket(double v)
        {
            for (int i = 0; i < edges.Length; i++) if (v <= edges[i]) return i;
            return edges.Length;
        }

        for (int b = 0; b <= edges.Length; b++)
        {
            var lb = learn.Where(r => Bucket(f(r)) == b).ToList();
            var eb = exam.Where(r => Bucket(f(r)) == b).ToList();

            string range = b == 0 ? $"<= {edges[0]:F2}"
                : b == edges.Length ? $"> {edges[^1]:F2}"
                : $"{edges[b - 1]:F2}-{edges[b]:F2}";

            Console.WriteLine(
                $"   Q{b + 1,-5} | {lb.Count,7:N0} {Exp(lb),7:F3} {Win(lb),5:F1}% | " +
                $"{eb.Count,7:N0} {Exp(eb),7:F3} {Win(eb),5:F1}%   [{range}]");
        }

        // winners vs losers medians (GPT: never profile winners alone)
        Console.WriteLine(
            $"   median | learn W {Median(learn.Where(r => r.NetR > 0).Select(f)):F2} " +
            $"L {Median(learn.Where(r => r.NetR <= 0).Select(f)):F2} | " +
            $"exam W {Median(exam.Where(r => r.NetR > 0).Select(f)):F2} " +
            $"L {Median(exam.Where(r => r.NetR <= 0).Select(f)):F2}");
    }

    private static double Exp(List<Row> rows) =>
        rows.Count > 0 ? rows.Average(r => r.NetR) : 0;

    private static double Win(List<Row> rows) =>
        rows.Count > 0 ? 100.0 * rows.Count(r => r.NetR > 0) / rows.Count : 0;

    private static double Median(IEnumerable<double> xs)
    {
        var l = xs.OrderBy(x => x).ToList();
        if (l.Count == 0) return 0;
        return l.Count % 2 == 1 ? l[l.Count / 2] : (l[l.Count / 2 - 1] + l[l.Count / 2]) / 2;
    }

    private static (double min, double max) Range(
        IReadOnlyList<ResearchCandle> c, int from, int to)
    {
        double mn = double.MaxValue, mx = double.MinValue;
        from = Math.Max(0, from); to = Math.Min(c.Count - 1, to);

        if (to < from) return (0, 0);

        for (int i = from; i <= to; i++)
        {
            mn = Math.Min(mn, c[i].Low);
            mx = Math.Max(mx, c[i].High);
        }

        return (mn, mx);
    }

    private static bool IsPivot(IReadOnlyList<ResearchCandle> c, int i, bool high)
    {
        for (int k = 1; k <= PivotStrength; k++)
        {
            if (high)
            {
                if (c[i - k].High > c[i].High || c[i + k].High > c[i].High) return false;
            }
            else
            {
                if (c[i - k].Low < c[i].Low || c[i + k].Low < c[i].Low) return false;
            }
        }
        return true;
    }

    private static double[] Atr(IReadOnlyList<ResearchCandle> c, int len)
    {
        var atr = new double[c.Count];
        var trs = new double[c.Count];
        double sum = 0;

        for (int i = 1; i < c.Count; i++)
        {
            double tr = Math.Max(c[i].High - c[i].Low,
                Math.Max(Math.Abs(c[i].High - c[i - 1].Close),
                         Math.Abs(c[i].Low - c[i - 1].Close)));
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

        double exitR = isShort
            ? (entry - c5[last].Close) / risk
            : (c5[last].Close - entry) / risk;

        return exitR - (MakerFee + TakerFee) * entry / risk;
    }
}
