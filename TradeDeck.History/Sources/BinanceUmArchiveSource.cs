using System.Globalization;
using System.IO.Compression;
using System.Net;
using TradeDeck.History.Models;

namespace TradeDeck.History.Sources;

public sealed record HistoricalMonth(
    bool Found,
    IReadOnlyList<HistoricalCandle> Candles
);

public sealed class BinanceUmArchiveSource : IDisposable
{
    private const string BaseUrl =
        "https://data.binance.vision/data/futures/um/monthly/klines";

    private readonly HttpClient _http = new()
    {
        Timeout = TimeSpan.FromMinutes(3)
    };

    public string Name => "binance_um";

    public BinanceUmArchiveSource()
    {
        _http.DefaultRequestHeaders.UserAgent.ParseAdd(
            "TradeDeck-History/1.0"
        );
    }

    public async Task<HistoricalMonth> Fetch5mMonthAsync(
        string market,
        DateOnly month,
        CancellationToken cancellationToken = default)
    {
        market = market.ToUpperInvariant();

        string monthText =
            $"{month.Year:D4}-{month.Month:D2}";

        string fileName =
            $"{market}-5m-{monthText}.zip";

        string url =
            $"{BaseUrl}/{market}/5m/{fileName}";

        using HttpResponseMessage response =
            await _http.GetAsync(
                url,
                HttpCompletionOption.ResponseHeadersRead,
                cancellationToken
            );

        if (response.StatusCode == HttpStatusCode.NotFound)
        {
            return new HistoricalMonth(
                false,
                Array.Empty<HistoricalCandle>()
            );
        }

        response.EnsureSuccessStatusCode();

        await using Stream stream =
            await response.Content.ReadAsStreamAsync(
                cancellationToken
            );

        using var archive =
            new ZipArchive(stream, ZipArchiveMode.Read);

        ZipArchiveEntry? csv =
            archive.Entries.FirstOrDefault(
                x => x.FullName.EndsWith(
                    ".csv",
                    StringComparison.OrdinalIgnoreCase
                )
            );

        if (csv == null)
            throw new InvalidDataException(
                $"No CSV inside {fileName}"
            );

        var candles =
            new List<HistoricalCandle>(10000);

        using var reader =
            new StreamReader(csv.Open());

        while (!reader.EndOfStream)
        {
            string? line =
                await reader.ReadLineAsync(
                    cancellationToken
                );

            if (string.IsNullOrWhiteSpace(line))
                continue;

            string[] p = line.Split(',');

            if (p.Length < 6)
                continue;

            if (!long.TryParse(
                    p[0],
                    NumberStyles.Integer,
                    CultureInfo.InvariantCulture,
                    out long timestamp))
            {
                continue;
            }

            if (timestamp > 10_000_000_000_000L)
                timestamp /= 1000L;

            candles.Add(
                new HistoricalCandle(
                    market,
                    timestamp,
                    Parse(p[1]),
                    Parse(p[2]),
                    Parse(p[3]),
                    Parse(p[4]),
                    Parse(p[5])
                )
            );
        }

        candles.Sort(
            (a, b) => a.Timestamp.CompareTo(b.Timestamp)
        );

        return new HistoricalMonth(
            true,
            candles
        );
    }

    private static double Parse(string value) =>
        double.Parse(
            value,
            NumberStyles.Float,
            CultureInfo.InvariantCulture
        );

    public void Dispose()
    {
        _http.Dispose();
    }
}
