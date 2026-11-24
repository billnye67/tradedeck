using Microsoft.Data.Sqlite;

namespace TradeDeck.Research;

/// <summary>
/// FIRST USE OF POSITIONING DATA — a feature test on Strategy #1.
/// Question: does a raid pay more when the crowd is leaning INTO
/// it (short a swept high while funding says the crowd is long =
/// trapped longs), less when the crowd is already on our side?
///
/// Feature: "aligned percentile" = where the latest funding print
/// (before the sweep) sits in this market's LEARN-era funding
/// distribution, oriented to the trade: for SHORT raids the raw
/// percentile (high = crowd long = trapped against us... for us);
/// for LONG raids 100 - percentile. Buckets fixed in advance.
/// No thresholds chosen; profile both eras. Run: dotnet run fslice
/// </summary>
public static class FundingSlice
{
    private const int WalkWindow = 480;
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private sealed record Row(long SweepTs, double NetR, double Aligned, string Market);

    public static void Run(string dbPath, string source, string[] markets, Func<FullSetup, bool> checklist)
    {
        var reader = new HistoryReader();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        using var db = new SqliteConnection($"Data Source={dbPath}");
        db.Open();

        var rows = new List<Row>();

        foreach (string market in markets)
        {
            var funding = LoadFunding(db, market);
            if (funding.Count < 500) continue;

            var learnRates = funding.Where(f => f.ts < eraSplit).Select(f => f.rate).OrderBy(x => x).ToList();
            if (learnRates.Count < 300) continue;

            var c15 = reader.GetAllCandles(source, market, "15m");
            var c5 = reader.GetAllCandles(source, market, "5m");
            if (c15.Count == 0 || c5.Count == 0) continue;

            foreach (var raid in SweepDetector.FindCandidates(market, c15))
            {
                FullSetup s = SetupDetector.Build(raid, c5);
                if (s.Stage != SetupStage.Complete || s.RiskPerUnit <= 0) continue;
                if (!checklist(s)) continue;

                // latest funding print strictly before the sweep (no lookahead)
                int idx = LastIndexBefore(funding, raid.SweepTimestamp);
                if (idx < 0) continue;

                double rate = funding[idx].rate;
                int below = LowerBound(learnRates, rate);
                double pct = 100.0 * below / learnRates.Count;

                bool isShort = raid.Direction == "HIGH";
                double aligned = isShort ? pct : 100.0 - pct;

                rows.Add(new Row(raid.SweepTimestamp, Walk(s, c5), aligned, market));
            }

            Console.WriteLine($"{market}: {rows.Count(r => r.Market == market):N0} trades tagged");
        }

        var learn = rows.Where(r => r.SweepTs < eraSplit).ToList();
        var exam = rows.Where(r => r.SweepTs >= eraSplit).ToList();

        Console.WriteLine();
        Console.WriteLine("FUNDING vs STRATEGY #1 — crowd leaning INTO the raid or AGAINST it?");
        Console.WriteLine("aligned = how crowded the side we are fading is (100 = max trapped against us)");
        Console.WriteLine($"baseline: learn {Exp(learn):F3}R (n {learn.Count:N0}) | exam {Exp(exam):F3}R (n {exam.Count:N0})");
        Console.WriteLine();
        Console.WriteLine($"{"bucket",-22} | {"LEARN n",7} {"exp",7} {"win%",6} | {"EXAM n",7} {"exp",7} {"win%",6}");
        Console.WriteLine(new string('-', 72));

        var buckets = new (string name, double lo, double hi)[]
        {
            ("crowd WITH us  <10",  0, 10),
            ("lean with us  10-30", 10, 30),
            ("neutral       30-70", 30, 70),
            ("lean against  70-90", 70, 90),
            ("TRAPPED against >90", 90, 101),
        };

        foreach (var (name, lo, hi) in buckets)
        {
            var l = learn.Where(r => r.Aligned >= lo && r.Aligned < hi).ToList();
            var e = exam.Where(r => r.Aligned >= lo && r.Aligned < hi).ToList();
            Console.WriteLine($"{name,-22} | {l.Count,7:N0} {Exp(l),7:F3} {Win(l),5:F1}% | {e.Count,7:N0} {Exp(e),7:F3} {Win(e),5:F1}%");
        }

        Console.WriteLine();
        Console.WriteLine("read: if TRAPPED-against beats baseline in BOTH eras and WITH-us lags in both,");
        Console.WriteLine("positioning is a real edge multiplier -> next: sizing/filter exam. Otherwise: noise.");

        // ---- CONVICTION SIZING EXAM (frozen: 1.5x risk when aligned >= 90) ----
        Console.WriteLine();
        Console.WriteLine("CONVICTION SIZING — flat 1x vs 1.5x on TRAPPED (>=90). Units = base-risk R.");
        Console.WriteLine($"{"model",-18} | {"LEARN total",11} {"PF",5} {"maxDD",7} | {"EXAM total",10} {"PF",5} {"maxDD",7}");
        Console.WriteLine(new string('-', 72));
        Sizing("FLAT 1x", learn, exam, _ => 1.0);
        Sizing("CONVICTION 1.5x", learn, exam, r => r.Aligned >= 90 ? 1.5 : 1.0);
        Console.WriteLine();
        Console.WriteLine("adopt only if EXAM total rises WITHOUT maxDD rising more than proportionally.");
    }

    private static void Sizing(string name, List<Row> learn, List<Row> exam, Func<Row, double> w)
    {
        string S(List<Row> rows)
        {
            var seq = rows.OrderBy(r => r.SweepTs).Select(r => r.NetR * w(r)).ToList();
            double total = seq.Sum();
            double gw = seq.Where(x => x > 0).Sum(), gl = Math.Abs(seq.Where(x => x < 0).Sum());
            double pf = gl > 0 ? gw / gl : 0, peak = 0, dd = 0, cum = 0;
            foreach (double x in seq) { cum += x; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
            return $"{total,11:F1} {pf,5:F2} {dd,7:F1}";
        }
        Console.WriteLine($"{name,-18} | {S(learn)} | {S(exam)}");
    }

    private static double Exp(List<Row> r) => r.Count > 0 ? r.Average(x => x.NetR) : 0;
    private static double Win(List<Row> r) => r.Count > 0 ? 100.0 * r.Count(x => x.NetR > 0) / r.Count : 0;

    private static List<(long ts, double rate)> LoadFunding(SqliteConnection db, string market)
    {
        var list = new List<(long, double)>();
        using var cmd = db.CreateCommand();
        cmd.CommandText = "SELECT ts, rate FROM funding_rates WHERE source='binance_um' AND market=$m ORDER BY ts;";
        cmd.Parameters.AddWithValue("$m", market);
        using var r = cmd.ExecuteReader();
        while (r.Read()) list.Add((r.GetInt64(0), r.GetDouble(1)));
        return list;
    }

    private static int LastIndexBefore(List<(long ts, double rate)> f, long ts)
    {
        int lo = 0, hi = f.Count - 1, ans = -1;
        while (lo <= hi) { int mid = (lo + hi) / 2; if (f[mid].ts < ts) { ans = mid; lo = mid + 1; } else hi = mid - 1; }
        return ans;
    }

    private static int LowerBound(List<double> sorted, double v)
    {
        int lo = 0, hi = sorted.Count;
        while (lo < hi) { int mid = (lo + hi) / 2; if (sorted[mid] < v) lo = mid + 1; else hi = mid; }
        return lo;
    }

    private static double Walk(FullSetup s, IReadOnlyList<ResearchCandle> c5)
    {
        bool isShort = s.Raid.Direction == "HIGH";
        double entry = s.EntryPrice, stop = s.StopLoss, risk = s.RiskPerUnit;
        double target = isShort ? entry - TargetR * risk : entry + TargetR * risk;
        int limit = Math.Min(s.EntryIndex5m + WalkWindow, c5.Count - 1);
        int last = s.EntryIndex5m;
        for (int i = s.EntryIndex5m; i <= limit; i++)
        {
            last = i;
            bool stopped = isShort ? c5[i].High >= stop : c5[i].Low <= stop;
            if (stopped) return -1.0 - (MakerFee + TakerFee) * entry / risk;
            bool hit = isShort ? c5[i].Low <= target : c5[i].High >= target;
            if (hit) return TargetR - (MakerFee + MakerFee) * entry / risk;
        }
        double exitR = isShort ? (entry - c5[last].Close) / risk : (c5[last].Close - entry) / risk;
        return exitR - (MakerFee + TakerFee) * entry / risk;
    }
}
