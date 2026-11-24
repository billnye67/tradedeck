using TradeDeck.History.Sources;
using TradeDeck.History.Storage;

string[] markets =
{
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "HYPEUSDT"
};

const long FiveMinutesMs =
    5 * 60 * 1000;

var db = new HistoryDb();

await db.InitializeAsync();

using var source =
    new BinanceUmArchiveSource();

using var api =
    new BinanceUmApiSource();

// Start before Binance USD-M futures existed.
// Missing months are detected and remembered automatically.
DateOnly firstMonthToCheck =
    new(2019, 1, 1);

DateTime utcNow = DateTime.UtcNow;

DateOnly currentMonth =
    new(utcNow.Year, utcNow.Month, 1);

// Monthly archives are only trusted through
// the last fully completed month.
DateOnly lastArchiveMonth =
    currentMonth.AddMonths(-1);

Console.WriteLine();
Console.WriteLine("TRADEDECK HISTORICAL COLLECTOR");
Console.WriteLine($"Database: {db.DbPath}");
Console.WriteLine(
    $"Archive range: {firstMonthToCheck:yyyy-MM} -> {lastArchiveMonth:yyyy-MM}"
);
Console.WriteLine();

foreach (string market in markets)
{
    Console.WriteLine(
        $"========== {market} =========="
    );

    for (
        DateOnly month = firstMonthToCheck;
        month <= lastArchiveMonth;
        month = month.AddMonths(1)
    )
    {
        string chunk =
            $"{month.Year:D4}-{month.Month:D2}";

        if (
            await db.IsChunkKnownAsync(
                source.Name,
                market,
                "5m",
                chunk
            )
        )
        {
            Console.WriteLine(
                $"SKIP {market} {chunk}"
            );

            continue;
        }

        Console.Write(
            $"GET  {market} {chunk} ... "
        );

        try
        {
            HistoricalMonth result =
                await source.Fetch5mMonthAsync(
                    market,
                    month
                );

            if (!result.Found)
            {
                await db.MarkChunkAsync(
                    source.Name,
                    market,
                    "5m",
                    chunk,
                    "NOT_AVAILABLE",
                    0
                );

                Console.WriteLine(
                    "NOT AVAILABLE"
                );

                continue;
            }

            int inserted =
                await db.InsertCandlesAsync(
                    source.Name,
                    "5m",
                    result.Candles
                );

            await db.MarkChunkAsync(
                source.Name,
                market,
                "5m",
                chunk,
                "COMPLETE",
                result.Candles.Count
            );

            Console.WriteLine(
                $"{result.Candles.Count:N0} rows / {inserted:N0} new"
            );
        }
        catch (Exception ex)
        {
            Console.WriteLine("ERROR");
            Console.WriteLine(ex.Message);

            Console.WriteLine();
            Console.WriteLine(
                "Collector stopped safely."
            );

            Console.WriteLine(
                "Run dotnet run again and it will resume."
            );

            return;
        }
    }

    // Fill the gap between the newest stored candle
    // and right now using the live Binance API.
    // Only closed candles are ever inserted, and the
    // primary key dedupes any overlap with archives.
    var current5mStats =
        await db.GetStatsAsync(
            source.Name,
            market,
            "5m"
        );

    if (current5mStats.LastTimestamp.HasValue)
    {
        Console.WriteLine();
        Console.Write(
            $"UPDATE {market} recent 5m ... "
        );

        try
        {
            IReadOnlyList<TradeDeck.History.Models.HistoricalCandle> recent =
                await api.FetchClosed5mAsync(
                    market,
                    current5mStats.LastTimestamp.Value + FiveMinutesMs
                );

            int recentInserted =
                await db.InsertCandlesAsync(
                    api.Name,
                    "5m",
                    recent
                );

            Console.WriteLine(
                $"{recent.Count:N0} fetched / {recentInserted:N0} new"
            );
        }
        catch (Exception ex)
        {
            // A failed update never blocks the rest of
            // the run; archives and existing data are
            // untouched and the next run will catch up.
            Console.WriteLine("ERROR");
            Console.WriteLine(ex.Message);
        }
    }
    else
    {
        Console.WriteLine();
        Console.WriteLine(
            $"UPDATE {market} recent 5m ... SKIPPED (no archive data yet)"
        );
    }

    // Build 15m candles from complete groups of
    // three stored 5m candles. Idempotent, so it
    // runs on every pass and only fills gaps.
    Console.Write(
        $"GENERATE {market} 15m from 5m ... "
    );

    int generated =
        await db.Generate15mFrom5mAsync(
            source.Name,
            market
        );

    Console.WriteLine(
        $"{generated:N0} new"
    );

    Console.WriteLine();

    foreach (string interval in new[] { "5m", "15m" })
    {
        var stats =
            await db.GetStatsAsync(
                source.Name,
                market,
                interval
            );

        if (
            stats.FirstTimestamp.HasValue &&
            stats.LastTimestamp.HasValue
        )
        {
            DateTimeOffset first =
                DateTimeOffset.FromUnixTimeMilliseconds(
                    stats.FirstTimestamp.Value
                );

            DateTimeOffset last =
                DateTimeOffset.FromUnixTimeMilliseconds(
                    stats.LastTimestamp.Value
                );

            Console.WriteLine(
                $"{market} {interval,-3} | {stats.Count:N0} candles | " +
                $"{first:yyyy-MM-dd HH:mm} UTC -> " +
                $"{last:yyyy-MM-dd HH:mm} UTC"
            );
        }
        else
        {
            Console.WriteLine(
                $"{market} {interval,-3} | NO DATA"
            );
        }
    }

    Console.WriteLine();
}

Console.WriteLine(
    "ARCHIVE COLLECTION COMPLETE"
);
