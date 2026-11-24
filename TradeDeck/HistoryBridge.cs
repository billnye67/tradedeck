using System.Collections.Concurrent;
using System.Text.Json;
using TradeDeck.Research;

namespace TradeDeck;

/// <summary>
/// A1.5 — deep chart history. Serves candles older than the live
/// feed from the local history.sqlite (6+ years). 5m and 15m come
/// straight from the DB; 1h is aggregated from complete 15m
/// quadruples. Cached per market/interval after first load.
///
/// Known seam: the DB is Binance-sourced, the live feed is
/// Hyperliquid — a few dollars of basis at the stitch point.
/// Visible zoomed in, irrelevant for multi-month context.
/// </summary>
public static class HistoryBridge
{
    private const string Source = "binance_um";

    private static readonly ConcurrentDictionary<string, List<ResearchCandle>> Cache = new();

    public static string Slice(string market, string interval, long beforeSec, int count)
    {
        try
        {
            var bars = Load(market, interval);

            long beforeMs = beforeSec * 1000;

            // binary search last index with ts < beforeMs
            int lo = 0, hi = bars.Count - 1, end = -1;

            while (lo <= hi)
            {
                int mid = (lo + hi) / 2;

                if (bars[mid].Timestamp < beforeMs) { end = mid; lo = mid + 1; }
                else hi = mid - 1;
            }

            if (end < 0)
                return Empty(market, interval);

            int start = Math.Max(0, end - count + 1);

            var slice = bars
                .Skip(start)
                .Take(end - start + 1)
                .Select(c => new
                {
                    time = c.Timestamp / 1000,
                    open = c.Open,
                    high = c.High,
                    low = c.Low,
                    close = c.Close
                })
                .ToList();

            return JsonSerializer.Serialize(new
            {
                type = "history-result",
                market,
                interval,
                bars = slice
            });
        }
        catch
        {
            return Empty(market, interval);
        }
    }

    private static string Empty(string market, string interval) =>
        JsonSerializer.Serialize(new
        {
            type = "history-result",
            market,
            interval,
            bars = Array.Empty<object>()
        });

    private static List<ResearchCandle> Load(string market, string interval)
    {
        string key = $"{market}|{interval}";

        return Cache.GetOrAdd(key, _ =>
        {
            var reader = new HistoryReader();

            if (interval == "5m" || interval == "15m")
                return reader.GetAllCandles(Source, market, interval).ToList();

            if (interval == "1h")
            {
                var c15 = reader.GetAllCandles(Source, market, "15m");
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

            return new List<ResearchCandle>();
        });
    }
}
