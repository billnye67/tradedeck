using System.Text.Json;

namespace TradeDeck.Research;

/// <summary>
/// LIVE SCANNER v1 (the brain — UI bolts on later).
///
/// Loop, per playbook market: pull the last ~3.5 days of closed
/// 5m candles from Binance, build 15m from complete triples, run
/// the LOCKED Strategy #1 spec, apply Playbook Rule #1, and:
///
///   WATCH  — displacement + FVG exist, retrace not yet filled:
///            the resting-limit moment. Entry/stop/size printed.
///   FILLED — price has touched the FVG edge (entry live).
///
/// Every alert is logged to reviews/scanner-log.jsonl with full
/// details — that file IS the forward-tracking record.
/// </summary>
public static class LiveScanner
{
    private static readonly string[] Markets =
        { "BTCUSDT", "ETHUSDT", "XRPUSDT" };

    private const int PollSeconds = 60;
    private const double LookbackDays = 3.5;
    private const long FifteenMs = 15 * 60 * 1000;

    // Playbook Rule #1 (out-of-sample verified)
    private const double MinFvgRatio = 0.35;
    private const double MinBreachPct = 0.0013;

    // Verified held-out record shown on every card (3R model)
    private const string PlaybookStats =
        "playbook record: ~36% win at 3R | NET +0.107R/trade after fees (held-out 24-26)";

    public static async Task Run(double riskDollars, string logPath)
    {
        using var http = new HttpClient
        {
            Timeout = TimeSpan.FromMinutes(1)
        };

        var alerted = new HashSet<string>();

        Console.WriteLine();
        Console.WriteLine("TRADEDECK LIVE SCANNER — Strategy #1, Playbook Rule #1");
        Console.WriteLine($"Markets: {string.Join(", ", Markets)} | risk per trade: ${riskDollars:F0}");
        Console.WriteLine($"Alert log: {logPath}");
        Console.WriteLine("Ctrl+C to stop.");

        while (true)
        {
            foreach (string market in Markets)
            {
                try
                {
                    await ScanMarket(http, market, riskDollars, alerted, logPath);
                }
                catch (Exception ex)
                {
                    Console.WriteLine(
                        $"[{DateTime.UtcNow:HH:mm}] {market} scan error: {ex.Message}");
                }
            }

            Console.WriteLine(
                $"[{DateTime.UtcNow:HH:mm:ss} UTC] scanned {Markets.Length} markets, " +
                $"{alerted.Count} active/seen setups");

            await Task.Delay(TimeSpan.FromSeconds(PollSeconds));
        }
    }

    private static async Task ScanMarket(
        HttpClient http,
        string market,
        double riskDollars,
        HashSet<string> alerted,
        string logPath)
    {
        long start = DateTimeOffset.UtcNow
            .AddDays(-LookbackDays)
            .ToUnixTimeMilliseconds();

        List<ResearchCandle> five =
            await FetchClosed5m(http, market, start);

        if (five.Count < 400) return;

        var fifteen = BuildFifteen(five);

        if (fifteen.Count < 60) return;

        var raids = SweepDetector.FindCandidates(market, fifteen);

        foreach (SweepCandidate raid in raids)
        {
            FullSetup s = SetupDetector.Build(raid, five);

            bool actionable =
                s.Stage == SetupStage.Complete ||
                s.Stage == SetupStage.NoRetrace;

            if (!actionable) continue;

            // Playbook Rule #1
            if (s.FvgSizeRatio < MinFvgRatio) continue;

            double breachPct =
                Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level;

            if (breachPct < MinBreachPct) continue;

            // Only care if the FVG is fresh enough that the retrace
            // window is still open (or entry just filled).
            long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long fvgAgeMs = nowMs - five[s.FvgMiddleIndex5m].Timestamp;

            if (fvgAgeMs > 3 * 60 * 60 * 1000) continue; // stale, window closed

            string key =
                $"{market}|{s.Raid.Direction}|{s.Raid.SweepTimestamp}";

            string status = s.Stage == SetupStage.Complete
                ? "FILLED"
                : "WATCH";

            string alertKey = $"{key}|{status}";

            if (!alerted.Add(alertKey)) continue;

            PrintCard(market, s, status, riskDollars);
            LogAlert(logPath, market, s, status, riskDollars);

            try { Console.Beep(); } catch { /* non-Windows */ }
        }
    }

    private static void PrintCard(
        string market,
        FullSetup s,
        string status,
        double riskDollars)
    {
        bool isShort = s.Raid.Direction == "HIGH";

        double units = riskDollars / s.RiskPerUnit;
        double notional = units * s.EntryPrice;

        double t3 = isShort
            ? s.EntryPrice - 3 * s.RiskPerUnit
            : s.EntryPrice + 3 * s.RiskPerUnit;

        Console.WriteLine();
        Console.WriteLine("==================================================");
        Console.WriteLine($"  {status}: {market} {(isShort ? "SHORT" : "LONG")}  [{DateTime.UtcNow:HH:mm} UTC]");
        Console.WriteLine($"  level {s.Raid.Level}  swept wick {s.SweepExtreme}");
        Console.WriteLine($"  ENTRY  {s.EntryPrice}   (resting limit at FVG edge)");
        Console.WriteLine($"  STOP   {s.StopLoss}");
        Console.WriteLine($"  TP 3R  {t3:F6}");
        Console.WriteLine($"  size for ${riskDollars:F0} risk: {units:F4} {market[..^4]} (~${notional:F0} notional)");
        Console.WriteLine($"  disp {s.DisplacementRatio:F2}x  fvg {s.FvgSizeRatio:F2}x  chop {s.ChopCrossings}");
        Console.WriteLine($"  {PlaybookStats}");
        Console.WriteLine("==================================================");
    }

    private static void LogAlert(
        string logPath,
        string market,
        FullSetup s,
        string status,
        double riskDollars)
    {
        var row = new
        {
            utc = DateTime.UtcNow.ToString("O"),
            market,
            status,
            direction = s.Raid.Direction == "HIGH" ? "SHORT" : "LONG",
            level = s.Raid.Level,
            sweepExtreme = s.SweepExtreme,
            entry = s.EntryPrice,
            stop = s.StopLoss,
            riskPerUnit = s.RiskPerUnit,
            dispRatio = s.DisplacementRatio,
            fvgRatio = s.FvgSizeRatio,
            sweepTs = s.Raid.SweepTimestamp
        };

        File.AppendAllText(
            logPath,
            JsonSerializer.Serialize(row) + Environment.NewLine);
    }

    private static List<ResearchCandle> BuildFifteen(
        List<ResearchCandle> five)
    {
        var result = new List<ResearchCandle>();

        var byBucket = five
            .GroupBy(k => k.Timestamp / FifteenMs)
            .OrderBy(g => g.Key);

        foreach (var g in byBucket)
        {
            var trio = g.OrderBy(k => k.Timestamp).ToList();
            if (trio.Count != 3) continue;

            result.Add(new ResearchCandle(
                trio[0].Timestamp,
                trio[0].Open,
                trio.Max(k => k.High),
                trio.Min(k => k.Low),
                trio[2].Close,
                trio.Sum(k => k.Volume)
            ));
        }

        return result;
    }

    // Self-contained kline fetch (Binance USD-M), closed candles only.
    private static async Task<List<ResearchCandle>> FetchClosed5m(
        HttpClient http,
        string market,
        long startTimestamp)
    {
        const long IntervalMs = 5 * 60 * 1000;
        const int PageLimit = 1500;

        var candles = new List<ResearchCandle>();

        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        long lastClosedOpen = ((nowMs / IntervalMs) * IntervalMs) - IntervalMs;

        long cursor = startTimestamp;

        while (cursor <= lastClosedOpen)
        {
            string url =
                "https://fapi.binance.com/fapi/v1/klines" +
                $"?symbol={market}&interval=5m&startTime={cursor}&limit={PageLimit}";

            using var response = await http.GetAsync(url);
            response.EnsureSuccessStatusCode();

            await using var stream =
                await response.Content.ReadAsStreamAsync();

            using var doc =
                await JsonDocument.ParseAsync(stream);

            long newest = -1;
            int received = 0;

            foreach (JsonElement k in doc.RootElement.EnumerateArray())
            {
                received++;

                long openTime = k[0].GetInt64();
                if (openTime > newest) newest = openTime;
                if (openTime > lastClosedOpen) continue;

                candles.Add(new ResearchCandle(
                    openTime,
                    double.Parse(k[1].GetString()!, System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(k[2].GetString()!, System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(k[3].GetString()!, System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(k[4].GetString()!, System.Globalization.CultureInfo.InvariantCulture),
                    double.Parse(k[5].GetString()!, System.Globalization.CultureInfo.InvariantCulture)
                ));
            }

            if (received == 0 || newest < cursor) break;

            cursor = newest + IntervalMs;

            if (received < PageLimit) break;
        }

        candles.Sort((a, b) => a.Timestamp.CompareTo(b.Timestamp));

        return candles;
    }
}
