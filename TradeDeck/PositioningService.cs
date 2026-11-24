using System.IO;
using System.Net.Http;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace TradeDeck;

/// <summary>
/// PHASE B brick 1 — positioning. Every few minutes, for each
/// market: current funding rate (who is paying to hold) and open
/// interest (how much leverage is loaded). Labels extremes against
/// that market's OWN learn-era distribution (p1/p99 from the DB —
/// no invented thresholds), and appends fresh OI snapshots into
/// history.sqlite so the dataset grows on its own.
/// Emits: { type:"positioning", market, fundingBp, pctile, oiChange24h, tag }
/// </summary>
public sealed class PositioningService : IDisposable
{
    private static readonly string[] Markets =
    {
        "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
        "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT"
    };

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(30) };
    private CancellationTokenSource? _cts;
    private readonly Dictionary<string, (double lo, double hi, List<double> sorted)> _dist = new();

    public event Action<string>? Message;

    private static string DbPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    public void Start()
    {
        if (_cts != null) return;
        _cts = new CancellationTokenSource();
        _ = Task.Run(() => Loop(_cts.Token));
    }

    public void Stop() { _cts?.Cancel(); _cts = null; }
    public void Dispose() => Stop();

    private async Task Loop(CancellationToken ct)
    {
        try { LoadDistributions(); } catch { }

        while (!ct.IsCancellationRequested)
        {
            foreach (string m in Markets)
            {
                try { await Tick(m, ct); }
                catch { /* one market failing must not stop the panel */ }
            }

            try { await Task.Delay(TimeSpan.FromMinutes(5), ct); }
            catch { break; }
        }
    }

    private void LoadDistributions()
    {
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
            .ToUnixTimeMilliseconds();

        using var db = new SqliteConnection($"Data Source={DbPath}");
        db.Open();
        using (var prag = db.CreateCommand())
        { prag.CommandText = "PRAGMA busy_timeout=5000;"; prag.ExecuteNonQuery(); }

        foreach (string m in Markets)
        {
            using var cmd = db.CreateCommand();
            cmd.CommandText = """
                SELECT rate FROM funding_rates
                WHERE source='binance_um' AND market=$m AND ts < $era
                ORDER BY rate;
                """;
            cmd.Parameters.AddWithValue("$m", m);
            cmd.Parameters.AddWithValue("$era", eraSplit);

            var rates = new List<double>();
            using var r = cmd.ExecuteReader();
            while (r.Read()) rates.Add(r.GetDouble(0) * 10_000);

            if (rates.Count < 300) continue;

            double P(double q) => rates[(int)Math.Clamp(q * (rates.Count - 1), 0, rates.Count - 1)];
            _dist[m] = (P(0.01), P(0.99), rates);
        }
    }

    private async Task Tick(string market, CancellationToken ct)
    {
        // funding + mark
        using var r1 = await _http.GetAsync(
            $"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={market}", ct);
        r1.EnsureSuccessStatusCode();
        using var d1 = JsonDocument.Parse(await r1.Content.ReadAsStringAsync(ct));

        double fundingBp = double.Parse(
            d1.RootElement.GetProperty("lastFundingRate").GetString()!,
            System.Globalization.CultureInfo.InvariantCulture) * 10_000;

        // open interest
        using var r2 = await _http.GetAsync(
            $"https://fapi.binance.com/fapi/v1/openInterest?symbol={market}", ct);
        r2.EnsureSuccessStatusCode();
        using var d2 = JsonDocument.Parse(await r2.Content.ReadAsStringAsync(ct));

        double oi = double.Parse(
            d2.RootElement.GetProperty("openInterest").GetString()!,
            System.Globalization.CultureInfo.InvariantCulture);

        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        double oiChange24h = 0;

        // persist OI snapshot (hourly bucket) + read 24h-ago OI
        try
        {
            using var db = new SqliteConnection($"Data Source={DbPath}");
            db.Open();
            using (var prag = db.CreateCommand())
            { prag.CommandText = "PRAGMA busy_timeout=5000;"; prag.ExecuteNonQuery(); }

            long hourTs = (nowMs / 3_600_000) * 3_600_000;

            using var ins = db.CreateCommand();
            ins.CommandText = """
                INSERT OR IGNORE INTO open_interest (source, market, ts, oi, oi_value)
                VALUES ('binance_um', $m, $t, $o, 0);
                """;
            ins.Parameters.AddWithValue("$m", market);
            ins.Parameters.AddWithValue("$t", hourTs);
            ins.Parameters.AddWithValue("$o", oi);
            ins.ExecuteNonQuery();

            using var q = db.CreateCommand();
            q.CommandText = """
                SELECT oi FROM open_interest
                WHERE source='binance_um' AND market=$m AND ts <= $t
                ORDER BY ts DESC LIMIT 1;
                """;
            q.Parameters.AddWithValue("$m", market);
            q.Parameters.AddWithValue("$t", nowMs - 24L * 3_600_000);

            object? past = q.ExecuteScalar();
            if (past is double p && p > 0) oiChange24h = (oi - p) / p * 100.0;
        }
        catch { }

        // percentile + tag against learn-era distribution
        double pctile = 50;
        string tag = "NORMAL";

        if (_dist.TryGetValue(market, out var dist))
        {
            int below = dist.sorted.Count(x => x < fundingBp);
            pctile = 100.0 * below / dist.sorted.Count;

            if (fundingBp >= dist.hi) tag = "CROWDED LONG";
            else if (fundingBp <= dist.lo) tag = "CROWDED SHORT";
            else if (pctile >= 90) tag = "LEANING LONG";
            else if (pctile <= 10) tag = "LEANING SHORT";
        }

        Message?.Invoke(JsonSerializer.Serialize(new
        {
            type = "positioning",
            market,
            fundingBp = Math.Round(fundingBp, 2),
            pctile = Math.Round(pctile, 0),
            oiChange24h = Math.Round(oiChange24h, 1),
            tag
        }));
    }
}
