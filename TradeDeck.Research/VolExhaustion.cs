namespace TradeDeck.Research;

/// <summary>
/// STRATEGY CANDIDATE — VOLATILITY EXHAUSTION MEAN REVERSION
/// (GPT's spec, frozen verbatim; constants are pre-registered
/// hypotheses — do NOT tune after seeing results.)
///
/// LONG (SHORT mirror):
///  1. 15m ATR(20) via true range.
///  2. Shock candle: range >= 2.0 x ATR20, closes bearish,
///     close within bottom 20% of its range.
///  3. Shock makes the lowest low of the prior 20 completed 15m.
///  4. No immediate entry. On 5m, within the next 6 completed
///     candles: one candle CLOSES above the high of the
///     immediately preceding 5m candle = reversal trigger.
///  5. Enter at that trigger candle's close.
///  6. Stop = lowest low from shock through entry - 0.10 x ATR20.
///  7. Reject if entry-to-stop risk > 1.5 x ATR20.
///  8. Score fixed 1R/2R/3R independently. One entry per shock.
///
/// Mechanism: late momentum chasers shorting after abnormal
/// expansion get trapped when continuation fails.
///
/// Run: dotnet run vx [MARKET|ALL]
/// </summary>
public static class VolExhaustion
{
    private const int AtrLen = 20;
    private const double ShockAtrMult = 2.0;
    private const double ClosePosMax = 0.20;     // close in bottom/top 20%
    private const int LowestLookback = 20;
    private const int TriggerWindow5 = 6;
    private const double StopAtrPad = 0.10;
    private const double MaxRiskAtrMult = 1.5;
    private const int WalkWindow5 = 480;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private static readonly double[] Targets = { 1.0, 2.0, 3.0 };

    public sealed record VxSetup(
        string Market,
        bool IsLong,
        long ShockTimestamp,
        int Entry5m,
        double Entry,
        double Stop,
        double Risk,
        double ShockRangeAtr,     // recorded features
        double RiskAtr
    );

    public static List<VxSetup> Detect(
        string market,
        IReadOnlyList<ResearchCandle> c15,
        IReadOnlyList<ResearchCandle> c5)
    {
        var setups = new List<VxSetup>();

        double[] atr = Atr(c15, AtrLen);

        for (int i = AtrLen + LowestLookback; i < c15.Count; i++)
        {
            double a = atr[i];
            if (a <= 0) continue;

            double range = c15[i].High - c15[i].Low;
            if (range < ShockAtrMult * a) continue;

            bool bearish = c15[i].Close < c15[i].Open;
            bool bullish = c15[i].Close > c15[i].Open;

            // close position within range
            double posFromLow = (c15[i].Close - c15[i].Low) / range;

            bool longShock =
                bearish &&
                posFromLow <= ClosePosMax &&
                IsExtremeOfPrior(c15, i, LowestLookback, low: true);

            bool shortShock =
                bullish &&
                (1.0 - posFromLow) <= ClosePosMax &&
                IsExtremeOfPrior(c15, i, LowestLookback, low: false);

            if (!longShock && !shortShock) continue;

            bool isLong = longShock;

            // 5m trigger hunt: next 6 completed 5m candles after
            // the shock candle CLOSES (shock open + 15m).
            long shockCloseTs = c15[i].Timestamp + 15 * 60 * 1000;

            int start5 = IndexAtOrAfter(c5, shockCloseTs);
            if (start5 <= 0) continue;

            int trigger = -1;

            for (int k = start5;
                 k < Math.Min(start5 + TriggerWindow5, c5.Count);
                 k++)
            {
                bool rev = isLong
                    ? c5[k].Close > c5[k - 1].High
                    : c5[k].Close < c5[k - 1].Low;

                if (rev) { trigger = k; break; }
            }

            if (trigger < 0) continue; // expired

            double entry = c5[trigger].Close;

            // stop: extreme from shock through entry, padded
            double ext = isLong ? c15[i].Low : c15[i].High;

            for (int k = start5; k <= trigger; k++)
            {
                if (isLong) ext = Math.Min(ext, c5[k].Low);
                else ext = Math.Max(ext, c5[k].High);
            }

            double stop = isLong
                ? ext - StopAtrPad * a
                : ext + StopAtrPad * a;

            double risk = Math.Abs(entry - stop);

            if (risk <= 0) continue;
            if (risk > MaxRiskAtrMult * a) continue; // spec rule 9

            setups.Add(new VxSetup(
                market,
                isLong,
                c15[i].Timestamp,
                trigger,
                entry,
                stop,
                risk,
                range / a,
                risk / a
            ));
        }

        return setups;
    }

    private static bool IsExtremeOfPrior(
        IReadOnlyList<ResearchCandle> c,
        int i,
        int lookback,
        bool low)
    {
        for (int k = 1; k <= lookback; k++)
        {
            if (low && c[i - k].Low <= c[i].Low) return false;
            if (!low && c[i - k].High >= c[i].High) return false;
        }

        return true;
    }

    private static double[] Atr(
        IReadOnlyList<ResearchCandle> c,
        int len)
    {
        var atr = new double[c.Count];
        double sum = 0;
        var trs = new double[c.Count];

        for (int i = 1; i < c.Count; i++)
        {
            double tr = Math.Max(
                c[i].High - c[i].Low,
                Math.Max(
                    Math.Abs(c[i].High - c[i - 1].Close),
                    Math.Abs(c[i].Low - c[i - 1].Close)));

            trs[i] = tr;
            sum += tr;

            if (i > len) sum -= trs[i - len];
            if (i >= len) atr[i] = sum / len;
        }

        return atr;
    }

    private static int IndexAtOrAfter(
        IReadOnlyList<ResearchCandle> c,
        long ts)
    {
        int lo = 0, hi = c.Count - 1, ans = -1;

        while (lo <= hi)
        {
            int mid = (lo + hi) / 2;

            if (c[mid].Timestamp >= ts) { ans = mid; hi = mid - 1; }
            else lo = mid + 1;
        }

        return ans;
    }

    // ---------------- scoring ----------------

    public static void Score(string[] markets, string source)
    {
        var reader = new HistoryReader();

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        Console.WriteLine();
        Console.WriteLine("CANDIDATE — VOLATILITY EXHAUSTION (GPT spec, frozen)");

        foreach (double t in Targets)
        {
            double totLearn = 0, totExam = 0;
            int nLearn = 0, nExam = 0;

            Console.WriteLine();
            Console.WriteLine($"TARGET {t:F0}R");
            Console.WriteLine(
                $"{"market",-9} | {"n",5} | {"LEARN exp",-10} | {"EXAM exp",-10}");
            Console.WriteLine(new string('-', 46));

            foreach (string market in markets)
            {
                var c15 = reader.GetAllCandles(source, market, "15m");
                var c5 = reader.GetAllCandles(source, market, "5m");

                if (c15.Count == 0 || c5.Count == 0) continue;

                var learn = new List<double>();
                var exam = new List<double>();

                foreach (VxSetup s in Detect(market, c15, c5))
                {
                    double r = Walk(s, c5, t);

                    if (s.ShockTimestamp < eraSplit) learn.Add(r);
                    else exam.Add(r);
                }

                double le = learn.Count > 0 ? learn.Average() : 0;
                double ee = exam.Count > 0 ? exam.Average() : 0;

                totLearn += learn.Sum(); nLearn += learn.Count;
                totExam += exam.Sum(); nExam += exam.Count;

                Console.WriteLine(
                    $"{market,-9} | {learn.Count + exam.Count,5:N0} | {le,8:F3}R  | {ee,8:F3}R");
            }

            Console.WriteLine(new string('-', 46));
            Console.WriteLine(
                $"{"ALL",-9} | {nLearn + nExam,5:N0} | " +
                $"{(nLearn > 0 ? totLearn / nLearn : 0),8:F3}R  | " +
                $"{(nExam > 0 ? totExam / nExam : 0),8:F3}R");
        }

        Console.WriteLine();
        Console.WriteLine("PASS BAR: positive after fees in BOTH eras, broad, big n.");
    }

    private static double Walk(
        VxSetup s,
        IReadOnlyList<ResearchCandle> c5,
        double targetR)
    {
        double target = s.IsLong
            ? s.Entry + targetR * s.Risk
            : s.Entry - targetR * s.Risk;

        int limit = Math.Min(s.Entry5m + WalkWindow5, c5.Count - 1);
        int last = s.Entry5m;

        for (int i = s.Entry5m + 1; i <= limit; i++)
        {
            last = i;

            bool stopped = s.IsLong
                ? c5[i].Low <= s.Stop
                : c5[i].High >= s.Stop;

            if (stopped)
                return -1.0 - (MakerFee + TakerFee) * s.Entry / s.Risk;

            bool hit = s.IsLong
                ? c5[i].High >= target
                : c5[i].Low <= target;

            if (hit)
                return targetR - (MakerFee + MakerFee) * s.Entry / s.Risk;
        }

        double exitR = s.IsLong
            ? (c5[last].Close - s.Entry) / s.Risk
            : (s.Entry - c5[last].Close) / s.Risk;

        return exitR - (MakerFee + TakerFee) * s.Entry / s.Risk;
    }
}
