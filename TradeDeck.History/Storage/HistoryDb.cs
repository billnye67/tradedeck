using Microsoft.Data.Sqlite;
using TradeDeck.History.Models;

namespace TradeDeck.History.Storage;

public sealed class HistoryDb
{
    // Aggregation definition: three complete 5m candles -> one 15m candle.
    private const string SourceInterval = "5m";
    private const string TargetInterval = "15m";
    private const long SourceIntervalMs = 5 * 60 * 1000;
    private const long TargetIntervalMs = 15 * 60 * 1000;
    private const long CandlesPerBucket =
        TargetIntervalMs / SourceIntervalMs;

    public string DbPath { get; }

    private string ConnectionString =>
        new SqliteConnectionStringBuilder
        {
            DataSource = DbPath,
            Mode = SqliteOpenMode.ReadWriteCreate,
            Cache = SqliteCacheMode.Shared
        }.ToString();

    public HistoryDb(string? dbPath = null)
    {
        if (dbPath == null)
        {
            string folder = Path.Combine(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData
                ),
                "TradeDeck",
                "Data"
            );

            Directory.CreateDirectory(folder);

            dbPath = Path.Combine(
                folder,
                "history.sqlite"
            );
        }

        DbPath = dbPath;
    }

    public async Task InitializeAsync()
    {
        await using var connection =
            new SqliteConnection(ConnectionString);

        await connection.OpenAsync();

        var command = connection.CreateCommand();

        command.CommandText =
        """
        PRAGMA journal_mode = WAL;
        PRAGMA synchronous = NORMAL;

        CREATE TABLE IF NOT EXISTS candles
        (
            source      TEXT NOT NULL,
            market      TEXT NOT NULL,
            interval    TEXT NOT NULL,
            timestamp   INTEGER NOT NULL,

            open        REAL NOT NULL,
            high        REAL NOT NULL,
            low         REAL NOT NULL,
            close       REAL NOT NULL,
            volume      REAL NOT NULL,

            PRIMARY KEY
            (
                source,
                market,
                interval,
                timestamp
            )
        );

        CREATE INDEX IF NOT EXISTS ix_candles_lookup
        ON candles
        (
            source,
            market,
            interval,
            timestamp
        );

        CREATE TABLE IF NOT EXISTS history_chunks
        (
            source        TEXT NOT NULL,
            market        TEXT NOT NULL,
            interval      TEXT NOT NULL,
            chunk_key     TEXT NOT NULL,
            status        TEXT NOT NULL,
            row_count     INTEGER NOT NULL,
            checked_utc   TEXT NOT NULL,

            PRIMARY KEY
            (
                source,
                market,
                interval,
                chunk_key
            )
        );
        """;

        await command.ExecuteNonQueryAsync();
    }

    /// <summary>
    /// Builds 15m candles from stored 5m candles for one market.
    ///
    /// A 15m candle is only created from a COMPLETE group of three
    /// 5m candles inside the same 15-minute bucket. Buckets missing
    /// any 5m candle are skipped entirely rather than guessed.
    ///
    /// Idempotent: already-generated 15m candles are ignored on
    /// re-run, so this is safe to call after every collection pass.
    ///
    /// Returns the number of newly inserted 15m candles.
    /// </summary>
    public async Task<int> Generate15mFrom5mAsync(
        string source,
        string market)
    {
        await using var connection =
            new SqliteConnection(ConnectionString);

        await connection.OpenAsync();

        var command = connection.CreateCommand();

        command.CommandText =
        """
        INSERT OR IGNORE INTO candles
        (
            source,
            market,
            interval,
            timestamp,
            open,
            high,
            low,
            close,
            volume
        )
        SELECT
            $source,
            $market,
            $targetInterval,
            bucket,
            MIN(bucket_open),
            MAX(high),
            MIN(low),
            MIN(bucket_close),
            SUM(volume)
        FROM
        (
            SELECT
                (timestamp / $bucketMs) * $bucketMs AS bucket,
                high,
                low,
                volume,
                FIRST_VALUE(open)  OVER w AS bucket_open,
                LAST_VALUE(close)  OVER w AS bucket_close
            FROM candles
            WHERE source = $source
              AND market = $market
              AND interval = $sourceInterval
            WINDOW w AS
            (
                PARTITION BY timestamp / $bucketMs
                ORDER BY timestamp
                ROWS BETWEEN UNBOUNDED PRECEDING
                         AND UNBOUNDED FOLLOWING
            )
        )
        GROUP BY bucket
        HAVING COUNT(*) = $candlesPerBucket;
        """;

        command.Parameters.AddWithValue("$source", source);
        command.Parameters.AddWithValue("$market", market);
        command.Parameters.AddWithValue("$sourceInterval", SourceInterval);
        command.Parameters.AddWithValue("$targetInterval", TargetInterval);
        command.Parameters.AddWithValue("$bucketMs", TargetIntervalMs);
        command.Parameters.AddWithValue("$candlesPerBucket", CandlesPerBucket);

        return await command.ExecuteNonQueryAsync();
    }

    public async Task<bool> IsChunkKnownAsync(
        string source,
        string market,
        string interval,
        string chunkKey)
    {
        await using var connection =
            new SqliteConnection(ConnectionString);

        await connection.OpenAsync();

        var command = connection.CreateCommand();

        command.CommandText =
        """
        SELECT 1
        FROM history_chunks
        WHERE source = $source
          AND market = $market
          AND interval = $interval
          AND chunk_key = $chunk
        LIMIT 1;
        """;

        command.Parameters.AddWithValue("$source", source);
        command.Parameters.AddWithValue("$market", market);
        command.Parameters.AddWithValue("$interval", interval);
        command.Parameters.AddWithValue("$chunk", chunkKey);

        return await command.ExecuteScalarAsync() != null;
    }

    public async Task MarkChunkAsync(
        string source,
        string market,
        string interval,
        string chunkKey,
        string status,
        int rowCount)
    {
        await using var connection =
            new SqliteConnection(ConnectionString);

        await connection.OpenAsync();

        var command = connection.CreateCommand();

        command.CommandText =
        """
        INSERT OR REPLACE INTO history_chunks
        (
            source,
            market,
            interval,
            chunk_key,
            status,
            row_count,
            checked_utc
        )
        VALUES
        (
            $source,
            $market,
            $interval,
            $chunk,
            $status,
            $rows,
            $checked
        );
        """;

        command.Parameters.AddWithValue("$source", source);
        command.Parameters.AddWithValue("$market", market);
        command.Parameters.AddWithValue("$interval", interval);
        command.Parameters.AddWithValue("$chunk", chunkKey);
        command.Parameters.AddWithValue("$status", status);
        command.Parameters.AddWithValue("$rows", rowCount);
        command.Parameters.AddWithValue(
            "$checked",
            DateTime.UtcNow.ToString("O")
        );

        await command.ExecuteNonQueryAsync();
    }

    public async Task<int> InsertCandlesAsync(
        string source,
        string interval,
        IReadOnlyList<HistoricalCandle> candles)
    {
        if (candles.Count == 0)
            return 0;

        await using var connection =
            new SqliteConnection(ConnectionString);

        await connection.OpenAsync();

        using var transaction =
            connection.BeginTransaction();

        var command = connection.CreateCommand();
        command.Transaction = transaction;

        command.CommandText =
        """
        INSERT OR IGNORE INTO candles
        (
            source,
            market,
            interval,
            timestamp,
            open,
            high,
            low,
            close,
            volume
        )
        VALUES
        (
            $source,
            $market,
            $interval,
            $timestamp,
            $open,
            $high,
            $low,
            $close,
            $volume
        );
        """;

        var sourceParam =
            command.Parameters.Add("$source", SqliteType.Text);

        var marketParam =
            command.Parameters.Add("$market", SqliteType.Text);

        var intervalParam =
            command.Parameters.Add("$interval", SqliteType.Text);

        var timestampParam =
            command.Parameters.Add("$timestamp", SqliteType.Integer);

        var openParam =
            command.Parameters.Add("$open", SqliteType.Real);

        var highParam =
            command.Parameters.Add("$high", SqliteType.Real);

        var lowParam =
            command.Parameters.Add("$low", SqliteType.Real);

        var closeParam =
            command.Parameters.Add("$close", SqliteType.Real);

        var volumeParam =
            command.Parameters.Add("$volume", SqliteType.Real);

        int inserted = 0;

        foreach (HistoricalCandle candle in candles)
        {
            sourceParam.Value = source;
            marketParam.Value = candle.Market;
            intervalParam.Value = interval;
            timestampParam.Value = candle.Timestamp;

            openParam.Value = candle.Open;
            highParam.Value = candle.High;
            lowParam.Value = candle.Low;
            closeParam.Value = candle.Close;
            volumeParam.Value = candle.Volume;

            inserted +=
                await command.ExecuteNonQueryAsync();
        }

        transaction.Commit();

        return inserted;
    }

    public async Task<HistoryStats> GetStatsAsync(
        string source,
        string market,
        string interval)
    {
        await using var connection =
            new SqliteConnection(ConnectionString);

        await connection.OpenAsync();

        var command = connection.CreateCommand();

        command.CommandText =
        """
        SELECT
            COUNT(*),
            MIN(timestamp),
            MAX(timestamp)
        FROM candles
        WHERE source = $source
          AND market = $market
          AND interval = $interval;
        """;

        command.Parameters.AddWithValue("$source", source);
        command.Parameters.AddWithValue("$market", market);
        command.Parameters.AddWithValue("$interval", interval);

        await using var reader =
            await command.ExecuteReaderAsync();

        await reader.ReadAsync();

        return new HistoryStats(
            reader.GetInt64(0),

            reader.IsDBNull(1)
                ? null
                : reader.GetInt64(1),

            reader.IsDBNull(2)
                ? null
                : reader.GetInt64(2)
        );
    }

    public async Task<List<HistoricalCandle>> GetCandlesAsync(
        string source,
        string market,
        string interval,
        long startTimestamp,
        long endTimestamp)
    {
        var result =
            new List<HistoricalCandle>();

        await using var connection =
            new SqliteConnection(ConnectionString);

        await connection.OpenAsync();

        var command = connection.CreateCommand();

        command.CommandText =
        """
        SELECT
            market,
            timestamp,
            open,
            high,
            low,
            close,
            volume
        FROM candles
        WHERE source = $source
          AND market = $market
          AND interval = $interval
          AND timestamp >= $start
          AND timestamp <= $end
        ORDER BY timestamp;
        """;

        command.Parameters.AddWithValue("$source", source);
        command.Parameters.AddWithValue("$market", market);
        command.Parameters.AddWithValue("$interval", interval);
        command.Parameters.AddWithValue("$start", startTimestamp);
        command.Parameters.AddWithValue("$end", endTimestamp);

        await using var reader =
            await command.ExecuteReaderAsync();

        while (await reader.ReadAsync())
        {
            result.Add(
                new HistoricalCandle(
                    reader.GetString(0),
                    reader.GetInt64(1),
                    reader.GetDouble(2),
                    reader.GetDouble(3),
                    reader.GetDouble(4),
                    reader.GetDouble(5),
                    reader.GetDouble(6)
                )
            );
        }

        return result;
    }
}
