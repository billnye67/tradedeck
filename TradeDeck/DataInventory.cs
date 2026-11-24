using System.IO;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace TradeDeck;

/// <summary>
/// DATA tab backend — the machine's inventory, in plain language.
/// Every card: what this data IS, the receipt (how much, since when),
/// a live read where one exists, and which future edge it feeds.
/// </summary>
public static class DataInventory
{
    private static string DbPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    public static string BuildJson()
    {
        var cards = new List<object>();

        try
        {
            using var db = new SqliteConnection($"Data Source={DbPath}");
            db.Open();

            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();

            // ---- candles (find the actual table name) ----
            try
            {
                string? candleTable = null;
                using (var c = db.CreateCommand())
                {
                    c.CommandText = "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%candle%' LIMIT 1;";
                    candleTable = c.ExecuteScalar() as string;
                }
                if (candleTable != null)
                {
                    var (n, first, last) = Count(db, candleTable);
                    cards.Add(new
                    {
                        title = "Price history (candles)",
                        what = "Every 5m/15m candle for our coins, six years deep. This is what every backtest chews on.",
                        receipt = $"{n:N0} candles · {Age(first)} of history",
                        read = "",
                        feeds = "All strategy testing — the learn/exam machine."
                    });
                }
            }
            catch { }

            // ---- funding ----
            try
            {
                var (n, first, last) = Count(db, "funding_rates");
                cards.Add(new
                {
                    title = "The crowd's rent (funding)",
                    what = "Every 8 hours, whichever side is more crowded PAYS the other side to stay in. Seven years of who paid whom.",
                    receipt = $"{n:N0} payments recorded since {Ymd(first)}",
                    read = "",
                    feeds = "Crowd meter, TRAPPED tag (proven: raids against a trapped crowd earn ~50% more)."
                });
            }
            catch { }

            // ---- venues ----
            try
            {
                var (n, first, last) = Count(db, "venue_snap");
                string read = "";
                try
                {
                    using var c = db.CreateCommand();
                    c.CommandText = """
                        SELECT MAX(price) - MIN(price), AVG(price) FROM venue_snap
                        WHERE market='BTCUSDT' AND ts = (SELECT MAX(ts) FROM venue_snap WHERE market='BTCUSDT');
                        """;
                    using var rd = c.ExecuteReader();
                    if (rd.Read() && !rd.IsDBNull(0) && rd.GetDouble(1) > 0)
                        read = $"Right now: BTC prices across venues are {rd.GetDouble(0) / rd.GetDouble(1) * 10_000:F1} bp apart.";
                }
                catch { }
                cards.Add(new
                {
                    title = "Five exchanges, one coin",
                    what = "Same coin, five venues (Binance spot+perp, Bybit, OKX, Hyperliquid), snapshotted every 5 minutes. When one venue runs ahead, someone there knows something.",
                    receipt = $"{n:N0} snapshots since {Ymd(first)} · {Fresh(last, now, 10 * 60_000)}",
                    read,
                    feeds = "Venue lead/lag + basis features (hunt round 2)."
                });
            }
            catch { }

            // ---- flow ----
            try
            {
                var (n, first, last) = Count(db, "agg_flow");
                string read = "";
                try
                {
                    using var c = db.CreateCommand();
                    c.CommandText = $"SELECT SUM(buy_vol), SUM(sell_vol) FROM agg_flow WHERE ts >= {now - 3_600_000};";
                    using var rd = c.ExecuteReader();
                    if (rd.Read() && !rd.IsDBNull(0))
                    {
                        double b = rd.GetDouble(0), s = rd.IsDBNull(1) ? 0 : rd.GetDouble(1);
                        if (b + s > 0)
                            read = $"Last hour on Hyperliquid: buyers were {b / (b + s) * 100:F0}% of ${(b + s) / 1e6:F1}M traded.";
                    }
                }
                catch { }
                cards.Add(new
                {
                    title = "Who's actually hitting buy vs sell",
                    what = "Every trade on the tape, sorted by who crossed the spread — real buying pressure vs real selling pressure, minute by minute. Candles can't tell you this.",
                    receipt = $"{n:N0} minutes recorded since {Ymd(first)} · {Fresh(last, now, 5 * 60_000)}",
                    read,
                    feeds = "\"Did that sweep have real sellers behind it?\" — the missing piece of the clean-setup score."
                });
            }
            catch { }

            // ---- book ----
            try
            {
                var (n, first, last) = Count(db, "book_snap");
                string read = "";
                try
                {
                    using var c = db.CreateCommand();
                    c.CommandText = """
                        SELECT spread_bp, bid_depth25, ask_depth25 FROM book_snap
                        WHERE market='BTCUSDT' ORDER BY ts DESC LIMIT 1;
                        """;
                    using var rd = c.ExecuteReader();
                    if (rd.Read())
                        read = $"BTC right now: ${(rd.GetDouble(1) + rd.GetDouble(2)) / 1e6:F1}M sitting within 0.25% of price.";
                }
                catch { }
                cards.Add(new
                {
                    title = "How thick the wall is (order book)",
                    what = "Every minute: how much real money sits waiting to buy or sell near the current price. Thin book = moves go far. Thick book = moves die.",
                    receipt = $"{n:N0} snapshots since {Ymd(first)} · {Fresh(last, now, 5 * 60_000)}",
                    read,
                    feeds = "Level-strength features — is there actually resistance at our levels."
                });
            }
            catch { }

            // ---- liquidations ----
            try
            {
                var (n, first, last) = Count(db, "liq_events");
                string read;
                if (n == 0)
                    read = "Quiet so far — no forced wrecks on our coins since the recorder went live. They come in storms.";
                else
                {
                    using var c = db.CreateCommand();
                    c.CommandText = $"SELECT COUNT(*), SUM(notional), MAX(notional) FROM liq_events WHERE ts >= {now - 86_400_000};";
                    using var rd = c.ExecuteReader();
                    read = rd.Read() && !rd.IsDBNull(0) && rd.GetInt64(0) > 0
                        ? $"Last 24h: {rd.GetInt64(0):N0} traders force-closed, ${rd.GetDouble(1) / 1e6:F2}M total, biggest single ${rd.GetDouble(2) / 1e3:F0}k."
                        : "Nothing in the last 24h.";
                }
                cards.Add(new
                {
                    title = "Forced liquidations",
                    what = "When a leveraged bet goes bad enough, the exchange closes it FOR them — market order, no choice. The purest \"somebody's trapped\" signal there is. Nobody sells this history; we record it ourselves.",
                    receipt = n == 0 ? "Recorder live — waiting for the first wreck" : $"{n:N0} events since {Ymd(first)}",
                    read,
                    feeds = "Liquidation-continuation candidate (parked until months of this exist)."
                });
            }
            catch { }

            // ---- open interest ----
            try
            {
                var (n, first, last) = Count(db, "open_interest");
                cards.Add(new
                {
                    title = "Total bets on the table (open interest)",
                    what = "How much money is currently IN positions per coin. Rising OI = new bets piling in; crashing OI = a flush just happened.",
                    receipt = $"{n:N0} hourly readings since {Ymd(first)} (exchange only shares ~30 days back — ours grows daily)",
                    read = "",
                    feeds = "Leverage-buildup features; the OI-flush detector."
                });
            }
            catch { }
        }
        catch { }

        return JsonSerializer.Serialize(new { type = "data-data", cards });
    }

    private static (long n, long first, long last) Count(SqliteConnection db, string table)
    {
        using var c = db.CreateCommand();
        c.CommandText = $"SELECT COUNT(*), MIN(ts), MAX(ts) FROM {table};";
        using var rd = c.ExecuteReader();
        if (rd.Read() && !rd.IsDBNull(0) && rd.GetInt64(0) > 0)
            return (rd.GetInt64(0), rd.GetInt64(1), rd.GetInt64(2));
        return (0, 0, 0);
    }

    private static string Ymd(long ts) =>
        ts <= 0 ? "—" : DateTimeOffset.FromUnixTimeMilliseconds(ts).UtcDateTime.ToString("MMM d, yyyy");

    private static string Age(long first)
    {
        if (first <= 0) return "—";
        double years = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - first) / 31_557_600_000.0;
        return years >= 1 ? $"{years:F1} years" : $"{years * 12:F0} months";
    }

    private static string Fresh(long last, long now, long staleMs) =>
        last <= 0 ? "" : (now - last <= staleMs ? "recording now" : $"last write {Ymd(last)}");
}
