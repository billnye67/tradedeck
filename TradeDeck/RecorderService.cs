using System.IO;
using System.Net.Http;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace TradeDeck;

/// <summary>
/// PHASE B RECORDERS — history for the layers nobody sells for free.
/// Runs inside the app; every day the app is open adds a day of
/// backtestable data. Three layers, GPT's top three:
///
///  venue_snap  (5 min)  Binance spot, Binance perp+funding+OI,
///                       Bybit perp+funding+OI, OKX perp, Hyperliquid mid.
///                       -> basis, cross-venue dispersion, venue lead/lag.
///  agg_flow    (1 min)  Hyperliquid trade tape: buyer-initiated vs
///                       seller-initiated volume and counts.
///                       -> who is actually crossing the spread.
///  book_snap   (1 min)  Hyperliquid L2: spread, depth within 25bp each side.
///                       -> how much resistance exists to a move.
///
/// Nothing here is used by any strategy yet. It is fuel for the grinder.
/// </summary>
public sealed class RecorderService : IDisposable
{
    private static readonly string[] Coins =
        { "BTC", "ETH", "SOL", "XRP", "DOGE", "LINK", "AVAX", "SUI", "HYPE" };

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private CancellationTokenSource? _cts;

    // per-coin 1-minute flow accumulators
    private readonly Dictionary<string, (double buyVol, double sellVol, int buyCnt, int sellCnt)> _flow = new();
    private readonly object _flowLock = new();

    public event Action<string>? Message;

    private static string DbPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    public void Start()
    {
        if (_cts != null) return;
        _cts = new CancellationTokenSource();
        EnsureTables();
        _ = Task.Run(() => VenueLoop(_cts.Token));
        _ = Task.Run(() => FlowSocketLoop(_cts.Token));
        _ = Task.Run(() => FlowFlushLoop(_cts.Token));
        _ = Task.Run(() => BookLoop(_cts.Token));
        _ = Task.Run(() => LiqPollLoop(_cts.Token));
    }

    public void Stop() { _cts?.Cancel(); _cts = null; }
    public void Dispose() => Stop();

    private void EnsureTables()
    {
        try
        {
            using var db = Open();
            Exec(db, """
                CREATE TABLE IF NOT EXISTS venue_snap (
                    venue TEXT NOT NULL, market TEXT NOT NULL, ts INTEGER NOT NULL,
                    price REAL, funding REAL, oi REAL, kind TEXT,
                    PRIMARY KEY (venue, market, ts));
                """);
            Exec(db, """
                CREATE TABLE IF NOT EXISTS agg_flow (
                    market TEXT NOT NULL, ts INTEGER NOT NULL,
                    buy_vol REAL, sell_vol REAL, buy_cnt INTEGER, sell_cnt INTEGER,
                    PRIMARY KEY (market, ts));
                """);
            Exec(db, """
                CREATE TABLE IF NOT EXISTS liq_events (
                    market TEXT NOT NULL, ts INTEGER NOT NULL,
                    side TEXT, qty REAL, price REAL, notional REAL,
                    PRIMARY KEY (market, ts, side, price));
                """);
            Exec(db, """
                CREATE TABLE IF NOT EXISTS book_snap (
                    market TEXT NOT NULL, ts INTEGER NOT NULL,
                    mid REAL, spread_bp REAL, bid_depth25 REAL, ask_depth25 REAL,
                    PRIMARY KEY (market, ts));
                """);
        }
        catch { }
    }

    // ------------------------------------------------------------
    // 1. multi-venue snapshots
    // ------------------------------------------------------------
    private async Task VenueLoop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            long ts = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 300_000) * 300_000;
            int rows = 0;

            foreach (string coin in Coins)
            {
                string sym = coin + "USDT";

                rows += await Try(async () =>
                {
                    // Binance spot
                    var j = await GetJson($"https://api.binance.com/api/v3/ticker/price?symbol={sym}", ct);
                    Save("binance_spot", sym, ts, Num(j, "price"), null, null, "spot");
                });

                rows += await Try(async () =>
                {
                    // Binance perp mark + funding
                    var j = await GetJson($"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={sym}", ct);
                    var o = await GetJson($"https://fapi.binance.com/fapi/v1/openInterest?symbol={sym}", ct);
                    Save("binance_perp", sym, ts, Num(j, "markPrice"), Num(j, "lastFundingRate"), Num(o, "openInterest"), "perp");
                });

                rows += await Try(async () =>
                {
                    // Bybit linear perp
                    var j = await GetJson($"https://api.bybit.com/v5/market/tickers?category=linear&symbol={sym}", ct);
                    var item = j.GetProperty("result").GetProperty("list")[0];
                    Save("bybit_perp", sym, ts, Num(item, "markPrice"), Num(item, "fundingRate"), Num(item, "openInterest"), "perp");
                });

                rows += await Try(async () =>
                {
                    // OKX swap
                    var j = await GetJson($"https://www.okx.com/api/v5/market/ticker?instId={coin}-USDT-SWAP", ct);
                    var item = j.GetProperty("data")[0];
                    Save("okx_perp", sym, ts, Num(item, "last"), null, null, "perp");
                });
            }

            // Hyperliquid mids, one call
            await Try(async () =>
            {
                using var content = new StringContent("{\"type\":\"allMids\"}", Encoding.UTF8, "application/json");
                using var resp = await _http.PostAsync("https://api.hyperliquid.xyz/info", content, ct);
                using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));
                foreach (string coin in Coins)
                    if (doc.RootElement.TryGetProperty(coin, out var p))
                        Save("hyperliquid_perp", coin + "USDT", ts, double.Parse(p.GetString()!, System.Globalization.CultureInfo.InvariantCulture), null, null, "perp");
                rows++;
            });

            Emit(new { type = "recorder", layer = "venues", rows });

            try { await Task.Delay(TimeSpan.FromMinutes(5), ct); } catch { break; }
        }
    }

    // ------------------------------------------------------------
    // 2. aggressor flow via Hyperliquid trades websocket
    // ------------------------------------------------------------
    private async Task FlowSocketLoop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                using var ws = new ClientWebSocket();
                await ws.ConnectAsync(new Uri("wss://api.hyperliquid.xyz/ws"), ct);

                foreach (string coin in Coins)
                {
                    string sub = JsonSerializer.Serialize(new { method = "subscribe", subscription = new { type = "trades", coin } });
                    await ws.SendAsync(Encoding.UTF8.GetBytes(sub), WebSocketMessageType.Text, true, ct);
                }

                var buf = new byte[1 << 16];
                var sb = new StringBuilder();

                while (ws.State == WebSocketState.Open && !ct.IsCancellationRequested)
                {
                    sb.Clear();
                    WebSocketReceiveResult r;
                    do
                    {
                        r = await ws.ReceiveAsync(buf, ct);
                        sb.Append(Encoding.UTF8.GetString(buf, 0, r.Count));
                    } while (!r.EndOfMessage);

                    if (r.MessageType == WebSocketMessageType.Close) break;

                    try
                    {
                        using var doc = JsonDocument.Parse(sb.ToString());
                        if (doc.RootElement.GetProperty("channel").GetString() != "trades") continue;

                        foreach (var t in doc.RootElement.GetProperty("data").EnumerateArray())
                        {
                            string coin = t.GetProperty("coin").GetString()!;
                            string side = t.GetProperty("side").GetString()!; // "B" buyer aggressor, "A" seller aggressor
                            double px = double.Parse(t.GetProperty("px").GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                            double sz = double.Parse(t.GetProperty("sz").GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                            double notional = px * sz;

                            lock (_flowLock)
                            {
                                _flow.TryGetValue(coin, out var f);
                                if (side == "B") { f.buyVol += notional; f.buyCnt++; }
                                else { f.sellVol += notional; f.sellCnt++; }
                                _flow[coin] = f;
                            }
                        }
                    }
                    catch { /* malformed frame */ }
                }
            }
            catch { }

            try { await Task.Delay(5000, ct); } catch { break; } // reconnect
        }
    }

    private async Task FlowFlushLoop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            // align to the minute
            long now = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long next = (now / 60_000 + 1) * 60_000;
            try { await Task.Delay((int)(next - now), ct); } catch { break; }

            long ts = next - 60_000;
            Dictionary<string, (double, double, int, int)> snapshot;
            lock (_flowLock) { snapshot = new(_flow); _flow.Clear(); }

            try
            {
                using var db = Open();
                using var tx = db.BeginTransaction();
                foreach (var (coin, f) in snapshot)
                {
                    using var cmd = db.CreateCommand();
                    cmd.Transaction = tx;
                    cmd.CommandText = """
                        INSERT OR REPLACE INTO agg_flow (market, ts, buy_vol, sell_vol, buy_cnt, sell_cnt)
                        VALUES ($m, $t, $bv, $sv, $bc, $sc);
                        """;
                    cmd.Parameters.AddWithValue("$m", coin + "USDT");
                    cmd.Parameters.AddWithValue("$t", ts);
                    cmd.Parameters.AddWithValue("$bv", f.Item1);
                    cmd.Parameters.AddWithValue("$sv", f.Item2);
                    cmd.Parameters.AddWithValue("$bc", f.Item3);
                    cmd.Parameters.AddWithValue("$sc", f.Item4);
                    cmd.ExecuteNonQuery();
                }
                tx.Commit();

                Emit(new { type = "recorder", layer = "flow", rows = snapshot.Count });
            }
            catch { }
        }
    }

    // ------------------------------------------------------------
    // 3. book depth snapshots via Hyperliquid l2Book
    // ------------------------------------------------------------
    private async Task BookLoop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            long ts = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 60_000) * 60_000;
            int rows = 0;

            foreach (string coin in Coins)
            {
                rows += await Try(async () =>
                {
                    using var content = new StringContent(
                        JsonSerializer.Serialize(new { type = "l2Book", coin }), Encoding.UTF8, "application/json");
                    using var resp = await _http.PostAsync("https://api.hyperliquid.xyz/info", content, ct);
                    using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));

                    var levels = doc.RootElement.GetProperty("levels");
                    var bids = levels[0]; var asks = levels[1];
                    if (bids.GetArrayLength() == 0 || asks.GetArrayLength() == 0) return;

                    double bestBid = P(bids[0], "px"), bestAsk = P(asks[0], "px");
                    double mid = (bestBid + bestAsk) / 2;
                    double spreadBp = (bestAsk - bestBid) / mid * 10_000;

                    double bidDepth = 0, askDepth = 0;
                    foreach (var l in bids.EnumerateArray()) { double px = P(l, "px"); if (px >= mid * (1 - 0.0025)) bidDepth += px * P(l, "sz"); }
                    foreach (var l in asks.EnumerateArray()) { double px = P(l, "px"); if (px <= mid * (1 + 0.0025)) askDepth += px * P(l, "sz"); }

                    using var db = Open();
                    using var cmd = db.CreateCommand();
                    cmd.CommandText = """
                        INSERT OR REPLACE INTO book_snap (market, ts, mid, spread_bp, bid_depth25, ask_depth25)
                        VALUES ($m, $t, $mid, $sp, $bd, $ad);
                        """;
                    cmd.Parameters.AddWithValue("$m", coin + "USDT");
                    cmd.Parameters.AddWithValue("$t", ts);
                    cmd.Parameters.AddWithValue("$mid", mid);
                    cmd.Parameters.AddWithValue("$sp", spreadBp);
                    cmd.Parameters.AddWithValue("$bd", bidDepth);
                    cmd.Parameters.AddWithValue("$ad", askDepth);
                    cmd.ExecuteNonQuery();
                });
            }

            Emit(new { type = "recorder", layer = "book", rows });
            try { await Task.Delay(TimeSpan.FromMinutes(1), ct); } catch { break; }
        }
    }

    // ------------------------------------------------------------
    // 4. forced liquidations via Binance futures forceOrder stream
    // ------------------------------------------------------------
    private static void LiqLog(string msg)
    {
        try
        {
            string dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
                "TradeDeck", "reviews", "vault");
            Directory.CreateDirectory(dir);
            File.AppendAllText(Path.Combine(dir, "liq.log"),
                $"{DateTime.UtcNow:yyyy-MM-dd HH:mm:ss}  {msg}\n");
        }
        catch { }
    }

    private async Task LiqPollLoop(CancellationToken ct)
    {
        // Binance's websocket connects through the VPN but passes no
        // frames (proven by liqprobe: even btcusdt@aggTrade is silent).
        // OKX publishes recent liquidations over plain REST — the
        // channel that demonstrably works here — so the camera moved
        // to OKX's window. Same signal class: forced closes on our
        // coins, which side, how big.
        var unders = Coins
            .Where(c => c != "HYPE") // not listed on OKX
            .Select(c => (coin: c, uly: $"{c}-USDT"))
            .ToList();

        LiqLog("poll mode: OKX liquidation-orders REST, 60s cycle");
        bool first = true;

        while (!ct.IsCancellationRequested)
        {
            int stored = 0;

            foreach (var (coin, uly) in unders)
            {
                try
                {
                    string url =
                        "https://www.okx.com/api/v5/public/liquidation-orders" +
                        $"?instType=SWAP&state=filled&uly={uly}";

                    using var resp = await _http.GetAsync(url, ct);
                    if (!resp.IsSuccessStatusCode) continue;
                    using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));

                    if (!doc.RootElement.TryGetProperty("data", out var data)) continue;

                    foreach (var inst in data.EnumerateArray())
                    {
                        if (!inst.TryGetProperty("details", out var details)) continue;

                        foreach (var d in details.EnumerateArray())
                        {
                            double pxv = double.Parse(d.GetProperty("bkPx").GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                            double sz = double.Parse(d.GetProperty("sz").GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                            long ts = long.Parse(d.GetProperty("ts").GetString()!);
                            // OKX: a forced BUY closes a trapped SHORT
                            string side = d.GetProperty("side").GetString() == "buy" ? "SHORT_LIQ" : "LONG_LIQ";

                            using var db = Open();
                            using var cmd = db.CreateCommand();
                            cmd.CommandText = """
                                INSERT OR IGNORE INTO liq_events (market, ts, side, qty, price, notional)
                                VALUES ($m, $t, $s, $q, $p, $n);
                                """;
                            cmd.Parameters.AddWithValue("$m", coin + "USDT");
                            cmd.Parameters.AddWithValue("$t", ts);
                            cmd.Parameters.AddWithValue("$s", side);
                            cmd.Parameters.AddWithValue("$q", sz);
                            cmd.Parameters.AddWithValue("$p", pxv);
                            cmd.Parameters.AddWithValue("$n", sz * pxv);
                            stored += cmd.ExecuteNonQuery();
                        }
                    }
                }
                catch (Exception ex) { LiqLog($"{coin}: {ex.Message}"); }
            }

            if (first && stored > 0)
            {
                first = false;
                LiqLog($"first wrecks captured: {stored} events");
            }

            try
            {
                using var db = Open();
                using var cmd = db.CreateCommand();
                cmd.CommandText = "SELECT COUNT(*) FROM liq_events;";
                long total = (long)cmd.ExecuteScalar()!;
                Emit(new { type = "recorder", layer = "liq", rows = total });
            }
            catch { }

            try { await Task.Delay(TimeSpan.FromSeconds(60), ct); } catch { break; }
        }
    }

    // ------------------------------------------------------------
    // helpers
    // ------------------------------------------------------------
    private static double P(JsonElement l, string k) =>
        double.Parse(l.GetProperty(k).GetString()!, System.Globalization.CultureInfo.InvariantCulture);

    private static double? Num(JsonElement e, string k)
    {
        if (!e.TryGetProperty(k, out var v)) return null;
        string? s = v.ValueKind == JsonValueKind.String ? v.GetString() : v.GetRawText();
        return double.TryParse(s, System.Globalization.NumberStyles.Float,
            System.Globalization.CultureInfo.InvariantCulture, out double d) ? d : null;
    }

    private async Task<JsonElement> GetJson(string url, CancellationToken ct)
    {
        using var resp = await _http.GetAsync(url, ct);
        resp.EnsureSuccessStatusCode();
        using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));
        return doc.RootElement.Clone();
    }

    private static async Task<int> Try(Func<Task> f)
    {
        try { await f(); return 1; } catch { return 0; }
    }

    private void Save(string venue, string market, long ts, double? price, double? funding, double? oi, string kind)
    {
        using var db = Open();
        using var cmd = db.CreateCommand();
        cmd.CommandText = """
            INSERT OR REPLACE INTO venue_snap (venue, market, ts, price, funding, oi, kind)
            VALUES ($v, $m, $t, $p, $f, $o, $k);
            """;
        cmd.Parameters.AddWithValue("$v", venue);
        cmd.Parameters.AddWithValue("$m", market);
        cmd.Parameters.AddWithValue("$t", ts);
        cmd.Parameters.AddWithValue("$p", (object?)price ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$f", (object?)funding ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$o", (object?)oi ?? DBNull.Value);
        cmd.Parameters.AddWithValue("$k", kind);
        cmd.ExecuteNonQuery();
    }

    private static SqliteConnection Open()
    {
        var db = new SqliteConnection($"Data Source={DbPath}");
        db.Open();

        // several loops write concurrently; WAL + busy wait so no
        // snapshot is ever dropped to a transient lock
        using var p = db.CreateCommand();
        p.CommandText = "PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;";
        p.ExecuteNonQuery();

        return db;
    }

    private static void Exec(SqliteConnection db, string sql)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }

    private void Emit(object o) => Message?.Invoke(JsonSerializer.Serialize(o));
}
