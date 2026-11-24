namespace TradeDeck.Research;

/// <summary>
/// SHOCK FADE TRIAL — the prospector's home-grown lead (F3), given
/// structure and examined. SOL + XRP only (the both-era YES rows).
///
/// Frozen spec, declared before running:
///   SHOCK   an hourly bar whose |return| >= that coin's LEARN-era
///           99th percentile (same definition the prospector used).
///   A IMMEDIATE  enter at next hour's open, direction OPPOSITE the
///           shock. Stop = the shock bar's extreme (structural: the
///           fade thesis is dead if the move extends). Exit at stop
///           or at the close of the 3rd hour after entry (the
///           measured horizon). No targets.
///   B RETRACE    same shock; instead of market entry, limit at the
///           shock bar's midpoint, filled only if touched within the
///           3h window; same stop; same clock exit at 3h from fill.
///           (One alternate expression, pre-registered, both shown.)
///   Fees: taker both sides for A; maker entry + taker exit for B.
/// Run: dotnet run shockfade
/// </summary>
public static class ShockFade
{
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private sealed record T(long Ts, double NetR, int Year);

    public static void Run(string source)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        var modelA = new List<T>();
        var modelB = new List<T>();

        foreach (string market in new[] { "SOLUSDT", "XRPUSDT" })
        {
            var c15 = reader.GetAllCandles(source, market, "15m");
            if (c15.Count == 0) continue;

            // hourly bars with 15m index of hour start
            var hours = new List<(long ts, double o, double h, double l, double c, int i15)>();
            long cur = -1; double o = 0, hi = 0, lo = 0, cl = 0; int i0 = 0;
            for (int i = 0; i < c15.Count; i++)
            {
                long hb = c15[i].Timestamp / 3_600_000L;
                if (hb != cur)
                {
                    if (cur >= 0) hours.Add((cur * 3_600_000L, o, hi, lo, cl, i0));
                    cur = hb; o = c15[i].Open; hi = c15[i].High; lo = c15[i].Low; i0 = i;
                }
                hi = Math.Max(hi, c15[i].High); lo = Math.Min(lo, c15[i].Low); cl = c15[i].Close;
            }
            if (cur >= 0) hours.Add((cur * 3_600_000L, o, hi, lo, cl, i0));

            var absLearn = hours.Where(x => x.ts < eraSplit)
                .Select(x => Math.Abs((x.c - x.o) / x.o)).OrderBy(x => x).ToList();
            double shock = absLearn[(int)(absLearn.Count * 0.99)];
            Console.WriteLine($"{market}: shock threshold {shock * 10_000:F0} bp (learn 99th pct), {hours.Count:N0} hours");

            for (int h = 1; h < hours.Count - 4; h++)
            {
                double ret = (hours[h].c - hours[h].o) / hours[h].o;
                if (Math.Abs(ret) < shock) continue;

                bool shockUp = ret > 0;         // fade => short an up-shock
                bool isShort = shockUp;
                double stop = shockUp ? hours[h].h : hours[h].l;

                // hour bars must be contiguous
                if (hours[h + 1].ts != hours[h].ts + 3_600_000L) continue;

                int year = DateTimeOffset.FromUnixTimeMilliseconds(hours[h + 1].ts).Year;

                // ---- A: immediate at next open ----
                {
                    double entry = hours[h + 1].o;
                    double risk = Math.Abs(entry - stop);
                    if (risk > 0)
                    {
                        double exitPx = WalkTo(c15, hours[h + 1].i15, hours[h].ts + 4 * 3_600_000L, isShort, stop,
                            out bool stopped);
                        double gross = isShort ? (entry - exitPx) / risk : (exitPx - entry) / risk;
                        double fees = 2 * TakerFee * entry / risk;
                        modelA.Add(new T(hours[h + 1].ts, gross - fees, year));
                    }
                }

                // ---- B: retrace limit at shock midpoint ----
                {
                    double entry = (hours[h].o + hours[h].c) / 2;
                    double risk = Math.Abs(entry - stop);
                    if (risk > 0)
                    {
                        // find touch within (h+1 .. h+3)
                        int fillIdx = -1;
                        for (int i = hours[h + 1].i15; i < c15.Count && c15[i].Timestamp < hours[h].ts + 4 * 3_600_000L; i++)
                        {
                            bool touch = isShort ? c15[i].High >= entry : c15[i].Low <= entry;
                            if (touch) { fillIdx = i; break; }
                        }
                        if (fillIdx >= 0)
                        {
                            double exitPx = WalkTo(c15, fillIdx, c15[fillIdx].Timestamp + 3 * 3_600_000L, isShort, stop,
                                out bool stopped);
                            double gross = isShort ? (entry - exitPx) / risk : (exitPx - entry) / risk;
                            double fees = (MakerFee + TakerFee) * entry / risk;
                            modelB.Add(new T(c15[fillIdx].Timestamp, gross - fees, year));
                        }
                    }
                }

                h += 3; // don't overlap the same shock window
            }
        }

        Print("A — IMMEDIATE FADE (next open, stop=shock wick, 3h clock)", modelA, eraSplit);
        Print("B — RETRACE FADE (limit at shock midpoint, same stop, 3h from fill)", modelB, eraSplit);

        Console.WriteLine();
        Console.WriteLine("bar: net positive in BOTH eras for an expression, with year breadth. Otherwise the lean stays a feature, not a strategy.");
    }

    private static double WalkTo(IReadOnlyList<ResearchCandle> c15, int from, long untilTs, bool isShort, double stop,
        out bool stopped)
    {
        stopped = false;
        double exitPx = c15[Math.Min(from, c15.Count - 1)].Close;
        for (int i = from; i < c15.Count && c15[i].Timestamp < untilTs; i++)
        {
            bool hit = isShort ? c15[i].High >= stop : c15[i].Low <= stop;
            if (hit) { stopped = true; return stop; }
            exitPx = c15[i].Close;
        }
        return exitPx;
    }

    private static void Print(string title, List<T> all, long eraSplit)
    {
        Console.WriteLine();
        Console.WriteLine(title);
        Console.WriteLine($"{"slice",-16} | {"n",6} {"exp",7} {"total",8} {"win%",6} {"maxDD",7}");
        Console.WriteLine(new string('-', 58));
        void Row(string name, List<T> rows)
        {
            if (rows.Count == 0) { Console.WriteLine($"{name,-16} | 0"); return; }
            var seq = rows.OrderBy(r => r.Ts).Select(r => r.NetR).ToList();
            double total = seq.Sum(), peak = 0, dd = 0, cum = 0;
            foreach (double x in seq) { cum += x; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
            Console.WriteLine($"{name,-16} | {seq.Count,6:N0} {total / seq.Count,7:F3} {total,8:F1} {100.0 * seq.Count(x => x > 0) / seq.Count,5:F1}% {dd,7:F1}");
        }
        Row("LEARN 2020-23", all.Where(t => t.Ts < eraSplit).ToList());
        Row("EXAM 2024-26", all.Where(t => t.Ts >= eraSplit).ToList());
        Console.WriteLine("  by year:");
        foreach (var g in all.GroupBy(t => t.Year).OrderBy(g => g.Key))
            Console.WriteLine($"    {g.Key}  n {g.Count(),4:N0}  {g.Sum(t => t.NetR),7:F1}R");
    }
}
