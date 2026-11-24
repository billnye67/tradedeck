namespace TradeDeck.Research;

/// <summary>
/// Finds CANDIDATE liquidity raids on 15m candles for human grading.
///
/// IMPORTANT: nothing here is "the strategy". These constants are
/// candidate-pool knobs, deliberately loose so the pool errs toward
/// including junk rather than missing real setups. the user's grades on
/// the review page are the ground truth; the knobs get tuned until
/// the survivors match his eye. (Locked rule: we do not fake
/// subjective definitions.)
///
/// Candidate definition (HIGH side; LOW side is the mirror):
///   1. A pivot high: candle whose high exceeds the highs of
///      PivotStrength candles on each side.
///   2. It stands out: it is the highest high of the trailing
///      ProminenceLookback candles ("most recent meaningful one").
///   3. Price moves away: at least MinCandlesBeforeSweep candles
///      pass before the level is touched again.
///   4. Untapped until the sweep: no candle CLOSES above the level
///      before the sweep (wicks are fine).
///   5. The sweep: a candle's high trades through the level, and a
///      close back below the level happens within ReturnWindow
///      candles. A close that stays above = breakout, not a raid,
///      and the candidate is discarded.
/// </summary>
public static class SweepDetector
{
    // ---- candidate-pool knobs (tunable, not strategy rules) ----
    private const int PivotStrength = 3;
    private static readonly int MinCandlesBeforeSweep = Environment.GetEnvironmentVariable("TD_W_AGE") is string _TD_W_AGE && _TD_W_AGE.Length > 0 ? int.Parse(_TD_W_AGE, System.Globalization.CultureInfo.InvariantCulture) : 8;   // TD_W_* = wiggle test only (research copy)    // 2 hours of 15m
    private static readonly int ReturnWindow = Environment.GetEnvironmentVariable("TD_W_RETURN") is string _TD_W_RETURN && _TD_W_RETURN.Length > 0 ? int.Parse(_TD_W_RETURN, System.Globalization.CultureInfo.InvariantCulture) : 3;   // TD_W_* = wiggle test only (research copy)

    // LEVEL DEFINITION KNOBS — defaults = current playbook. Made
    // settable ONLY so the level-prominence experiment can A/B
    // "bigger levels live longer" through the exam. The exam picks;
    // nobody hand-sets these.
    // LEVEL DEFINITION — FROZEN FORWARD CANDIDATE (ablation study,
    // GPT-audited): permissive swing net + "price must have left
    // the level by >= 2.0 ATR before returning" (displacement-away).
    // Modes exist ONLY so the scanner can shadow-log the alternatives
    // on the same future. Do not tune 2.0. Any change = new experiment.
    public enum LevelModeKind { D_ONLY, RAW, OLD_8H }
    public static LevelModeKind LevelMode = LevelModeKind.D_ONLY;

    public static readonly double MinDisplacementAwayAtr = Environment.GetEnvironmentVariable("TD_W_DISP") is string _TD_W_DISP && _TD_W_DISP.Length > 0 ? double.Parse(_TD_W_DISP, System.Globalization.CultureInfo.InvariantCulture) : 2.00;   // TD_W_* = wiggle test only (research copy)
    private const int AtrLen = 20;

    public static int ProminenceLookback = 8;       // permissive net (was 32 under OLD_8H)
    public static int MaxSweepDelay = 672;          // 7 days of 15m (was 96 under OLD_8H)

    private static (int prom, int expiry) KnobsFor(LevelModeKind mode) =>
        mode == LevelModeKind.OLD_8H ? (32, 96) : (8, 672);
    private const int ContextBefore = 40;           // chart candles left of pivot
    private const int ContextAfter = 6;             // enough to see the reclaim, not the aftermath

    public static List<SweepCandidate> FindCandidates(
        string market,
        IReadOnlyList<ResearchCandle> candles)
    {
        var result = new List<SweepCandidate>();

        (ProminenceLookback, MaxSweepDelay) = KnobsFor(LevelMode);

        double[] atr = Atr(candles, AtrLen);

        int first = Math.Max(PivotStrength, ProminenceLookback);
        int last = candles.Count - PivotStrength - 1;

        for (int i = first; i <= last; i++)
        {
            TryAddCandidate(market, candles, i, isHigh: true, result, atr);
            TryAddCandidate(market, candles, i, isHigh: false, result, atr);
        }

        return result;
    }

    /// <summary>Displacement-away in ATR: max excursion from the level
    /// between pivot confirmation and the candle before first breach.</summary>
    public static double DisplacementAwayAtr(
        IReadOnlyList<ResearchCandle> c,
        double[] atr,
        int pivotIndex,
        int breachIndex,
        double level,
        bool isHigh)
    {
        double a = atr[pivotIndex];
        if (a <= 0) return 0;

        int from = pivotIndex + PivotStrength;
        int to = breachIndex - 1;
        if (to < from) return 0;

        double ext = isHigh ? double.MaxValue : double.MinValue;

        for (int i = from; i <= to; i++)
            ext = isHigh ? Math.Min(ext, c[i].Low) : Math.Max(ext, c[i].High);

        return (isHigh ? level - ext : ext - level) / a;
    }

    private static double[] Atr(IReadOnlyList<ResearchCandle> c, int len)
    {
        var atr = new double[c.Count];
        var trs = new double[c.Count];
        double sum = 0;

        for (int i = 1; i < c.Count; i++)
        {
            double tr = Math.Max(
                c[i].High - c[i].Low,
                Math.Max(Math.Abs(c[i].High - c[i - 1].Close),
                         Math.Abs(c[i].Low - c[i - 1].Close)));

            trs[i] = tr; sum += tr;
            if (i > len) sum -= trs[i - len];
            if (i >= len) atr[i] = sum / len;
        }

        return atr;
    }

    private static void TryAddCandidate(
        string market,
        IReadOnlyList<ResearchCandle> candles,
        int pivotIndex,
        bool isHigh,
        List<SweepCandidate> result,
        double[] atr)
    {
        double level = isHigh
            ? candles[pivotIndex].High
            : candles[pivotIndex].Low;

        // 1. Pivot: strictly beyond PivotStrength neighbours each side.
        for (int k = 1; k <= PivotStrength; k++)
        {
            if (isHigh)
            {
                if (candles[pivotIndex - k].High >= level) return;
                if (candles[pivotIndex + k].High >= level) return;
            }
            else
            {
                if (candles[pivotIndex - k].Low <= level) return;
                if (candles[pivotIndex + k].Low <= level) return;
            }
        }

        // 2. Prominence: extreme of the trailing window.
        for (int k = PivotStrength + 1; k <= ProminenceLookback; k++)
        {
            int idx = pivotIndex - k;
            if (idx < 0) break;

            if (isHigh && candles[idx].High > level) return;
            if (!isHigh && candles[idx].Low < level) return;
        }

        // 3-5. Walk forward to the first touch of the level.
        for (int j = pivotIndex + PivotStrength + 1; j < candles.Count; j++)
        {
            bool touched = isHigh
                ? candles[j].High > level
                : candles[j].Low < level;

            bool closedThrough = isHigh
                ? candles[j].Close > level
                : candles[j].Close < level;

            // Levels expire: the user trades the RECENT meaningful
            // level, not one from weeks ago. Past the delay window
            // the candidate is dead.
            if (j - pivotIndex > MaxSweepDelay)
                return;

            if (!touched)
            {
                // 4. A close through the level without touching first
                // is impossible; a close through on a touch candle is
                // handled below. Nothing to check for non-touch candles.
                continue;
            }

            // 3. Too soon after the pivot = continuation chop.
            if (j - pivotIndex < MinCandlesBeforeSweep)
                return;

            // D_ONLY (frozen candidate): price must have LEFT the
            // level by >= 2.0 ATR before this first breach. Uses only
            // bars before j — no lookahead.
            if (LevelMode == LevelModeKind.D_ONLY &&
                DisplacementAwayAtr(candles, atr, pivotIndex, j, level, isHigh)
                    < MinDisplacementAwayAtr)
                return;

            // 5. Touched. Raid requires a close back inside within
            // ReturnWindow candles (the touch candle counts).
            int returnLimit = Math.Min(
                j + ReturnWindow - 1,
                candles.Count - 1
            );

            for (int r = j; r <= returnLimit; r++)
            {
                bool backInside = isHigh
                    ? candles[r].Close < level
                    : candles[r].Close > level;

                if (backInside)
                {
                    result.Add(
                        BuildCandidate(
                            market, candles,
                            pivotIndex, j, level, isHigh
                        )
                    );

                    return;
                }
            }

            // Never closed back inside = breakout, not a raid.
            return;
        }
    }

    /// <summary>
    /// HUNT 3 (additive, live pivot path untouched): walk a level that is
    /// DEFINED BY THE CALENDAR (prior-day high, session range edge, ...)
    /// rather than by a swing. Steps 3-5 of TryAddCandidate verbatim: first
    /// touch >= MinCandlesBeforeSweep bars after the level is known,
    /// displacement-away >= 2.0 ATR before the breach, close back inside
    /// within ReturnWindow. No pivot-shape or prominence test. Expiry is
    /// structural: validUntilIndex (exclusive).
    /// </summary>
    public static SweepCandidate? WalkKnownLevel(
        string market,
        IReadOnlyList<ResearchCandle> candles,
        double[] atr,
        int knownIndex,
        double level,
        bool isHigh,
        int validUntilIndex)
    {
        int last = Math.Min(validUntilIndex, candles.Count) - 1;
        for (int j = knownIndex + 1; j <= last; j++)
        {
            bool touched = isHigh ? candles[j].High > level : candles[j].Low < level;
            if (!touched) continue;
            if (j - knownIndex < MinCandlesBeforeSweep) return null;
            if (DisplacementAwayAtr(candles, atr, knownIndex, j, level, isHigh) < MinDisplacementAwayAtr)
                return null;
            int returnLimit = Math.Min(j + ReturnWindow - 1, candles.Count - 1);
            for (int r = j; r <= returnLimit; r++)
            {
                bool backInside = isHigh ? candles[r].Close < level : candles[r].Close > level;
                if (backInside) return BuildCandidate(market, candles, knownIndex, j, level, isHigh);
            }
            return null;
        }
        return null;
    }

    public static double[] AtrSeries(IReadOnlyList<ResearchCandle> c) => Atr(c, AtrLen);

    private static SweepCandidate BuildCandidate(
        string market,
        IReadOnlyList<ResearchCandle> candles,
        int pivotIndex,
        int sweepIndex,
        double level,
        bool isHigh)
    {
        int start = Math.Max(0, pivotIndex - ContextBefore);
        int end = Math.Min(candles.Count - 1, sweepIndex + ContextAfter);

        var context = new List<ResearchCandle>(end - start + 1);

        for (int i = start; i <= end; i++)
            context.Add(candles[i]);

        return new SweepCandidate(
            market,
            isHigh ? "HIGH" : "LOW",
            candles[pivotIndex].Timestamp,
            level,
            candles[sweepIndex].Timestamp,
            pivotIndex,
            sweepIndex,
            context,
            start
        );
    }
}
