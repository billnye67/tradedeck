using System.Globalization;
using System.Text.Json;
using TradeDeck.History.Models;

namespace TradeDeck.History.Sources;

/// <summary>
/// Fetches recent 5m candles straight from the Binance USD-M
/// futures REST API. This covers the gap between the last
/// complete monthly archive and right now.
///
/// Only CLOSED candles are returned. The candle currently
/// forming is always excluded, so nothing half-baked ever
/// enters the database.
/// </summary>
public sealed class BinanceUmApiSource : IDisposable
{
    private const string BaseUrl =
        "https://fapi.binance.com/fapi/v1/klines";

    private const string Interval = "5m";

    private const long IntervalMs =
        5 * 60 * 1000;

    // Binance API maximum per request.
    private const int MaxCandlesPerRequest = 1500;

    // Small pause between paged requests to stay
    // politely under Binance rate limits.
    private const int DelayBetweenPagesMs = 250;

    private readonly HttpClient _http = new()
    {
        Timeout = TimeSpan.FromMinutes(1)
    };

    // Same source name as the archive collector so API
    // candles merge into the same dataset and the
    // primary key dedupes any overlap.
    public string Name => "binance_um";

    public BinanceUmApiSource()
    {
        _http.DefaultRequestHeaders.UserAgent.ParseAdd(
            "TradeDeck-History/1.0"
        );
    }

    public async Task<IReadOnlyList<HistoricalCandle>> FetchClosed5mAsync(
        string market,
        long startTimestamp,
        CancellationToken cancellationToken = default)
    {
        market = market.ToUpperInvariant();

        var candles =
            new List<HistoricalCandle>();

        long nowMs =
            DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

        // Open time of the newest candle whose full
        // 5m window has already completed.
        long lastClosedOpenTime =
            ((nowMs / IntervalMs) * IntervalMs) - IntervalMs;

        long cursor = startTimestamp;

        while (cursor <= lastClosedOpenTime)
        {
            string url =
                $"{BaseUrl}" +
                $"?symbol={market}" +
                $"&interval={Interval}" +
                $"&startTime={cursor}" +
                $"&limit={MaxCandlesPerRequest}";

            using HttpResponseMessage response =
                await _http.GetAsync(
                    url,
                    cancellationToken
                );

            response.EnsureSuccessStatusCode();

            await using Stream stream =
                await response.Content.ReadAsStreamAsync(
                    cancellationToken
                );

            using JsonDocument doc =
                await JsonDocument.ParseAsync(
                    stream,
                    cancellationToken: cancellationToken
                );

            long newestOpenTimeInPage = -1;
            int receivedInPage = 0;

            foreach (
                JsonElement kline in
                doc.RootElement.EnumerateArray()
            )
            {
                receivedInPage++;

                long openTime =
                    kline[0].GetInt64();

                if (openTime > newestOpenTimeInPage)
                    newestOpenTimeInPage = openTime;

                // Skip the still-forming candle.
                if (openTime > lastClosedOpenTime)
                    continue;

                candles.Add(
                    new HistoricalCandle(
                        market,
                        openTime,
                        ParseElement(kline[1]),
                        ParseElement(kline[2]),
                        ParseElement(kline[3]),
                        ParseElement(kline[4]),
                        ParseElement(kline[5])
                    )
                );
            }

            // Nothing came back, or the page did not
            // move the cursor forward: we are done.
            if (
                receivedInPage == 0 ||
                newestOpenTimeInPage < cursor
            )
            {
                break;
            }

            cursor = newestOpenTimeInPage + IntervalMs;

            if (receivedInPage < MaxCandlesPerRequest)
                break;

            await Task.Delay(
                DelayBetweenPagesMs,
                cancellationToken
            );
        }

        candles.Sort(
            (a, b) => a.Timestamp.CompareTo(b.Timestamp)
        );

        return candles;
    }

    // Binance klines encode prices/volumes as JSON strings.
    private static double ParseElement(JsonElement element) =>
        double.Parse(
            element.GetString()!,
            NumberStyles.Float,
            CultureInfo.InvariantCulture
        );

    public void Dispose()
    {
        _http.Dispose();
    }
}
