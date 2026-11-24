using Microsoft.Data.Sqlite;

namespace TradeDeck.Research;

/// <summary>
/// Funding distribution per market, LEARN era only (pre-2024),
/// so "extreme funding" gets DEFINED from data the exam never
/// sees. Prints percentiles in basis points per 8h interval and
/// how often each tail fires. These numbers become the frozen
/// thresholds in the positioning-unwind spec.
///
/// Run: dotnet run fundingstats
/// </summary>
public static class FundingStats
{
    public static void Run(string dbPath)
    {
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        using var db = new SqliteConnection($"Data Source={dbPath}");
        db.Open();

        Console.WriteLine();
        Console.WriteLine("FUNDING DISTRIBUTION — LEARN ERA ONLY (pre-2024), bp per 8h");
        Console.WriteLine(
            $"{"market",-9} | {"n",6} | {"p1",7} | {"p5",7} | {"p50",7} | {"p95",7} | {"p99",7} | days>p99/yr");
        Console.WriteLine(new string('-', 78));

        using var cmd = db.CreateCommand();
        cmd.CommandText = """
            SELECT market, rate FROM funding_rates
            WHERE source = 'binance_um' AND ts < $era
            ORDER BY market;
            """;
        cmd.Parameters.AddWithValue("$era", eraSplit);

        var byMarket = new Dictionary<string, List<double>>();

        using var r = cmd.ExecuteReader();

        while (r.Read())
        {
            string m = r.GetString(0);

            if (!byMarket.TryGetValue(m, out var list))
                byMarket[m] = list = new List<double>();

            list.Add(r.GetDouble(1) * 10_000); // to basis points
        }

        foreach (var (market, rates) in byMarket.OrderBy(kv => kv.Key))
        {
            if (rates.Count < 500)
            {
                Console.WriteLine($"{market,-9} | too little learn-era data ({rates.Count})");
                continue;
            }

            rates.Sort();

            double P(double q) =>
                rates[(int)Math.Clamp(q * (rates.Count - 1), 0, rates.Count - 1)];

            double p99 = P(0.99);

            int above = rates.Count(x => x >= p99);
            double years = rates.Count / (3.0 * 365.0);
            double perYear = above / Math.Max(years, 0.01) / 3.0; // 8h intervals -> days

            Console.WriteLine(
                $"{market,-9} | {rates.Count,6:N0} | {P(0.01),7:F2} | {P(0.05),7:F2} | " +
                $"{P(0.50),7:F2} | {P(0.95),7:F2} | {p99,7:F2} | {perYear,7:F1}");
        }

        Console.WriteLine();
        Console.WriteLine("read: p50 = normal life. p99 / p1 = the crowded tails.");
        Console.WriteLine("(baseline funding is ~1.00bp/8h; tails are the trade zone)");
    }
}
