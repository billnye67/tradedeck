using Microsoft.Data.Sqlite;

namespace TradeDeck.Research;

/// <summary>
/// HUNT ROUND 1.5 — two untested mechanism families on the DEEP
/// archives (6y candles, 7y funding). Frozen before running; every
/// pre-declared variant reported; no picking winners after the fact.
///
///   mrev  CRASH-BOUNCE: when a coin prints one of its own worst
///         3-day bleeds (bottom decile of ITS history), buy the
///         wreckage; exit after H days. H in {3, 5, 10} — all shown.
///
///   fext  FUNDING-EXTREME FADE: when the crowd pays top-5% rent to
///         hold a side (per-coin percentile of 7y funding), take the
///         other side; exit when funding normalizes below p70 or at
///         10 days, whichever first.
///
/// Accounting for both: entries at next daily close after signal,
/// PnL in ATR14-normalized R, taker fees both sides, learn era
/// (pre-2024) vs exam era, per-year totals, worst losing stretch.
/// Run: dotnet run mrev   |   dotnet run fext
/// </summary>
public static class Reversion
{
    private const double TakerFee = 0.00045;

    private static string DbPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    private sealed record Day(long Ts, double Close, double High, double Low, double Atr);

    // ------------------------------------------------------------
    private static Dictionary<string, List<Day>> LoadDaily(string source, string[] markets)
    {
        var reader = new HistoryReader();
        var outp = new Dictionary<string, List<Day>>();

        foreach (string m in markets)
        {
            var c15 = reader.GetAllCandles(source, m, "15m");
            if (c15.Count == 0) continue;

            var days = new List<(long ts, double cl, double hi, double lo)>();
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

            // ATR14 on daily
            var list = new List<Day>();
            for (int i = 0; i < days.Count; i++)
            {
                double atr = 0;
                if (i >= 14)
                {
                    double s = 0;
                    for (int k = i - 13; k <= i; k++)
                    {
                        double tr = Math.Max(days[k].hi - days[k].lo,
                            Math.Max(Math.Abs(days[k].hi - days[k - 1].cl),
                                     Math.Abs(days[k].lo - days[k - 1].cl)));
                        s += tr;
                    }
                    atr = s / 14;
                }
                list.Add(new Day(days[i].ts, days[i].cl, days[i].hi, days[i].lo, atr));
            }
            outp[m] = list;
            Console.WriteLine($"{m}: {list.Count:N0} days");
        }
        return outp;
    }

    private sealed record Trade(string Market, long Ts, int Year, double R);

    private static void Report(string title, List<Trade> trades, long eraSplit)
    {
        Console.WriteLine();
        Console.WriteLine(title);
        Console.WriteLine($"{"slice",-8} | {"n",5} {"avg R",7} {"win%",6} {"total R",9} {"maxDD R",8}");
        Console.WriteLine(new string('-', 52));
        foreach (var (label, rows) in new[]
        {
            ("LEARN", trades.Where(t => t.Ts < eraSplit).ToList()),
            ("EXAM", trades.Where(t => t.Ts >= eraSplit).ToList())
        })
        {
            if (rows.Count == 0) { Console.WriteLine($"{label,-8} | 0"); continue; }
            var seq = rows.OrderBy(t => t.Ts).Select(t => t.R).ToList();
            double cum = 0, peak = 0, dd = 0;
            foreach (double r in seq) { cum += r; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
            Console.WriteLine($"{label,-8} | {seq.Count,5:N0} {seq.Average(),7:F3} " +
                $"{100.0 * seq.Count(x => x > 0) / seq.Count,5:F0}% {seq.Sum(),9:F1} {dd,8:F1}");
        }
        foreach (var g in trades.GroupBy(t => t.Year).OrderBy(g => g.Key))
            Console.WriteLine($"   {g.Key}  n {g.Count(),4:N0}  {g.Sum(t => t.R),8:F1}R");
    }

    // ------------------------------------------------------------
    // CRASH-BOUNCE
    // ------------------------------------------------------------
    public static void CrashBounce(string source, string[] markets)
    {
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();
        var daily = LoadDaily(source, markets);

        foreach (int hold in new[] { 3, 5, 10 })
        {
            var trades = new List<Trade>();

            foreach (var (m, days) in daily)
            {
                // trailing distribution of 3-day returns, expanding,
                // min 120 days of history before any signal
                var hist = new List<double>();

                for (int i = 3; i < days.Count - hold - 1; i++)
                {
                    if (days[i].Atr <= 0 || days[i - 3].Close <= 0) { continue; }

                    double r3 = days[i].Close / days[i - 3].Close - 1;
                    hist.Add(r3);
                    if (hist.Count < 120) continue;

                    // bottom decile of THIS coin's own history so far
                    var sorted = hist.OrderBy(x => x).ToList();
                    double p10 = sorted[(int)(sorted.Count * 0.10)];
                    if (r3 > p10) continue;

                    // enter long next close, exit close + hold days
                    double e = days[i + 1].Close;
                    double x = days[i + 1 + hold].Close;
                    double atr = days[i + 1].Atr;
                    if (e <= 0 || x <= 0 || atr <= 0) continue;

                    double gross = (x - e) / atr;
                    double fees = 2 * TakerFee * e / atr;
                    long ts = days[i + 1].Ts;
                    trades.Add(new Trade(m, ts,
                        DateTimeOffset.FromUnixTimeMilliseconds(ts).Year,
                        Math.Clamp(gross - fees, -15, 15)));

                    i += hold; // no overlapping trades per coin
                }
            }

            Report($"CRASH-BOUNCE — buy own-history bottom-decile 3d bleed, hold {hold}d (ATR-R)", trades, eraSplit);
        }

        Console.WriteLine();
        Console.WriteLine("law bar: positive avg BOTH eras with year breadth on at least one pre-declared hold. no picking.");
    }

    // ------------------------------------------------------------
    // FUNDING-EXTREME FADE
    // ------------------------------------------------------------
    public static void FundingFade(string source, string[] markets)
    {
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();
        var daily = LoadDaily(source, markets);

        // daily avg funding per market from the 7y archive
        var fund = new Dictionary<string, SortedDictionary<long, double>>();
        using (var db = new SqliteConnection($"Data Source={DbPath}"))
        {
            db.Open();
            foreach (var m in markets)
            {
                var s = new SortedDictionary<long, double>();
                using var cmd = db.CreateCommand();
                cmd.CommandText = $"SELECT ts/86400000*86400000 d, AVG(rate) FROM funding_rates WHERE market='{m}' GROUP BY d;";
                using var rd = cmd.ExecuteReader();
                while (rd.Read()) s[rd.GetInt64(0)] = rd.GetDouble(1);
                if (s.Count > 0) fund[m] = s;
            }
        }
        Console.WriteLine($"funding series: {fund.Count} markets");

        var trades = new List<Trade>();

        foreach (var (m, days) in daily)
        {
            if (!fund.TryGetValue(m, out var fs)) continue;
            var idx = days.Select((d, i) => (d.Ts, i)).ToDictionary(x => x.Ts, x => x.i);
            var hist = new List<double>();

            for (int i = 0; i < days.Count - 12; i++)
            {
                if (!fs.TryGetValue(days[i].Ts, out double f)) continue;
                hist.Add(f);
                if (hist.Count < 180) continue;

                var sorted = hist.OrderBy(x => x).ToList();
                double p95 = sorted[(int)(sorted.Count * 0.95)];
                double p05 = sorted[(int)(sorted.Count * 0.05)];
                double p70 = sorted[(int)(sorted.Count * 0.70)];
                double p30 = sorted[(int)(sorted.Count * 0.30)];

                int side; // fade the crowd paying the rent
                if (f >= p95) side = -1;      // longs crowded and paying -> short
                else if (f <= p05) side = +1; // shorts crowded and paying -> long
                else continue;

                if (days[i + 1].Atr <= 0) continue;
                double e = days[i + 1].Close;
                double atr = days[i + 1].Atr;

                // exit: funding normalized (inside p30..p70) or 10 days
                int xIdx = Math.Min(i + 1 + 10, days.Count - 1);
                for (int k = i + 2; k <= Math.Min(i + 1 + 10, days.Count - 1); k++)
                {
                    if (fs.TryGetValue(days[k].Ts, out double fk) && fk < p70 && fk > p30)
                    { xIdx = k; break; }
                }

                double x = days[xIdx].Close;
                if (e <= 0 || x <= 0) continue;

                double gross = side * (x - e) / atr;
                double fees = 2 * TakerFee * e / atr;
                long ts = days[i + 1].Ts;
                trades.Add(new Trade(m, ts,
                    DateTimeOffset.FromUnixTimeMilliseconds(ts).Year,
                    Math.Clamp(gross - fees, -15, 15)));

                i = xIdx; // no overlap per coin
            }
        }

        Report("FUNDING-EXTREME FADE — against the side paying own-history p95 rent, exit on normalize or 10d (ATR-R)", trades, eraSplit);
        Console.WriteLine();
        Console.WriteLine("law bar: positive avg BOTH eras with year breadth. one pre-declared spec, nothing tuned.");
    }
}
