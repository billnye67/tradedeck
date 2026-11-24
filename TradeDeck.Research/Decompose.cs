namespace TradeDeck.Research;

/// <summary>X8: decompose Strategy #1 into raid-only / gap-only / both. Run: dotnet run decompose. Spec: specs/X8-decompose-strategy1.md</summary>
public static class Decompose
{
    private const int Walk = 480; private const double TargetR = 3.0;
    private static double Env(string k, double d) => double.TryParse(Environment.GetEnvironmentVariable(k), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : d;
    private static readonly double Maker = Env("TD_MAKER_BP", 1.5) / 1e4, Taker = Env("TD_TAKER_BP", 4.5) / 1e4, Slip = Env("TD_SLIP_BP", 0) / 1e4, Swap = Env("TD_SWAP_BP", 0) / 1e4;
    private sealed record T(string Cell, string Market, long Ts, double NetR);

    // generic replay: entry price E at bar i, stop, 3R target, 40h window; returns net R after costs
    private static double Replay(IReadOnlyList<ResearchCandle> c5, int i, bool isShort, double E, double stop, bool entryTaker)
    {
        double risk = isShort ? stop - E : E - stop; if (risk <= 0) return double.NaN;
        double target = isShort ? E - TargetR * risk : E + TargetR * risk; int limit = Math.Min(i + Walk, c5.Count - 1);
        double feeIn = entryTaker ? Taker : Maker;
        for (int k = i; k <= limit; k++)
        {
            bool stopped = isShort ? c5[k].High >= stop : c5[k].Low <= stop; bool hit = isShort ? c5[k].Low <= target : c5[k].High >= target;
            long nights = c5[k].Timestamp / 86_400_000L - c5[i].Timestamp / 86_400_000L;
            if (stopped) return -1.0 - (feeIn + Taker + 2 * Slip + nights * Swap) * E / risk;
            if (hit) return TargetR - (feeIn + Maker + 2 * Slip + nights * Swap) * E / risk;
        }
        long n2 = c5[limit].Timestamp / 86_400_000L - c5[i].Timestamp / 86_400_000L; double x = c5[limit].Close;
        return (isShort ? (E - x) : (x - E)) / risk - (feeIn + Taker + 2 * Slip + n2 * Swap) * E / risk;
    }

    private static int Reclaim(IReadOnlyList<ResearchCandle> c5, int extreme, double level, bool isShort)
    {
        for (int i = extreme; i < Math.Min(extreme + 24, c5.Count); i++) if (isShort ? c5[i].Close < level : c5[i].Close > level) return i;
        return -1;
    }

    public static void Run(string source)
    {
        var reader = new HistoryReader(); var all = new List<T>(); var markets = new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
        foreach (var mkt in markets)
        {
            var c15 = reader.GetAllCandles(source, mkt, "15m"); var c5 = reader.GetAllCandles(source, mkt, "5m"); if (c15.Count == 0) continue;
            var raids = SweepDetector.FindCandidates(mkt, c15); var raidBreach5 = new HashSet<int>();
            var setups = raids.Select(r => SetupDetector.Build(r, c5)).ToList();
            foreach (var s in setups)
            {
                bool isShort = s.Raid.Direction == "HIGH"; raidBreach5.Add(s.BreachIndex5m);
                if (s.Stage != SetupStage.Complete || s.RiskPerUnit <= 0) continue;
                double rB = Replay(c5, s.EntryIndex5m, isShort, s.EntryPrice, s.StopLoss, false);
                all.Add(new T("B0 both, no checklist", mkt, s.Raid.SweepTimestamp, rB));
                bool ck = s.FvgSizeRatio >= 0.35 && Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013;
                if (ck) all.Add(new T("B  both = #1 checklist", mkt, s.Raid.SweepTimestamp, rB));
                int rec = Reclaim(c5, s.ExtremeIndex5m, s.Raid.Level, isShort);
                if (ck && rec > 0) all.Add(new T("D  raid+gap, entry at reclaim", mkt, s.Raid.SweepTimestamp, Replay(c5, rec, isShort, c5[rec].Close, s.SweepExtreme, true)));
                // X8b cell E: enter at the open of the bar after the FVG is confirmed (mid+2), no retrace wait; knowable at that time
                int e2 = s.FvgMiddleIndex5m + 2;
                if (ck && e2 < c5.Count) all.Add(new T("E  raid+gap, entry after FVG confirms", mkt, s.Raid.SweepTimestamp, Replay(c5, e2, isShort, c5[e2].Open, s.SweepExtreme, true)));
            }
            foreach (var s in setups)
            {
                bool isShort = s.Raid.Direction == "HIGH"; if (s.Stage == SetupStage.BecameBreakout) continue;
                if (Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level < 0.0013) continue;
                int rec = Reclaim(c5, s.ExtremeIndex5m, s.Raid.Level, isShort); if (rec < 0) continue;
                all.Add(new T("A  raid only (reclaim close)", mkt, s.Raid.SweepTimestamp, Replay(c5, rec, isShort, c5[rec].Close, s.SweepExtreme, true)));
            }
            var breaches = raidBreach5.OrderBy(x => x).ToArray(); int lastEntry = -1000;
            for (int mid = 21; mid + 1 < c5.Count - Walk - 40; mid++)
            {
                double avg = 0; for (int k = 1; k <= 20; k++) avg += c5[mid - k].High - c5[mid - k].Low; avg /= 20; if (avg <= 0) continue;
                if ((c5[mid].High - c5[mid].Low) < 1.5 * avg) continue;
                bool bull = c5[mid + 1].Low > c5[mid - 1].High, bear = c5[mid + 1].High < c5[mid - 1].Low; if (!bull && !bear) continue;
                if (mid - lastEntry < 12) continue;
                int lo = Array.BinarySearch(breaches, mid - 60); if (lo < 0) lo = ~lo; if (lo < breaches.Length && breaches[lo] <= mid) continue;
                bool isShort = bear; double proximal = bull ? c5[mid + 1].Low : c5[mid + 1].High, distal = bull ? c5[mid - 1].High : c5[mid - 1].Low;
                double stop = bull ? c5[mid - 1].Low : c5[mid - 1].High; int entry = -1;
                for (int k = mid + 2; k <= Math.Min(mid + 1 + 36, c5.Count - 1); k++)
                {
                    bool invalid = isShort ? c5[k].High > stop : c5[k].Low < stop; bool touched = isShort ? c5[k].High >= proximal : c5[k].Low <= proximal; bool inverted = isShort ? c5[k].Close > distal : c5[k].Close < distal;
                    if (touched && !invalid) { if (!inverted) entry = k; break; }
                    if (invalid) break;
                }
                if (entry < 0) continue; lastEntry = entry;
                all.Add(new T("C  gap only (no raid)", mkt, c5[mid].Timestamp, Replay(c5, entry, isShort, proximal, stop, false)));
            }
            Console.Error.WriteLine($"  {mkt}: raids {raids.Count:N0}, cells built");
        }
        long split = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();
        Console.WriteLine("X8 DECOMPOSE #1 - BTC/ETH/XRP, strict costs, 3R target, 40h window");
        foreach (bool exam in new[] { false, true })
        {
            Console.WriteLine(); Console.WriteLine(exam ? "EXAM ERA 2024-2026" : "LEARN ERA 2020-2023");
            foreach (var g in all.Where(t => !double.IsNaN(t.NetR) && (t.Ts >= split) == exam).GroupBy(t => t.Cell).OrderBy(g => g.Key))
            {
                var rs = g.OrderBy(t => t.Ts).ToList(); double eq = 0, pk = 0, dd = 0; foreach (var t in rs) { eq += t.NetR; pk = Math.Max(pk, eq); dd = Math.Max(dd, pk - eq); }
                string per = string.Join(" ", markets.Select(m => { var x = rs.Where(t => t.Market == m).ToList(); return x.Count == 0 ? "" : $"{m.Substring(0, 3)} {x.Average(t => t.NetR):+0.000;-0.000}"; }));
                Console.WriteLine($"  {g.Key,-32} | trades {rs.Count,6:N0} | win {100.0 * rs.Count(t => t.NetR > 0) / rs.Count,5:F1}% | NET exp {rs.Average(t => t.NetR),7:F3}R | total {rs.Sum(t => t.NetR),8:F1}R | maxDD {dd,6:F1}R | {per}");
            }
        }
    }
}
