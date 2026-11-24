using Microsoft.Data.Sqlite;
using System.Text.Json;

namespace TradeDeck.Research;

/// <summary>
/// NEW DATA LAYER — positioning. Collects into history.sqlite:
///
///  funding_rates: full history (Binance USD-M pays/charges every
///    8h; API serves the whole record) — WHO is paying to hold.
///  open_interest: 1h snapshots — HOW MUCH leverage is out.
///    Binance only serves ~30 days back, so this table grows
///    forward from first run; run the collector regularly.
///
/// Resumable: picks up from MAX(ts) per market. Run:
///   dotnet run collectfunding
/// </summary>
public static class FundingCollector
{
    private static readonly string[] Markets =
    {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
        "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT"
    };

    private const string Source = "binance_um";

    public static async Task Run(string dbPath)
    {
        using var http = new HttpClient
        {
            Timeout = TimeSpan.FromMinutes(2)
        };

        using var db = new SqliteConnection($"Data Source={dbPath}");
        db.Open();

        Exec(db, """
            CREATE TABLE IF NOT EXISTS funding_rates (
                source TEXT NOT NULL,
                market TEXT NOT NULL,
                ts INTEGER NOT NULL,
                rate REAL NOT NULL,
                PRIMARY KEY (source, market, ts)
            );
            """);

        Exec(db, """
            CREATE TABLE IF NOT EXISTS open_interest (
                source TEXT NOT NULL,
                market TEXT NOT NULL,
                ts INTEGER NOT NULL,
                oi REAL NOT NULL,
                oi_value REAL NOT NULL,
                PRIMARY KEY (source, market, ts)
            );
            """);

        foreach (string market in Markets)
        {
            try
            {
                int f = await CollectFunding(http, db, market);
                int o = await CollectOi(http, db, market);

                Console.WriteLine(
                    $"{market}: +{f:N0} funding rows, +{o:N0} OI rows");
            }
            catch (Exception ex)
            {
                Console.WriteLine($"{market}: ERROR {ex.Message} (resumable — rerun)");
            }
        }

        // coverage report
        Console.WriteLine();
        Console.WriteLine("COVERAGE");

        using var cmd = db.CreateCommand();
        cmd.CommandText = """
            SELECT market,
                   COUNT(*),
                   MIN(ts),
                   MAX(ts)
            FROM funding_rates
            WHERE source = $s
            GROUP BY market
            ORDER BY market;
            """;
        cmd.Parameters.AddWithValue("$s", Source);

        using var r = cmd.ExecuteReader();

        while (r.Read())
        {
            string mkt = r.GetString(0);
            long n = r.GetInt64(1);

            string from = DateTimeOffset
                .FromUnixTimeMilliseconds(r.GetInt64(2))
                .UtcDateTime.ToString("yyyy-MM-dd");

            string to = DateTimeOffset
                .FromUnixTimeMilliseconds(r.GetInt64(3))
                .UtcDateTime.ToString("yyyy-MM-dd");

            Console.WriteLine(
                $"   {mkt,-9} funding {n,6:N0} rows | {from} -> {to}");
        }
    }

    // ---------------- funding (full history) ----------------

    private static async Task<int> CollectFunding(
        HttpClient http,
        SqliteConnection db,
        string market)
    {
        long cursor = MaxTs(db, "funding_rates", market) + 1;

        if (cursor <= 1)
            cursor = new DateTimeOffset(2019, 12, 1, 0, 0, 0, TimeSpan.Zero)
                .ToUnixTimeMilliseconds();

        int added = 0;

        while (true)
        {
            string url =
                "https://fapi.binance.com/fapi/v1/fundingRate" +
                $"?symbol={market}&startTime={cursor}&limit=1000";

            using var resp = await http.GetAsync(url);
            resp.EnsureSuccessStatusCode();

            await using var stream = await resp.Content.ReadAsStreamAsync();
            using var doc = await JsonDocument.ParseAsync(stream);

            long newest = -1;
            int got = 0;

            using var tx = db.BeginTransaction();

            foreach (JsonElement k in doc.RootElement.EnumerateArray())
            {
                got++;

                long ts = k.GetProperty("fundingTime").GetInt64();
                if (ts > newest) newest = ts;

                double rate = double.Parse(
                    k.GetProperty("fundingRate").GetString()!,
                    System.Globalization.CultureInfo.InvariantCulture);

                using var ins = db.CreateCommand();
                ins.Transaction = tx;
                ins.CommandText = """
                    INSERT OR IGNORE INTO funding_rates
                    (source, market, ts, rate)
                    VALUES ($s, $m, $t, $r);
                    """;
                ins.Parameters.AddWithValue("$s", Source);
                ins.Parameters.AddWithValue("$m", market);
                ins.Parameters.AddWithValue("$t", ts);
                ins.Parameters.AddWithValue("$r", rate);

                added += ins.ExecuteNonQuery();
            }

            tx.Commit();

            if (got == 0 || newest < cursor) break;

            cursor = newest + 1;

            if (got < 1000) break;

            await Task.Delay(120); // polite to the API
        }

        return added;
    }

    // ---------------- open interest (30d max back) ----------------

    private static async Task<int> CollectOi(
        HttpClient http,
        SqliteConnection db,
        string market)
    {
        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        long cursor = Math.Max(
            MaxTs(db, "open_interest", market) + 1,
            nowMs - 29L * 24 * 3600 * 1000); // API limit ~30d

        int added = 0;

        while (cursor < nowMs)
        {
            string url =
                "https://fapi.binance.com/futures/data/openInterestHist" +
                $"?symbol={market}&period=1h&startTime={cursor}&limit=500";

            using var resp = await http.GetAsync(url);
            resp.EnsureSuccessStatusCode();

            await using var stream = await resp.Content.ReadAsStreamAsync();
            using var doc = await JsonDocument.ParseAsync(stream);

            long newest = -1;
            int got = 0;

            using var tx = db.BeginTransaction();

            foreach (JsonElement k in doc.RootElement.EnumerateArray())
            {
                got++;

                long ts = k.GetProperty("timestamp").GetInt64();
                if (ts > newest) newest = ts;

                double oi = double.Parse(
                    k.GetProperty("sumOpenInterest").GetString()!,
                    System.Globalization.CultureInfo.InvariantCulture);

                double oiVal = double.Parse(
                    k.GetProperty("sumOpenInterestValue").GetString()!,
                    System.Globalization.CultureInfo.InvariantCulture);

                using var ins = db.CreateCommand();
                ins.Transaction = tx;
                ins.CommandText = """
                    INSERT OR IGNORE INTO open_interest
                    (source, market, ts, oi, oi_value)
                    VALUES ($s, $m, $t, $o, $v);
                    """;
                ins.Parameters.AddWithValue("$s", Source);
                ins.Parameters.AddWithValue("$m", market);
                ins.Parameters.AddWithValue("$t", ts);
                ins.Parameters.AddWithValue("$o", oi);
                ins.Parameters.AddWithValue("$v", oiVal);

                added += ins.ExecuteNonQuery();
            }

            tx.Commit();

            if (got == 0 || newest < cursor) break;

            cursor = newest + 1;

            if (got < 500) break;

            await Task.Delay(120);
        }

        return added;
    }

    private static long MaxTs(
        SqliteConnection db,
        string table,
        string market)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText =
            $"SELECT COALESCE(MAX(ts), 0) FROM {table} " +
            "WHERE source = $s AND market = $m;";
        cmd.Parameters.AddWithValue("$s", Source);
        cmd.Parameters.AddWithValue("$m", market);

        return (long)cmd.ExecuteScalar()!;
    }

    private static void Exec(SqliteConnection db, string sql)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }
}
