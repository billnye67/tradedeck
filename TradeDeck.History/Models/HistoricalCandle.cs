namespace TradeDeck.History.Models;

public sealed record HistoricalCandle(
    string Market,
    long Timestamp,
    double Open,
    double High,
    double Low,
    double Close,
    double Volume
);

public sealed record HistoryStats(
    long Count,
    long? FirstTimestamp,
    long? LastTimestamp
);
