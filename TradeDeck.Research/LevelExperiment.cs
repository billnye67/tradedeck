namespace TradeDeck.Research;

/// <summary>
/// LEVEL DEFINITION EXPERIMENT (the user's "that's not the real 15m
/// level" / "a giant range low should still count" complaint,
/// GPT's "define the current 15m range more clearly").
///
/// Thesis: bigger levels should live longer. Variants pair a
/// prominence window (how much history the level must be the
/// extreme of) with an expiry (how long a sweep of it counts):
///
///   CURRENT   prominence 8h  (32 x 15m)  expiry 24h (96)
///   MEDIUM    prominence 24h (96)        expiry 72h (288)
///   BIG       prominence 48h (192)       expiry 7d  (672)
///   HUGE      prominence 96h (384)       expiry 14d (1344)
///
/// Everything downstream (trigger, displacement, FVG, entry,
/// checklist, fees, 3R) is unchanged. Learn era picks; held-out
/// era judges vs CURRENT. Run: dotnet run levels
/// </summary>
public static class LevelExperiment
{
    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private static readonly (string name, int prom, int expiry)[] Variants =
    {
        ("CURRENT 8h/24h",   32,  96),
        ("SMALL 4h/24h",     16,  96),   // the user's eye: minor swing highs count
        ("MINI 2h/12h",       8,  48),
        ("SMALL 4h/12h",     16,  48),
    };

    public static void Run(
        string source,
        string[] markets,
        Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();

        var data = markets
            .Select(m => (
                market: m,
                c15: reader.GetAllCandles(source, m, "15m"),
                c5: reader.GetAllCandles(source, m, "5m")))
            .ToList();

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        int origProm = SweepDetector.ProminenceLookback;
        int origExp = SweepDetector.MaxSweepDelay;

        Console.WriteLine();
        Console.WriteLine("LEVEL DEFINITION — do SMALLER swing levels count?");
        Console.WriteLine(
            $"{"variant",-17} | {"n",5} | {"LEARN exp",-10} | {"EXAM exp",-10} | {"EXAM total",-10}");
        Console.WriteLine(new string('-', 66));

        double bestLearn = double.MinValue;
        string bestName = Variants[0].name;
        var examBy = new Dictionary<string, double>();
        double currentExam = 0;

        foreach (var (name, prom, expiry) in Variants)
        {
            SweepDetector.ProminenceLookback = prom;
            SweepDetector.MaxSweepDelay = expiry;

            var learn = new List<double>();
            var exam = new List<double>();

            foreach (var (market, c15, c5) in data)
            {
                if (c15.Count == 0 || c5.Count == 0) continue;

                foreach (var raid in SweepDetector.FindCandidates(market, c15))
                {
                    FullSetup s = SetupDetector.Build(raid, c5);

                    if (s.Stage != SetupStage.Complete) continue;
                    if (s.RiskPerUnit <= 0) continue;
                    if (!checklist(s)) continue;

                    double r = Walk(s, c5);

                    if (s.Raid.SweepTimestamp < eraSplit) learn.Add(r);
                    else exam.Add(r);
                }
            }

            double le = learn.Count > 0 ? learn.Average() : 0;
            double ee = exam.Count > 0 ? exam.Average() : 0;

            examBy[name] = ee;
            if (name.StartsWith("CURRENT")) currentExam = ee;

            Console.WriteLine(
                $"{name,-17} | {learn.Count + exam.Count,5:N0} | {le,8:F3}R  | {ee,8:F3}R  | {exam.Sum(),8:F1}R");

            if (le > bestLearn) { bestLearn = le; bestName = name; }
        }

        // restore playbook defaults
        SweepDetector.ProminenceLookback = origProm;
        SweepDetector.MaxSweepDelay = origExp;

        Console.WriteLine();
        Console.WriteLine($"LEARN-ERA PICK: {bestName}");
        Console.WriteLine(
            $"EXAM: {bestName} nets {examBy[bestName]:F3}R vs CURRENT {currentExam:F3}R");
        Console.WriteLine(!bestName.StartsWith("CURRENT") && examBy[bestName] > currentExam
            ? $"VERDICT: {bestName} BEATS current level rule out-of-sample — new playbook level definition."
            : "VERDICT: current level definition stays law.");
        Console.WriteLine("(watch EXAM total too: a variant with more trades AND higher exp is the real win)");
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
