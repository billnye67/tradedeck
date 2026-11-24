using System.Text.Json;
using System.Text.Json.Nodes;

namespace TradeDeck.Research;

/// <summary>
/// The machine evaluating its own trades. Reads the vault journal
/// (written live by the in-app scanner), fetches what price did
/// AFTER each call, and resolves every FILLED call at the 3R
/// model with the same conservative rules as the backtester
/// (stop touched = loss even if target also touched, maker/maker
/// wins, maker/taker losses). Prints the forward scoreboard vs
/// the backtest expectation and writes outcomes back into the
/// journal so each call is graded exactly once.
/// </summary>
public static class JournalEvaluator
{
    private const int WalkWindow = 480;         // 40h of 5m candles
    private const double TargetR = 3.0;
    private const double MakerFee = 0.00015;
    private const double TakerFee = 0.00045;
    private const long FiveMs = 5 * 60 * 1000;

    public static async Task Run(string vaultFolder)
    {
        string journalPath =
            Path.Combine(vaultFolder, "journal.jsonl");

        if (!File.Exists(journalPath))
        {
            Console.WriteLine($"No journal at {journalPath} yet.");
            return;
        }

        var lines = File.ReadAllLines(journalPath)
            .Where(l => !string.IsNullOrWhiteSpace(l))
            .Select(l => JsonNode.Parse(l)!.AsObject())
            .ToList();

        using var http = new HttpClient
        {
            Timeout = TimeSpan.FromMinutes(1)
        };

        int newlyResolved = 0;

        foreach (JsonObject row in lines)
        {
            // Only FILLED calls are trades; WATCH rows are context.
            if ((string?)row["status"] != "FILLED") continue;
            if (row.ContainsKey("outcomeR")) continue;

            string market = (string)row["market"]!;
            bool isShort = (string)row["direction"]! == "SHORT";

            double entry = (double)row["entry"]!;
            double stop = (double)row["stop"]!;
            double risk = (double)row["riskPerUnit"]!;
            long fromMs = (long)row["epochMs"]!;

            double target = isShort
                ? entry - TargetR * risk
                : entry + TargetR * risk;

            List<double[]> klines =
                await FetchKlines(http, market, fromMs);

            if (klines.Count == 0) continue;

            bool resolved = false;

            foreach (double[] k in klines.Take(WalkWindow))
            {
                double high = k[1], low = k[2];

                bool stopped = isShort ? high >= stop : low <= stop;
                bool hit = isShort ? low <= target : high >= target;

                if (stopped)
                {
                    Grade(row, -1.0, MakerFee + TakerFee, entry, risk);
                    resolved = true;
                    break;
                }

                if (hit)
                {
                    Grade(row, TargetR, MakerFee + MakerFee, entry, risk);
                    resolved = true;
                    break;
                }
            }

            if (!resolved && klines.Count >= WalkWindow)
            {
                // window expired: exit at last close (taker)
                double exit = klines[WalkWindow - 1][3];

                double r = isShort
                    ? (entry - exit) / risk
                    : (exit - entry) / risk;

                Grade(row, r, MakerFee + TakerFee, entry, risk);
                resolved = true;
            }

            if (resolved) newlyResolved++;
            // still-open trades stay ungraded until next run
        }

        File.WriteAllLines(
            journalPath,
            lines.Select(l => l.ToJsonString()));

        // ---- scoreboard ----
        var graded = lines
            .Where(l => l.ContainsKey("outcomeR"))
            .ToList();

        Console.WriteLine();
        Console.WriteLine("FORWARD RECORD — the machine's own calls, graded");
        Console.WriteLine(
            $"calls journaled {lines.Count(l => (string?)l["status"] == "FILLED")} | " +
            $"graded {graded.Count} (+{newlyResolved} new) | still open {lines.Count(l => (string?)l["status"] == "FILLED") - graded.Count}");

        if (graded.Count == 0) return;

        foreach (var g in graded.GroupBy(l => (string)l["tier"]!))
        {
            int n = g.Count();
            int wins = g.Count(l => (double)l["outcomeR"]! > 0);
            double net = g.Sum(l => (double)l["netR"]!);

            Console.WriteLine(
                $"   {Short(g.Key),-14} | trades {n,3} | win {100.0 * wins / n,5:F1}% | " +
                $"NET {net,6:F1}R | NET/trade {net / n,6:F3}R");
        }

        Console.WriteLine();
        Console.WriteLine(
            "compare vs backtest held-out: PLAYBOOK NET ~+0.107R/trade. " +
            "Forward matching backtest = trust earned.");
    }

    private static void Grade(
        JsonObject row,
        double resultR,
        double roundTripFee,
        double entry,
        double risk)
    {
        double feeR = roundTripFee * entry / risk;

        row["outcomeR"] = resultR;
        row["feeR"] = feeR;
        row["netR"] = resultR - feeR;
        row["gradedUtc"] = DateTime.UtcNow.ToString("O");
    }

    private static string Short(string tier) =>
        tier.StartsWith("PLAYBOOK") ? "PLAYBOOK"
        : tier.StartsWith("NEGATIVE") ? "NEGATIVE-EDGE"
        : "UNPROVEN";

    private static async Task<List<double[]>> FetchKlines(
        HttpClient http,
        string market,
        long fromMs)
    {
        // returns [openTime, high, low, close] per closed candle
        var result = new List<double[]>();

        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        long lastClosed = ((nowMs / FiveMs) * FiveMs) - FiveMs;

        long cursor = fromMs;

        while (cursor <= lastClosed && result.Count < WalkWindow + 4)
        {
            string url =
                "https://fapi.binance.com/fapi/v1/klines" +
                $"?symbol={market}&interval=5m&startTime={cursor}&limit=1500";

            using var resp = await http.GetAsync(url);
            resp.EnsureSuccessStatusCode();

            await using var stream =
                await resp.Content.ReadAsStreamAsync();

            using var doc = await JsonDocument.ParseAsync(stream);

            long newest = -1;
            int got = 0;

            foreach (JsonElement k in doc.RootElement.EnumerateArray())
            {
                got++;
                long open = k[0].GetInt64();
                if (open > newest) newest = open;
                if (open > lastClosed) continue;

                result.Add(new[]
                {
                    (double)open,
                    P(k[2]), P(k[3]), P(k[4])
                });
            }

            if (got == 0 || newest < cursor) break;

            cursor = newest + FiveMs;

            if (got < 1500) break;
        }

        return result;
    }

    private static double P(JsonElement e) =>
        double.Parse(
            e.GetString()!,
            System.Globalization.CultureInfo.InvariantCulture);
}
