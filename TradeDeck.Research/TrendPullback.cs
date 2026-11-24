namespace TradeDeck.Research;

/// <summary>
/// STRATEGY CANDIDATE — HTF TREND PULLBACK RESUMPTION
/// (GPT spec, frozen verbatim. Constants are pre-registered;
/// one frozen exam, no rescues, no tuning after results.)
///
/// LONG (SHORT mirror):
///  Regime (1h, built from 15m quadruples): Close>EMA20,
///    EMA20>EMA50, EMA20 rising vs 5 bars ago, EMA50 rising.
///  Pullback (15m): Low<=EMA20(15m) AND Close>=EMA50(15m)
///    starts an episode; killed if a 15m close < EMA50 or the
///    1h regime turns false before entry.
///  Trigger (5m): close > highest high of prior 3 completed 5m.
///    Entry at trigger close (MARKET order -> taker fee in).
///  Stop: pullbackLow - 0.10 x ATR14(15m); reject if risk >
///    2.0 x ATR14(15m). Expiry: 12 completed 5m candles.
///  One trade per episode; new episode requires a 15m close
///    back above EMA20 first.
///
/// Mechanism: countertrend traders fading an ordinary pullback
/// in an intact trend get trapped when 5m makes new highs and
/// sidelined trend traders re-enter.
///
/// Run: dotnet run pullback [MARKET|ALL]
/// </summary>
public static class TrendPullback
{
    private const int EmaFast = 20;
    private const int EmaSlow = 50;
    private const int SlopeBars = 5;
    private const int AtrLen = 14;
    private const int Resume5Bars = 3;
    private const int Expiry5Bars = 12;
    private const double StopAtrPad = 0.10;
    private const double MaxRiskAtrMult = 2.0;
    private const int WalkWindow5 = 480;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;

    private static readonly double[] Targets = { 1.0, 2.0, 3.0 };

    public sealed record PbSetup(
        string Market,
        bool IsLong,
        long EpisodeTs,
        int Entry5m,
        double Entry,
        double Stop,
        double Risk
    );

    // ---------------- detection ----------------

    public static List<PbSetup> Detect(
        string market,
        IReadOnlyList<ResearchCandle> c15,
        IReadOnlyList<ResearchCandle> c5)
    {
        var setups = new List<PbSetup>();

        // 1h from complete 15m quadruples
        var c1h = BuildHour(c15);
        if (c1h.Count < EmaSlow + SlopeBars + 2) return setups;

        double[] h20 = Ema(c1h, EmaFast);
        double[] h50 = Ema(c1h, EmaSlow);

        double[] m20 = Ema(c15, EmaFast);
        double[] m50 = Ema(c15, EmaSlow);
        double[] mAtr = Atr(c15, AtrLen);

        // regime per 1h index: +1 long, -1 short, 0 none
        var regime = new int[c1h.Count];

        for (int i = EmaSlow + SlopeBars; i < c1h.Count; i++)
        {
            bool lng =
                c1h[i].Close > h20[i] &&
                h20[i] > h50[i] &&
                h20[i] > h20[i - SlopeBars] &&
                h50[i] > h50[i - SlopeBars];

            bool sht =
                c1h[i].Close < h20[i] &&
                h20[i] < h50[i] &&
                h20[i] < h20[i - SlopeBars] &&
                h50[i] < h50[i - SlopeBars];

            regime[i] = lng ? 1 : sht ? -1 : 0;
        }

        int hPtr = 0;

        // Spec-exact arming: a new episode requires (a) a completed
        // 15m close back above/below EMA20 on an EARLIER candle
        // than the touch, and (b) the previous episode fully over.
        int armLongIdx = -1, armShortIdx = -1;
        long lastEpisodeEndTs = 0;

        Audit = new AuditCounters();

        for (int i = EmaSlow + 1; i < c15.Count; i++)
        {
            long closeTs = c15[i].Timestamp + 15 * 60 * 1000;

            while (hPtr + 1 < c1h.Count &&
                   c1h[hPtr + 1].Timestamp + 3_600_000 <= closeTs)
                hPtr++;

            int reg = regime[hPtr];

            // episode start: must be armed by a PRIOR candle
            bool startLong =
                reg == 1 &&
                armLongIdx >= 0 && armLongIdx < i &&
                closeTs > lastEpisodeEndTs &&
                c15[i].Low <= m20[i] &&
                c15[i].Close >= m50[i];

            bool startShort =
                reg == -1 &&
                armShortIdx >= 0 && armShortIdx < i &&
                closeTs > lastEpisodeEndTs &&
                c15[i].High >= m20[i] &&
                c15[i].Close <= m50[i];

            if (startLong || startShort)
            {
                bool isLong = startLong;

                // consume the arm: re-arming needs a fresh close
                if (isLong) armLongIdx = -1; else armShortIdx = -1;

                Audit.Episodes++;

                PbSetup? s = HuntTrigger(
                    market, isLong, i, closeTs,
                    c15, c5, c1h, regime, m50, mAtr);

                // episode occupies its full possible hunt window
                lastEpisodeEndTs =
                    closeTs + (Expiry5Bars + 1) * 5 * 60 * 1000;

                if (s != null)
                {
                    Audit.Traded++;
                    setups.Add(s);

                    if (Audit.Rows.Count < 20)
                        Audit.Rows.Add(
                            $"ep {Ts(c15[i].Timestamp)} | reg {(isLong ? "LONG" : "SHORT")} | " +
                            $"ema20 {m20[i]:F2} ema50 {m50[i]:F2} | entry {Ts(c5[s.Entry5m].Timestamp)}");
                }
                else
                {
                    Audit.Expired++;

                    if (Audit.Rows.Count < 20)
                        Audit.Rows.Add(
                            $"ep {Ts(c15[i].Timestamp)} | reg {(isLong ? "LONG" : "SHORT")} | " +
                            $"ema20 {m20[i]:F2} ema50 {m50[i]:F2} | expired/invalidated");
                }

                continue; // this candle cannot also re-arm
            }

            // arming: close on the trend side of EMA20, only while
            // no episode is pending on this side
            if (closeTs > lastEpisodeEndTs)
            {
                if (c15[i].Close > m20[i]) armLongIdx = i;
                if (c15[i].Close < m20[i]) armShortIdx = i;
            }
        }

        return setups;
    }

    public sealed class AuditCounters
    {
        public int Episodes, Traded, Expired;
        public List<string> Rows = new();
    }

    public static AuditCounters Audit = new();

    private static string Ts(long ms) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ms)
            .UtcDateTime.ToString("MM-dd HH:mm");

    private static PbSetup? HuntTrigger(
        string market,
        bool isLong,
        int i15,
        long episodeCloseTs,
        IReadOnlyList<ResearchCandle> c15,
        IReadOnlyList<ResearchCandle> c5,
        IReadOnlyList<ResearchCandle> c1h,
        int[] regime,
        double[] m50,
        double[] mAtr)
    {
        int start5 = IndexAtOrAfter(c5, episodeCloseTs);
        if (start5 < Resume5Bars + 1) return null;

        double pullExt = isLong
            ? c15[i15].Low
            : c15[i15].High;

        int hPtr = 0;

        for (int k = start5;
             k < Math.Min(start5 + Expiry5Bars, c5.Count);
             k++)
        {
            // track pullback extreme through the hunt
            if (isLong) pullExt = Math.Min(pullExt, c5[k].Low);
            else pullExt = Math.Max(pullExt, c5[k].High);

            long t5close = c5[k].Timestamp + 5 * 60 * 1000;

            // invalidation: 15m close beyond EMA50 or regime lost,
            // checked at every completed 15m boundary inside hunt
            int i15now = LastIndexAtOrBefore15(c15, t5close);

            if (i15now > i15)
            {
                bool broke = isLong
                    ? c15[i15now].Close < m50[i15now]
                    : c15[i15now].Close > m50[i15now];

                if (broke) return null;

                while (hPtr + 1 < c1h.Count &&
                       c1h[hPtr + 1].Timestamp + 3_600_000 <= t5close)
                    hPtr++;

                if ((isLong ? 1 : -1) != regime[hPtr] && regime[hPtr] != 0)
                    return null;

                if (regime[hPtr] == 0) return null;
            }

            // resumption trigger: close beyond extreme of prior 3
            double trigLevel = isLong
                ? Math.Max(c5[k - 1].High,
                    Math.Max(c5[k - 2].High, c5[k - 3].High))
                : Math.Min(c5[k - 1].Low,
                    Math.Min(c5[k - 2].Low, c5[k - 3].Low));

            bool fired = isLong
                ? c5[k].Close > trigLevel
                : c5[k].Close < trigLevel;

            if (!fired) continue;

            double atr = mAtr[Math.Min(
                LastIndexAtOrBefore15(c15, t5close), mAtr.Length - 1)];

            if (atr <= 0) return null;

            double entry = c5[k].Close;

            double stop = isLong
                ? pullExt - StopAtrPad * atr
                : pullExt + StopAtrPad * atr;

            double risk = Math.Abs(entry - stop);

            if (risk <= 0) return null;
            if (risk > MaxRiskAtrMult * atr) return null;

            return new PbSetup(
                market, isLong, c15[i15].Timestamp, k, entry, stop, risk);
        }

        return null;
    }

    // ---------------- scoring ----------------

    public static void Score(string[] markets, string source)
    {
        var reader = new HistoryReader();

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        Console.WriteLine();
        Console.WriteLine("CANDIDATE — HTF TREND PULLBACK RESUMPTION (GPT spec, frozen)");
        Console.WriteLine("(entry = market order: taker in; winners taker+maker, losers taker+taker)");

        foreach (double t in Targets)
        {
            double totLearn = 0, totExam = 0;
            int nLearn = 0, nExam = 0;

            Console.WriteLine();
            Console.WriteLine($"TARGET {t:F0}R");
            Console.WriteLine(
                $"{"market",-9} | {"n",5} | {"LEARN exp",-10} | {"EXAM exp",-10}");
            Console.WriteLine(new string('-', 46));

            foreach (string market in markets)
            {
                var c15 = reader.GetAllCandles(source, market, "15m");
                var c5 = reader.GetAllCandles(source, market, "5m");

                if (c15.Count == 0 || c5.Count == 0) continue;

                var learn = new List<double>();
                var exam = new List<double>();

                foreach (PbSetup s in Detect(market, c15, c5))
                {
                    double r = Walk(s, c5, t);

                    if (s.EpisodeTs < eraSplit) learn.Add(r);
                    else exam.Add(r);
                }

                double le = learn.Count > 0 ? learn.Average() : 0;
                double ee = exam.Count > 0 ? exam.Average() : 0;

                totLearn += learn.Sum(); nLearn += learn.Count;
                totExam += exam.Sum(); nExam += exam.Count;

                Console.WriteLine(
                    $"{market,-9} | {learn.Count + exam.Count,5:N0} | {le,8:F3}R  | {ee,8:F3}R");
            }

            Console.WriteLine(new string('-', 46));
            Console.WriteLine(
                $"{"ALL",-9} | {nLearn + nExam,5:N0} | " +
                $"{(nLearn > 0 ? totLearn / nLearn : 0),8:F3}R  | " +
                $"{(nExam > 0 ? totExam / nExam : 0),8:F3}R");
        }

        Console.WriteLine();
        Console.WriteLine("PASS BAR: positive after fees in BOTH eras, broad, big n.");
        Console.WriteLine();
        Console.WriteLine("AUDIT (last market scanned): " +
            $"episodes {Audit.Episodes:N0} | traded {Audit.Traded:N0} | " +
            $"expired {Audit.Expired:N0} | trades/episode " +
            $"{(Audit.Episodes > 0 ? (double)Audit.Traded / Audit.Episodes : 0):F2}");
        Console.WriteLine("first 20 episodes:");
        foreach (string row in Audit.Rows)
            Console.WriteLine("  " + row);
    }

    private static double Walk(
        PbSetup s,
        IReadOnlyList<ResearchCandle> c5,
        double targetR)
    {
        double target = s.IsLong
            ? s.Entry + targetR * s.Risk
            : s.Entry - targetR * s.Risk;

        int limit = Math.Min(s.Entry5m + WalkWindow5, c5.Count - 1);
        int last = s.Entry5m;

        for (int i = s.Entry5m + 1; i <= limit; i++)
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

    // ---------------- shared helpers ----------------

    private static List<ResearchCandle> BuildHour(
        IReadOnlyList<ResearchCandle> c15)
    {
        var result = new List<ResearchCandle>();

        foreach (var g in c15
            .GroupBy(k => k.Timestamp / 3_600_000)
            .OrderBy(g => g.Key))
        {
            var quad = g.OrderBy(k => k.Timestamp).ToList();
            if (quad.Count != 4) continue;

            result.Add(new ResearchCandle(
                quad[0].Timestamp,
                quad[0].Open,
                quad.Max(k => k.High),
                quad.Min(k => k.Low),
                quad[3].Close,
                quad.Sum(k => k.Volume)));
        }

        return result;
    }

    private static double[] Ema(
        IReadOnlyList<ResearchCandle> c,
        int len)
    {
        var ema = new double[c.Count];
        double k = 2.0 / (len + 1);
        double val = c[0].Close;

        for (int i = 0; i < c.Count; i++)
        {
            val = c[i].Close * k + val * (1 - k);
            ema[i] = val;
        }

        return ema;
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

    private static int IndexAtOrAfter(
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

    private static int LastIndexAtOrBefore15(
        IReadOnlyList<ResearchCandle> c,
        long closeTs)
    {
        int lo = 0, hi = c.Count - 1, ans = 0;

        while (lo <= hi)
        {
            int mid = (lo + hi) / 2;

            if (c[mid].Timestamp + 15 * 60 * 1000 <= closeTs)
            { ans = mid; lo = mid + 1; }
            else hi = mid - 1;
        }

        return ans;
    }
}
