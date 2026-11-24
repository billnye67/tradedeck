namespace TradeDeck.Research;

/// <summary>
/// SLOW MOMENTUM TRIAL (time-series momentum, the literature's
/// strongest surviving crypto family). Mechanism: capital arrives
/// over weeks (allocators, ETF flows, retail waves), so multi-week
/// direction persists; leveraged counter-trend traders supply the
/// fuel. NOT the dead 15m breakout — this is the weeks version.
///
/// Frozen spec: daily closes (00:00 UTC from 15m). For lookback L
/// in {7, 30, 90} days (ALL reported, robustness across all three
/// is the bar — no picking the best):
///   direction(day) = sign of trailing L-day return
///   exposure held for the next day, PnL in ATR-normalized R:
///   dayR = direction x (close_t+1 - close_t) / ATR14(daily)
///   costs: taker x 2 on direction-flip days.
/// Run: dotnet run mom
/// </summary>
public static class Momentum
{
    private const double TakerFee = 0.00045;

    private sealed record D(string Market, long Ts, int Year, double R);

    public static void Run(string source, string[] markets)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        // build daily series per market
        var daily = new Dictionary<string, List<(long ts, double close, double high, double low)>>();

        foreach (string m in markets)
        {
            var c15 = reader.GetAllCandles(source, m, "15m");
            if (c15.Count == 0) continue;

            var days = new List<(long, double, double, double)>();
            long curDay = -1; double hi = 0, lo = 0, cl = 0;

            foreach (var c in c15)
            {
                long d = c.Timestamp / 86_400_000L;
                if (d != curDay)
                {
                    if (curDay >= 0) days.Add((curDay * 86_400_000L, cl, hi, lo));
                    curDay = d; hi = c.High; lo = c.Low;
                }
                hi = Math.Max(hi, c.High); lo = Math.Min(lo, c.Low); cl = c.Close;
            }
            if (curDay >= 0) days.Add((curDay * 86_400_000L, cl, hi, lo));
            daily[m] = days;
            Console.WriteLine($"{m}: {days.Count:N0} days");
        }

        foreach (int L in new[] { 7, 30, 90 })
        {
            var rows = new List<D>();

            foreach (var (m, days) in daily)
            {
                int n = days.Count;
                var atr = new double[n];
                double sum = 0; var trs = new double[n];
                for (int i = 1; i < n; i++)
                {
                    double tr = Math.Max(days[i].high - days[i].low,
                        Math.Max(Math.Abs(days[i].high - days[i - 1].close),
                                 Math.Abs(days[i].low - days[i - 1].close)));
                    trs[i] = tr; sum += tr;
                    if (i > 14) sum -= trs[i - 14];
                    if (i >= 14) atr[i] = sum / 14;
                }

                int prevDir = 0;
                for (int i = Math.Max(L, 15); i < n - 1; i++)
                {
                    if (atr[i] <= 0) continue;
                    int dir = Math.Sign(days[i].close - days[i - L].close);
                    if (dir == 0) { prevDir = 0; continue; }

                    double r = dir * (days[i + 1].close - days[i].close) / atr[i];
                    if (dir != prevDir) r -= 2 * TakerFee * days[i].close / atr[i];
                    prevDir = dir;

                    rows.Add(new D(m, days[i + 1].ts,
                        DateTimeOffset.FromUnixTimeMilliseconds(days[i + 1].ts).Year, r));
                }
            }

            Console.WriteLine();
            Console.WriteLine($"LOOKBACK {L} DAYS  (day-R = next-day move / daily ATR, signed by trailing {L}d direction)");
            Console.WriteLine($"{"slice",-16} | {"days",7} {"exp",7} {"total",9} {"win%",6} {"maxDD",8}");
            Console.WriteLine(new string('-', 62));
            Slice("LEARN 2020-23", rows.Where(r => r.Ts < eraSplit).ToList());
            Slice("EXAM 2024-26", rows.Where(r => r.Ts >= eraSplit).ToList());

            Console.WriteLine("  by coin (total R):");
            foreach (var g in rows.GroupBy(r => r.Market).OrderByDescending(g => g.Sum(x => x.R)))
                Console.WriteLine($"    {g.Key,-9} {g.Sum(x => x.R),9:F1}R");
            Console.WriteLine("  by year (total R):");
            foreach (var g in rows.GroupBy(r => r.Year).OrderBy(g => g.Key))
                Console.WriteLine($"    {g.Key}  {g.Sum(x => x.R),9:F1}R");
        }

        Console.WriteLine();
        Console.WriteLine("bar: positive exp in BOTH eras for ALL THREE lookbacks (robustness), with coin+year breadth.");
    }

    private static void Slice(string name, List<D> rows)
    {
        if (rows.Count == 0) { Console.WriteLine($"{name,-16} | 0"); return; }
        var seq = rows.OrderBy(r => r.Ts).Select(r => r.R).ToList();
        double total = seq.Sum(), peak = 0, dd = 0, cum = 0;
        foreach (double x in seq) { cum += x; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
        Console.WriteLine($"{name,-16} | {seq.Count,7:N0} {total / seq.Count,7:F3} {total,9:F1} {100.0 * seq.Count(x => x > 0) / seq.Count,5:F1}% {dd,8:F1}");
    }
}
