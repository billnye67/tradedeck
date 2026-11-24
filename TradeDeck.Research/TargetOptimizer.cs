namespace TradeDeck.Research;

/// <summary>
/// TP optimizer, evidence-first. For every checklist-passing
/// playbook trade it measures MFE (how many R the trade ran in
/// our favor before the stop traded or the window closed), then
/// scores a grid of fixed targets (1R..6R) with real fees.
///
/// Overfit guard: the winning target is chosen on the LEARN era
/// (2020-2023) and must also beat the current 3R baseline on the
/// HELD-OUT era (2024-2026), or 3R stays law.
///
/// Liquidity-pool targets (the user's idea) are the next iteration;
/// this run produces the MFE evidence they'll be judged against.
/// </summary>
public static class TargetOptimizer
{
    private const int WalkWindow = 480;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private static readonly double[] Grid =
        { 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0 };

    private sealed record Trade(
        FullSetup S,
        double Mfe,          // best favorable excursion in R before stop/window end
        bool StoppedFirst,   // stop traded before anything else mattered
        double ExitR         // R at window-expiry close (only meaningful when never stopped)
    );

    public static void Run(
        string source,
        string interval5,
        string[] markets,
        Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        var trades = new List<Trade>();

        foreach (string market in markets)
        {
            var m15 = reader.GetAllCandles(source, market, "15m");
            var m5 = reader.GetAllCandles(source, market, interval5);

            var raids = SweepDetector.FindCandidates(market, m15);

            foreach (var raid in raids)
            {
                FullSetup s = SetupDetector.Build(raid, m5);

                if (s.Stage != SetupStage.Complete) continue;
                if (s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;

                trades.Add(Measure(s, m5));
            }

            Console.WriteLine($"{market}: measured.");
        }

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        var learn = trades
            .Where(t => t.S.Raid.SweepTimestamp < eraSplit).ToList();

        var exam = trades
            .Where(t => t.S.Raid.SweepTimestamp >= eraSplit).ToList();

        // ---- MFE distribution: how far winners actually run ----
        Console.WriteLine();
        Console.WriteLine($"HOW FAR TRADES RUN ({trades.Count:N0} trades, all eras)");

        foreach (double r in Grid)
        {
            int reached = trades.Count(t => t.Mfe >= r);

            Console.WriteLine(
                $"   reach {r,3:F1}R before stopping: {reached,5:N0} " +
                $"({100.0 * reached / trades.Count,5:F1}%)");
        }

        // ---- target grid, learn era picks, exam era judges ----
        Console.WriteLine();
        Console.WriteLine(
            $"{"target",-7} | {"LEARN exp",-10} | {"EXAM exp",-10}");
        Console.WriteLine(new string('-', 34));

        double bestLearn = double.MinValue;
        double bestTarget = 3.0;

        var examByTarget = new Dictionary<double, double>();

        foreach (double target in Grid)
        {
            double le = Expectancy(learn, target);
            double ee = Expectancy(exam, target);

            examByTarget[target] = ee;

            Console.WriteLine(
                $"{target,5:F1}R  | {le,8:F3}R  | {ee,8:F3}R");

            if (le > bestLearn)
            {
                bestLearn = le;
                bestTarget = target;
            }
        }

        double examAtBest = examByTarget[bestTarget];
        double examAt3 = examByTarget[3.0];

        Console.WriteLine();
        Console.WriteLine($"LEARN-ERA WINNER: {bestTarget:F1}R");
        Console.WriteLine(
            $"EXAM: {bestTarget:F1}R nets {examAtBest:F3}R/trade vs 3R baseline {examAt3:F3}R/trade");
        Console.WriteLine(examAtBest > examAt3
            ? $"VERDICT: {bestTarget:F1}R passes the exam — new playbook target."
            : "VERDICT: challenger fails the held-out exam — 3R stays law.");
    }

    private static double Expectancy(List<Trade> trades, double target)
    {
        if (trades.Count == 0) return 0;

        double total = 0;

        foreach (Trade t in trades)
        {
            double entry = t.S.EntryPrice;
            double risk = t.S.RiskPerUnit;

            // Mfe accrues strictly BEFORE the stop candle, so
            // Mfe >= target means the target filled first — a win
            // even if the stop traded later.
            if (t.Mfe >= target)
            {
                // winner at this target: maker in, maker out
                total += target -
                    (MakerFee + MakerFee) * entry / risk;
            }
            else if (t.StoppedFirst)
            {
                total += -1.0 -
                    (MakerFee + TakerFee) * entry / risk;
            }
            else
            {
                // window expired still open: exit at last close,
                // SAME rule as the official playbook scorer.
                total += t.ExitR -
                    (MakerFee + TakerFee) * entry / risk;
            }
        }

        return total / trades.Count;
    }

    private static Trade Measure(
        FullSetup s,
        IReadOnlyList<ResearchCandle> c5)
    {
        bool isShort = s.Raid.Direction == "HIGH";

        double entry = s.EntryPrice;
        double stop = s.StopLoss;
        double risk = s.RiskPerUnit;

        double mfe = 0;
        bool stoppedFirst = false;

        int limit = Math.Min(s.EntryIndex5m + WalkWindow, c5.Count - 1);
        int lastWalked = s.EntryIndex5m;

        for (int i = s.EntryIndex5m; i <= limit; i++)
        {
            lastWalked = i;
            bool stopped = isShort
                ? c5[i].High >= stop
                : c5[i].Low <= stop;

            // conservative: a stop-touching candle contributes no
            // favorable excursion — the trade is dead there.
            if (stopped)
            {
                stoppedFirst = true;
                break;
            }

            double favorable = isShort
                ? (entry - c5[i].Low) / risk
                : (c5[i].High - entry) / risk;

            if (favorable > mfe) mfe = favorable;
        }

        double exitR = 0;

        if (!stoppedFirst)
        {
            double exitClose = c5[lastWalked].Close;

            exitR = isShort
                ? (entry - exitClose) / risk
                : (exitClose - entry) / risk;
        }

        return new Trade(s, mfe, stoppedFirst, exitR);
    }
}
