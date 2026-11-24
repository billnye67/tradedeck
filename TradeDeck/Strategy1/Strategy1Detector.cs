using TradeDeck.Core.Models;

namespace TradeDeck.Strategy1;

public sealed class Strategy1Detector
{
    public Strategy1Setup Analyze(
        IReadOnlyList<Candle> candles15m,
        IReadOnlyList<Candle> candles5m)
    {
        var setup = new Strategy1Setup();

        if (candles15m.Count < 10 || candles5m.Count < 30)
        {
            setup.InvalidReason = "Not enough candle data.";
            return setup;
        }

        // STEP 1:
        // Identify the current meaningful 15m structural high/low.
        //
        // We are deliberately NOT implementing this yet with
        // a generic pivot/fractal formula.
        //
        // Next we will define exactly what makes a 15m level
        // "meaningful current structure" using real chart examples.

        return setup;
    }
}
