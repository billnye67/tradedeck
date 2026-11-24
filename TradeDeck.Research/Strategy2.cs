namespace TradeDeck.Research;

/// <summary>
/// STRATEGY #2 TRIAL — compression breakout trend continuation.
/// Mechanism: a coiled market breaks its prior-24h extreme; the side
/// positioned inside the range is trapped and feeds the move.
/// Spec (frozen before running, GPT research pass):
///   CONTEXT  24h range = rolling 96 x 15m bars. Compression = current
///            completed 24h range below the MEDIAN of the prior 30 days
///            of 24h ranges (sampled hourly). No tuned constants.
///   TRIGGER  15m candle CLOSES beyond the prior-24h extreme while the
///            market was compressed at the previous bar.
///   ENTRY    first completed 5m INSIDE BAR after the trigger (within
///            12 x 5m); stop-entry beyond its extreme (fill within 12).
///   STOP     inside-bar opposite extreme; if that is closer than 25%
///            of the trigger candle's range, use the trigger candle's
///            opposite extreme. Predetermined, identical everywhere.
///   EXITS    tested separately, no cherry-pick:
///            X1 RANGE-RECLAIM  1h close back inside the broken range.
///            X2 TRAIL-24H      15m close through the trailing prior-24h
///                              opposite extreme.
///            both: hard 7-day time stop. No profit targets.
///   COSTS    taker both sides (stop entries): 0.045% x 2.
/// Run: dotnet run s2
/// </summary>
public static class Strategy2
{
    private const double TakerFee = 0.00045;
    private const int Bars24h15m = 96;
    private const int MedianDays = 30;
    private const int InsideBarWindow = 12;   // 5m bars to find the pause
    private const int FillWindow = 12;        // 5m bars to get filled
    private const int MaxHold5m = 2016;       // 7 days

    private sealed record T(string Market, long Ts, double NetR, double Risk, bool Long, int Year,
        double Entry, double Stop);

    public static bool Audit;

    public static void Run(string source, string[] markets)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        var x1 = new List<T>();
        var x2 = new List<T>();

        foreach (string market in markets)
        {
            var c15 = reader.GetAllCandles(source, market, "15m");
            var c5 = reader.GetAllCandles(source, market, "5m");
            if (c15.Count < Bars24h15m * (MedianDays + 2) || c5.Count == 0) { Console.WriteLine($"{market}: skipped (thin)"); continue; }

            // index 5m by timestamp for trigger->entry mapping
            var idx5 = new Dictionary<long, int>();
            for (int i = 0; i < c5.Count; i++) idx5[c5[i].Timestamp] = i;

            // rolling 24h high/low/range on 15m (window ENDS at bar i inclusive)
            int n = c15.Count;
            var hi24 = new double[n]; var lo24 = new double[n]; var rng24 = new double[n];
            var dqHi = new LinkedList<int>(); var dqLo = new LinkedList<int>();
            for (int i = 0; i < n; i++)
            {
                while (dqHi.Count > 0 && c15[dqHi.Last!.Value].High <= c15[i].High) dqHi.RemoveLast();
                dqHi.AddLast(i);
                while (dqHi.First!.Value <= i - Bars24h15m) dqHi.RemoveFirst();
                while (dqLo.Count > 0 && c15[dqLo.Last!.Value].Low >= c15[i].Low) dqLo.RemoveLast();
                dqLo.AddLast(i);
                while (dqLo.First!.Value <= i - Bars24h15m) dqLo.RemoveFirst();
                hi24[i] = c15[dqHi.First!.Value].High;
                lo24[i] = c15[dqLo.First!.Value].Low;
                rng24[i] = hi24[i] - lo24[i];
            }

            int start = Bars24h15m * (MedianDays + 1);
            int trades = 0;

            for (int i = start; i < n - 1; i++)
            {
                // compression measured at the PREVIOUS completed bar
                // median of 24h ranges sampled hourly over prior 30 days
                var sample = new List<double>(MedianDays * 24);
                for (int k = i - 1 - Bars24h15m * MedianDays; k < i - 1; k += 4)
                    if (k >= 0) sample.Add(rng24[k]);
                sample.Sort();
                double med = sample[sample.Count / 2];
                bool compressed = rng24[i - 1] < med;
                if (!compressed) continue;

                double dHigh = hi24[i - 1];
                double dLow = lo24[i - 1];

                bool longBreak = c15[i].Close > dHigh && c15[i].Open <= dHigh;
                bool shortBreak = c15[i].Close < dLow && c15[i].Open >= dLow;
                if (!longBreak && !shortBreak) continue;

                bool isLong = longBreak;
                double trigRange = c15[i].High - c15[i].Low;
                if (trigRange <= 0) continue;

                if (!idx5.TryGetValue(c15[i].Timestamp, out int t5)) continue;
                int after = t5 + 3; // first 5m bar AFTER the trigger 15m completed

                // find first completed inside bar
                int ib = -1;
                for (int j = after + 1; j < Math.Min(after + 1 + InsideBarWindow, c5.Count); j++)
                {
                    if (c5[j].High < c5[j - 1].High && c5[j].Low > c5[j - 1].Low) { ib = j; break; }
                }
                if (ib < 0) continue;

                double entryPx = isLong ? c5[ib].High : c5[ib].Low;
                double stopPx = isLong ? c5[ib].Low : c5[ib].High;

                if (Math.Abs(entryPx - stopPx) < 0.25 * trigRange)
                    stopPx = isLong ? c15[i].Low : c15[i].High;

                double risk = Math.Abs(entryPx - stopPx);
                if (risk <= 0) continue;

                // fill?
                int fill = -1;
                for (int j = ib + 1; j < Math.Min(ib + 1 + FillWindow, c5.Count); j++)
                {
                    bool hit = isLong ? c5[j].High > entryPx : c5[j].Low < entryPx;
                    if (hit) { fill = j; break; }
                    // invalidated before fill?
                    bool inval = isLong ? c5[j].Low < stopPx : c5[j].High > stopPx;
                    if (inval) break;
                }
                if (fill < 0) continue;

                trades++;
                int year = DateTimeOffset.FromUnixTimeMilliseconds(c5[fill].Timestamp).Year;

                x1.Add(WalkTrade(market, c5, c15, idx15Of(c15, c5[fill].Timestamp), fill, isLong, entryPx, stopPx, risk,
                    dHigh, dLow, hi24, lo24, year, exitReclaim: true));
                x2.Add(WalkTrade(market, c5, c15, idx15Of(c15, c5[fill].Timestamp), fill, isLong, entryPx, stopPx, risk,
                    dHigh, dLow, hi24, lo24, year, exitReclaim: false));

                i += 4; // skip past this trigger region to avoid immediate duplicates
            }

            Console.WriteLine($"{market}: {trades:N0} triggers filled");
        }

        if (Audit)
        {
            Console.WriteLine();
            Console.WriteLine("SPOT AUDIT — verify these on the chart (X1 exit model, exam era):");
            var rnd = new Random(7);
            var pool = x1.Where(t => t.Ts >= eraSplit).OrderBy(_ => rnd.Next()).Take(5);
            foreach (var t in pool)
                Console.WriteLine(
                    $"  {t.Market,-9} {(t.Long ? "LONG " : "SHORT")} " +
                    $"{DateTimeOffset.FromUnixTimeMilliseconds(t.Ts).UtcDateTime:yyyy-MM-dd HH:mm} UTC " +
                    $"entry {t.Entry:G6} stop {t.Stop:G6} -> net {t.NetR:+0.00;-0.00}R");
        }

        Print("EXIT X1 — range reclaim (1h close back inside)", x1, eraSplit);
        Print("EXIT X2 — trail the prior-24h extreme", x2, eraSplit);

        Console.WriteLine();
        Console.WriteLine("read: this is a trend engine — expect low win%, few big winners paying for many scratches.");
        Console.WriteLine("law only if NET positive in BOTH eras, breadth across coins and years, for at least one exit.");
    }

    private static int idx15Of(IReadOnlyList<ResearchCandle> c15, long ts5)
    {
        long ts = ts5 - (ts5 % 900_000);
        int lo = 0, hi = c15.Count - 1, ans = 0;
        while (lo <= hi) { int mid = (lo + hi) / 2; if (c15[mid].Timestamp <= ts) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
        return ans;
    }

    private static T WalkTrade(
        string market,
        IReadOnlyList<ResearchCandle> c5, IReadOnlyList<ResearchCandle> c15, int i15,
        int fill, bool isLong, double entry, double stop, double risk,
        double dHigh, double dLow, double[] hi24, double[] lo24, int year, bool exitReclaim)
    {
        int limit = Math.Min(fill + MaxHold5m, c5.Count - 1);
        double exitPx = c5[limit].Close;

        for (int j = fill; j <= limit; j++)
        {
            // stop first, conservative
            bool stopped = isLong ? c5[j].Low <= stop : c5[j].High >= stop;
            if (stopped) { exitPx = stop; break; }

            // hourly / structural exits evaluated on completed 15m closes
            if (c5[j].Timestamp % 900_000 == 600_000) // last 5m of a 15m bar
            {
                int k = i15;
                while (k + 1 < c15.Count && c15[k + 1].Timestamp <= c5[j].Timestamp - 600_000) k++;
                i15 = k;

                if (exitReclaim)
                {
                    // 1h close back inside the broken range
                    bool hourEnd = c15[k].Timestamp % 3_600_000 == 2_700_000;
                    if (hourEnd)
                    {
                        bool back = isLong ? c15[k].Close < dHigh : c15[k].Close > dLow;
                        if (back) { exitPx = c15[k].Close; break; }
                    }
                }
                else
                {
                    // trail: 15m close through trailing prior-24h opposite extreme
                    if (k >= 1)
                    {
                        double trail = isLong ? lo24[k - 1] : hi24[k - 1];
                        bool broke = isLong ? c15[k].Close < trail : c15[k].Close > trail;
                        if (broke) { exitPx = c15[k].Close; break; }
                    }
                }
            }
        }

        double gross = isLong ? (exitPx - entry) / risk : (entry - exitPx) / risk;
        double fees = 2 * TakerFee * entry / risk;
        return new T(market, c5[fill].Timestamp, gross - fees, risk, isLong, year, entry, stop);
    }

    private static void Print(string title, List<T> all, long eraSplit)
    {
        Console.WriteLine();
        Console.WriteLine(title);
        Console.WriteLine($"{"slice",-18} | {"n",6} {"exp",7} {"total",8} {"win%",6} {"maxDD",7}");
        Console.WriteLine(new string('-', 60));

        void Row(string name, List<T> rows)
        {
            if (rows.Count == 0) { Console.WriteLine($"{name,-18} | {"0",6}"); return; }
            var seq = rows.OrderBy(r => r.Ts).Select(r => r.NetR).ToList();
            double total = seq.Sum(), peak = 0, dd = 0, cum = 0;
            foreach (double x in seq) { cum += x; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
            double win = 100.0 * seq.Count(x => x > 0) / seq.Count;
            Console.WriteLine($"{name,-18} | {seq.Count,6:N0} {total / seq.Count,7:F3} {total,8:F1} {win,5:F1}% {dd,7:F1}");
        }

        Row("LEARN 2020-23", all.Where(t => t.Ts < eraSplit).ToList());
        Row("EXAM 2024-26", all.Where(t => t.Ts >= eraSplit).ToList());

        Console.WriteLine("  by coin (total R, all years):");
        foreach (var g in all.GroupBy(t => t.Market).OrderByDescending(g => g.Sum(t => t.NetR)))
            Console.WriteLine($"    {g.Key,-9} n {g.Count(),5:N0}  {g.Sum(t => t.NetR),8:F1}R");

        Console.WriteLine("  by year (total R):");
        foreach (var g in all.GroupBy(t => t.Year).OrderBy(g => g.Key))
            Console.WriteLine($"    {g.Key}  n {g.Count(),5:N0}  {g.Sum(t => t.NetR),8:F1}R");
    }
}
