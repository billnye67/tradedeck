namespace TradeDeck.Research;

/// <summary>
/// TEST MY SETUP v1. Replays every COMPLETE Strategy #1 setup
/// against the 5m history and reports results in R.
///
/// Model per the gameplan:
///   entry = FVG proximal edge (filled when touched)
///   stop  = the actual sweep wick
///   target = fixed R multiple (1R / 2R / 3R each reported)
///
/// Conservative rules:
///   - if a candle could hit both stop and target, it counts as a LOSS
///   - on the entry candle itself, if the stop price trades, it's a LOSS
///   - unresolved after the walk window exits at the final close
///
/// No fees/slippage yet — v1 measures the raw edge; costs get
/// subtracted in v2 once the raw number exists.
/// </summary>
public static class BacktestScorer
{
    private const int WalkWindow = 480;         // 40h of 5m candles per trade
    private static readonly double[] Targets = { 1.0, 2.0, 3.0 };

    // Hyperliquid perps, base tier: maker 0.015%, taker 0.045%.
    // Entry = resting limit at the FVG (maker). Target = resting
    // limit (maker). Stop-out = market (taker). So winners pay
    // maker+maker and losers pay maker+taker.
    // Per-side costs. Crypto default = Binance maker 1.5bp / taker 4.5bp (= Broker 3+3 round trip).
    // TD_MAKER_BP / TD_TAKER_BP override (e.g. forex: 0.4bp commission + half-spread each side).
    private static readonly double MakerFee = double.TryParse(Environment.GetEnvironmentVariable("TD_MAKER_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var mk) ? mk / 10_000.0 : 0.00015;
    private static readonly double TakerFee = double.TryParse(Environment.GetEnvironmentVariable("TD_TAKER_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var tk) ? tk / 10_000.0 : 0.00045;
    // Strict-cost switches (GPT audit): TD_SLIP_BP adverse slippage per execution (entry and exit),
    // TD_SWAP_BP financing per 00:00 UTC crossing while the trade is open. Default 0 = legacy behaviour.
    private static readonly double SlipBp = double.TryParse(Environment.GetEnvironmentVariable("TD_SLIP_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var sl) ? sl / 10_000.0 : 0.0;
    private static readonly double SwapBp = double.TryParse(Environment.GetEnvironmentVariable("TD_SWAP_BP"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var sw) ? sw / 10_000.0 : 0.0;
    private static readonly bool SlipStopsOnly = Environment.GetEnvironmentVariable("TD_SLIP_MODE") == "stops";
    private static double Costs(IReadOnlyList<ResearchCandle> c5, int entryIdx, int exitIdx, double fees, bool stopped = true)
    {
        long nights = c5[exitIdx].Timestamp / 86_400_000L - c5[entryIdx].Timestamp / 86_400_000L;
        return fees + (SlipStopsOnly ? (stopped ? SlipBp : 0.0) : 2 * SlipBp) + nights * SwapBp;
    }

    private sealed record TradeResult(
        FullSetup Setup,
        double Target,
        double ResultR,
        double FeeR,        // round-trip fees expressed in R for THIS trade
        bool Resolved
    );

    public sealed record MarketSummary(
        string Market, int Trades, double NetR3, double MaxDd3);

    public static MarketSummary Score(
        string market,
        IReadOnlyList<FullSetup> setups,
        IReadOnlyList<ResearchCandle> c5)
    {
        var complete = setups
            .Where(s => s.Stage == SetupStage.Complete && s.RiskPerUnit > 0)
            .ToList();

        Console.WriteLine();
        Console.WriteLine($"TEST MY SETUP — {market} — Strategy #1");
        Console.WriteLine($"Complete setups scored: {complete.Count:N0}");
        Console.WriteLine();

        double netR3 = 0;
        double maxDd3 = 0;

        foreach (double t in Targets)
        {
            var results = complete
                .Select(s => Replay(s, c5, t))
                .ToList();

            (double netTotal, double dd) =
                Report($"TARGET {t:F0}R", results);

            if (t == 3.0) { netR3 = netTotal; maxDd3 = dd; }
        }

        // Breakdowns use the 2R model.
        var r2 = complete
            .Select(s => Replay(s, c5, 2.0))
            .ToList();

        Console.WriteLine();
        Console.WriteLine("BREAKDOWNS (2R model)");

        Breakdown(r2, "direction",
            r => r.Setup.Raid.Direction == "HIGH" ? "SHORT" : "LONG");

        Breakdown(r2, "session (UTC)",
            r => SessionOf(r.Setup.Raid.SweepTimestamp));

        Breakdown(r2, "displacement force",
            r => r.Setup.DisplacementRatio < 1.5 ? "weak <1.5x"
               : r.Setup.DisplacementRatio < 2.5 ? "mid 1.5-2.5x"
               : "strong >2.5x");

        Breakdown(r2, "FVG size",
            r => r.Setup.FvgSizeRatio < 0.5 ? "dust <0.5x"
               : r.Setup.FvgSizeRatio < 1.0 ? "small 0.5-1x"
               : "real >1x");

        Breakdown(r2, "trigger chop",
            r => r.Setup.ChopCrossings == 0 ? "clean"
               : r.Setup.ChopCrossings <= 2 ? "some"
               : "chewed");

        Breakdown(r2, "year",
            r => DateTimeOffset
                .FromUnixTimeMilliseconds(r.Setup.Raid.SweepTimestamp)
                .Year.ToString());

        return new MarketSummary(market, complete.Count, netR3, maxDd3);
    }

    /// <summary>Index of the 5m bar where the fixed-3R trade exits (stop, target or walk limit).</summary>
    public static int ExitIndex(FullSetup s, IReadOnlyList<ResearchCandle> c5)
    {
        bool isShort = s.Raid.Direction == "HIGH";
        double target = isShort ? s.EntryPrice - 3.0 * s.RiskPerUnit : s.EntryPrice + 3.0 * s.RiskPerUnit;
        int limit = Math.Min(s.EntryIndex5m + WalkWindow, c5.Count - 1);
        for (int i = s.EntryIndex5m; i <= limit; i++)
        {
            if (isShort ? c5[i].High >= s.StopLoss : c5[i].Low <= s.StopLoss) return i;
            if (isShort ? c5[i].Low <= target : c5[i].High >= target) return i;
        }
        return limit;
    }

    private static TradeResult Replay(
        FullSetup s,
        IReadOnlyList<ResearchCandle> c5,
        double targetR)
    {
        bool isShort = s.Raid.Direction == "HIGH";

        double entry = s.EntryPrice;
        double stop = s.StopLoss;
        double risk = s.RiskPerUnit;

        double target = isShort
            ? entry - targetR * risk
            : entry + targetR * risk;

        int limit = Math.Min(s.EntryIndex5m + WalkWindow, c5.Count - 1);

        for (int i = s.EntryIndex5m; i <= limit; i++)
        {
            bool stopped = isShort
                ? c5[i].High >= stop
                : c5[i].Low <= stop;

            bool hitTarget = isShort
                ? c5[i].Low <= target
                : c5[i].High >= target;

            // conservative: any candle that trades the stop is a loss,
            // even if it also trades the target
            if (stopped)
                return new TradeResult(s, targetR, -1.0,
                    FeeR(s, Costs(c5, s.EntryIndex5m, i, MakerFee + TakerFee)), true);

            if (hitTarget)
                return new TradeResult(s, targetR, targetR,
                    FeeR(s, Costs(c5, s.EntryIndex5m, i, MakerFee + MakerFee, false)), true);
        }

        // unresolved: exit at the last walked close
        double exitClose = c5[limit].Close;

        double resultR = isShort
            ? (entry - exitClose) / risk
            : (exitClose - entry) / risk;

        // unresolved exits close at market = taker
        return new TradeResult(s, targetR, resultR,
            FeeR(s, Costs(c5, s.EntryIndex5m, limit, MakerFee + TakerFee)), false);
    }

    // fees are a % of position value; converted into R they scale
    // with how tight the stop is: tight stop = big position = fees
    // eat a bigger slice of each R.
    private static double FeeR(FullSetup s, double roundTrip) =>
        roundTrip * s.EntryPrice / s.RiskPerUnit;

    private static (double netTotal, double maxDd) Report(
        string label,
        List<TradeResult> results)
    {
        int n = results.Count;
        if (n == 0) return (0, 0);

        int wins = results.Count(r => r.Resolved && r.ResultR > 0);
        int losses = results.Count(r => r.Resolved && r.ResultR < 0);
        int unresolved = results.Count(r => !r.Resolved);

        double totalR = results.Sum(r => r.ResultR);
        double expectancy = totalR / n;

        double netTotal = results.Sum(r => r.ResultR - r.FeeR);
        double netExpectancy = netTotal / n;
        double avgFee = results.Average(r => r.FeeR);

        double grossWin = results.Where(r => r.ResultR > 0).Sum(r => r.ResultR);
        double grossLoss = Math.Abs(results.Where(r => r.ResultR < 0).Sum(r => r.ResultR));

        double profitFactor = grossLoss > 0 ? grossWin / grossLoss : 0;

        // max drawdown on the cumulative R curve, chronological order
        double peak = 0, dd = 0, cum = 0;

        foreach (var r in results.OrderBy(r => r.Setup.Raid.SweepTimestamp))
        {
            cum += r.ResultR;
            if (cum > peak) peak = cum;
            if (peak - cum > dd) dd = peak - cum;
        }

        Console.WriteLine(
            $"{label,-10} | trades {n,5:N0} | win {100.0 * wins / n,5:F1}% | " +
            $"exp {expectancy,6:F3}R | NET after fees {netExpectancy,6:F3}R " +
            $"(avg fee {avgFee:F3}R) | PF {profitFactor,5:F2} | " +
            $"net total {netTotal,8:F1}R | maxDD {dd,7:F1}R"
        );

        return (netTotal, dd);
    }

    private static void Breakdown(
        List<TradeResult> results,
        string title,
        Func<TradeResult, string> keyOf)
    {
        Console.WriteLine();
        Console.WriteLine($"-- by {title}");

        foreach (var g in results
            .GroupBy(keyOf)
            .OrderBy(g => g.Key))
        {
            int n = g.Count();
            int wins = g.Count(r => r.ResultR > 0);
            double exp = g.Sum(r => r.ResultR - r.FeeR) / n;

            Console.WriteLine(
                $"   {g.Key,-14} | trades {n,5:N0} | win {100.0 * wins / n,5:F1}% | NET exp {exp,6:F3}R"
            );
        }
    }

    /// <summary>
    /// Winner profiling: replays trades (3R model), then for every
    /// measured trait splits trades into 5 equal-size buckets and
    /// shows win rate + NET expectancy per bucket. Traits where the
    /// buckets separate hard are checklist candidates — subject to
    /// out-of-sample verification before becoming filters.
    /// </summary>
    public static void Profile(
        string market,
        IReadOnlyList<FullSetup> setups,
        IReadOnlyList<ResearchCandle> c5,
        List<(double resultR, double feeR, FullSetup s)> sink)
    {
        foreach (var s in setups.Where(
            x => x.Stage == SetupStage.Complete && x.RiskPerUnit > 0))
        {
            TradeResult r = Replay(s, c5, 3.0);
            sink.Add((r.ResultR, r.FeeR, s));
        }
    }

    public static void PrintProfile(
        List<(double resultR, double feeR, FullSetup s)> trades)
    {
        Console.WriteLine();
        Console.WriteLine($"WINNER PROFILE — {trades.Count:N0} trades (3R model, net of fees)");

        int w = trades.Count(t => t.resultR > 0);
        Console.WriteLine($"winners {w:N0} | losers {trades.Count - w:N0}");

        Trait(trades, "displacement force (x avg range)",
            t => t.s.DisplacementRatio);

        Trait(trades, "FVG size (x avg range)",
            t => t.s.FvgSizeRatio);

        Trait(trades, "trigger chop (wicks through)",
            t => t.s.ChopCrossings);

        Trait(trades, "stop distance (% of price)",
            t => t.s.RiskPerUnit / t.s.EntryPrice * 100.0);

        Trait(trades, "breach depth (% of level)",
            t => Math.Abs(t.s.SweepExtreme - t.s.Raid.Level) / t.s.Raid.Level * 100.0);

        Trait(trades, "hour of day (UTC)",
            t => DateTimeOffset
                .FromUnixTimeMilliseconds(t.s.Raid.SweepTimestamp)
                .UtcDateTime.Hour);
    }

    private static void Trait(
        List<(double resultR, double feeR, FullSetup s)> trades,
        string name,
        Func<(double resultR, double feeR, FullSetup s), double> valueOf)
    {
        var sorted = trades
            .OrderBy(valueOf)
            .ToList();

        Console.WriteLine();
        Console.WriteLine($"-- {name}");

        const int Buckets = 5;
        int per = sorted.Count / Buckets;

        for (int b = 0; b < Buckets; b++)
        {
            var slice = sorted
                .Skip(b * per)
                .Take(b == Buckets - 1 ? sorted.Count - b * per : per)
                .ToList();

            if (slice.Count == 0) continue;

            double lo = valueOf(slice[0]);
            double hi = valueOf(slice[^1]);

            int wins = slice.Count(t => t.resultR > 0);
            double net = slice.Sum(t => t.resultR - t.feeR) / slice.Count;

            Console.WriteLine(
                $"   {lo,8:F2} - {hi,8:F2} | trades {slice.Count,5:N0} | " +
                $"win {100.0 * wins / slice.Count,5:F1}% | NET exp {net,6:F3}R"
            );
        }
    }

    private static string SessionOf(long unixMs)
    {
        int hour = DateTimeOffset
            .FromUnixTimeMilliseconds(unixMs)
            .UtcDateTime.Hour;

        if (hour < 7) return "Asia 00-07";
        if (hour < 13) return "London 07-13";
        if (hour < 21) return "NY 13-21";
        return "Late 21-24";
    }
}
