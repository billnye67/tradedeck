namespace TradeDeck.Research;

/// <summary>
/// Hunt round 2: Strategy #1 at larger scales of the SAME bar-unit rules.
/// Higher-timeframe bars are aggregated from stored 15m bars, so no new
/// data and no new knobs. See reviews/hunt2-spec.md (pre-registered).
/// </summary>
public static class Scale
{
    // wiggle test (2026-10-14): TD_W_FVG / TD_W_BREACH override the checklist numbers 0.35 / 0.0013
    private static readonly double WFvg = double.TryParse(Environment.GetEnvironmentVariable("TD_W_FVG"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var wf) ? wf : 0.35;
    private static readonly double WBreach = double.TryParse(Environment.GetEnvironmentVariable("TD_W_BREACH"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var wb) ? wb : 0.0013;
    // TD_MARKETS=XAUUSD,EURUSD,... overrides the universe (forex/metals runs); TD_PLAYBOOK likewise.
    private static readonly string[] Playbook = Environment.GetEnvironmentVariable("TD_PLAYBOOK") is string pbEnv && pbEnv.Length > 0
        ? pbEnv.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        : new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
    private static readonly string[] All = Environment.GetEnvironmentVariable("TD_MARKETS") is string mkEnv && mkEnv.Length > 0
        ? mkEnv.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
        : new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT", "LINKUSDT", "HYPEUSDT" };

    public static List<ResearchCandle> Aggregate(IReadOnlyList<ResearchCandle> src, long bucketMs)
    {
        var outp = new List<ResearchCandle>(src.Count / 4 + 1);
        long curKey = long.MinValue;
        double o = 0, h = 0, l = 0, c = 0, v = 0;

        void Flush()
        {
            if (curKey != long.MinValue) outp.Add(new ResearchCandle(curKey, o, h, l, c, v));
        }

        foreach (var k in src)
        {
            long key = k.Timestamp - (k.Timestamp % bucketMs);
            if (key != curKey)
            {
                Flush();
                curKey = key; o = k.Open; h = k.High; l = k.Low; c = k.Close; v = k.Volume;
            }
            else
            {
                h = Math.Max(h, k.High); l = Math.Min(l, k.Low); c = k.Close; v += k.Volume;
            }
        }
        Flush();
        return outp;
    }

    /// <summary>pair: "15m" (control), "1h" (1h raid / 15m entry), "4h" (4h raid / 1h entry)</summary>
    public static void Run(string source, string pair)
    {
        const long M = 60_000;
        (long raidMs, long entryMs, string label) = pair switch
        {
            "15m" => (15 * M, 5 * M, "CONTROL 15m raid -> 5m entry"),
            "1h" => (60 * M, 15 * M, "C1 1h raid -> 15m entry"),
            "4h" => (240 * M, 60 * M, "C2 4h raid -> 1h entry"),
            _ => throw new ArgumentException("pair must be 15m | 1h | 4h"),
        };

        var reader = new HistoryReader();
        var pool = new List<(double resultR, double feeR, FullSetup s)>();
        var perMarket = new Dictionary<string, List<(double resultR, double feeR, FullSetup s)>>();
        var atrByMarket = new Dictionary<string, double[]>();
        // TD_BREACH_ATR=0.30 -> breach test in ATR units (forex adaptation A1); unset -> live 0.13% of price
        double? breachAtr = double.TryParse(Environment.GetEnvironmentVariable("TD_BREACH_ATR"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var ba) ? ba : null;

        foreach (string mkt in All)
        {
            var s15 = reader.GetAllCandles(source, mkt, "15m");
            var s5 = reader.GetAllCandles(source, mkt, "5m");
            if (s15.Count == 0) continue;

            var raidBars = raidMs == 15 * M ? s15 : Aggregate(s15, raidMs);
            var entryBars = entryMs == 5 * M ? s5
                          : entryMs == 15 * M ? s15
                          : Aggregate(s15, entryMs);

            var raids = SweepDetector.FindCandidates(mkt, raidBars);
            atrByMarket[mkt] = SweepDetector.AtrSeries(raidBars);
            var setups = raids.Select(rc => SetupDetector.Build(rc, entryBars)).ToList();

            var sink = new List<(double resultR, double feeR, FullSetup s)>();
            BacktestScorer.Profile(mkt, setups, entryBars, sink);
            perMarket[mkt] = sink;
            pool.AddRange(sink);
            SessionLevels.Dump(mkt, sink, entryBars);
            Console.Error.WriteLine(
                $"  {mkt}: raid bars {raidBars.Count:N0}, raids {raids.Count:N0}, complete setups {sink.Count:N0}");
        }

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        bool Checklist((double resultR, double feeR, FullSetup s) t)
        {
            if (t.s.FvgSizeRatio < WFvg) return false;
            double breach = Math.Abs(t.s.SweepExtreme - t.s.Raid.Level);
            if (breachAtr is double k && atrByMarket.TryGetValue(t.s.Raid.Market, out var atr) && t.s.Raid.SweepIndex < atr.Length && atr[t.s.Raid.SweepIndex] > 0)
                return breach >= k * atr[t.s.Raid.SweepIndex];
            return breach / t.s.Raid.Level >= WBreach;
        }

        void Row(string lbl, List<(double resultR, double feeR, FullSetup s)> set)
        {
            if (set.Count == 0) { Console.WriteLine($"   {lbl,-26} | no trades"); return; }
            double net = set.Sum(t => t.resultR - t.feeR);
            int wins = set.Count(t => t.resultR > 0);
            double eq = 0, pk = 0, dd = 0;
            foreach (var t in set.OrderBy(t => t.s.Raid.SweepTimestamp))
            {
                eq += t.resultR - t.feeR; pk = Math.Max(pk, eq); dd = Math.Max(dd, pk - eq);
            }
            Console.WriteLine(
                $"   {lbl,-26} | trades {set.Count,5:N0} | win {100.0 * wins / set.Count,5:F1}% | " +
                $"NET exp {net / set.Count,6:F3}R | NET total {net,7:F1}R | maxDD {dd,5:F1}R");
        }

        Console.WriteLine();
        Console.WriteLine($"HUNT 2 — {label}");
        foreach (bool exam in new[] { false, true })
        {
            Console.WriteLine();
            Console.WriteLine(exam ? "EXAM ERA 2024-2026" : "LEARN ERA 2020-2023");
            var era = pool.Where(t => (t.s.Raid.SweepTimestamp >= eraSplit) == exam).ToList();
            var pb = era.Where(t => Playbook.Contains(t.s.Raid.Market)).ToList();
            Row("playbook baseline", pb);
            Row("playbook CHECKLIST", pb.Where(Checklist).ToList());
            Row("all-7 baseline", era);
            Row("all-7 CHECKLIST", era.Where(Checklist).ToList());
            double AgeH((double resultR, double feeR, FullSetup s) t) => (t.s.Raid.SweepTimestamp - t.s.Raid.PivotTimestamp) / 3_600_000.0;
            Row("playbook CL age<=3.5d", pb.Where(Checklist).Where(t => AgeH(t) <= 84).ToList());
            Row("playbook CL age>3.5d", pb.Where(Checklist).Where(t => AgeH(t) > 84).ToList());
            Row("all-7 CL age<=3.5d", era.Where(Checklist).Where(t => AgeH(t) <= 84).ToList());
            Row("all-7 CL age>3.5d", era.Where(Checklist).Where(t => AgeH(t) > 84).ToList());
            foreach (string mkt in All)
            {
                if (!perMarket.TryGetValue(mkt, out var s)) continue;
                Row($"  {mkt} checklist",
                    s.Where(t => (t.s.Raid.SweepTimestamp >= eraSplit) == exam).Where(Checklist).ToList());
            }
        }
        // 1g (2026-10-06): TD_DUMP=path writes the playbook CHECKLIST trades (market,entryTs,exitTs,netR) for the account simulation.
        if (Environment.GetEnvironmentVariable("TD_DUMP") is string dumpPath && dumpPath.Length > 0)
        {
            var lines = new List<string> { "market,entryTs,exitTs,netR" + (Environment.GetEnvironmentVariable("TD_DUMP_FULL") == "1" ? ",side,entry,stop,grossR,feeR" : "") };
            foreach (string mkt in Playbook)
            {
                if (!perMarket.TryGetValue(mkt, out var sinkM)) continue;
                var s15 = reader.GetAllCandles(source, mkt, "15m");
                var entryBars = entryMs == 5 * M ? reader.GetAllCandles(source, mkt, "5m") : entryMs == 15 * M ? s15 : Aggregate(s15, entryMs);
                foreach (var t in sinkM.Where(Checklist).OrderBy(t => t.s.Raid.SweepTimestamp))
                {
                    int xi = BacktestScorer.ExitIndex(t.s, entryBars);
                    long eTs = entryBars[t.s.EntryIndex5m].Timestamp, xTs = entryBars[Math.Min(Math.Max(xi, t.s.EntryIndex5m), entryBars.Count - 1)].Timestamp;
                    lines.Add($"{mkt},{eTs},{xTs},{t.resultR - t.feeR:F4}" + (Environment.GetEnvironmentVariable("TD_DUMP_FULL") == "1"
                        ? FormattableString.Invariant($",{(t.s.Raid.Direction == "HIGH" ? -1 : 1)},{t.s.EntryPrice:R},{t.s.StopLoss:R},{t.resultR:F4},{t.feeR:F4}") : ""));
                }
            }
            File.WriteAllLines(dumpPath, lines);
            Console.Error.WriteLine($"  dumped {lines.Count - 1} playbook checklist trades to {dumpPath}");
        }
        Console.WriteLine();
        Console.WriteLine("VERDICT RULE (pre-registered): playbook CHECKLIST NET exp > 0 both eras, exam trades >= 100, exam NET exp >= 0.05R.");
    }
}
