namespace TradeDeck.Research;

/// <summary>
/// The adaptive exit challenger (the user's thesis): instead of a
/// fixed R multiple, target the nearest UNTAPPED opposing 15m
/// liquidity pool — where resting stops actually sit.
///
/// SHORT: nearest 15m pivot LOW below entry, untapped since it
/// formed, and at least MinFloor R away (pools closer than the
/// floor are skipped for the next one; no qualifying pool =
/// fall back to the 3R champion for that trade).
///
/// MinFloor grid is chosen on the LEARN era; the pick must beat
/// the 3R champion on the HELD-OUT era or fixed 3R stays law.
/// Scoring identical to the champion: conservative stop-first,
/// Hyperliquid fees, expiry exits at close.
/// </summary>
public static class LiquidityExit
{
    private const int WalkWindow = 480;
    private const int PoolPivotStrength = 3;     // same pivot definition as the raid detector
    private const double ChampionR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private static readonly double[] FloorGrid = { 0.5, 1.0, 1.5, 2.0 };

    private sealed record Ctx(
        FullSetup S,
        IReadOnlyList<ResearchCandle> C5,
        IReadOnlyList<ResearchCandle> C15
    );

    public static void Run(
        string source,
        string[] markets,
        Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        var pool = new List<Ctx>();

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

                pool.Add(new Ctx(s, m5, m15));
            }

            Console.WriteLine($"{market}: loaded.");
        }

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        var learn = pool.Where(c => c.S.Raid.SweepTimestamp < eraSplit).ToList();
        var exam = pool.Where(c => c.S.Raid.SweepTimestamp >= eraSplit).ToList();

        Console.WriteLine();
        Console.WriteLine(
            $"{"exit model",-22} | {"LEARN exp",-10} | {"EXAM exp",-10} | fallback%");
        Console.WriteLine(new string('-', 62));

        double championLearn = Evaluate(learn, null, out _);
        double championExam = Evaluate(exam, null, out _);

        Console.WriteLine(
            $"{"CHAMPION fixed 3R",-22} | {championLearn,8:F3}R  | {championExam,8:F3}R  |    —");

        double bestLearn = double.MinValue;
        double bestFloor = FloorGrid[0];

        var examByFloor = new Dictionary<double, double>();

        foreach (double floor in FloorGrid)
        {
            double le = Evaluate(learn, floor, out double fbLearn);
            double ee = Evaluate(exam, floor, out _);

            examByFloor[floor] = ee;

            Console.WriteLine(
                $"{"pool, floor " + floor.ToString("F1") + "R",-22} | {le,8:F3}R  | {ee,8:F3}R  | {fbLearn,5:F1}%");

            if (le > bestLearn)
            {
                bestLearn = le;
                bestFloor = floor;
            }
        }

        double examBest = examByFloor[bestFloor];

        Console.WriteLine();
        Console.WriteLine($"LEARN-ERA PICK: pool exit, floor {bestFloor:F1}R");
        Console.WriteLine(
            $"EXAM: {examBest:F3}R/trade vs champion 3R {championExam:F3}R/trade");
        Console.WriteLine(examBest > championExam
            ? "VERDICT: adaptive pool exit BEATS fixed 3R out-of-sample."
            : "VERDICT: challenger fails the exam — fixed 3R stays law.");
    }

    /// <summary>floorR == null runs the fixed-3R champion.</summary>
    private static double Evaluate(
        List<Ctx> trades,
        double? floorR,
        out double fallbackPct)
    {
        if (trades.Count == 0) { fallbackPct = 0; return 0; }

        double total = 0;
        int fallbacks = 0;

        foreach (Ctx c in trades)
        {
            bool isShort = c.S.Raid.Direction == "HIGH";

            double entry = c.S.EntryPrice;
            double risk = c.S.RiskPerUnit;

            double target;

            if (floorR == null)
            {
                target = isShort
                    ? entry - ChampionR * risk
                    : entry + ChampionR * risk;
            }
            else
            {
                double? poolPrice = FindPool(c, isShort, floorR.Value);

                if (poolPrice == null)
                {
                    fallbacks++;
                    target = isShort
                        ? entry - ChampionR * risk
                        : entry + ChampionR * risk;
                }
                else
                {
                    target = poolPrice.Value;
                }
            }

            total += Walk(c, isShort, entry, risk, target);
        }

        fallbackPct = 100.0 * fallbacks / trades.Count;
        return total / trades.Count;
    }

    private static double Walk(
        Ctx c,
        bool isShort,
        double entry,
        double risk,
        double target)
    {
        double stop = c.S.StopLoss;

        int limit = Math.Min(c.S.EntryIndex5m + WalkWindow, c.C5.Count - 1);
        int last = c.S.EntryIndex5m;

        for (int i = c.S.EntryIndex5m; i <= limit; i++)
        {
            last = i;

            bool stopped = isShort
                ? c.C5[i].High >= stop
                : c.C5[i].Low <= stop;

            if (stopped)
                return -1.0 - (MakerFee + TakerFee) * entry / risk;

            bool hit = isShort
                ? c.C5[i].Low <= target
                : c.C5[i].High >= target;

            if (hit)
            {
                double winR = isShort
                    ? (entry - target) / risk
                    : (target - entry) / risk;

                return winR - (MakerFee + MakerFee) * entry / risk;
            }
        }

        double exitClose = c.C5[last].Close;

        double exitR = isShort
            ? (entry - exitClose) / risk
            : (exitClose - entry) / risk;

        return exitR - (MakerFee + TakerFee) * entry / risk;
    }

    /// <summary>
    /// Nearest untapped opposing 15m pivot at least floorR away
    /// from entry, using only candles closed BEFORE the entry
    /// candle (no lookahead).
    /// </summary>
    private static double? FindPool(Ctx c, bool isShort, double floorR)
    {
        double entry = c.S.EntryPrice;
        double risk = c.S.RiskPerUnit;

        long entryTime = c.C5[c.S.EntryIndex5m].Timestamp;

        int end15 = LastIndexBefore(c.C15, entryTime);
        if (end15 < PoolPivotStrength * 2) return null;

        double limitPrice = isShort
            ? entry - floorR * risk
            : entry + floorR * risk;

        double? best = null;

        for (int i = PoolPivotStrength; i <= end15 - PoolPivotStrength; i++)
        {
            double price;

            if (isShort)
            {
                price = c.C15[i].Low;

                bool pivot = true;

                for (int k = 1; k <= PoolPivotStrength; k++)
                {
                    if (c.C15[i - k].Low <= price ||
                        c.C15[i + k].Low <= price)
                    {
                        pivot = false;
                        break;
                    }
                }

                if (!pivot) continue;
                if (price > limitPrice) continue; // too close for the floor

                // untapped: nothing traded below it before entry
                bool tapped = false;

                for (int k = i + PoolPivotStrength + 1; k <= end15; k++)
                {
                    if (c.C15[k].Low < price) { tapped = true; break; }
                }

                if (tapped) continue;

                if (best == null || price > best) best = price; // nearest below
            }
            else
            {
                price = c.C15[i].High;

                bool pivot = true;

                for (int k = 1; k <= PoolPivotStrength; k++)
                {
                    if (c.C15[i - k].High >= price ||
                        c.C15[i + k].High >= price)
                    {
                        pivot = false;
                        break;
                    }
                }

                if (!pivot) continue;
                if (price < limitPrice) continue;

                bool tapped = false;

                for (int k = i + PoolPivotStrength + 1; k <= end15; k++)
                {
                    if (c.C15[k].High > price) { tapped = true; break; }
                }

                if (tapped) continue;

                if (best == null || price < best) best = price; // nearest above
            }
        }

        return best;
    }

    private static int LastIndexBefore(
        IReadOnlyList<ResearchCandle> c,
        long timestamp)
    {
        int lo = 0, hi = c.Count - 1, ans = -1;

        while (lo <= hi)
        {
            int mid = (lo + hi) / 2;

            if (c[mid].Timestamp < timestamp)
            {
                ans = mid;
                lo = mid + 1;
            }
            else hi = mid - 1;
        }

        return ans;
    }
}
