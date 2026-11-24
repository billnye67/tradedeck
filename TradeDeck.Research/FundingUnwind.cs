using Microsoft.Data.Sqlite;

namespace TradeDeck.Research;

/// <summary>
/// STRATEGY CANDIDATE — CROWDED FUNDING UNWIND
/// First non-candle candidate: positioning data.
///
/// SHORT (LONG mirror at the p1 tail):
///  1. A funding print >= that market's LEARN-ERA p99 (per-market
///     threshold, frozen from pre-2024 data only — no invented
///     constants, the distribution IS the constant).
///  2. Price failure: 24h return (print vs 24h earlier) <= 0.
///     Crowd is max-long, paying premium, and NOT getting paid.
///  3. Entry: close of the first 15m candle after the print.
///  4. Stop: last 15m pivot high (strength 2) before entry;
///     reject if risk > 2.0 x ATR14(15m).
///  5. One entry per episode: consecutive extreme prints within
///     24h count as one episode (first print only).
///  6. Targets 1R/2R/3R. Taker entry, maker target, taker stop.
///
/// Mechanism: late levered longs crowd in at a premium; when
/// price stops rewarding them the carry bleeds them out and the
/// unwind is forced. We're fading a crowd that is provably
/// paying to lose.
///
/// Run: dotnet run unwind
/// </summary>
public static class FundingUnwind
{
    private const int PivotStrength = 2;
    private const int AtrLen = 14;
    private const double MaxRiskAtrMult = 2.0;
    private const long Interval8h = 8L * 3600 * 1000;
    private const long Day = 24L * 3600 * 1000;
    private const int WalkWindow5 = 480;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private static readonly double[] Targets = { 1.0, 2.0, 3.0 };

    private static readonly string[] Markets =
    {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
        "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT"
    };

    private sealed record FRow(long Ts, double Rate);

    private sealed record UwSetup(
        string Market,
        bool IsLong,
        long PrintTs,
        int Entry5m,
        double Entry,
        double Stop,
        double Risk
    );

    public static void Run(string dbPath, string source)
    {
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        var reader = new HistoryReader();

        using var db = new SqliteConnection($"Data Source={dbPath}");
        db.Open();

        Console.WriteLine();
        Console.WriteLine("CANDIDATE — CROWDED FUNDING UNWIND (positioning data)");
        Console.WriteLine("(thresholds = per-market learn-era p99/p1; frozen)");

        foreach (double t in Targets)
        {
            double totLearn = 0, totExam = 0;
            int nLearn = 0, nExam = 0;

            Console.WriteLine();
            Console.WriteLine($"TARGET {t:F0}R");
            Console.WriteLine(
                $"{"market",-9} | {"n",4} | {"LEARN exp",-10} | {"EXAM exp",-10}");
            Console.WriteLine(new string('-', 44));

            foreach (string market in Markets)
            {
                var funding = LoadFunding(db, market);
                if (funding.Count < 800) continue;

                var c15 = reader.GetAllCandles(source, market, "15m");
                var c5 = reader.GetAllCandles(source, market, "5m");

                if (c15.Count == 0 || c5.Count == 0) continue;

                // learn-era thresholds
                var learnRates = funding
                    .Where(f => f.Ts < eraSplit)
                    .Select(f => f.Rate)
                    .OrderBy(x => x)
                    .ToList();

                if (learnRates.Count < 500) continue;

                double P(double q) => learnRates[
                    (int)Math.Clamp(q * (learnRates.Count - 1),
                        0, learnRates.Count - 1)];

                double hi = P(0.99);
                double lo = P(0.01);

                var learn = new List<double>();
                var exam = new List<double>();

                foreach (UwSetup s in Detect(
                    market, funding, hi, lo, c15, c5))
                {
                    double r = Walk(s, c5, t);

                    if (s.PrintTs < eraSplit) learn.Add(r);
                    else exam.Add(r);
                }

                double le = learn.Count > 0 ? learn.Average() : 0;
                double ee = exam.Count > 0 ? exam.Average() : 0;

                totLearn += learn.Sum(); nLearn += learn.Count;
                totExam += exam.Sum(); nExam += exam.Count;

                Console.WriteLine(
                    $"{market,-9} | {learn.Count + exam.Count,4:N0} | {le,8:F3}R  | {ee,8:F3}R");
            }

            Console.WriteLine(new string('-', 44));
            Console.WriteLine(
                $"{"ALL",-9} | {nLearn + nExam,4:N0} | " +
                $"{(nLearn > 0 ? totLearn / nLearn : 0),8:F3}R  | " +
                $"{(nExam > 0 ? totExam / nExam : 0),8:F3}R");
        }

        Console.WriteLine();
        Console.WriteLine("PASS BAR: positive after fees BOTH eras, broad, real n.");
    }

    private static List<UwSetup> Detect(
        string market,
        List<FRow> funding,
        double hiThresh,
        double loThresh,
        IReadOnlyList<ResearchCandle> c15,
        IReadOnlyList<ResearchCandle> c5)
    {
        var setups = new List<UwSetup>();

        double[] atr = Atr(c15, AtrLen);

        long lastEpisodeTs = -1; // -1 = no prior episode

        for (int i = 0; i < funding.Count; i++)
        {
            FRow f = funding[i];

            bool crowdLong = f.Rate >= hiThresh;
            bool crowdShort = f.Rate <= loThresh;

            if (!crowdLong && !crowdShort) continue;

            // one entry per episode (24h). (v1 bug: long.MinValue
            // sentinel overflowed this subtraction and skipped every
            // print — the zero-trade run.)
            if (lastEpisodeTs > 0 && f.Ts - lastEpisodeTs < Day) continue;

            // price failure: 24h return vs crowd direction
            int pNow = LastIndexAtOrBefore(c15, f.Ts);
            int pThen = LastIndexAtOrBefore(c15, f.Ts - Day);

            if (pNow < AtrLen + PivotStrength + 2 || pThen < 0) continue;

            double ret24 =
                (c15[pNow].Close - c15[pThen].Close) / c15[pThen].Close;

            bool failing = crowdLong ? ret24 <= 0 : ret24 >= 0;
            if (!failing) continue;

            lastEpisodeTs = f.Ts;

            bool isLong = crowdShort; // fade the crowd

            // entry: close of first COMPLETED 15m after the print
            int e15 = FirstIndexAtOrAfter(c15, f.Ts);
            if (e15 < 0 || e15 + 1 >= c15.Count) continue;

            double entry = c15[e15].Close;
            long entryTs = c15[e15].Timestamp + 15 * 60 * 1000;

            int e5 = FirstIndexAtOrAfter(c5, entryTs);
            if (e5 < 0) continue;

            // stop: last opposing 15m pivot before entry
            double? stop = LastPivot(c15, e15, isLong);
            if (stop == null) continue;

            double risk = Math.Abs(entry - stop.Value);
            double a = atr[e15];

            if (risk <= 0 || a <= 0) continue;
            if (risk > MaxRiskAtrMult * a) continue;

            setups.Add(new UwSetup(
                market, isLong, f.Ts, e5, entry, stop.Value, risk));
        }

        return setups;
    }

    private static double Walk(
        UwSetup s,
        IReadOnlyList<ResearchCandle> c5,
        double targetR)
    {
        double target = s.IsLong
            ? s.Entry + targetR * s.Risk
            : s.Entry - targetR * s.Risk;

        int limit = Math.Min(s.Entry5m + WalkWindow5, c5.Count - 1);
        int last = s.Entry5m;

        for (int i = s.Entry5m; i <= limit; i++)
        {
            last = i;

            bool stopped = s.IsLong
                ? c5[i].Low <= s.Stop
                : c5[i].High >= s.Stop;

            if (stopped)
                return -1.0 - (TakerFee + TakerFee) * s.Entry / s.Risk;

            bool hit = s.IsLong
                ? c5[i].High >= target
                : c5[i].Low <= target;

            if (hit)
                return targetR - (TakerFee + MakerFee) * s.Entry / s.Risk;
        }

        double exitR = s.IsLong
            ? (c5[last].Close - s.Entry) / s.Risk
            : (s.Entry - c5[last].Close) / s.Risk;

        return exitR - (TakerFee + TakerFee) * s.Entry / s.Risk;
    }

    // ---------------- helpers ----------------

    private static List<FRow> LoadFunding(
        SqliteConnection db,
        string market)
    {
        var rows = new List<FRow>();

        using var cmd = db.CreateCommand();
        cmd.CommandText = """
            SELECT ts, rate FROM funding_rates
            WHERE source = 'binance_um' AND market = $m
            ORDER BY ts;
            """;
        cmd.Parameters.AddWithValue("$m", market);

        using var r = cmd.ExecuteReader();

        while (r.Read())
            rows.Add(new FRow(r.GetInt64(0), r.GetDouble(1)));

        return rows;
    }

    private static double? LastPivot(
        IReadOnlyList<ResearchCandle> c,
        int before,
        bool isLong)
    {
        for (int i = before - 1 - PivotStrength; i >= PivotStrength; i--)
        {
            bool pivot = true;

            for (int k = 1; k <= PivotStrength; k++)
            {
                if (isLong)
                {
                    if (c[i - k].Low <= c[i].Low ||
                        c[i + k].Low <= c[i].Low)
                    { pivot = false; break; }
                }
                else
                {
                    if (c[i - k].High >= c[i].High ||
                        c[i + k].High >= c[i].High)
                    { pivot = false; break; }
                }
            }

            if (pivot)
                return isLong ? c[i].Low : c[i].High;
        }

        return null;
    }

    private static double[] Atr(
        IReadOnlyList<ResearchCandle> c,
        int len)
    {
        var atr = new double[c.Count];
        var trs = new double[c.Count];
        double sum = 0;

        for (int i = 1; i < c.Count; i++)
        {
            double tr = Math.Max(
                c[i].High - c[i].Low,
                Math.Max(
                    Math.Abs(c[i].High - c[i - 1].Close),
                    Math.Abs(c[i].Low - c[i - 1].Close)));

            trs[i] = tr;
            sum += tr;

            if (i > len) sum -= trs[i - len];
            if (i >= len) atr[i] = sum / len;
        }

        return atr;
    }

    private static int FirstIndexAtOrAfter(
        IReadOnlyList<ResearchCandle> c,
        long ts)
    {
        int lo = 0, hi = c.Count - 1, ans = -1;

        while (lo <= hi)
        {
            int mid = (lo + hi) / 2;

            if (c[mid].Timestamp >= ts) { ans = mid; hi = mid - 1; }
            else lo = mid + 1;
        }

        return ans;
    }

    private static int LastIndexAtOrBefore(
        IReadOnlyList<ResearchCandle> c,
        long ts)
    {
        int lo = 0, hi = c.Count - 1, ans = -1;

        while (lo <= hi)
        {
            int mid = (lo + hi) / 2;

            if (c[mid].Timestamp <= ts) { ans = mid; lo = mid + 1; }
            else hi = mid - 1;
        }

        return ans;
    }
}
