using Microsoft.Data.Sqlite;

namespace TradeDeck.Research;

public sealed record ResearchCandle(
    long Timestamp,
    double Open,
    double High,
    double Low,
    double Close,
    double Volume
);

/// <summary>
/// One candidate liquidity raid for human review.
/// </summary>
public sealed record SweepCandidate(
    string Market,
    string Direction,        // "HIGH" = raid above a high, "LOW" = raid below a low
    long PivotTimestamp,     // when the swept level formed
    double Level,            // the exact price of the level
    long SweepTimestamp,     // the candle that wicked through it
    int PivotIndex,
    int SweepIndex,
    IReadOnlyList<ResearchCandle> Context,
    int ContextStartIndex    // index in full series of Context[0]
);

/// <summary>
/// Read-only access to the same history.sqlite the collector writes.
/// Intentionally duplicated from TradeDeck.History to keep this
/// project standalone; it never writes anything.
/// </summary>
public sealed class HistoryReader
{
    public string DbPath { get; }

    public HistoryReader(string? dbPath = null)
    {
        if (dbPath == null)
        {
            dbPath = Path.Combine(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData
                ),
                "TradeDeck",
                "Data",
                "history.sqlite"
            );
        }

        DbPath = dbPath;
    }

    public List<ResearchCandle> GetAllCandles(
        string source,
        string market,
        string interval)
    {
        var result = new List<ResearchCandle>();

        var connectionString =
            new SqliteConnectionStringBuilder
            {
                DataSource = DbPath,
                Mode = SqliteOpenMode.ReadOnly
            }.ToString();

        using var connection =
            new SqliteConnection(connectionString);

        connection.Open();

        var command = connection.CreateCommand();

        command.CommandText =
        """
        SELECT
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
        ORDER BY timestamp;
        """;

        command.Parameters.AddWithValue("$source", source);
        command.Parameters.AddWithValue("$market", market);
        command.Parameters.AddWithValue("$interval", interval);

        using var reader = command.ExecuteReader();

        while (reader.Read())
        {
            result.Add(
                new ResearchCandle(
                    reader.GetInt64(0),
                    reader.GetDouble(1),
                    reader.GetDouble(2),
                    reader.GetDouble(3),
                    reader.GetDouble(4),
                    reader.GetDouble(5)
                )
            );
        }

        return result;
    }
}
