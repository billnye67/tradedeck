// Replays history through the LIVE C# forex brain (FxMidnightEngine) bar by bar, exactly as the bot would see it,
// and writes the trades it would take. Compared afterwards with tools/r13_midnight.py day_trade() on the same data.
using TradeDeck;
string dir = args[0];
foreach (var pair in new[] { "EURUSD", "USDJPY" })
{
    var five = File.ReadAllLines(Path.Combine(dir, pair + "_5m.csv")).Skip(1).Select(l => l.Split(','))
        .Select(c => (t: long.Parse(c[0]), o: double.Parse(c[1]), h: double.Parse(c[2]), l: double.Parse(c[3]), c: double.Parse(c[4]))).ToList();
    var tz = FxMidnightEngine.NyZone; double pip = FxMidnightEngine.Pip(pair);
    DateTime NyOf(long ms) => TimeZoneInfo.ConvertTimeFromUtc(DateTimeOffset.FromUnixTimeMilliseconds(ms).UtcDateTime, tz);
    var days = five.Select(x => NyOf(x.t).Date).Distinct().OrderBy(d => d).ToList();
    var outRows = new List<string> { "date,side,E,stop,q_hr,how" };
    foreach (var day in days.Skip(1))
    {
        if (day.DayOfWeek is DayOfWeek.Saturday or DayOfWeek.Sunday) continue;
        long dayEndMs = new DateTimeOffset(TimeZoneInfo.ConvertTimeToUtc(day.AddHours(17), tz)).ToUnixTimeMilliseconds();
        var all = FxMidnightEngine.To15m(five, day, dayEndMs + 15 * 60_000);          // the full day's 15m bars
        int i0 = all.FindIndex(b => b.Ny.Date == day); if (i0 < 0) continue;
        double mo = five.Where(x => NyOf(x.t) >= day).Select(x => x.o).FirstOrDefault(); if (mo == 0) continue;
        foreach (int side in new[] { 1, -1 })
        {
            for (int q = i0; q < all.Count; q++)
            {
                if (!FxMidnightEngine.InKillZone(all[q].Ny)) continue;
                var valid = FxMidnightEngine.Valid(all, q, mo, side, pip);                 // what the bot knows at the start of bar q
                var c = FxMidnightEngine.Pick(valid, all[q - 1].C, pip, out _);            // the resting order it would hold
                if (c == null) continue;
                bool touch = side == 1 ? all[q].L <= c.Trigger : all[q].H >= c.Trigger;
                if (!touch) continue;
                bool through = side == 1 ? all[q - 1].C <= c.Trigger : all[q - 1].C >= c.Trigger;
                double ictStop = side == 1 ? c.Trigger - (c.Stop - c.Trigger) : c.Trigger + (c.Trigger - c.Stop);
                outRows.Add($"{day:yyyy-MM-dd},{side},{c.Trigger:F6},{ictStop:F6},{all[q].Ny.Hour + all[q].Ny.Minute / 60.0:F2},{(through ? "market" : "stop")}");
                break;
            }
        }
    }
    File.WriteAllLines(Path.Combine(dir, pair + "_cs_trades.csv"), outRows);
    Console.WriteLine($"{pair}: {outRows.Count - 1} trades");
}
