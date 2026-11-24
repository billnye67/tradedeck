namespace TradeDeck.Research;

using Microsoft.Data.Sqlite;

/// <summary>
/// FUNDING DISPERSION PORTFOLIO TRIAL (last candidate of hunt round 1).
/// Mechanism: perps transfer cash between sides every 8h. Crowded
/// longs PAY; shorting them collects. Negative-funding coins have
/// trapped shorts paying; longing them collects AND holds squeeze
/// fuel. Cross-sectional (relative) so the dead "naive carry level"
/// trade is not what's tested.
///
/// Frozen spec: daily at 00:00 UTC — rank coins by trailing 7-day
/// accrued funding. SHORT top 2, LONG bottom 2, middle flat. Legs
/// ATR-normalized (equal risk). PnL = price leg + ACTUAL funding
/// cash. Costs: taker x 2 whenever a coin's assignment changes.
/// Both eras, per year. Run: dotnet run fspread
/// </summary>
public static class FundingSpread
{
    private const double TakerFee = 0.00045;

    public static void Run(string dbPath, string source, string[] markets)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        using var db = new SqliteConnection($"Data Source={dbPath}");
        db.Open();

        // daily closes + ATR + daily accrued funding per market
        var closes = new Dictionary<string, Dictionary<long, double>>();
        var atrs = new Dictionary<string, Dictionary<long, double>>();
        var fund = new Dictionary<string, Dictionary<long, double>>();   // sum of rates per UTC day

        foreach (string m in markets)
        {
            var c15 = reader.GetAllCandles(source, m, "15m");
            if (c15.Count == 0) continue;

            var days = new List<(long day, double close, double high, double low)>();
            long cur = -1; double hi = 0, lo = 0, cl = 0;
            foreach (var c in c15)
            {
                long d = c.Timestamp / 86_400_000L;
                if (d != cur)
                {
                    if (cur >= 0) days.Add((cur, cl, hi, lo));
                    cur = d; hi = c.High; lo = c.Low;
                }
                hi = Math.Max(hi, c.High); lo = Math.Min(lo, c.Low); cl = c.Close;
            }
            if (cur >= 0) days.Add((cur, cl, hi, lo));

            var cd = new Dictionary<long, double>();
            var ad = new Dictionary<long, double>();
            double sum = 0; var trs = new double[days.Count];
            for (int i = 0; i < days.Count; i++)
            {
                cd[days[i].day] = days[i].close;
                if (i >= 1)
                {
                    double tr = Math.Max(days[i].high - days[i].low,
                        Math.Max(Math.Abs(days[i].high - days[i - 1].close),
                                 Math.Abs(days[i].low - days[i - 1].close)));
                    trs[i] = tr; sum += tr;
                    if (i > 14) sum -= trs[i - 14];
                    if (i >= 14) ad[days[i].day] = sum / 14;
                }
            }
            closes[m] = cd; atrs[m] = ad;

            var fd = new Dictionary<long, double>();
            using var cmd = db.CreateCommand();
            cmd.CommandText = "SELECT ts, rate FROM funding_rates WHERE source='binance_um' AND market=$m ORDER BY ts;";
            cmd.Parameters.AddWithValue("$m", m);
            using var r = cmd.ExecuteReader();
            while (r.Read())
            {
                long d = r.GetInt64(0) / 86_400_000L;
                fd.TryGetValue(d, out double s0);
                fd[d] = s0 + r.GetDouble(1);
            }
            fund[m] = fd;
            Console.WriteLine($"{m}: {cd.Count:N0} days, {fd.Count:N0} funding days");
        }

        // walk days
        long firstDay = closes.Values.SelectMany(d => d.Keys).Min();
        long lastDay = closes.Values.SelectMany(d => d.Keys).Max();

        var dayPnl = new List<(long ts, double r)>();
        var prevPos = new Dictionary<string, int>();

        for (long d = firstDay + 8; d < lastDay; d++)
        {
            // trailing 7d accrued funding, all coins present
            var ranks = new List<(string m, double f)>();
            foreach (string m in markets)
            {
                if (!closes.ContainsKey(m) || !closes[m].ContainsKey(d) || !closes[m].ContainsKey(d + 1)) continue;
                if (!atrs[m].TryGetValue(d, out double a) || a <= 0) continue;

                double f7 = 0; bool ok = true;
                for (long k = d - 7; k < d; k++)
                {
                    if (fund[m].TryGetValue(k, out double fk)) f7 += fk;
                    else { ok = false; break; }
                }
                if (ok) ranks.Add((m, f7));
            }
            if (ranks.Count < 6) { prevPos.Clear(); continue; }

            var sorted = ranks.OrderByDescending(x => x.f).ToList();
            var pos = new Dictionary<string, int>();
            pos[sorted[0].m] = -1; pos[sorted[1].m] = -1;      // crowded: short
            pos[sorted[^1].m] = 1; pos[sorted[^2].m] = 1;      // uncrowded: long

            double dayR = 0;

            foreach (var (m, p) in pos)
            {
                double a = atrs[m][d];
                double px = closes[m][d];
                double nxt = closes[m][d + 1];

                // price leg (per unit risk)
                dayR += p * (nxt - px) / a * 0.25;   // 4 legs, equal risk budget

                // funding cash on day d+1: positive funding = longs pay
                // shorts, so a position p receives -p x rate.
                if (fund[m].TryGetValue(d + 1, out double fpay))
                    dayR += -p * fpay * px / a * 0.25;
            }

            // costs on assignment changes
            foreach (var (m, p) in pos)
                if (!prevPos.TryGetValue(m, out int pp) || pp != p)
                    dayR -= 2 * TakerFee * closes[m][d] / atrs[m][d] * 0.25;
            foreach (var (m, pp) in prevPos)
                if (!pos.ContainsKey(m))
                    dayR -= 2 * TakerFee * closes[m][d] / atrs[m][d] * 0.25;

            prevPos = pos;
            dayPnl.Add((d * 86_400_000L, dayR));
        }

        Console.WriteLine();
        Console.WriteLine("FUNDING DISPERSION PORTFOLIO — short crowded 2 / long uncrowded 2 (daily)");
        Console.WriteLine($"{"slice",-16} | {"days",6} {"exp/day",8} {"total",8} {"maxDD",7}");
        Console.WriteLine(new string('-', 54));
        Slice("LEARN 2020-23", dayPnl.Where(x => x.ts < eraSplit).ToList());
        Slice("EXAM 2024-26", dayPnl.Where(x => x.ts >= eraSplit).ToList());

        Console.WriteLine("  by year:");
        foreach (var g in dayPnl.GroupBy(x => DateTimeOffset.FromUnixTimeMilliseconds(x.ts).Year).OrderBy(g => g.Key))
            Console.WriteLine($"    {g.Key}  {g.Sum(x => x.r),8:F1}R");

        Console.WriteLine();
        Console.WriteLine("bar: positive BOTH eras with year breadth. Warning honored: 6 coins is a thin basket.");
    }

    private static void Slice(string name, List<(long ts, double r)> rows)
    {
        if (rows.Count == 0) { Console.WriteLine($"{name,-16} | 0"); return; }
        var seq = rows.OrderBy(x => x.ts).Select(x => x.r).ToList();
        double total = seq.Sum(), peak = 0, dd = 0, cum = 0;
        foreach (double x in seq) { cum += x; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
        Console.WriteLine($"{name,-16} | {seq.Count,6:N0} {total / seq.Count,8:F4} {total,8:F1} {dd,7:F1}");
    }
}
