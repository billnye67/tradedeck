using System.IO;
using System.Net.Http;
using System.Text.Json;
using Microsoft.Data.Sqlite;

namespace TradeDeck;

/// <summary>
/// INTEL LAYER — BRICK 1: crypto exchange announcements.
/// Polls Binance's public notice feeds (new listings + delistings)
/// every minute, stores every announcement permanently, surfaces new
/// ones in the alert drawer and out loud. The point: hear it from the
/// SOURCE in ~60s, not from Instagram after the move.
/// </summary>
public sealed class NewsService : IDisposable
{
    // Binance CMS catalogs: 48 = new listings, 161 = delistings
    private static readonly (long id, string cat)[] Catalogs =
        { (48, "LISTING"), (161, "DELISTING") };

    // SEC EDGAR current-filings feeds — the company lane (GoPro-catcher).
    // SC 13D = activist stake reveal; S-4 = merger paperwork.
    private static readonly (string type, string cat, string spoken)[] EdgarTypes =
    {
        ("SC 13D", "STAKE", "S.E.C. filing: activist stake"),
        ("S-4", "MERGER", "S.E.C. filing: merger paperwork"),
    };

    // broad crypto press, RSS — the actual NEWS feed
    private static readonly (string name, string url)[] RssFeeds =
    {
        ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
        ("Cointelegraph", "https://cointelegraph.com/rss"),
        ("The Block", "https://www.theblock.co/rss.xml"),
        ("Decrypt", "https://decrypt.co/feed"),
        ("Blockworks", "https://blockworks.co/feed"),
        ("DL News", "https://www.dlnews.com/arc/outboundfeeds/rss/"),
        ("CryptoSlate", "https://cryptoslate.com/feed/"),
        ("Bitcoin Mag", "https://bitcoinmagazine.com/feed"),
    };

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private readonly HashSet<string> _seen = new();
    private readonly HashSet<string> _cbProducts = new();
    private readonly HashSet<string> _upMarkets = new();
    private bool _marketsPrimed;
    private CancellationTokenSource? _cts;
    private bool _primed;

    public event Action<string>? Message;

    private static System.Speech.Synthesis.SpeechSynthesizer? _tts;
    public static void StopSpeaking()
    {
        try { _tts?.SpeakAsyncCancelAll(); } catch { }
    }

    private static void Speak(string text)
    {
        if (ScannerService.VoiceMuted) return;
        try
        {
            _tts ??= new System.Speech.Synthesis.SpeechSynthesizer { Rate = 1 };
            // replace, never queue — a reboot flood was stacking a
            // 20-minute spoken backlog; latest announcement wins
            _tts.SpeakAsyncCancelAll();
            _tts.SpeakAsync(text);
        }
        catch { }
    }

    private static string DbPath => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    public void Start()
    {
        if (_cts != null) return;
        _cts = new CancellationTokenSource();
        EnsureTable();
        LoadSeen();
        _ = Task.Run(() => Loop(_cts.Token));
    }

    public void Stop() { _cts?.Cancel(); _cts = null; }
    public void Dispose() => Stop();

    private void EnsureTable()
    {
        try
        {
            using var db = Open();
            using var cmd = db.CreateCommand();
            cmd.CommandText = """
                CREATE TABLE IF NOT EXISTS news_events (
                    source TEXT NOT NULL, code TEXT NOT NULL, ts INTEGER,
                    category TEXT, title TEXT, url TEXT,
                    PRIMARY KEY (source, code));
                """;
            cmd.ExecuteNonQuery();
            try
            {
                using var mig = db.CreateCommand();
                mig.CommandText = "ALTER TABLE news_events ADD COLUMN url TEXT;";
                mig.ExecuteNonQuery();
            }
            catch { /* column already there */ }
        }
        catch { }
    }

    private void LoadSeen()
    {
        try
        {
            using var db = Open();
            using var cmd = db.CreateCommand();
            cmd.CommandText = "SELECT code FROM news_events;";
            using var rd = cmd.ExecuteReader();
            while (rd.Read()) _seen.Add(rd.GetString(0));
        }
        catch { }
    }

    private async Task Loop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            int stored = 0;

            foreach (var (catalogId, cat) in Catalogs)
            {
                try
                {
                    string url =
                        "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query" +
                        $"?type=1&catalogId={catalogId}&pageNo=1&pageSize=10";

                    using var resp = await _http.GetAsync(url, ct);
                    resp.EnsureSuccessStatusCode();
                    using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));

                    var catalogs = doc.RootElement.GetProperty("data").GetProperty("catalogs");
                    if (catalogs.GetArrayLength() == 0) continue;
                    var articles = catalogs[0].GetProperty("articles");

                    foreach (var a in articles.EnumerateArray())
                    {
                        string code = a.GetProperty("code").GetString()!;
                        if (_seen.Contains(code)) continue;

                        string title = a.GetProperty("title").GetString() ?? "";
                        long ts = a.TryGetProperty("releaseDate", out var rd0) ? rd0.GetInt64() : 0;

                        using (var db = Open())
                        using (var cmd = db.CreateCommand())
                        {
                            cmd.CommandText = """
                                INSERT OR IGNORE INTO news_events (source, code, ts, category, title, url)
                                VALUES ('binance', $c, $t, $cat, $ti, $u);
                                """;
                            cmd.Parameters.AddWithValue("$c", code);
                            cmd.Parameters.AddWithValue("$t", ts);
                            cmd.Parameters.AddWithValue("$cat", cat);
                            cmd.Parameters.AddWithValue("$ti", title);
                            cmd.Parameters.AddWithValue("$u", $"https://www.binance.com/en/support/announcement/{code}");
                            cmd.ExecuteNonQuery();
                        }

                        _seen.Add(code);
                        stored++;

                        // only announce NEW arrivals after the first prime
                        // pass (startup backfill of the current page stays
                        // silent — old news is not news)
                        if (_primed)
                        {
                            Message?.Invoke(JsonSerializer.Serialize(new
                            {
                                type = "news",
                                category = cat,
                                title,
                                ts
                            }));

                            Speak($"Binance {(cat == "LISTING" ? "listing" : "delisting")} notice: " +
                                  (title.Length > 70 ? title[..70] : title));

                            if (cat == "DELISTING")
                                EmitAction(
                                    "RISK",
                                    title.Length > 40 ? title[..40] : title,
                                    "Delisted coins get force-settled and measured -17% vs BTC in the week after notice (exam era). " +
                                    "If you hold anything named here, exit before removal.",
                                    "MEASURED — act only if you hold it");
                        }
                    }
                }
                catch { /* feed hiccup; retry next cycle */ }
            }

            // ---- NEW MARKETS: Coinbase + Upbit product lists (the
            // sniper method — a new tradeable pair IS the listing) ----
            try
            {
                using var req = new HttpRequestMessage(HttpMethod.Get, "https://api.exchange.coinbase.com/products");
                req.Headers.UserAgent.ParseAdd("TradeDeck/1.0");
                using var resp = await _http.SendAsync(req, ct);
                resp.EnsureSuccessStatusCode();
                using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));

                foreach (var p in doc.RootElement.EnumerateArray())
                {
                    string id = p.GetProperty("id").GetString()!;
                    if (!_cbProducts.Add(id)) continue;

                    if (_marketsPrimed)
                        NewMarketEvent("coinbase", id,
                            $"Coinbase just listed {id} — US retail buying wave usually follows a new Coinbase pair");
                }
            }
            catch { }

            try
            {
                using var resp = await _http.GetAsync("https://api.upbit.com/v1/market/all", ct);
                resp.EnsureSuccessStatusCode();
                using var doc = JsonDocument.Parse(await resp.Content.ReadAsStringAsync(ct));

                foreach (var m in doc.RootElement.EnumerateArray())
                {
                    string id = m.GetProperty("market").GetString()!;
                    if (!_upMarkets.Add(id)) continue;

                    if (_marketsPrimed)
                        NewMarketEvent("upbit", id,
                            $"Upbit just listed {id} — Korean listings historically spike +10-20% in the first minutes");
                }
            }
            catch { }

            _marketsPrimed = true;

            // ---- SEC EDGAR: current filings (Atom) ----
            foreach (var (ftype, cat, spoken) in EdgarTypes)
            {
                try
                {
                    string url =
                        "https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent" +
                        $"&type={Uri.EscapeDataString(ftype)}&company=&dateb=&owner=include&count=20&output=atom";

                    using var req = new HttpRequestMessage(HttpMethod.Get, url);
                    // SEC requires an identifying user agent
                    req.Headers.UserAgent.ParseAdd("TradeDeck/1.0 (personal research; contact via github)");
                    using var resp = await _http.SendAsync(req, ct);
                    resp.EnsureSuccessStatusCode();

                    var doc = System.Xml.Linq.XDocument.Parse(await resp.Content.ReadAsStringAsync(ct));
                    System.Xml.Linq.XNamespace a = "http://www.w3.org/2005/Atom";

                    foreach (var entry in doc.Descendants(a + "entry").Take(20))
                    {
                        string link = entry.Element(a + "link")?.Attribute("href")?.Value ?? "";
                        if (link.Length == 0 || _seen.Contains(link)) continue;

                        string title = entry.Element(a + "title")?.Value?.Trim() ?? "";
                        long ts = 0;
                        if (DateTimeOffset.TryParse(entry.Element(a + "updated")?.Value, out var up))
                            ts = up.ToUnixTimeMilliseconds();

                        using (var db = Open())
                        using (var cmd = db.CreateCommand())
                        {
                            cmd.CommandText = """
                                INSERT OR IGNORE INTO news_events (source, code, ts, category, title, url)
                                VALUES ('edgar', $c, $t, $cat, $ti, $u);
                                """;
                            cmd.Parameters.AddWithValue("$c", link);
                            cmd.Parameters.AddWithValue("$t", ts);
                            cmd.Parameters.AddWithValue("$cat", cat);
                            cmd.Parameters.AddWithValue("$ti", title);
                            cmd.Parameters.AddWithValue("$u", link);
                            cmd.ExecuteNonQuery();
                        }

                        _seen.Add(link);

                        if (_primed)
                        {
                            Message?.Invoke(JsonSerializer.Serialize(new
                            { type = "news", category = cat, source = "EDGAR", title, ts }));

                            string company = title.Contains('-') ? title[(title.IndexOf('-') + 1)..].Trim() : title;
                            Speak($"{spoken}: {(company.Length > 60 ? company[..60] : company)}");

                            EmitAction(
                                "STOCK WATCH",
                                company.Length > 40 ? company[..40] : company,
                                cat == "STAKE"
                                    ? "Someone just disclosed a 5%+ stake — the GoPro/Markiplier signal. Read the filing, judge the buyer."
                                    : "Merger paperwork filed. Deal terms inside — target usually gaps toward the offer price.",
                                "INFO — stock play via your brokerage, not the crypto machine");
                        }
                    }
                }
                catch { /* SEC hiccup; next cycle */ }
            }

            // ---- broad press via RSS ----
            foreach (var (srcName, feedUrl) in RssFeeds)
            {
                try
                {
                    string xml = await _http.GetStringAsync(feedUrl, ct);
                    var doc = System.Xml.Linq.XDocument.Parse(xml);

                    foreach (var item in doc.Descendants("item").Take(15))
                    {
                        string link = item.Element("link")?.Value?.Trim() ?? "";
                        if (link.Length == 0 || _seen.Contains(link)) continue;

                        string title = item.Element("title")?.Value?.Trim() ?? "";
                        long ts = 0;
                        if (DateTimeOffset.TryParse(item.Element("pubDate")?.Value, out var pd))
                            ts = pd.ToUnixTimeMilliseconds();

                        using (var db = Open())
                        using (var cmd = db.CreateCommand())
                        {
                            cmd.CommandText = """
                                INSERT OR IGNORE INTO news_events (source, code, ts, category, title, url)
                                VALUES ($s, $c, $t, 'PRESS', $ti, $u);
                                """;
                            cmd.Parameters.AddWithValue("$s", srcName);
                            cmd.Parameters.AddWithValue("$c", link);
                            cmd.Parameters.AddWithValue("$t", ts);
                            cmd.Parameters.AddWithValue("$ti", title);
                            cmd.Parameters.AddWithValue("$u", link);
                            cmd.ExecuteNonQuery();
                        }

                        _seen.Add(link);

                        if (_primed)
                            Message?.Invoke(JsonSerializer.Serialize(new
                            { type = "news", category = "PRESS", source = srcName, title, ts }));
                        // press headlines are frequent: cards only, no voice
                    }
                }
                catch { /* one feed down; others carry on */ }
            }

            _primed = true;
            Message?.Invoke(JsonSerializer.Serialize(new { type = "recorder", layer = "news", rows = _seen.Count }));

            try { await Task.Delay(TimeSpan.FromSeconds(30), ct); } catch { break; }
        }
    }

    /// <summary>Every actionable event ships pre-graded: action, side,
    /// and the measured reason — our stats, not opinions.</summary>
    private void EmitAction(string action, string symbol, string why, string status)
    {
        Message?.Invoke(JsonSerializer.Serialize(new
        {
            type = "action",
            action,      // e.g. SHORT CANDIDATE / RISK / STOCK WATCH
            symbol,
            why,
            status       // MEASURED / TRYOUT / INFO
        }));
    }

    private void NewMarketEvent(string exchange, string marketId, string title)
    {
        long ts = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        try
        {
            using var db = Open();
            using var cmd = db.CreateCommand();
            cmd.CommandText = """
                INSERT OR IGNORE INTO news_events (source, code, ts, category, title, url)
                VALUES ($s, $c, $t, 'LISTING', $ti, '');
                """;
            cmd.Parameters.AddWithValue("$s", exchange);
            cmd.Parameters.AddWithValue("$c", $"{exchange}:{marketId}");
            cmd.Parameters.AddWithValue("$t", ts);
            cmd.Parameters.AddWithValue("$ti", title);
            cmd.ExecuteNonQuery();
        }
        catch { }

        Message?.Invoke(JsonSerializer.Serialize(new
        { type = "news", category = "LISTING", source = exchange, title, ts }));

        Speak($"New listing. {title}");

        if (exchange == "binance" || marketId.EndsWith("USDT"))
            EmitAction(
                "SHORT CANDIDATE",
                marketId,
                "Measured: new listings averaged -7% vs BTC over week one across 352 events, both eras. " +
                "Short after day-1 close, cover day 7.",
                "TRYOUT — 2024 ran red; paper-grade until the record proves it");
    }

    /// <summary>Coverage board: every specced intel source, LIVE or
    /// NOT BUILT, generated from code — never from anyone's memory.</summary>
    public static string CoverageJson()
    {
        var lanes = new object[]
        {
            new { lane = "CRYPTO", name = "Binance notices (listings/delistings)", live = true },
            new { lane = "CRYPTO", name = "Coinbase new-market tripwire", live = true },
            new { lane = "CRYPTO", name = "Upbit new-market tripwire", live = true },
            new { lane = "CRYPTO", name = "8 crypto newsrooms (press RSS)", live = true },
            new { lane = "CRYPTO", name = "Bybit / OKX / Bithumb listings", live = false },
            new { lane = "CRYPTO", name = "Stablecoin mint watcher", live = false },
            new { lane = "CRYPTO", name = "Exchange status pages", live = false },
            new { lane = "COMPANY", name = "SEC 13D activist stakes", live = true },
            new { lane = "COMPANY", name = "SEC S-4 merger filings", live = true },
            new { lane = "COMPANY", name = "Earnings calendar", live = false },
            new { lane = "COMPANY", name = "PR wires (releases as they drop)", live = false },
            new { lane = "COMPANY", name = "SEC 8-K (needs item filtering)", live = false },
            new { lane = "COMPANY", name = "Trading halts (NASDAQ/NYSE)", live = false },
            new { lane = "COMPANY", name = "FDA approvals", live = false },
            new { lane = "MACRO", name = "Fed / economic calendar", live = false },
            new { lane = "GEO", name = "Ship tracking (AIS)", live = false },
            new { lane = "GEO", name = "Flight tracking (ADS-B)", live = false },
            new { lane = "GEO", name = "NASA FIRMS / USGS feeds", live = false },
        };
        return JsonSerializer.Serialize(new { type = "news-coverage", lanes });
    }

    /// <summary>The stored feed, newest first, for the NEWS overlay.</summary>
    public static string BuildFeedJson()
    {
        var items = new List<object>();
        try
        {
            using var db = Open();
            using var cmd = db.CreateCommand();
            cmd.CommandText = """
                SELECT ts, category, title, source, url FROM news_events
                ORDER BY ts DESC LIMIT 150;
                """;
            using var rd = cmd.ExecuteReader();
            while (rd.Read())
                items.Add(new
                {
                    ts = rd.IsDBNull(0) ? 0L : rd.GetInt64(0),
                    category = rd.IsDBNull(1) ? "" : rd.GetString(1),
                    title = rd.IsDBNull(2) ? "" : rd.GetString(2),
                    source = rd.IsDBNull(3) ? "" : rd.GetString(3),
                    url = rd.IsDBNull(4) ? "" : rd.GetString(4)
                });
        }
        catch { }

        return JsonSerializer.Serialize(new { type = "news-feed", items });
    }

    private static SqliteConnection Open()
    {
        var db = new SqliteConnection($"Data Source={DbPath}");
        db.Open();
        using var p = db.CreateCommand();
        p.CommandText = "PRAGMA busy_timeout=5000;";
        p.ExecuteNonQuery();
        return db;
    }
}
