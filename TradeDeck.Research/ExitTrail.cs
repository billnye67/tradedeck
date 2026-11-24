namespace TradeDeck.Research;

/// <summary>
/// EXIT EXPERIMENT — TRAILING (the user's standing objection to fixed 3R).
/// Frozen variants, same trades, same fees:
///   FIXED 3R        champion.
///   BE@1R + 3R      once +1R is touched, stop moves to entry; target 3R.
///   TRAIL 5m        once +1R is touched, stop trails behind the last
///                   completed 5m swing (strength 2) against the trade;
///                   no fixed target; window exit at 40h.
///   BE@1R + TRAIL   breakeven first, then the structure trail.
/// Learn era discovery, exam era judges vs FIXED 3R.
/// Run: dotnet run trail
/// </summary>
public static class ExitTrail
{
    private const int WalkWindow = 480;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;
    private const int Strength = 2;

    private sealed record T(long SweepTs, double NetR);

    public static void Run(string source, string[] markets, Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        var setups = new List<(FullSetup s, IReadOnlyList<ResearchCandle> c5)>();

        foreach (string market in markets)
        {
            var c15 = reader.GetAllCandles(source, market, "15m");
            var c5 = reader.GetAllCandles(source, market, "5m");
            if (c15.Count == 0 || c5.Count == 0) continue;

            foreach (var raid in SweepDetector.FindCandidates(market, c15))
            {
                FullSetup s = SetupDetector.Build(raid, c5);
                if (s.Stage != SetupStage.Complete || s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;
                setups.Add((s, c5));
            }
            Console.WriteLine($"{market}: loaded");
        }

        var models = new (string name, Func<FullSetup, IReadOnlyList<ResearchCandle>, double> f)[]
        {
            ("FIXED 3R", (s, c) => Walk(s, c, breakeven: false, trail: false, target: 3.0)),
            ("BE@1R + 3R", (s, c) => Walk(s, c, breakeven: true, trail: false, target: 3.0)),
            ("TRAIL 5m", (s, c) => Walk(s, c, breakeven: false, trail: true, target: null)),
            ("BE@1R + TRAIL", (s, c) => Walk(s, c, breakeven: true, trail: true, target: null)),
        };

        Console.WriteLine();
        Console.WriteLine("EXIT MODELS — fixed vs trailing");
        Console.WriteLine($"{"model",-15} | {"LEARN n",7} {"exp",7} {"total",8} {"win%",6} {"maxDD",7} | {"EXAM n",7} {"exp",7} {"total",8} {"win%",6} {"maxDD",7}");
        Console.WriteLine(new string('-', 100));

        foreach (var (name, f) in models)
        {
            var rows = setups.Select(x => new T(x.s.Raid.SweepTimestamp, f(x.s, x.c5))).ToList();
            var l = rows.Where(r => r.SweepTs < eraSplit).OrderBy(r => r.SweepTs).ToList();
            var e = rows.Where(r => r.SweepTs >= eraSplit).OrderBy(r => r.SweepTs).ToList();
            Console.WriteLine($"{name,-15} | {Stats(l)} | {Stats(e)}");
        }

        Console.WriteLine();
        Console.WriteLine("adopt a trail only if it beats FIXED 3R on total R in BOTH eras without worse maxDD.");
    }

    private static string Stats(List<T> rows)
    {
        if (rows.Count == 0) return "";
        double total = rows.Sum(r => r.NetR);
        double win = 100.0 * rows.Count(r => r.NetR > 0) / rows.Count;
        double peak = 0, dd = 0, cum = 0;
        foreach (var r in rows) { cum += r.NetR; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
        return $"{rows.Count,7:N0} {total / rows.Count,7:F3} {total,8:F1} {win,5:F1}% {dd,7:F1}";
    }

    private static double Walk(
        FullSetup s, IReadOnlyList<ResearchCandle> c5,
        bool breakeven, bool trail, double? target)
    {
        bool isShort = s.Raid.Direction == "HIGH";
        double entry = s.EntryPrice, risk = s.RiskPerUnit;
        double stop = s.StopLoss;
        double tgt = target.HasValue
            ? (isShort ? entry - target.Value * risk : entry + target.Value * risk)
            : double.NaN;
        double oneR = isShort ? entry - risk : entry + risk;

        bool armed = false; // +1R touched
        int limit = Math.Min(s.EntryIndex5m + WalkWindow, c5.Count - 1);
        int last = s.EntryIndex5m;

        for (int i = s.EntryIndex5m; i <= limit; i++)
        {
            last = i;

            // stop first (conservative)
            bool stopped = isShort ? c5[i].High >= stop : c5[i].Low <= stop;
            if (stopped)
            {
                double r = isShort ? (entry - stop) / risk : (stop - entry) / risk;
                // stop fills are taker exits
                return r - (MakerFee + TakerFee) * entry / risk;
            }

            if (target.HasValue)
            {
                bool hit = isShort ? c5[i].Low <= tgt : c5[i].High >= tgt;
                if (hit) return target.Value - (MakerFee + MakerFee) * entry / risk;
            }

            bool touched1R = isShort ? c5[i].Low <= oneR : c5[i].High >= oneR;
            if (touched1R) armed = true;

            if (armed)
            {
                if (breakeven)
                    stop = isShort ? Math.Min(stop, entry) : Math.Max(stop, entry);

                if (trail && i - Strength >= s.EntryIndex5m)
                {
                    // last completed swing against the trade (strength 2)
                    int p = i - Strength;
                    bool pivot = true;
                    for (int k = 1; k <= Strength; k++)
                    {
                        if (isShort)
                        {
                            if (c5[p - k].High >= c5[p].High || c5[p + k].High >= c5[p].High) { pivot = false; break; }
                        }
                        else
                        {
                            if (c5[p - k].Low <= c5[p].Low || c5[p + k].Low <= c5[p].Low) { pivot = false; break; }
                        }
                    }
                    if (pivot)
                    {
                        double newStop = isShort ? c5[p].High : c5[p].Low;
                        // only ratchet in the trade's favor
                        stop = isShort ? Math.Min(stop, newStop) : Math.Max(stop, newStop);
                    }
                }
            }
        }

        double exitR = isShort ? (entry - c5[last].Close) / risk : (c5[last].Close - entry) / risk;
        return exitR - (MakerFee + TakerFee) * entry / risk;
    }
}
