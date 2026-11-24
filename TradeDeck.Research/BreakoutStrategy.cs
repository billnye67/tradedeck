namespace TradeDeck.Research;

/// <summary>
/// STRATEGY #2 — BREAKOUT CONTINUATION (hypothesis-first).
///
/// The opposite bet to Strategy #1. Where #1 fades a failed
/// break (sweep + reclaim), #2 rides a REAL one:
///
/// LONG (SHORT is the mirror):
///  1. Meaningful current 15m high (same level rules as #1 —
///     prominence, expiry; single source of truth).
///  2. Price CLOSES above it, and the NEXT 15m candle also
///     closes above: acceptance, not a wick raid. (Mirror of
///     #1's reclaim rule — principled, not a tuned knob.)
///  3. Within 24h, price RETESTS the broken level from above
///     (former resistance acting as support).
///  4. Entry at the level on the retest touch.
///  5. Stop: last 15m pivot low BEFORE the breakout candle —
///     the structure the breakout launched from.
///  6. Target: fixed 3R (the verified champion exit).
///
/// Recorded per setup for the profiler (never filtered v1):
/// breakout candle range ratio, acceptance closes count,
/// retest depth beyond the level, hours from breakout to retest.
///
/// Run: dotnet run s2 [MARKET|ALL]
/// Prints per-era results with Hyperliquid fees, same standards
/// Strategy #1 had to pass.
/// </summary>
public static class BreakoutStrategy
{
    private const int PivotStrength = 2;         // same pivot rule as #1
    private const int AcceptCloses = 2;          // breakout close + 1 confirm
    private const int RetestWindow15 = 96;       // 24h of 15m candles
    private const int WalkWindow5 = 480;         // 40h trade window
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    public sealed record S2Setup(
        string Market,
        bool IsLong,
        double Level,
        long BreakTimestamp,
        int Retest5m,          // 5m index of the retest touch (entry)
        double Entry,
        double Stop,
        double Risk,
        double BreakRangeRatio,
        int AcceptCount,
        double RetestDepthR,
        double HoursToRetest
    );

    // ---------------- detection ----------------

    public static List<S2Setup> Detect(
        string market,
        IReadOnlyList<ResearchCandle> c15,
        IReadOnlyList<ResearchCandle> c5)
    {
        var setups = new List<S2Setup>();

        // Reuse #1's raid candidates to get the SAME level set,
        // then keep the ones that became genuine acceptance.
        foreach (SweepCandidate raid in
            SweepDetector.FindCandidates(market, c15))
        {
            bool isLong = raid.Direction == "HIGH"; // broken high -> continuation up

            int b15 = FirstAcceptClose(c15, raid, isLong);
            if (b15 < 0) continue;

            // stop: last opposing 15m pivot BEFORE the breakout
            double? stopPx = LastPivotBefore(c15, b15, isLong);
            if (stopPx == null) continue;

            double level = raid.Level;
            double risk = Math.Abs(level - stopPx.Value);
            if (risk <= 0) continue;

            // retest on 5m after acceptance is confirmed
            long confirmTs = c15[Math.Min(b15 + AcceptCloses - 1, c15.Count - 1)].Timestamp;
            long deadline = c15[b15].Timestamp + RetestWindow15 * 15 * 60 * 1000;

            int retest = -1;
            double depth = 0;

            for (int i = IndexAtOrAfter(c5, confirmTs + 15 * 60 * 1000);
                 i >= 0 && i < c5.Count && c5[i].Timestamp <= deadline;
                 i++)
            {
                bool touched = isLong
                    ? c5[i].Low <= level
                    : c5[i].High >= level;

                if (!touched) continue;

                // invalidation before entry: retest that trades
                // through the STOP is a failed breakout, skip.
                bool through = isLong
                    ? c5[i].Low <= stopPx.Value
                    : c5[i].High >= stopPx.Value;

                if (through) { retest = -1; break; }

                retest = i;

                depth = isLong
                    ? (level - c5[i].Low) / risk
                    : (c5[i].High - level) / risk;

                break;
            }

            if (retest < 0) continue;

            // features
            double range = c15[b15].High - c15[b15].Low;
            double avg = AvgRange(c15, b15, 20);

            setups.Add(new S2Setup(
                market,
                isLong,
                level,
                c15[b15].Timestamp,
                retest,
                level,
                stopPx.Value,
                risk,
                avg > 0 ? range / avg : 0,
                CountAcceptCloses(c15, b15, raid.Level, isLong),
                depth,
                (c5[retest].Timestamp - c15[b15].Timestamp) / 3_600_000.0
            ));
        }

        return setups;
    }

    private static int FirstAcceptClose(
        IReadOnlyList<ResearchCandle> c15,
        SweepCandidate raid,
        bool isLong)
    {
        // find the first 15m close beyond the level at/after the
        // sweep, then require the next candle to close beyond too
        int start = IndexAtOrAfter15(c15, raid.SweepTimestamp);
        if (start < 0) return -1;

        int limit = Math.Min(start + RetestWindow15, c15.Count - AcceptCloses);

        for (int i = start; i <= limit; i++)
        {
            bool closedBeyond = isLong
                ? c15[i].Close > raid.Level
                : c15[i].Close < raid.Level;

            if (!closedBeyond) continue;

            bool confirmed = true;

            for (int k = 1; k < AcceptCloses; k++)
            {
                bool also = isLong
                    ? c15[i + k].Close > raid.Level
                    : c15[i + k].Close < raid.Level;

                if (!also) { confirmed = false; break; }
            }

            return confirmed ? i : -1; // first close decides; no reclaim = #1 territory anyway
        }

        return -1;
    }

    private static int CountAcceptCloses(
        IReadOnlyList<ResearchCandle> c15,
        int from,
        double level,
        bool isLong)
    {
        int n = 0;

        for (int i = from; i < Math.Min(from + 12, c15.Count); i++)
        {
            bool beyond = isLong
                ? c15[i].Close > level
                : c15[i].Close < level;

            if (!beyond) break;
            n++;
        }

        return n;
    }

    private static double? LastPivotBefore(
        IReadOnlyList<ResearchCandle> c15,
        int index,
        bool isLong)
    {
        for (int i = index - 1 - PivotStrength; i >= PivotStrength; i--)
        {
            bool pivot = true;

            for (int k = 1; k <= PivotStrength; k++)
            {
                if (isLong)
                {
                    if (c15[i - k].Low <= c15[i].Low ||
                        c15[i + k].Low <= c15[i].Low)
                    { pivot = false; break; }
                }
                else
                {
                    if (c15[i - k].High >= c15[i].High ||
                        c15[i + k].High >= c15[i].High)
                    { pivot = false; break; }
                }
            }

            if (pivot)
                return isLong ? c15[i].Low : c15[i].High;
        }

        return null;
    }

    private static double AvgRange(
        IReadOnlyList<ResearchCandle> c,
        int before,
        int n)
    {
        double sum = 0;
        int cnt = 0;

        for (int k = 1; k <= n && before - k >= 0; k++)
        {
            sum += c[before - k].High - c[before - k].Low;
            cnt++;
        }

        return cnt > 0 ? sum / cnt : 0;
    }

    private static int IndexAtOrAfter15(
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

    private static int IndexAtOrAfter(
        IReadOnlyList<ResearchCandle> c,
        long ts) => IndexAtOrAfter15(c, ts);

    // ---------------- scoring ----------------

    public static void Score(string[] markets, string source)
    {
        var reader = new HistoryReader();

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        double totLearn = 0, totExam = 0;
        int nLearn = 0, nExam = 0;

        Console.WriteLine();
        Console.WriteLine("STRATEGY #2 — BREAKOUT CONTINUATION — first grind");
        Console.WriteLine(
            $"{"market",-9} | {"n",5} | {"LEARN exp",-10} | {"EXAM exp",-10}");
        Console.WriteLine(new string('-', 46));

        foreach (string market in markets)
        {
            var c15 = reader.GetAllCandles(source, market, "15m");
            var c5 = reader.GetAllCandles(source, market, "5m");

            if (c15.Count == 0 || c5.Count == 0) continue;

            var setups = Detect(market, c15, c5);

            var learn = new List<double>();
            var exam = new List<double>();

            foreach (S2Setup s in setups)
            {
                double r = Walk(s, c5);

                if (s.BreakTimestamp < eraSplit) learn.Add(r);
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

        Console.WriteLine();
        Console.WriteLine("PASS BAR: positive after fees in BOTH eras, broad across");
        Console.WriteLine("markets, enough trades. Anything less = trash or retool.");
    }

    private static double Walk(S2Setup s, IReadOnlyList<ResearchCandle> c5)
    {
        double target = s.IsLong
            ? s.Entry + TargetR * s.Risk
            : s.Entry - TargetR * s.Risk;

        int limit = Math.Min(s.Retest5m + WalkWindow5, c5.Count - 1);
        int last = s.Retest5m;

        for (int i = s.Retest5m; i <= limit; i++)
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
                return TargetR - (MakerFee + MakerFee) * s.Entry / s.Risk;
        }

        double exitR = s.IsLong
            ? (c5[last].Close - s.Entry) / s.Risk
            : (s.Entry - c5[last].Close) / s.Risk;

        return exitR - (MakerFee + TakerFee) * s.Entry / s.Risk;
    }
}
