namespace TradeDeck.Research;

/// <summary>
/// Hunt round 3: Strategy #1's engine on CALENDAR levels instead of swing
/// pivots. Spec: specs/hunt3-spec.md (pre-registered). All times UTC.
///   S1 prior-day high/low      S2 prior-week high/low
///   S3 Asia 00-08  S4 London 08-13  S5 New York 13:30-20
/// dotnet run sessions S1|S2|S3|S4|S5
/// </summary>
public static class SessionLevels
{
    // TD_MARKETS=XAUUSD,EURUSD,... overrides the universe (forex/metals runs); TD_PLAYBOOK likewise.
    private static readonly string[] Playbook = Environment.GetEnvironmentVariable("TD_PLAYBOOK") is string pbEnv && pbEnv.Length > 0
        ? pbEnv.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        : new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
    private static readonly string[] All = Environment.GetEnvironmentVariable("TD_MARKETS") is string mkEnv && mkEnv.Length > 0
        ? mkEnv.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        : new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT", "LINKUSDT", "HYPEUSDT" };

    private sealed record Level(int KnownIndex, int ValidUntil, double High, double Low);

    /// <summary>Windows [startMinOfDay, endMinOfDay) on the 15m series, keyed by UTC day
    /// (or ISO week for S2). Level is known at the first bar at/after window end; valid
    /// until the next level of the same kind is known.</summary>
    private static List<Level> Build(string which, IReadOnlyList<ResearchCandle> c15)
    {
        const long Day = 86_400_000L;
        var levels = new List<Level>();
        // group bars into windows
        var windows = new SortedDictionary<long, (double hi, double lo, int lastIdx)>();
        foreach (var (k, i) in c15.Select((k, i) => (k, i)))
        {
            long dayKey = k.Timestamp / Day;
            int mod = (int)((k.Timestamp % Day) / 60_000L); // minute of UTC day at bar open
            long key; bool inWin;
            switch (which)
            {
                case "S1": key = dayKey; inWin = true; break;
                case "S2": key = (k.Timestamp + 3 * Day) / (7 * Day); inWin = true; break; // weeks starting Monday 00:00 UTC
                case "S3": key = dayKey; inWin = mod >= 0 && mod < 480; break;
                case "S4": key = dayKey; inWin = mod >= 480 && mod < 780; break;
                case "S5": key = dayKey; inWin = mod >= 810 && mod < 1200; break;
                default: throw new ArgumentException(which);
            }
            if (!inWin) continue;
            if (windows.TryGetValue(key, out var w))
                windows[key] = (Math.Max(w.hi, k.High), Math.Min(w.lo, k.Low), i);
            else
                windows[key] = (k.High, k.Low, i);
        }
        var ordered = windows.Values.ToList();
        for (int n = 0; n < ordered.Count; n++)
        {
            int known = ordered[n].lastIdx + 1;                  // first bar after the window closes
            int until = n + 1 < ordered.Count ? ordered[n + 1].lastIdx + 1 : c15.Count;
            if (known >= c15.Count) break;
            levels.Add(new Level(known, until, ordered[n].hi, ordered[n].lo));
        }
        return levels;
    }


    /// <summary>TD_DUMP: append checklist trades (market,entryTs,exitTs,netR) for the merged portfolio gate.</summary>
    public static void Dump(string mkt, List<(double resultR, double feeR, FullSetup s)> sink, IReadOnlyList<ResearchCandle> bars)
    {
        string? path = Environment.GetEnvironmentVariable("TD_DUMP");
        if (string.IsNullOrEmpty(path)) return;
        var lines = sink
            .Where(t => t.s.FvgSizeRatio >= 0.35 && Math.Abs(t.s.SweepExtreme - t.s.Raid.Level) / t.s.Raid.Level >= 0.0013)
            .Select(t => $"{mkt},{bars[t.s.EntryIndex5m].Timestamp},{bars[BacktestScorer.ExitIndex(t.s, bars)].Timestamp},{(t.resultR - t.feeR):F4}");
        System.IO.File.AppendAllLines(path, lines);
    }

    public static void Run(string source, string which)
    {
        string label = which switch
        {
            "S1" => "S1 prior-day high/low", "S2" => "S2 prior-week high/low",
            "S3" => "S3 Asia 00-08 range", "S4" => "S4 London 08-13 range", "S5" => "S5 New York 13:30-20 range",
            _ => throw new ArgumentException("S1..S5")
        };
        var reader = new HistoryReader();
        var pool = new List<(double resultR, double feeR, FullSetup s)>();
        var perMarket = new Dictionary<string, List<(double resultR, double feeR, FullSetup s)>>();

        foreach (string mkt in All)
        {
            var c15 = reader.GetAllCandles(source, mkt, "15m");
            var c5 = reader.GetAllCandles(source, mkt, "5m");
            if (c15.Count == 0) continue;
            var atr = SweepDetector.AtrSeries(c15);
            var raids = new List<SweepCandidate>();
            foreach (var L in Build(which, c15))
            {
                var h = SweepDetector.WalkKnownLevel(mkt, c15, atr, L.KnownIndex, L.High, true, L.ValidUntil);
                var l = SweepDetector.WalkKnownLevel(mkt, c15, atr, L.KnownIndex, L.Low, false, L.ValidUntil);
                if (h != null) raids.Add(h);
                if (l != null) raids.Add(l);
            }
            var setups = raids.Select(r => SetupDetector.Build(r, c5)).ToList();
            var sink = new List<(double resultR, double feeR, FullSetup s)>();
            BacktestScorer.Profile(mkt, setups, c5, sink);
            perMarket[mkt] = sink; pool.AddRange(sink); Dump(mkt, sink, c5);
            Console.Error.WriteLine($"  {mkt}: levels walked, raids {raids.Count:N0}, complete setups {sink.Count:N0}");
        }

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();
        bool Checklist((double resultR, double feeR, FullSetup s) t) =>
            t.s.FvgSizeRatio >= 0.35 && Math.Abs(t.s.SweepExtreme - t.s.Raid.Level) / t.s.Raid.Level >= 0.0013;

        void Row(string lbl, List<(double resultR, double feeR, FullSetup s)> set)
        {
            if (set.Count == 0) { Console.WriteLine($"   {lbl,-26} | no trades"); return; }
            double net = set.Sum(t => t.resultR - t.feeR); int wins = set.Count(t => t.resultR > 0);
            double eq = 0, pk = 0, dd = 0;
            foreach (var t in set.OrderBy(t => t.s.Raid.SweepTimestamp)) { eq += t.resultR - t.feeR; pk = Math.Max(pk, eq); dd = Math.Max(dd, pk - eq); }
            Console.WriteLine($"   {lbl,-26} | trades {set.Count,5:N0} | win {100.0 * wins / set.Count,5:F1}% | NET exp {net / set.Count,6:F3}R | NET total {net,7:F1}R | maxDD {dd,5:F1}R");
        }

        Console.WriteLine(); Console.WriteLine($"HUNT 3 — {label}");
        foreach (bool exam in new[] { false, true })
        {
            Console.WriteLine(); Console.WriteLine(exam ? "EXAM ERA 2024-2026" : "LEARN ERA 2020-2023");
            var era = pool.Where(t => (t.s.Raid.SweepTimestamp >= eraSplit) == exam).ToList();
            var pb = era.Where(t => Playbook.Contains(t.s.Raid.Market)).ToList();
            Row("playbook baseline", pb); Row("playbook CHECKLIST", pb.Where(Checklist).ToList());
            Row("all-7 baseline", era); Row("all-7 CHECKLIST", era.Where(Checklist).ToList());
            foreach (string mkt in All)
                if (perMarket.TryGetValue(mkt, out var s))
                    Row($"  {mkt} checklist", s.Where(t => (t.s.Raid.SweepTimestamp >= eraSplit) == exam).Where(Checklist).ToList());
        }
        Console.WriteLine(); Console.WriteLine("VERDICT RULE (pre-registered): playbook CHECKLIST NET exp > 0 both eras, exam trades >= 100, exam NET exp >= 0.05R.");
    }
}
