namespace TradeDeck.Research;

/// <summary>
/// PROSPECTOR — our data speaks, nobody's opinions. Four families,
/// pre-registered before running, judged only by "same direction in
/// BOTH eras with real sample size":
///   F1 hour-of-day drift        which UTC hours lean one way
///   F2 BTC -> alt lead-lag      big BTC hour, do alts follow next hour
///   F3 post-shock behavior      after a 99th-pct hour: continue or fade
///   F4 S1 x weekly alignment    do Strategy #1 trades WITH the 30d trend
///                               beat counter-trend ones (selection layer)
/// Thresholds (90th/99th pct, 30d) come from LEARN era only.
/// Run: dotnet run prospect
/// </summary>
public static class Prospector
{
    private sealed record H(long Ts, double RetBp);

    public static void Run(string source, string[] markets, Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        // hourly bars per market from 15m
        var hourly = new Dictionary<string, List<H>>();
        var hourlyOC = new Dictionary<string, List<(long ts, double open, double close)>>();

        foreach (string m in markets)
        {
            var c15 = reader.GetAllCandles(source, m, "15m");
            if (c15.Count == 0) continue;

            var hs = new List<H>();
            var oc = new List<(long, double, double)>();
            long cur = -1; double o = 0, c = 0;

            foreach (var k in c15)
            {
                long h = k.Timestamp / 3_600_000L;
                if (h != cur)
                {
                    if (cur >= 0) { hs.Add(new H(cur * 3_600_000L, (c - o) / o * 10_000)); oc.Add((cur * 3_600_000L, o, c)); }
                    cur = h; o = k.Open;
                }
                c = k.Close;
            }
            if (cur >= 0) { hs.Add(new H(cur * 3_600_000L, (c - o) / o * 10_000)); oc.Add((cur * 3_600_000L, o, c)); }
            hourly[m] = hs; hourlyOC[m] = oc;
            Console.WriteLine($"{m}: {hs.Count:N0} hours");
        }

        // ---------------- F1 hour-of-day ----------------
        Console.WriteLine();
        Console.WriteLine("F1 — HOUR-OF-DAY DRIFT (mean bp per hour, pooled across coins)");
        Console.WriteLine($"{"UTC hr",-7} | {"LEARN n",8} {"bp",7} | {"EXAM n",8} {"bp",7} | consistent?");
        var f1Hits = new List<int>();
        for (int hr = 0; hr < 24; hr++)
        {
            var l = new List<double>(); var e = new List<double>();
            foreach (var (m, hs) in hourly)
                foreach (var h in hs)
                {
                    int hh = (int)(h.Ts / 3_600_000L % 24);
                    if (hh != hr) continue;
                    if (h.Ts < eraSplit) l.Add(h.RetBp); else e.Add(h.RetBp);
                }
            double lm = l.Count > 0 ? l.Average() : 0, em = e.Count > 0 ? e.Average() : 0;
            bool consistent = Math.Sign(lm) == Math.Sign(em) && Math.Abs(lm) > 1.0 && Math.Abs(em) > 1.0;
            if (consistent) f1Hits.Add(hr);
            Console.WriteLine($"{hr,-7} | {l.Count,8:N0} {lm,7:F2} | {e.Count,8:N0} {em,7:F2} | {(consistent ? "YES" : "")}");
        }
        Console.WriteLine($"F1 verdict: {(f1Hits.Count > 0 ? $"hours [{string.Join(",", f1Hits)}] consistent both eras" : "no consistent hourly drift")}");

        // ---------------- F2 BTC -> alt lead-lag ----------------
        Console.WriteLine();
        Console.WriteLine("F2 — BTC BIG HOUR -> ALT NEXT HOUR (aligned bp: + = alts follow BTC's direction)");
        var btc = hourly["BTCUSDT"];
        var btcAbsLearn = btc.Where(h => h.Ts < eraSplit).Select(h => Math.Abs(h.RetBp)).OrderBy(x => x).ToList();
        double big = btcAbsLearn[(int)(btcAbsLearn.Count * 0.90)];
        Console.WriteLine($"(BTC 'big' = |1h move| >= {big:F1} bp, learn-era 90th pct)");
        Console.WriteLine($"{"alt",-9} | {"LEARN n",7} {"bp",7} | {"EXAM n",7} {"bp",7} | consistent?");

        var btcByTs = btc.ToDictionary(h => h.Ts, h => h.RetBp);
        foreach (string m in markets)
        {
            if (m == "BTCUSDT") continue;
            var l = new List<double>(); var e = new List<double>();
            foreach (var h in hourly[m])
            {
                if (!btcByTs.TryGetValue(h.Ts - 3_600_000L, out double b)) continue;
                if (Math.Abs(b) < big) continue;
                double aligned = Math.Sign(b) * h.RetBp;
                if (h.Ts < eraSplit) l.Add(aligned); else e.Add(aligned);
            }
            double lm = l.Count > 0 ? l.Average() : 0, em = e.Count > 0 ? e.Average() : 0;
            bool consistent = Math.Sign(lm) == Math.Sign(em) && Math.Abs(lm) > 1.0 && Math.Abs(em) > 1.0;
            Console.WriteLine($"{m,-9} | {l.Count,7:N0} {lm,7:F2} | {e.Count,7:N0} {em,7:F2} | {(consistent ? "YES" : "")}");
        }

        // ---------------- F3 post-shock ----------------
        Console.WriteLine();
        Console.WriteLine("F3 — AFTER A 99th-PCT HOUR: next-3h aligned bp (+ = continuation, - = fade)");
        Console.WriteLine($"{"coin",-9} | {"LEARN n",7} {"bp",8} | {"EXAM n",7} {"bp",8} | consistent?");
        foreach (string m in markets)
        {
            var hs = hourly[m];
            var absLearn = hs.Where(h => h.Ts < eraSplit).Select(h => Math.Abs(h.RetBp)).OrderBy(x => x).ToList();
            if (absLearn.Count < 500) continue;
            double shock = absLearn[(int)(absLearn.Count * 0.99)];

            var byTs = hs.ToDictionary(h => h.Ts, h => h.RetBp);
            var l = new List<double>(); var e = new List<double>();

            foreach (var h in hs)
            {
                if (Math.Abs(h.RetBp) < shock) continue;
                double next3 = 0; bool ok = true;
                for (int k = 1; k <= 3; k++)
                {
                    if (!byTs.TryGetValue(h.Ts + k * 3_600_000L, out double r)) { ok = false; break; }
                    next3 += r;
                }
                if (!ok) continue;
                double aligned = Math.Sign(h.RetBp) * next3;
                if (h.Ts < eraSplit) l.Add(aligned); else e.Add(aligned);
            }
            double lm = l.Count > 0 ? l.Average() : 0, em = e.Count > 0 ? e.Average() : 0;
            bool consistent = Math.Sign(lm) == Math.Sign(em) && Math.Abs(lm) > 5.0 && Math.Abs(em) > 5.0;
            Console.WriteLine($"{m,-9} | {l.Count,7:N0} {lm,8:F1} | {e.Count,7:N0} {em,8:F1} | {(consistent ? "YES" : "")}");
        }

        // ---------------- F4 S1 x weekly alignment ----------------
        Console.WriteLine();
        Console.WriteLine("F4 — STRATEGY #1 x 30-DAY TREND ALIGNMENT (net R per trade)");
        var s1Markets = new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
        var alignedL = new List<double>(); var counterL = new List<double>();
        var alignedE = new List<double>(); var counterE = new List<double>();

        foreach (string m in s1Markets)
        {
            var c15 = reader.GetAllCandles(source, m, "15m");
            var c5 = reader.GetAllCandles(source, m, "5m");
            if (c15.Count == 0 || c5.Count == 0) continue;

            // daily closes for 30d trend
            var dayClose = new Dictionary<long, double>();
            long cd = -1; double cl = 0;
            foreach (var k in c15)
            {
                long d = k.Timestamp / 86_400_000L;
                if (d != cd) { if (cd >= 0) dayClose[cd] = cl; cd = d; }
                cl = k.Close;
            }
            if (cd >= 0) dayClose[cd] = cl;

            foreach (var raid in SweepDetector.FindCandidates(m, c15))
            {
                FullSetup s = SetupDetector.Build(raid, c5);
                if (s.Stage != SetupStage.Complete || s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;

                long d0 = raid.SweepTimestamp / 86_400_000L;
                if (!dayClose.TryGetValue(d0 - 1, out double now) ||
                    !dayClose.TryGetValue(d0 - 31, out double old)) continue;

                bool trendUp = now > old;
                bool isLong = raid.Direction == "LOW";
                bool aligned = trendUp == isLong;

                double r = Walk(s, c5);
                bool exam = raid.SweepTimestamp >= eraSplit;

                (aligned ? (exam ? alignedE : alignedL) : (exam ? counterE : counterL)).Add(r);
            }
        }

        void F4Row(string name, List<double> l, List<double> e) =>
            Console.WriteLine($"{name,-22} | LEARN n {l.Count,5:N0} exp {(l.Count > 0 ? l.Average() : 0),7:F3} | EXAM n {e.Count,5:N0} exp {(e.Count > 0 ? e.Average() : 0),7:F3}");

        F4Row("WITH 30d trend", alignedL, alignedE);
        F4Row("AGAINST 30d trend", counterL, counterE);

        Console.WriteLine();
        Console.WriteLine("read: YES rows = consistent both eras = home-grown leads. Everything else is noise. No thresholds were tuned.");
    }

    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

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
