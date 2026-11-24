using Microsoft.Data.Sqlite;
using System;
using System.IO;

namespace TradeDeck.Data;

public static class TradeDeckDb
{
    public static string DatabasePath =>
        Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "TradeDeck",
            "tradedeck.db"
        );

    public static void Initialize()
    {
        Directory.CreateDirectory(Path.GetDirectoryName(DatabasePath)!);

        using var connection =
            new SqliteConnection($"Data Source={DatabasePath}");

        connection.Open();

        using var command = connection.CreateCommand();

        command.CommandText =
        """
        CREATE TABLE IF NOT EXISTS candles (
            market TEXT NOT NULL,
            timestamp INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            PRIMARY KEY (market, timestamp)
        );

        CREATE INDEX IF NOT EXISTS idx_candles_market_time
        ON candles(market, timestamp);

        CREATE TABLE IF NOT EXISTS setups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            market TEXT NOT NULL,
            direction TEXT,
            detected_at INTEGER NOT NULL,
            liquidity_level REAL,
            sweep_price REAL,
            trigger_price REAL,
            displacement_size REAL,
            fvg_low REAL,
            fvg_high REAL,
            entry REAL,
            stop REAL,
            tp1 REAL,
            tp2 REAL,
            rr REAL,
            status TEXT,
            mae REAL,
            mfe REAL,
            outcome_r REAL
        );
        """;

        command.ExecuteNonQuery();
    }
}
