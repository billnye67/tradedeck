namespace TradeDeck.Research;

public enum SetupStage
{
    BecameBreakout,
    NoTrigger,
    NoDisplacement,
    NoFvg,
    NoRetrace,
    InvalidatedBeforeEntry,
    FvgInverted,
    Complete
}

public sealed record FullSetup(
    SweepCandidate Raid,
    SetupStage Stage,
    int BreachIndex5m,
    int ExtremeIndex5m,
    double SweepExtreme,
    int TriggerIndex5m,
    double TriggerPrice,
    int DisplacementIndex5m,
    int ChopCrossings,
    int FvgMiddleIndex5m,
    double FvgProximal,
    double FvgDistal,
    int EntryIndex5m,
    double EntryPrice,
    double StopLoss,
    double RiskPerUnit,
    double DisplacementRatio,   // displacement candle range vs avg of prior 20 (recorded, not filtered)
    double FvgSizeRatio,        // FVG height vs same average (recorded, not filtered)
    int ChartStart5m,
    int ChartEnd5m
);

/// <summary>
/// Builds the full Strategy #1 chain from a detected 15m raid.
///
/// v2: the sweep extreme is the TRUE raid wick. Raids often dig
/// below/above the level in several pushes before the real move;
/// the stop belongs beyond the deepest one, and the trigger is the
/// pullback that produced the FINAL push into that deepest wick.
/// Implemented as a knob-free iteration: derive trigger and
/// displacement from the current extreme; if price dug deeper
/// before (or instead of) displacing, advance the extreme to the
/// deeper wick and re-derive. Converges because the extreme only
/// moves deeper and forward. Verified against two-dip and
/// single-dip synthetic tapes.
///
/// Locked rules unchanged: trigger = last clear 5m structural
/// pullback directly producing the final push (not simply the last
/// candle, not the deepest swing); displacement must CLOSE through
/// it; FVG verified separately; retrace into FVG is the entry
/// model; chop is recorded, not filtered.
/// </summary>
public static class SetupDetector
{
    private static readonly bool NoPeek = Environment.GetEnvironmentVariable("TD_NO_PEEK") == "1";

    // Trigger definition A/B (disputed reading of the spec):
    // false (A): trigger = last unbroken pivot before the TRUE
    //            deepest wick (mid-episode pivots allowed).
    // true  (B): trigger must also have formed BEFORE the first
    //            breach of the level (GPT's reading).
    // A/B tested 2020-2026: pre-breach (B) won the held-out era
    // and held stable across eras. B is law. Flag kept only so a
    // future re-test can flip it consciously.
    public static bool PreBreachTriggerOnly = true;

    private static readonly int TriggerPivotStrength = Environment.GetEnvironmentVariable("TD_W_TRIG") is string _TD_W_TRIG && _TD_W_TRIG.Length > 0 ? int.Parse(_TD_W_TRIG, System.Globalization.CultureInfo.InvariantCulture) : 2;   // TD_W_* = wiggle test only
    private const int DisplacementWindow = 24;
    private const int FvgSearchAfter = 3;
    private const int RetraceWindow = 36;
    private const int ChartPad = 12;

    public static FullSetup Build(
        SweepCandidate raid,
        IReadOnlyList<ResearchCandle> c5)
    {
        bool isShort = raid.Direction == "HIGH";
        double level = raid.Level;

        // ---- first breach of the level on 5m ----
        int sweepBarStart = FindIndexAt(c5, raid.SweepTimestamp);

        int breach = -1;

        for (int i = sweepBarStart; i < Math.Min(sweepBarStart + 3, c5.Count); i++)
        {
            bool through = isShort
                ? c5[i].High > level
                : c5[i].Low < level;

            if (through) { breach = i; break; }
        }

        if (breach < 0)
            breach = sweepBarStart;

        // ---- initial extreme: deepest wick of the first leg ----
        int reclaim = breach;

        for (int i = breach; i < Math.Min(breach + 12, c5.Count); i++)
        {
            bool inside = isShort
                ? c5[i].Close < level
                : c5[i].Close > level;

            reclaim = i;
            if (inside) break;
        }

        int extreme = breach;

        for (int i = breach; i <= reclaim; i++)
        {
            if (Deeper(c5, i, extreme, isShort))
                extreme = i;
        }

        // ---- episode iteration: settle the TRUE raid extreme ----
        int trigger;
        int displacement;
        int chop;

        while (true)
        {
            // A raid REQUIRES the level to be reclaimed: at least one
            // close back inside after the extreme. Price accepting
            // beyond the level = breakout, not a raid. (Found via QA:
            // without this the detector longed the June-2022 crash.)
            if (!Reclaimed(c5, extreme, level, extreme + DisplacementWindow, isShort))
            {
                int deeperR = DeeperWithin(
                    c5, extreme, extreme + DisplacementWindow, isShort);

                if (deeperR >= 0) { extreme = deeperR; continue; }

                return Incomplete(raid, SetupStage.BecameBreakout,
                    breach, extreme, Extreme(c5, extreme, isShort), c5);
            }

            trigger = FindTrigger(c5, extreme, isShort, breach, level);

            if (trigger < 0)
            {
                int deeper = DeeperWithin(
                    c5, extreme, extreme + DisplacementWindow, isShort);

                if (deeper >= 0) { extreme = deeper; continue; }

                return Incomplete(raid, SetupStage.NoTrigger,
                    breach, extreme, Extreme(c5, extreme, isShort), c5);
            }

            double tp = isShort
                ? c5[trigger].Low
                : c5[trigger].High;

            (displacement, chop) =
                FindDisplacement(c5, extreme, tp, isShort);

            if (displacement < 0)
            {
                int deeper = DeeperWithin(
                    c5, extreme, extreme + DisplacementWindow, isShort);

                if (deeper >= 0) { extreme = deeper; continue; }

                return Incomplete(raid, SetupStage.NoDisplacement,
                    breach, extreme, Extreme(c5, extreme, isShort), c5,
                    trigger, tp);
            }

            // price dug deeper between the extreme and displacement:
            // that deeper wick is the real raid extreme — re-derive.
            int deeper2 = DeeperWithin(
                c5, extreme, displacement - 1, isShort);

            if (deeper2 >= 0) { extreme = deeper2; continue; }

            break;
        }

        double sweepExtreme = Extreme(c5, extreme, isShort);

        double triggerPrice = isShort
            ? c5[trigger].Low
            : c5[trigger].High;

        // ---- Step 4: verified 3-candle FVG from the displacement leg ----
        int fvgMid = -1;
        double proximal = 0, distal = 0;

        int fvgLimit = Math.Min(displacement + FvgSearchAfter, c5.Count - 1);

        for (int mid = displacement; mid <= fvgLimit && fvgMid < 0; mid++)
        {
            if (mid - 1 < 0 || mid + 1 >= c5.Count) continue;

            if (isShort)
            {
                double gapTop = c5[mid - 1].Low;
                double gapBottom = c5[mid + 1].High;

                if (gapTop > gapBottom)
                {
                    fvgMid = mid;
                    proximal = gapBottom;
                    distal = gapTop;
                }
            }
            else
            {
                double gapBottom = c5[mid - 1].High;
                double gapTop = c5[mid + 1].Low;

                if (gapTop > gapBottom)
                {
                    fvgMid = mid;
                    proximal = gapTop;
                    distal = gapBottom;
                }
            }
        }

        if (fvgMid < 0)
            return Incomplete(raid, SetupStage.NoFvg,
                breach, extreme, sweepExtreme, c5,
                trigger, triggerPrice, displacement, chop);

        // ---- Step 5: retrace into the FVG = entry ----
        int entry = -1;
        var stage = SetupStage.NoRetrace;

        int retraceLimit = Math.Min(fvgMid + 1 + RetraceWindow, c5.Count - 1);

        for (int i = fvgMid + 2; i <= retraceLimit; i++)
        {
            bool invalidated = isShort
                ? c5[i].High > sweepExtreme
                : c5[i].Low < sweepExtreme;

            bool touched = isShort
                ? c5[i].High >= proximal
                : c5[i].Low <= proximal;

            // FVG INVERSION (the user caught live on DOGE; GPT flagged
            // in review): wicks through the gap are allowed, but a
            // candle that CLOSES through the far edge inverts the
            // FVG — the entry premise is dead, no trade.
            bool inverted = isShort
                ? c5[i].Close > distal
                : c5[i].Close < distal;

            // TD_NO_PEEK=1 (2026-10-14 audit): a resting limit is FILLED the moment price touches it, before the bar's
            // close or a later stop-through is known. Count every touch as a fill (stop on the same bar = -1R in the scorer).
            if (touched && NoPeek)
            {
                entry = i;
                stage = SetupStage.Complete;
                break;
            }

            if (touched && !invalidated)
            {
                if (inverted)
                {
                    stage = SetupStage.FvgInverted;
                    break;
                }

                entry = i;
                stage = SetupStage.Complete;
                break;
            }

            if (invalidated)
            {
                stage = SetupStage.InvalidatedBeforeEntry;
                break;
            }
        }

        double baselineRange = 0;
        int nb = 0;

        for (int k = 1; k <= 20; k++)
        {
            int idx = displacement - k;
            if (idx < 0) break;
            baselineRange += c5[idx].High - c5[idx].Low;
            nb++;
        }

        if (nb > 0) baselineRange /= nb;

        double dispRatio = baselineRange > 0
            ? (c5[displacement].High - c5[displacement].Low) / baselineRange
            : 0;

        double fvgRatio = baselineRange > 0
            ? Math.Abs(distal - proximal) / baselineRange
            : 0;

        double entryPrice = proximal;
        double stop = sweepExtreme;

        double risk = isShort
            ? stop - entryPrice
            : entryPrice - stop;

        int chartStart = Math.Max(0, trigger - ChartPad - 20);
        int chartEnd = Math.Min(c5.Count - 1,
            (entry > 0 ? entry : Math.Min(retraceLimit, c5.Count - 1)) + ChartPad);

        return new FullSetup(
            raid, stage,
            breach, extreme, sweepExtreme,
            trigger, triggerPrice,
            displacement, chop,
            fvgMid, proximal, distal,
            entry, entryPrice, stop, risk,
            dispRatio, fvgRatio,
            chartStart, chartEnd
        );
    }

    // ---------- helpers ----------

    private static double Extreme(
        IReadOnlyList<ResearchCandle> c, int i, bool isShort) =>
        isShort ? c[i].High : c[i].Low;

    private static bool Deeper(
        IReadOnlyList<ResearchCandle> c, int i, int than, bool isShort) =>
        isShort
            ? c[i].High > c[than].High
            : c[i].Low < c[than].Low;

    private static int DeeperWithin(
        IReadOnlyList<ResearchCandle> c,
        int extreme,
        int upto,
        bool isShort)
    {
        int deepest = -1;

        int limit = Math.Min(upto, c.Count - 1);

        for (int k = extreme + 1; k <= limit; k++)
        {
            if (!Deeper(c, k, extreme, isShort)) continue;

            if (deepest < 0 || Deeper(c, k, deepest, isShort))
                deepest = k;
        }

        return deepest;
    }

    private static bool Reclaimed(
        IReadOnlyList<ResearchCandle> c,
        int extreme,
        double level,
        int upto,
        bool isShort)
    {
        int limit = Math.Min(upto, c.Count - 1);

        for (int k = extreme + 1; k <= limit; k++)
        {
            bool inside = isShort
                ? c[k].Close < level
                : c[k].Close > level;

            if (inside) return true;
        }

        return false;
    }

    private static int FindTrigger(
        IReadOnlyList<ResearchCandle> c,
        int extreme,
        bool isShort,
        int breach,
        double level)
    {
        // Last unbroken pivot before the extreme = the pullback that
        // directly produced the final push. Scan starts right next
        // to the extreme (pivot validity itself handles neighbours).
        for (int i = extreme - 1; i >= TriggerPivotStrength; i--)
        {
            if (PreBreachTriggerOnly && i >= breach)
                continue;

            if (!IsPivot(c, i, TriggerPivotStrength, oppositeOfShort: isShort))
                continue;

            // HARD INVARIANT (bug #4, caught live by the user on XRP):
            // a short's trigger low must sit BELOW the level, a
            // long's trigger high ABOVE it. A trigger on the wrong
            // side means the anchor logic mis-picked the breach —
            // reject it no matter what upstream believed.
            double sidePrice = isShort ? c[i].Low : c[i].High;

            bool wrongSide = isShort
                ? sidePrice >= level
                : sidePrice <= level;

            if (wrongSide)
                continue;

            double pivotPrice = isShort ? c[i].Low : c[i].High;

            bool broken = false;

            for (int k = i + 1; k < extreme; k++)
            {
                bool beyond = isShort
                    ? c[k].Low < pivotPrice
                    : c[k].High > pivotPrice;

                if (beyond) { broken = true; break; }
            }

            if (!broken)
                return i;
        }

        return -1;
    }

    private static (int index, int chop) FindDisplacement(
        IReadOnlyList<ResearchCandle> c,
        int extreme,
        double triggerPrice,
        bool isShort)
    {
        // chop = candles that WICK through the trigger without a
        // close through, before displacement. Wicks are allowed by
        // the locked rules; lots of them = chewed-up trigger. This
        // is recorded so the backtest can judge, never filtered.
        int chop = 0;

        int limit = Math.Min(extreme + DisplacementWindow, c.Count - 1);

        for (int i = extreme + 1; i <= limit; i++)
        {
            bool closedThrough = isShort
                ? c[i].Close < triggerPrice
                : c[i].Close > triggerPrice;

            if (closedThrough)
                return (i, chop);

            bool wickedThrough = isShort
                ? c[i].Low < triggerPrice
                : c[i].High > triggerPrice;

            if (wickedThrough) chop++;
        }

        return (-1, chop);
    }

    private static FullSetup Incomplete(
        SweepCandidate raid,
        SetupStage stage,
        int breach,
        int extreme,
        double sweepExtreme,
        IReadOnlyList<ResearchCandle> c5,
        int trigger = -1,
        double triggerPrice = 0,
        int displacement = -1,
        int chop = 0)
    {
        int chartStart = Math.Max(0, (trigger > 0 ? trigger : extreme) - 32);
        int chartEnd = Math.Min(c5.Count - 1, extreme + 32);

        return new FullSetup(
            raid, stage,
            breach, extreme, sweepExtreme,
            trigger, triggerPrice,
            displacement, chop,
            -1, 0, 0,
            -1, 0, sweepExtreme, 0,
            0, 0,
            chartStart, chartEnd
        );
    }

    private static bool IsPivot(
        IReadOnlyList<ResearchCandle> c,
        int i,
        int strength,
        bool oppositeOfShort)
    {
        if (i - strength < 0 || i + strength >= c.Count)
            return false;

        for (int k = 1; k <= strength; k++)
        {
            // Ties do NOT disqualify structure (bug caught live by
            // the user on XRP: an equal-low double bottom — the obvious
            // launch point — was skipped for an older low). Neighbors
            // may equal the pivot; they just can't exceed it.
            if (oppositeOfShort)
            {
                if (c[i - k].Low < c[i].Low) return false;
                if (c[i + k].Low < c[i].Low) return false;
            }
            else
            {
                if (c[i - k].High > c[i].High) return false;
                if (c[i + k].High > c[i].High) return false;
            }
        }

        return true;
    }

    private static int FindIndexAt(
        IReadOnlyList<ResearchCandle> c,
        long timestamp)
    {
        int lo = 0, hi = c.Count - 1;

        while (lo < hi)
        {
            int mid = (lo + hi) / 2;

            if (c[mid].Timestamp < timestamp) lo = mid + 1;
            else hi = mid;
        }

        return lo;
    }
}
