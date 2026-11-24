using TradeDeck.Core.Models;

namespace TradeDeck.Strategy1;

public enum StructuralSide
{
    High,
    Low
}

public sealed class StructuralLevel
{
    public StructuralSide Side { get; init; }
    public double Price { get; init; }
    public long Timestamp { get; init; }
    public int CandleIndex { get; init; }
    public double Atr { get; init; }
    public double MoveAway { get; init; }
}

public static class Strategy1StructureDetector
{
    private const int SwingStrength = 2;
    private const int AtrPeriod = 14;
    private const int MoveAwayBars = 6;
    private const double MinMoveAwayAtr = 0.80;
    private const int LookbackBars = 80;

    public static StructuralLevel? FindCurrentHigh(
        IReadOnlyList<Candle> candles)
    {
        if (candles.Count < AtrPeriod + SwingStrength + 2)
            return null;

        int start = Math.Max(
            AtrPeriod,
            candles.Count - LookbackBars);

        for (int i = candles.Count - SwingStrength - 1; i >= start; i--)
        {
            if (!IsSwingHigh(candles, i))
                continue;

            double atr = GetAtr(candles, i, AtrPeriod);

            if (atr <= 0)
                continue;

            double moveAway = GetMoveAwayFromHigh(candles, i);

            if (moveAway < atr * MinMoveAwayAtr)
                continue;

            if (!HighStillValid(candles, i))
                continue;

            return new StructuralLevel
            {
                Side = StructuralSide.High,
                Price = candles[i].High,
                Timestamp = candles[i].Timestamp,
                CandleIndex = i,
                Atr = atr,
                MoveAway = moveAway
            };
        }

        return null;
    }

    public static StructuralLevel? FindCurrentLow(
        IReadOnlyList<Candle> candles)
    {
        if (candles.Count < AtrPeriod + SwingStrength + 2)
            return null;

        int start = Math.Max(
            AtrPeriod,
            candles.Count - LookbackBars);

        for (int i = candles.Count - SwingStrength - 1; i >= start; i--)
        {
            if (!IsSwingLow(candles, i))
                continue;

            double atr = GetAtr(candles, i, AtrPeriod);

            if (atr <= 0)
                continue;

            double moveAway = GetMoveAwayFromLow(candles, i);

            if (moveAway < atr * MinMoveAwayAtr)
                continue;

            if (!LowStillValid(candles, i))
                continue;

            return new StructuralLevel
            {
                Side = StructuralSide.Low,
                Price = candles[i].Low,
                Timestamp = candles[i].Timestamp,
                CandleIndex = i,
                Atr = atr,
                MoveAway = moveAway
            };
        }

        return null;
    }

    private static bool IsSwingHigh(
        IReadOnlyList<Candle> candles,
        int index)
    {
        double high = candles[index].High;

        for (int offset = 1; offset <= SwingStrength; offset++)
        {
            if (high <= candles[index - offset].High)
                return false;

            if (high <= candles[index + offset].High)
                return false;
        }

        return true;
    }

    private static bool IsSwingLow(
        IReadOnlyList<Candle> candles,
        int index)
    {
        double low = candles[index].Low;

        for (int offset = 1; offset <= SwingStrength; offset++)
        {
            if (low >= candles[index - offset].Low)
                return false;

            if (low >= candles[index + offset].Low)
                return false;
        }

        return true;
    }

    private static double GetMoveAwayFromHigh(
        IReadOnlyList<Candle> candles,
        int index)
    {
        int end = Math.Min(
            candles.Count - 1,
            index + MoveAwayBars);

        double lowest = candles[index].Low;

        for (int i = index + 1; i <= end; i++)
            lowest = Math.Min(lowest, candles[i].Low);

        return candles[index].High - lowest;
    }

    private static double GetMoveAwayFromLow(
        IReadOnlyList<Candle> candles,
        int index)
    {
        int end = Math.Min(
            candles.Count - 1,
            index + MoveAwayBars);

        double highest = candles[index].High;

        for (int i = index + 1; i <= end; i++)
            highest = Math.Max(highest, candles[i].High);

        return highest - candles[index].Low;
    }

    private static bool HighStillValid(
        IReadOnlyList<Candle> candles,
        int index)
    {
        double level = candles[index].High;

        for (int i = index + 1; i < candles.Count; i++)
        {
            if (candles[i].Close > level)
                return false;
        }

        return true;
    }

    private static bool LowStillValid(
        IReadOnlyList<Candle> candles,
        int index)
    {
        double level = candles[index].Low;

        for (int i = index + 1; i < candles.Count; i++)
        {
            if (candles[i].Close < level)
                return false;
        }

        return true;
    }

    private static double GetAtr(
        IReadOnlyList<Candle> candles,
        int endIndex,
        int period)
    {
        if (endIndex < period)
            return 0;

        double total = 0;
        int start = endIndex - period + 1;

        for (int i = start; i <= endIndex; i++)
        {
            double previousClose = candles[i - 1].Close;

            double trueRange = Math.Max(
                candles[i].High - candles[i].Low,
                Math.Max(
                    Math.Abs(candles[i].High - previousClose),
                    Math.Abs(candles[i].Low - previousClose)));

            total += trueRange;
        }

        return total / period;
    }
}
