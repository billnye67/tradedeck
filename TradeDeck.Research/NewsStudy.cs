using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Data.Sqlite;

namespace TradeDeck.Research;

/// <summary>
/// NEWS -> MONEY BRIDGE.
///   newsfill   walk Binance's announcement archive back in time and
///              store every dated listing/delisting notice (the
///              history the live watcher can't see).
///   liststudy  event study on those events: for each listed symbol,
///              fetch its daily candles around the announcement and
///              measure drift at +1/+3/+7/+14 days IN EXCESS OF BTC;
///              for delistings, the bleed from notice to removal.
///              Learn era (pre-2024) vs exam era, no tuning.
/// </summary>
public static class NewsStudy
{
    private static readonly HttpClient Http = new() { Timeout = TimeSpan.FromSeconds(25) };

    private static string DbPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    // ------------------------------------------------------------
    // BACKFILL
    // ------------------------------------------------------------
    public static async Task Backfill()
    {
        using var db = Open();
        Exec(db, """
            CREATE TABLE IF NOT EXISTS news_events (
                source TEXT NOT NULL, code TEXT NOT NULL, ts INTEGER,
                category TEXT, title TEXT, url TEXT,
                PRIMARY KEY (source, code));
            """);

        foreach (var (catalogId, cat) in new[] { (48L, "LISTING"), (161L, "DELISTING") })
        {
            int stored = 0;

            for (int page = 1; page <= 60; page++)
            {
                string url =
                    "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query" +
                    $"?type=1&catalogId={catalogId}&pageNo={page}&pageSize=50";

                JsonDocument doc;
                try
                {
                    using var resp = await Http.GetAsync(url);
                    resp.EnsureSuccessStatusCode();
                    doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"  page {page}: {ex.Message} — stopping this catalog");
                    break;
                }

                using (doc)
                {
                    var catalogs = doc.RootElement.GetProperty("data").GetProperty("catalogs");
                    if (catalogs.GetArrayLength() == 0) break;
                    var articles = catalogs[0].GetProperty("articles");
                    if (articles.GetArrayLength() == 0) break;

                    foreach (var a in articles.EnumerateArray())
                    {
                        string code = a.GetProperty("code").GetString()!;
                        string title = a.GetProperty("title").GetString() ?? "";
                        long ts = a.TryGetProperty("releaseDate", out var rd) ? rd.GetInt64() : 0;

                        using var cmd = db.CreateCommand();
                        cmd.CommandText = """
                            INSERT OR IGNORE INTO news_events (source, code, ts, category, title, url)
                            VALUES ('binance', $c, $t, $cat, $ti, $u);
                            """;
                        cmd.Parameters.AddWithValue("$c", code);
                        cmd.Parameters.AddWithValue("$t", ts);
                        cmd.Parameters.AddWithValue("$cat", cat);
                        cmd.Parameters.AddWithValue("$ti", title);
                        cmd.Parameters.AddWithValue("$u", $"https://www.binance.com/en/support/announcement/{code}");
                        stored += cmd.ExecuteNonQuery();
                    }
                }

                Console.Write($"\r{cat}: page {page}, stored {stored}   ");
                await Task.Delay(300); // polite
            }
            Console.WriteLine();
        }

        using (var cmd = db.CreateCommand())
        {
            cmd.CommandText = """
                SELECT category, COUNT(*), MIN(ts), MAX(ts) FROM news_events
                WHERE source='binance' GROUP BY category;
                """;
            using var rd = cmd.ExecuteReader();
            Console.WriteLine();
            while (rd.Read())
                Console.WriteLine($"{rd.GetString(0),-10} {rd.GetInt64(1),5:N0} notices  " +
                    $"{Ymd(rd.GetInt64(2))} -> {Ymd(rd.GetInt64(3))}");
        }
    }

    // ------------------------------------------------------------
    // EVENT STUDY
    // ------------------------------------------------------------
    private sealed record Ev(long Ts, string Symbol, string Title, bool Perp = false);

    public static async Task ListStudy()
    {
        using var db = Open();
        Exec(db, """
            CREATE TABLE IF NOT EXISTS ev_klines (
                symbol TEXT NOT NULL, ts INTEGER NOT NULL, close REAL,
                PRIMARY KEY (symbol, ts));
            """);

        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();

        // ---- parse events from stored notices ----
        var listings = new List<Ev>();
        var delistings = new List<Ev>();

        using (var cmd = db.CreateCommand())
        {
            cmd.CommandText = "SELECT ts, category, title FROM news_events WHERE source='binance' AND ts > 0;";
            using var rd = cmd.ExecuteReader();
            while (rd.Read())
            {
                long ts = rd.GetInt64(0);
                string cat = rd.GetString(1);
                string title = rd.GetString(2);

                if (cat == "LISTING")
                {
                    // futures: "... Launch USDⓈ-Margined XXXUSDT Perpetual ..."
                    var m = Regex.Match(title, @"([A-Z0-9]{2,15})USDT\s+(?:Quanto\s+)?Perpetual", RegexOptions.IgnoreCase);
                    if (m.Success) { listings.Add(new Ev(ts, m.Groups[1].Value.ToUpper() + "USDT", title, Perp: true)); continue; }

                    // spot: "Binance Will List Something (XYZ)"
                    m = Regex.Match(title, @"Will List .*?\(([A-Z0-9]{2,10})\)");
                    if (m.Success) listings.Add(new Ev(ts, m.Groups[1].Value + "USDT", title));
                }
                else if (cat == "DELISTING" && title.Contains("Will Delist", StringComparison.OrdinalIgnoreCase))
                {
                    foreach (Match m in Regex.Matches(title, @"\b([A-Z0-9]{2,8})\b"))
                    {
                        string tok = m.Groups[1].Value;
                        if (tok is "ON" or "AND" or "WILL" or "DELIST" or "BINANCE" or "MARGIN" or "LOAN") continue;
                        if (Regex.IsMatch(tok, @"^\d")) continue;
                        delistings.Add(new Ev(ts, tok + "USDT", title));
                    }
                }
            }
        }

        Console.WriteLine($"parsed {listings.Count:N0} listing events, {delistings.Count:N0} delisting legs");
        Console.WriteLine("fetching daily candles per event (cached after first run)…");

        // BTC baseline — full history, paginated (the 40-candle cap
        // silently starved every event of its BTC comparison last run)
        Exec(db, "DELETE FROM ev_klines WHERE symbol='BTCUSDT';");
        {
            long cur = listings.Concat(delistings).Min(e => e.Ts) - 5L * 86_400_000;
            long end = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            int got = 0;
            while (cur < end)
            {
                string url = "https://fapi.binance.com/fapi/v1/klines" +
                    $"?symbol=BTCUSDT&interval=1d&startTime={cur}&limit=1000";
                using var resp = await Http.GetAsync(url);
                if (!resp.IsSuccessStatusCode) break;
                using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
                int len = doc.RootElement.GetArrayLength();
                if (len == 0) break;

                using var tx = db.BeginTransaction();
                foreach (var k in doc.RootElement.EnumerateArray())
                {
                    using var cmd = db.CreateCommand();
                    cmd.Transaction = tx;
                    cmd.CommandText = "INSERT OR IGNORE INTO ev_klines (symbol, ts, close) VALUES ('BTCUSDT', $t, $c);";
                    cmd.Parameters.AddWithValue("$t", k[0].GetInt64());
                    cmd.Parameters.AddWithValue("$c", double.Parse(k[4].GetString()!, System.Globalization.CultureInfo.InvariantCulture));
                    cmd.ExecuteNonQuery();
                }
                tx.Commit();
                got += len;
                cur = doc.RootElement[len - 1][0].GetInt64() + 86_400_000;
                await Task.Delay(150);
            }
            Console.WriteLine($"BTC baseline: {got:N0} daily candles");
        }

        // ---- listings: excess drift ----
        var horizons = new[] { 1, 3, 7, 14 };
        var res = new Dictionary<int, (List<double> learn, List<double> exam)>();
        foreach (int h in horizons) res[h] = (new List<double>(), new List<double>());
        int usable = 0, fetched = 0;

        foreach (var ev in listings)
        {
            bool ok = await EnsureDaily(db, ev.Symbol, ev.Ts - 3L * 86_400_000, ev.Ts + 20L * 86_400_000);
            if (!ok) continue;
            if (++fetched % 25 == 0) Console.Write($"\r  {fetched} symbols fetched   ");

            double? p0 = CloseOnOrAfter(db, ev.Symbol, ev.Ts);
            double? b0 = CloseOnOrAfter(db, "BTCUSDT", ev.Ts);
            if (p0 is null or 0 || b0 is null or 0) continue;

            bool any = false;
            foreach (int h in horizons)
            {
                double? ph = CloseOnOrAfter(db, ev.Symbol, ev.Ts + h * 86_400_000L);
                double? bh = CloseOnOrAfter(db, "BTCUSDT", ev.Ts + h * 86_400_000L);
                if (ph is null or 0 || bh is null or 0) continue;

                double excess = (ph.Value / p0.Value - 1) - (bh.Value / b0.Value - 1);
                (ev.Ts < eraSplit ? res[h].learn : res[h].exam).Add(excess * 100);
                any = true;
            }
            if (any) usable++;
        }

        Console.WriteLine($"\r  {usable:N0} listing events with usable candles          ");
        Console.WriteLine();
        Console.WriteLine("POST-LISTING DRIFT — excess return vs BTC (%, mean / median / win% / n)");
        Console.WriteLine($"{"horizon",-9} | {"LEARN",-34} | {"EXAM",-34}");
        Console.WriteLine(new string('-', 84));
        foreach (int h in horizons)
            Console.WriteLine($"D+{h,-7} | {Stats(res[h].learn),-34} | {Stats(res[h].exam),-34}");

        // ---- delistings: notice -> +7d bleed ----
        var dl = (learn: new List<double>(), exam: new List<double>());
        foreach (var ev in delistings)
        {
            bool ok = await EnsureDaily(db, ev.Symbol, ev.Ts - 3L * 86_400_000, ev.Ts + 10L * 86_400_000);
            if (!ok) continue;

            double? p0 = CloseOnOrAfter(db, ev.Symbol, ev.Ts);
            double? p7 = CloseOnOrAfter(db, ev.Symbol, ev.Ts + 7L * 86_400_000);
            double? b0 = CloseOnOrAfter(db, "BTCUSDT", ev.Ts);
            double? b7 = CloseOnOrAfter(db, "BTCUSDT", ev.Ts + 7L * 86_400_000);
            if (p0 is null or 0 || p7 is null or 0 || b0 is null or 0 || b7 is null or 0) continue;

            double excess = (p7.Value / p0.Value - 1) - (b7.Value / b0.Value - 1);
            (ev.Ts < eraSplit ? dl.learn : dl.exam).Add(excess * 100);
        }

        Console.WriteLine();
        Console.WriteLine("POST-DELISTING-NOTICE BLEED — excess vs BTC over next 7 days (%)");
        Console.WriteLine($"LEARN: {Stats(dl.learn)}");
        Console.WriteLine($"EXAM:  {Stats(dl.exam)}");
        Console.WriteLine();
        if (Fails.Count > 0)
        {
            Console.WriteLine();
            Console.WriteLine("fetch failures (why symbols were skipped):");
            foreach (var (r, n) in Fails.OrderByDescending(kv => kv.Value).Take(6))
                Console.WriteLine($"  {r}: {n:N0}");
        }

        Console.WriteLine();
        Console.WriteLine("read: a real effect shows the SAME SIGN in both eras with n big enough to matter.");
        Console.WriteLine("If it holds, next step is a frozen entry/exit spec and the full trial. If not, news stays context.");
    }

    // ------------------------------------------------------------
    // LISTING-SHORT TRIAL — frozen before running:
    //   universe: PERP listings only (shortable by definition)
    //   entry: SHORT at the D+1 daily close (skip listing-day chaos)
    //   exit:  D+7 close (D+14 variant reported, no picking)
    //   fees:  taker both sides. Funding not modeled (new listings
    //          usually pay shorts — unmodeled edge is in our favor,
    //          stated, not counted).
    // ------------------------------------------------------------
    public static async Task ListShort()
    {
        using var db = Open();
        long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero).ToUnixTimeMilliseconds();
        const double fees = 2 * 0.00045 * 100; // % round trip

        var events = new List<Ev>();
        using (var cmd = db.CreateCommand())
        {
            cmd.CommandText = "SELECT ts, title FROM news_events WHERE source='binance' AND category='LISTING' AND ts > 0;";
            using var rd = cmd.ExecuteReader();
            while (rd.Read())
            {
                var m = Regex.Match(rd.GetString(1), @"([A-Z0-9]{2,15})USDT\s+(?:Quanto\s+)?Perpetual", RegexOptions.IgnoreCase);
                if (m.Success)
                    events.Add(new Ev(rd.GetInt64(0), m.Groups[1].Value.ToUpper() + "USDT", rd.GetString(1), true));
            }
        }
        Console.WriteLine($"{events.Count:N0} perp listing events");

        var t7 = (learn: new List<(long ts, double r)>(), exam: new List<(long ts, double r)>());
        var t14 = (learn: new List<(long ts, double r)>(), exam: new List<(long ts, double r)>());

        foreach (var ev in events)
        {
            await EnsureDaily(db, ev.Symbol, ev.Ts - 2L * 86_400_000, ev.Ts + 20L * 86_400_000);

            double? e = CloseOnOrAfter(db, ev.Symbol, ev.Ts + 1L * 86_400_000);
            double? x7 = CloseOnOrAfter(db, ev.Symbol, ev.Ts + 7L * 86_400_000);
            double? x14 = CloseOnOrAfter(db, ev.Symbol, ev.Ts + 14L * 86_400_000);
            if (e is null or 0) continue;

            if (x7 is not null and not 0)
            {
                double r = (e.Value - x7.Value) / e.Value * 100 - fees;
                (ev.Ts < eraSplit ? t7.learn : t7.exam).Add((ev.Ts, Math.Clamp(r, -300, 95)));
            }
            if (x14 is not null and not 0)
            {
                double r = (e.Value - x14.Value) / e.Value * 100 - fees;
                (ev.Ts < eraSplit ? t14.learn : t14.exam).Add((ev.Ts, Math.Clamp(r, -300, 95)));
            }
        }

        void Report(string name, (List<(long ts, double r)> learn, List<(long ts, double r)> exam) t)
        {
            Console.WriteLine();
            Console.WriteLine(name);
            Console.WriteLine($"{"slice",-14} | {"n",5} {"avg%",7} {"med%",7} {"win%",6} {"total%",9} {"maxDD%",8}");
            Console.WriteLine(new string('-', 64));
            foreach (var (label, rows) in new[] { ("LEARN", t.learn), ("EXAM", t.exam) })
            {
                if (rows.Count == 0) { Console.WriteLine($"{label,-14} | 0"); continue; }
                var seq = rows.OrderBy(x => x.ts).Select(x => x.r).ToList();
                var srt = seq.OrderBy(x => x).ToList();
                double cum = 0, peak = 0, dd = 0;
                foreach (double r in seq) { cum += r; if (cum > peak) peak = cum; if (peak - cum > dd) dd = peak - cum; }
                Console.WriteLine($"{label,-14} | {seq.Count,5:N0} {seq.Average(),7:F2} {srt[srt.Count / 2],7:F2} " +
                    $"{100.0 * seq.Count(x => x > 0) / seq.Count,5:F0}% {seq.Sum(),9:F0} {dd,8:F0}");
            }
            Console.WriteLine("  by year (total %):");
            foreach (var g in t.learn.Concat(t.exam)
                .GroupBy(x => DateTimeOffset.FromUnixTimeMilliseconds(x.ts).Year).OrderBy(g => g.Key))
                Console.WriteLine($"    {g.Key}  n {g.Count(),4:N0}  {g.Sum(x => x.r),8:F0}%");
        }

        Report("SHORT AT D+1 CLOSE -> COVER AT D+7 (fees in, per-trade %)", t7);
        Report("SHORT AT D+1 CLOSE -> COVER AT D+14", t14);

        Console.WriteLine();
        Console.WriteLine("law bar: positive avg BOTH eras, year breadth, for at least one horizon.");
        Console.WriteLine("unmodeled: funding received on shorts (typically in our favor) and borrow limits on day-1 perps.");
    }

    // ------------------------------------------------------------
    private static string Stats(List<double> xs)
    {
        if (xs.Count == 0) return "n 0";
        // clip data-glitch outliers so one bad print can't own the mean
        var clipped = xs.Select(x => Math.Clamp(x, -95, 300)).OrderBy(x => x).ToList();
        int trim = clipped.Count / 20;
        var core = clipped.Skip(trim).Take(clipped.Count - 2 * trim).ToList();
        double mean = core.Count > 0 ? core.Average() : clipped.Average();
        double med = clipped[clipped.Count / 2];
        double win = 100.0 * clipped.Count(x => x > 0) / clipped.Count;
        return $"{mean,7:F2}% / {med,6:F2}% / {win,4:F0}% / n {clipped.Count:N0}";
    }

    private static double? CloseOnOrAfter(SqliteConnection db, string symbol, long ts)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = """
            SELECT close FROM ev_klines WHERE symbol=$s AND ts >= $t ORDER BY ts LIMIT 1;
            """;
        cmd.Parameters.AddWithValue("$s", symbol);
        cmd.Parameters.AddWithValue("$t", ts);
        object? r = cmd.ExecuteScalar();
        return r is double d ? d : r is long l ? l : null;
    }

    /// <summary>Daily closes for symbol in range, fetched once and cached. Spot first, futures fallback.</summary>
    private static async Task<bool> EnsureDaily(SqliteConnection db, string symbol, long from, long to)
    {
        using (var chk = db.CreateCommand())
        {
            chk.CommandText = "SELECT COUNT(*) FROM ev_klines WHERE symbol=$s AND ts BETWEEN $a AND $b;";
            chk.Parameters.AddWithValue("$s", symbol);
            chk.Parameters.AddWithValue("$a", from);
            chk.Parameters.AddWithValue("$b", to);
            if ((long)chk.ExecuteScalar()! > 3) return true; // cached
        }

        foreach (string baseUrl in new[]
        {
            "https://data-api.binance.vision/api/v3/klines",   // spot mirror, not geo-blocked
            "https://fapi.binance.com/fapi/v1/klines"
        })
        {
            try
            {
                string url = $"{baseUrl}?symbol={symbol}&interval=1d&startTime={from}&endTime={to}&limit=40";
                using var resp = await Http.GetAsync(url);
                if (!resp.IsSuccessStatusCode)
                {
                    Fail($"{(int)resp.StatusCode} {(baseUrl.Contains("fapi") ? "fapi" : "spot")}");
                    continue;
                }
                using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync());
                if (doc.RootElement.ValueKind != JsonValueKind.Array || doc.RootElement.GetArrayLength() == 0) continue;

                using var tx = db.BeginTransaction();
                foreach (var k in doc.RootElement.EnumerateArray())
                {
                    long ts = k[0].GetInt64();
                    double close = double.Parse(k[4].GetString()!, System.Globalization.CultureInfo.InvariantCulture);
                    using var cmd = db.CreateCommand();
                    cmd.Transaction = tx;
                    cmd.CommandText = "INSERT OR IGNORE INTO ev_klines (symbol, ts, close) VALUES ($s, $t, $c);";
                    cmd.Parameters.AddWithValue("$s", symbol);
                    cmd.Parameters.AddWithValue("$t", ts);
                    cmd.Parameters.AddWithValue("$c", close);
                    cmd.ExecuteNonQuery();
                }
                tx.Commit();
                await Task.Delay(120); // polite
                return true;
            }
            catch (Exception ex) { Fail(ex.GetType().Name); }
        }
        return false;
    }

    private static readonly Dictionary<string, int> Fails = new();
    private static void Fail(string reason)
    {
        Fails.TryGetValue(reason, out int n);
        Fails[reason] = n + 1;
    }

    private static string Ymd(long ts) =>
        DateTimeOffset.FromUnixTimeMilliseconds(ts).UtcDateTime.ToString("yyyy-MM-dd");

    private static SqliteConnection Open()
    {
        var db = new SqliteConnection($"Data Source={DbPath}");
        db.Open();
        using var p = db.CreateCommand();
        p.CommandText = "PRAGMA busy_timeout=5000;";
        p.ExecuteNonQuery();
        return db;
    }

    private static void Exec(SqliteConnection db, string sql)
    {
        using var cmd = db.CreateCommand();
        cmd.CommandText = sql;
        cmd.ExecuteNonQuery();
    }
}
