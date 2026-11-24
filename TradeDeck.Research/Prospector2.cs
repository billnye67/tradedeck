using Microsoft.Data.Sqlite;

namespace TradeDeck.Research;

/// <summary>
/// PROSPECTOR ROUND 2 — the private-tape scan.  dotnet run prospect2
///
/// Round 1 hunted published patterns (mostly dead, as published
/// patterns are). Round 2 hunts OUR OWN recorded tape — order flow,
/// book depth, venue prices — data nobody else has in this form.
///
/// Four pre-declared questions, zero tuning:
///   P1  does heavy real BUYING (flow imbalance) predict the next 15/60m?
///   P2  do THIN-book moments precede bigger moves (vol expansion)?
///   P3  does a lopsided book (bids >> asks waiting) push price its way?
///   P4  does Hyperliquid lead Binance's perp, or follow it?
///
/// Honesty rails: the tape is only ~9 days old, so this is a LEAD
/// scan, not a law test — results are reported per half (first vs
/// second half of the tape) and a lead only counts if both halves
/// agree in sign. Anything that survives graduates to a frozen-spec
/// trial once the tape is months deep.
/// </summary>
public static class Prospector2
{
    private static string DbPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    private const long Step = 300_000; // 5-minute grid

    public static void Run()
    {
        using var db = new SqliteConnection($"Data Source={DbPath}");
        db.Open();

        // markets with real flow coverage
        var markets = Query(db,
            "SELECT market, COUNT(*) c FROM agg_flow GROUP BY market HAVING c > 2000 ORDER BY c DESC;")
            .Select(r => (string)r[0]!).ToList();

        Console.WriteLine($"tape: {markets.Count} markets with flow coverage");

        // price source per market: the venue with the most snapshots
        var priceVenue = new Dictionary<string, string>();
        foreach (var m in markets)
        {
            var v = Query(db,
                $"SELECT venue, COUNT(*) c FROM venue_snap WHERE market='{m}' AND price > 0 GROUP BY venue ORDER BY c DESC LIMIT 1;")
                .FirstOrDefault();
            if (v != null) priceVenue[m] = (string)v[0]!;
        }

        // ---- load 5m series per market ----
        var px = new Dictionary<string, SortedDictionary<long, double>>();     // bucket -> price
        var flowShare = new Dictionary<string, SortedDictionary<long, double>>(); // buy share of volume
        var depth = new Dictionary<string, SortedDictionary<long, double>>();  // total depth
        var skew = new Dictionary<string, SortedDictionary<long, double>>();   // bid/(bid+ask)

        foreach (var m in markets)
        {
            var p = new SortedDictionary<long, double>();
            foreach (var r in Query(db,
                $"SELECT ts/{Step}*{Step} b, AVG(price) FROM venue_snap WHERE market='{m}' AND venue='{priceVenue[m]}' AND price>0 GROUP BY b;"))
                p[(long)r[0]!] = Convert.ToDouble(r[1]);
            px[m] = p;

            var f = new SortedDictionary<long, double>();
            foreach (var r in Query(db,
                $"SELECT ts/{Step}*{Step} b, SUM(buy_vol), SUM(sell_vol) FROM agg_flow WHERE market='{m}' GROUP BY b;"))
            {
                double b = Convert.ToDouble(r[1]), s = Convert.ToDouble(r[2]);
                if (b + s > 0) f[(long)r[0]!] = b / (b + s);
            }
            flowShare[m] = f;

            var d = new SortedDictionary<long, double>();
            var k = new SortedDictionary<long, double>();
            foreach (var r in Query(db,
                $"SELECT ts/{Step}*{Step} b, AVG(bid_depth25), AVG(ask_depth25) FROM book_snap WHERE market='{m}' GROUP BY b;"))
            {
                double bd = Convert.ToDouble(r[1]), ad = Convert.ToDouble(r[2]);
                if (bd + ad > 0)
                {
                    d[(long)r[0]!] = bd + ad;
                    k[(long)r[0]!] = bd / (bd + ad);
                }
            }
            depth[m] = d;
            skew[m] = k;
        }

        long tapeStart = px.Values.SelectMany(s => s.Keys).Min();
        long tapeEnd = px.Values.SelectMany(s => s.Keys).Max();
        long mid = (tapeStart + tapeEnd) / 2;
        Console.WriteLine($"tape span: {Ymd(tapeStart)} -> {Ymd(tapeEnd)} (half split at {Ymd(mid)})");
        Console.WriteLine();

        // forward returns in bp at +15m and +60m off the price grid
        double? Fwd(string m, long b, int steps)
        {
            if (!px[m].TryGetValue(b, out double p0) || p0 <= 0) return null;
            if (!px[m].TryGetValue(b + steps * Step, out double p1) || p1 <= 0) return null;
            return Math.Clamp((p1 / p0 - 1) * 10_000, -300, 300);
        }

        // percentile rank within each market's own history of the signal
        Dictionary<long, double> Ranks(SortedDictionary<long, double> series)
        {
            var vals = series.Values.OrderBy(x => x).ToList();
            var outp = new Dictionary<long, double>();
            foreach (var (b, v) in series)
            {
                int lo = vals.BinarySearch(v);
                if (lo < 0) lo = ~lo;
                outp[b] = 100.0 * lo / Math.Max(1, vals.Count - 1);
            }
            return outp;
        }

        void Report(string title, string loLabel, string hiLabel,
            Func<string, SortedDictionary<long, double>?> signal, bool absMove)
        {
            Console.WriteLine(title);
            Console.WriteLine($"{"bucket",-12} | {"HALF A 15m",10} {"HALF B 15m",10} | {"HALF A 60m",10} {"HALF B 60m",10} |  n");
            Console.WriteLine(new string('-', 74));

            // quintiles of the signal's own percentile rank
            var buckets = new[] { (0, 20, loLabel), (40, 60, "middle"), (80, 100, hiLabel) };

            foreach (var (qlo, qhi, label) in buckets)
            {
                var a15 = new List<double>(); var b15 = new List<double>();
                var a60 = new List<double>(); var b60 = new List<double>();

                foreach (var m in markets)
                {
                    var s = signal(m);
                    if (s == null || s.Count < 500) continue;
                    var rk = Ranks(s);

                    foreach (var (bkt, _) in s)
                    {
                        if (rk[bkt] < qlo || rk[bkt] > qhi) continue;
                        double? f15 = Fwd(m, bkt, 3), f60 = Fwd(m, bkt, 12);
                        if (f15 == null || f60 == null) continue;
                        double v15 = absMove ? Math.Abs(f15.Value) : f15.Value;
                        double v60 = absMove ? Math.Abs(f60.Value) : f60.Value;
                        if (bkt < mid) { a15.Add(v15); a60.Add(v60); }
                        else { b15.Add(v15); b60.Add(v60); }
                    }
                }

                Console.WriteLine($"{label,-12} | {Avg(a15),10:F1} {Avg(b15),10:F1} | {Avg(a60),10:F1} {Avg(b60),10:F1} | {a15.Count + b15.Count,6:N0}");
            }
            Console.WriteLine();
        }

        Console.WriteLine("P1 — FLOW IMBALANCE: when real buyers dominate the tape, what happens next? (signed bp)");
        Report("", "heavy sell", "heavy buy", m => flowShare.GetValueOrDefault(m), absMove: false);

        Console.WriteLine("P2 — BOOK THINNESS: do thin books precede bigger moves? (ABSOLUTE bp — vol expansion)");
        Report("", "thin book", "thick book", m => depth.GetValueOrDefault(m), absMove: true);

        Console.WriteLine("P3 — BOOK SKEW: bids piled deeper than asks — does price go where the wall is? (signed bp)");
        Report("", "ask-heavy", "bid-heavy", m => skew.GetValueOrDefault(m), absMove: false);

        // ---- P4 venue lead: HL vs the price venue ----
        Console.WriteLine("P4 — VENUE LEAD: when Hyperliquid trades rich vs Binance perp, does Binance follow up? (signed bp)");
        var gap = new Dictionary<string, SortedDictionary<long, double>>();
        foreach (var m in markets)
        {
            var hl = new SortedDictionary<long, double>();
            foreach (var r in Query(db,
                $"SELECT ts/{Step}*{Step} b, AVG(price) FROM venue_snap WHERE market='{m}' AND venue LIKE '%hyper%' AND price>0 GROUP BY b;"))
                hl[(long)r[0]!] = Convert.ToDouble(r[1]);

            if (hl.Count < 500) continue;
            var g = new SortedDictionary<long, double>();
            foreach (var (b, hp) in hl)
                if (px[m].TryGetValue(b, out double bp0) && bp0 > 0)
                    g[b] = (hp / bp0 - 1) * 10_000;
            if (g.Count >= 500) gap[m] = g;
        }
        if (gap.Count == 0)
            Console.WriteLine("  (no hyperliquid venue rows found — skipped)");
        else
            Report("", "HL cheap", "HL rich", m => gap.GetValueOrDefault(m), absMove: false);

        Console.WriteLine("read: a lead is real only if BOTH halves agree in sign and the size beats fees (~9bp round trip).");
        Console.WriteLine("survivors graduate to a frozen-spec trial when the tape is months deep. everything else stays a feature note.");
    }

    private static double Avg(List<double> xs) => xs.Count == 0 ? 0 : xs.Average();

    private static List<object?[]> Query(SqliteConnection db, string sql)
    {
        var rows = new List<object?[]>();
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        using var rd = cmd.ExecuteReader();
        while (rd.Read())
        {
            var row = new object?[rd.FieldCount];
            rd.GetValues(row!);
            rows.Add(row);
        }
        return rows;
    }

    private static string Ymd(long ts) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ts).UtcDateTime.ToString("MMM d HH:mm");
}
