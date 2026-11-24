namespace TradeDeck.Research;

/// <summary>
/// STOP BUFFER EXPERIMENT (the user's call after the DOGE wick-out):
/// stop placed a little BEYOND the sweep wick instead of exactly
/// on it. Buffer expressed as a fraction of the original risk
/// (entry-to-wick distance) so it scales with each setup.
///
/// Trade-off measured, not argued: fewer wick-outs vs bigger
/// risk per trade (smaller size, less R per winner). Champion =
/// buffer 0 (current playbook). Learn era picks; held-out era
/// judges. Fixed 3R target on the NEW risk. Hyperliquid fees.
///
/// Run: dotnet run stops
/// </summary>
public static class StopBuffer
{
    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private static readonly double[] Buffers = { 0.0, 0.10, 0.20, 0.30 };

    public static void Run(
        string source,
        string[] markets,
        Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        var trades = new List<(FullSetup s, IReadOnlyList<ResearchCandle> c5)>();

        foreach (string market in markets)
        {
            var m15 = reader.GetAllCandles(source, market, "15m");
            var m5 = reader.GetAllCandles(source, market, "5m");

            foreach (var raid in SweepDetector.FindCandidates(market, m15))
            {
                FullSetup s = SetupDetector.Build(raid, m5);

                if (s.Stage != SetupStage.Complete) continue;
                if (s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;

                trades.Add((s, m5));
            }

            Console.WriteLine($"{market}: loaded.");
        }

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        var learn = trades.Where(t => t.s.Raid.SweepTimestamp < eraSplit).ToList();
        var exam = trades.Where(t => t.s.Raid.SweepTimestamp >= eraSplit).ToList();

        Console.WriteLine();
        Console.WriteLine("STOP BUFFER — beyond the sweep wick, as % of original risk");
        Console.WriteLine(
            $"{"buffer",-9} | {"LEARN exp",-10} | {"EXAM exp",-10} | {"win% exam",-9} | wick-outs saved (exam)");
        Console.WriteLine(new string('-', 72));

        double bestLearn = double.MinValue, bestBuf = 0;
        var examBy = new Dictionary<double, double>();

        int baseWickOuts = CountStops(exam, 0.0);

        foreach (double b in Buffers)
        {
            double le = Expectancy(learn, b, out _);
            double ee = Expectancy(exam, b, out double winPct);

            examBy[b] = ee;

            int saved = baseWickOuts - CountStops(exam, b);

            Console.WriteLine(
                $"{("+" + (b * 100).ToString("F0") + "%"),-9} | {le,8:F3}R  | {ee,8:F3}R  | {winPct,7:F1}%  | {saved,5}");

            if (le > bestLearn) { bestLearn = le; bestBuf = b; }
        }

        Console.WriteLine();
        Console.WriteLine($"LEARN-ERA PICK: +{bestBuf * 100:F0}%");
        Console.WriteLine(
            $"EXAM: +{bestBuf * 100:F0}% nets {examBy[bestBuf]:F3}R vs exact-wick {examBy[0.0]:F3}R");
        Console.WriteLine(bestBuf > 0 && examBy[bestBuf] > examBy[0.0]
            ? $"VERDICT: buffered stop (+{bestBuf * 100:F0}%) BEATS exact wick out-of-sample — new playbook stop."
            : "VERDICT: exact-wick stop stays law.");
    }

    private static (double stop, double risk) Buffered(FullSetup s, double b)
    {
        bool isShort = s.Raid.Direction == "HIGH";
        double baseRisk = s.RiskPerUnit;

        double stop = isShort
            ? s.StopLoss + b * baseRisk
            : s.StopLoss - b * baseRisk;

        return (stop, Math.Abs(s.EntryPrice - stop));
    }

    private static int CountStops(
        List<(FullSetup s, IReadOnlyList<ResearchCandle> c5)> trades,
        double b)
    {
        int n = 0;

        foreach (var (s, c5) in trades)
            if (Walk(s, c5, b, out bool stopped) < 0 && stopped) n++;

        return n;
    }

    private static double Expectancy(
        List<(FullSetup s, IReadOnlyList<ResearchCandle> c5)> trades,
        double b,
        out double winPct)
    {
        if (trades.Count == 0) { winPct = 0; return 0; }

        double total = 0;
        int wins = 0;

        foreach (var (s, c5) in trades)
        {
            double r = Walk(s, c5, b, out _);
            total += r;
            if (r > 0) wins++;
        }

        winPct = 100.0 * wins / trades.Count;
        return total / trades.Count;
    }

    private static double Walk(
        FullSetup s,
        IReadOnlyList<ResearchCandle> c5,
        double b,
        out bool stopped)
    {
        bool isShort = s.Raid.Direction == "HIGH";
        (double stop, double risk) = Buffered(s, b);

        double entry = s.EntryPrice;

        double target = isShort
            ? entry - TargetR * risk
            : entry + TargetR * risk;

        int limit = Math.Min(s.EntryIndex5m + WalkWindow, c5.Count - 1);
        int last = s.EntryIndex5m;

        stopped = false;

        for (int i = s.EntryIndex5m; i <= limit; i++)
        {
            last = i;

            bool hitStop = isShort
                ? c5[i].High >= stop
                : c5[i].Low <= stop;

            if (hitStop)
            {
                stopped = true;
                return -1.0 - (MakerFee + TakerFee) * entry / risk;
            }

            bool hit = isShort
                ? c5[i].Low <= target
                : c5[i].High >= target;

            if (hit)
                return TargetR - (MakerFee + MakerFee) * entry / risk;
        }

        double exitR = isShort
            ? (entry - c5[last].Close) / risk
            : (c5[last].Close - entry) / risk;

        return exitR - (MakerFee + TakerFee) * entry / risk;
    }
}
