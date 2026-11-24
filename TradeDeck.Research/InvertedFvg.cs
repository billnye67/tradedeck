namespace TradeDeck.Research;

/// <summary>X9 inverted-FVG continuation. Run: dotnet run ifvg. Spec: specs/X9-inverted-fvg.md</summary>
public static class InvertedFvg
{
    private const int Walk = 480; private const double TargetR = 3.0;
    private static double Env(string k, double d) => double.TryParse(Environment.GetEnvironmentVariable(k), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var v) ? v : d;
    private static readonly double Maker = Env("TD_MAKER_BP", 1.5) / 1e4, Taker = Env("TD_TAKER_BP", 4.5) / 1e4, Slip = Env("TD_SLIP_BP", 0) / 1e4, Swap = Env("TD_SWAP_BP", 0) / 1e4;
    private sealed record Ev(string Market, long Ts, bool IsShort, double[] Fwd, double R1, double R2);

    private static double Replay(IReadOnlyList<ResearchCandle> c5, int i, bool isShort, double E, double stop)
    {
        double risk = isShort ? stop - E : E - stop; if (risk <= 0) return double.NaN;
        double target = isShort ? E - TargetR * risk : E + TargetR * risk; int limit = Math.Min(i + Walk, c5.Count - 1);
        for (int k = i; k <= limit; k++)
        {
            bool stopped = isShort ? c5[k].High >= stop : c5[k].Low <= stop; bool hit = isShort ? c5[k].Low <= target : c5[k].High >= target;
            long n = c5[k].Timestamp / 86_400_000L - c5[i].Timestamp / 86_400_000L;
            if (stopped) return -1.0 - (Maker + Taker + 2 * Slip + n * Swap) * E / risk;
            if (hit) return TargetR - (Maker + Maker + 2 * Slip + n * Swap) * E / risk;
        }
        long n2 = c5[limit].Timestamp / 86_400_000L - c5[i].Timestamp / 86_400_000L; double x = c5[limit].Close;
        return (isShort ? (E - x) : (x - E)) / risk - (Maker + Taker + 2 * Slip + n2 * Swap) * E / risk;
    }

    public static void Run(string source)
    {
        int[] HZ = { 4, 8, 24, 48, 96 }; var reader = new HistoryReader(); var evs = new List<Ev>(); var ctl = new Dictionary<string, (double[] sum, long n)>();
        var markets = new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
        foreach (var mkt in markets)
        {
            var c5 = reader.GetAllCandles(source, mkt, "5m"); if (c5.Count == 0) continue;
            double[] sum = new double[HZ.Length]; long cn = 0;
            for (int i = 100; i + 100 < c5.Count; i += 7) { for (int h = 0; h < HZ.Length; h++) sum[h] += Math.Abs(c5[i + HZ[h]].Close / c5[i].Close - 1) * 1e4; cn++; }
            ctl[mkt] = (sum, cn);
            int lastSig = -1000; int before = evs.Count;
            for (int mid = 21; mid + 1 < c5.Count - 200; mid++)
            {
                double avg = 0; for (int k = 1; k <= 20; k++) avg += c5[mid - k].High - c5[mid - k].Low; avg /= 20; if (avg <= 0) continue;
                if ((c5[mid].High - c5[mid].Low) < 1.5 * avg) continue;
                bool bull = c5[mid + 1].Low > c5[mid - 1].High, bear = c5[mid + 1].High < c5[mid - 1].Low; if (!bull && !bear) continue;
                double prox = bull ? c5[mid + 1].Low : c5[mid + 1].High, dist = bull ? c5[mid - 1].High : c5[mid - 1].Low;
                int react = -1;
                for (int k = mid + 2; k <= Math.Min(mid + 1 + 36, c5.Count - 2); k++)
                {
                    bool through = bull ? c5[k].Close < dist : c5[k].Close > dist; if (through) break;
                    bool touch = bull ? c5[k].Low <= prox : c5[k].High >= prox;
                    if (touch && (bull ? c5[k + 1].Close > prox : c5[k + 1].Close < prox)) { react = k + 1; break; }
                }
                if (react < 0) continue;
                int sig = -1;
                for (int k = react + 1; k <= Math.Min(react + 72, c5.Count - 100); k++) { if (bull ? c5[k].Close < dist : c5[k].Close > dist) { sig = k; break; } }
                if (sig < 0 || sig - lastSig < 12) continue; lastSig = sig;
                bool isShort = bull; double s = isShort ? -1 : 1; var fwd = HZ.Select(h => s * (c5[sig + h].Close / c5[sig].Close - 1) * 1e4).ToArray();
                int entry = -1;
                for (int k = sig + 1; k <= Math.Min(sig + 36, c5.Count - 1); k++)
                {
                    bool voided = isShort ? c5[k].Close > prox : c5[k].Close < prox; if (voided) break;
                    bool touch = isShort ? c5[k].High >= dist : c5[k].Low <= dist; if (touch) { entry = k; break; }
                }
                double r1 = double.NaN, r2 = double.NaN;
                if (entry > 0) { double w = Math.Abs(prox - dist); r1 = Replay(c5, entry, isShort, dist, prox); r2 = Replay(c5, entry, isShort, dist, isShort ? dist + 1.5 * w : dist - 1.5 * w); }
                evs.Add(new Ev(mkt, c5[sig].Timestamp, isShort, fwd, r1, r2));
            }
            Console.Error.WriteLine($"  {mkt}: inversions {evs.Count - before:N0}");
        }
        long split = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();
        Console.WriteLine("X9 INVERTED FVG - BTC/ETH/XRP 5m, strict costs");
        foreach (bool exam in new[] { false, true })
        {
            var g = evs.Where(e => (e.Ts >= split) == exam).ToList(); Console.WriteLine(); Console.WriteLine((exam ? "EXAM 2024-2026" : "LEARN 2020-2023") + $"  inversions {g.Count:N0} (short {g.Count(e => e.IsShort):N0} / long {g.Count(e => !e.IsShort):N0})");
            if (g.Count == 0) continue;
            Console.WriteLine("  RAW signed move bp:  " + string.Join(" | ", HZ.Select((h, i) => $"{h,3}b {g.Average(e => e.Fwd[i]),6:F0}")));
            Console.WriteLine("  control |move| bp:   " + string.Join(" | ", HZ.Select((h, i) => $"{h,3}b {ctl.Values.Sum(v => v.sum[i]) / ctl.Values.Sum(v => v.n),6:F0}")));
            foreach (var m in markets) { var gm = g.Where(e => e.Market == m).ToList(); if (gm.Count > 0) Console.WriteLine($"  {m.Substring(0, 3)} n={gm.Count,5}: " + string.Join(" | ", HZ.Select((h, i) => $"{h,3}b {gm.Average(e => e.Fwd[i]),6:F0}"))); }
            foreach (var (lbl, sel) in new (string, Func<Ev, double>)[] { ("stop = old near edge", e => e.R1), ("stop = 1.5x gap width", e => e.R2) })
            {
                var t = g.Where(e => !double.IsNaN(sel(e))).OrderBy(e => e.Ts).Select(e => (e.Market, R: sel(e))).ToList(); if (t.Count == 0) continue;
                double eq = 0, pk = 0, dd = 0; foreach (var x in t) { eq += x.R; pk = Math.Max(pk, eq); dd = Math.Max(dd, pk - eq); }
                Console.WriteLine($"  TRADE {lbl,-22} | trades {t.Count,5:N0} | win {100.0 * t.Count(x => x.R > 0) / t.Count,5:F1}% | NET exp {t.Average(x => x.R),7:F3}R | total {t.Sum(x => x.R),7:F1}R | maxDD {dd,6:F1}R | " + string.Join(" ", t.GroupBy(x => x.Market).Select(k => $"{k.Key.Substring(0, 3)} {k.Average(x => x.R):+0.000;-0.000}")));
            }
        }
    }
}
