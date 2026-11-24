using TradeDeck.Research;

// Usage:
//   dotnet run                  -> BTCUSDT, 40 candidates, round 1
//   dotnet run SOLUSDT          -> SOLUSDT, 40 candidates, round 1
//   dotnet run SOLUSDT 60       -> SOLUSDT, 60 candidates, round 1
//   dotnet run BTCUSDT 40 2     -> round 2: a fresh, non-overlapping batch

string SourceName = Environment.GetEnvironmentVariable("TD_SOURCE") is string srcEnv && srcEnv.Length > 0 ? srcEnv : "binance_um"; // TD_SOURCE=dukascopy for forex/metals
const string Interval = "15m";
const int DefaultSampleCount = 40;

// Analysis mode: dotnet run analyze <path-to-grades.json>
if (args.Length >= 2 && args[0].Equals("analyze", StringComparison.OrdinalIgnoreCase))
{
    GradeAnalyzer.Run(args[1], SourceName, Interval);
    return;
}

// Forward-record audit: dotnet run replay <dir> <prefix>
if (args.Length >= 3 && args[0].Equals("replay", StringComparison.OrdinalIgnoreCase))
{
    Replay.Run(args[1], args[2]);
    return;
}

// Hunt round 3 calendar levels: dotnet run sessions S1|S2|S3|S4|S5  (spec: specs/hunt3-spec.md)
if (args.Length >= 2 && args[0].Equals("sessions", StringComparison.OrdinalIgnoreCase))
{
    SessionLevels.Run(SourceName, args[1].ToUpperInvariant());
    return;
}

// Hunt round 2 scale test: dotnet run scale 15m|1h|4h  (spec: reviews/hunt2-spec.md)
if (args.Length >= 1 && args[0].Equals("ifvg", StringComparison.OrdinalIgnoreCase))
{
    InvertedFvg.Run(SourceName);
    return;
}

if (args.Length >= 1 && args[0].Equals("decompose", StringComparison.OrdinalIgnoreCase))
{
    Decompose.Run(SourceName);
    return;
}

if (args.Length >= 2 && args[0].Equals("scale", StringComparison.OrdinalIgnoreCase))
{
    Scale.Run(SourceName, args[1]);
    return;
}

// Strategy #2 trial (compression breakout): dotnet run s2  [s2 audit prints sample trades]
if (args.Length >= 1 && args[0].Equals("s2", StringComparison.OrdinalIgnoreCase))
{
    Strategy2.Audit = args.Length >= 2 && args[1].Equals("audit", StringComparison.OrdinalIgnoreCase);

    Strategy2.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT" });

    return;
}

// Backfill Binance announcement archive: dotnet run newsfill
if (args.Length >= 1 && args[0].Equals("newsfill", StringComparison.OrdinalIgnoreCase))
{
    await NewsStudy.Backfill();
    return;
}

// Liquidation stream probe (60s of raw frames): dotnet run liqprobe
if (args.Length >= 1 && args[0].Equals("liqprobe", StringComparison.OrdinalIgnoreCase))
{
    foreach (string wsUrl in new[]
    {
        "wss://fstream.binance.com/stream?streams=!forceOrder@arr",
        "wss://fstream.binance.com/ws/btcusdt@forceOrder",
        "wss://fstream.binance.com/ws/btcusdt@aggTrade",
    })
    {
        Console.WriteLine($"--- {wsUrl}");
        try
        {
            using var ws = new System.Net.WebSockets.ClientWebSocket();
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(20));
            await ws.ConnectAsync(new Uri(wsUrl), cts.Token);
            Console.WriteLine("connected; listening 20s…");

            var buf = new byte[1 << 16];
            int frames = 0;
            while (!cts.IsCancellationRequested && frames < 3)
            {
                var sb2 = new System.Text.StringBuilder();
                System.Net.WebSockets.WebSocketReceiveResult r2;
                do
                {
                    r2 = await ws.ReceiveAsync(buf, cts.Token);
                    sb2.Append(System.Text.Encoding.UTF8.GetString(buf, 0, r2.Count));
                } while (!r2.EndOfMessage);
                frames++;
                string s = sb2.ToString();
                Console.WriteLine($"  frame {frames}: {(s.Length > 160 ? s[..160] : s)}");
            }
            if (frames == 0) Console.WriteLine("  20s of SILENCE — nothing arrived");
        }
        catch (OperationCanceledException) { Console.WriteLine("  20s of SILENCE — nothing arrived"); }
        catch (Exception ex) { Console.WriteLine($"  ERROR: {ex.Message}"); }
    }
    return;
}

// Prospector round 2 — private-tape scan: dotnet run prospect2
if (args.Length >= 1 && args[0].Equals("prospect2", StringComparison.OrdinalIgnoreCase))
{
    Prospector2.Run();
    return;
}

// Listing-short TRIAL (frozen spec): dotnet run listshort
if (args.Length >= 1 && args[0].Equals("listshort", StringComparison.OrdinalIgnoreCase))
{
    await NewsStudy.ListShort();
    return;
}

// Post-listing/delisting event study: dotnet run liststudy
if (args.Length >= 1 && args[0].Equals("liststudy", StringComparison.OrdinalIgnoreCase))
{
    await NewsStudy.ListStudy();
    return;
}

// Data receipts (what have the recorders actually stored): dotnet run datacheck
if (args.Length >= 1 && args[0].Equals("datacheck", StringComparison.OrdinalIgnoreCase))
{
    string dbs = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    using var dbc = new Microsoft.Data.Sqlite.SqliteConnection($"Data Source={dbs}");
    dbc.Open();

    Console.WriteLine($"database: {dbs}");
    Console.WriteLine();
    Console.WriteLine($"{"table",-14} {"rows",12}  {"first (UTC)",-17} {"last (UTC)",-17}");
    Console.WriteLine(new string('-', 64));

    foreach (string t in new[] { "candles", "funding_rates", "open_interest", "venue_snap", "agg_flow", "book_snap", "liq_events" })
    {
        try
        {
            using var c = dbc.CreateCommand();
            c.CommandText = $"SELECT COUNT(*), MIN(ts), MAX(ts) FROM {t};";
            using var rd = c.ExecuteReader();
            if (rd.Read() && !rd.IsDBNull(0) && rd.GetInt64(0) > 0)
            {
                string f = DateTimeOffset.FromUnixTimeMilliseconds(rd.GetInt64(1)).UtcDateTime.ToString("yyyy-MM-dd HH:mm");
                string l = DateTimeOffset.FromUnixTimeMilliseconds(rd.GetInt64(2)).UtcDateTime.ToString("yyyy-MM-dd HH:mm");
                Console.WriteLine($"{t,-14} {rd.GetInt64(0),12:N0}  {f,-17} {l,-17}");
            }
            else Console.WriteLine($"{t,-14} {"0",12}  (empty)");
        }
        catch { Console.WriteLine($"{t,-14} {"—",12}  (table not found)"); }
    }

    return;
}

// Shock fade trial (home-grown lead F3): dotnet run shockfade
if (args.Length >= 1 && args[0].Equals("shockfade", StringComparison.OrdinalIgnoreCase))
{
    ShockFade.Run(SourceName);
    return;
}

// Prospector (our-data anomaly mining): dotnet run prospect
if (args.Length >= 1 && args[0].Equals("prospect", StringComparison.OrdinalIgnoreCase))
{
    Prospector.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Funding dispersion portfolio: dotnet run fspread
if (args.Length >= 1 && args[0].Equals("fspread", StringComparison.OrdinalIgnoreCase))
{
    string dbs = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    FundingSpread.Run(
        dbs, SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT" });

    return;
}

// Crash-bounce mean reversion: dotnet run mrev
if (args.Length >= 1 && args[0].Equals("mrev", StringComparison.OrdinalIgnoreCase))
{
    Reversion.CrashBounce(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT" });

    return;
}

// Funding-extreme fade: dotnet run fext
if (args.Length >= 1 && args[0].Equals("fext", StringComparison.OrdinalIgnoreCase))
{
    Reversion.FundingFade(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT" });

    return;
}

// Slow momentum trial: dotnet run mom
if (args.Length >= 1 && args[0].Equals("mom", StringComparison.OrdinalIgnoreCase))
{
    Momentum.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "LINKUSDT" });

    return;
}

// Exit trailing experiment: dotnet run trail
if (args.Length >= 1 && args[0].Equals("trail", StringComparison.OrdinalIgnoreCase))
{
    ExitTrail.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Funding vs Strategy #1 feature test: dotnet run fslice
if (args.Length >= 1 && args[0].Equals("fslice", StringComparison.OrdinalIgnoreCase))
{
    string dbs = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    FundingSlice.Run(
        dbs, SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Executable per-market simulation: dotnet run sim
if (args.Length >= 1 && args[0].Equals("sim", StringComparison.OrdinalIgnoreCase))
{
    PositionSim.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Level ablation study (GPT audit spec): dotnet run levelablate
if (args.Length >= 1 && args[0].Equals("levelablate", StringComparison.OrdinalIgnoreCase))
{
    LevelAblation.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Level profiler (GPT's 7 structural features): dotnet run levelprofile
if (args.Length >= 1 && args[0].Equals("levelprofile", StringComparison.OrdinalIgnoreCase))
{
    LevelProfiler.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Level definition experiment: dotnet run levels
if (args.Length >= 1 && args[0].Equals("levels", StringComparison.OrdinalIgnoreCase))
{
    LevelExperiment.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Stop buffer experiment: dotnet run stops
if (args.Length >= 1 && args[0].Equals("stops", StringComparison.OrdinalIgnoreCase))
{
    StopBuffer.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Crowded funding unwind candidate: dotnet run unwind
if (args.Length >= 1 && args[0].Equals("unwind", StringComparison.OrdinalIgnoreCase))
{
    string dbu = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    FundingUnwind.Run(dbu, SourceName);
    return;
}

// Funding distribution: dotnet run fundingstats
if (args.Length >= 1 && args[0].Equals("fundingstats", StringComparison.OrdinalIgnoreCase))
{
    string dbf = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    FundingStats.Run(dbf);
    return;
}

// Positioning data layer: dotnet run collectfunding
if (args.Length >= 1 && args[0].Equals("collectfunding", StringComparison.OrdinalIgnoreCase))
{
    string dbp = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "TradeDeck", "Data", "history.sqlite");

    await FundingCollector.Run(dbp);
    return;
}

// HTF trend pullback candidate (GPT spec): dotnet run pullback [MARKET|ALL]
if (args.Length >= 1 && args[0].Equals("pullback", StringComparison.OrdinalIgnoreCase))
{
    string tp2 = args.Length > 1 ? args[1].ToUpperInvariant() : "ALL";

    string[] pbMarkets = tp2 == "ALL"
        ? new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                  "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT" }
        : new[] { tp2 };

    TrendPullback.Score(pbMarkets, SourceName);
    return;
}

// Volatility exhaustion candidate (GPT spec): dotnet run vx [MARKET|ALL]
if (args.Length >= 1 && args[0].Equals("vx", StringComparison.OrdinalIgnoreCase))
{
    string tv = args.Length > 1 ? args[1].ToUpperInvariant() : "ALL";

    string[] vxMarkets = tv == "ALL"
        ? new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                  "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT" }
        : new[] { tv };

    VolExhaustion.Score(vxMarkets, SourceName);
    return;
}

// Strategy #2 first grind: dotnet run s2 [MARKET|ALL]
if (args.Length >= 1 && args[0].Equals("s2", StringComparison.OrdinalIgnoreCase))
{
    string t2 = args.Length > 1 ? args[1].ToUpperInvariant() : "ALL";

    string[] s2Markets = t2 == "ALL"
        ? new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                  "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT" }
        : new[] { t2 };

    BreakoutStrategy.Score(s2Markets, SourceName);
    return;
}

// Adaptive exit challenger: dotnet run adaptive
if (args.Length >= 1 && args[0].Equals("adaptive", StringComparison.OrdinalIgnoreCase))
{
    LiquidityExit.Run(
        SourceName,
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Target optimizer: dotnet run targets
if (args.Length >= 1 && args[0].Equals("targets", StringComparison.OrdinalIgnoreCase))
{
    TargetOptimizer.Run(
        SourceName,
        "5m",
        new[] { "BTCUSDT", "ETHUSDT", "XRPUSDT" },
        s => s.FvgSizeRatio >= 0.35 &&
             Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level >= 0.0013);

    return;
}

// Journal mode: dotnet run journal
// The machine grades its own vaulted scanner calls.
if (args.Length >= 1 && args[0].Equals("journal", StringComparison.OrdinalIgnoreCase))
{
    string vault = Path.GetFullPath(Path.Combine(
        AppContext.BaseDirectory, "..", "..", "..", "..", "reviews", "vault"));

    await JournalEvaluator.Run(vault);
    return;
}

// Live mode: dotnet run live [risk$]
if (args.Length >= 1 && args[0].Equals("live", StringComparison.OrdinalIgnoreCase))
{
    double risk = args.Length > 1 && double.TryParse(args[1], out double rr)
        ? rr
        : 100;

    string logFolder = Path.GetFullPath(Path.Combine(
        AppContext.BaseDirectory, "..", "..", "..", "..", "reviews"));

    Directory.CreateDirectory(logFolder);

    await LiveScanner.Run(
        risk,
        Path.Combine(logFolder, "scanner-log.jsonl"));

    return;
}

// A/B mode: dotnet run ab
// Trigger definition A (deepest-wick derived) vs B (must form
// before first breach). Both run the full playbook pipeline,
// both eras. The better NET in the HELD-OUT era wins the spec.
if (args.Length >= 1 && args[0].Equals("ab", StringComparison.OrdinalIgnoreCase))
{
    string[] provenA = { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
    var rda = new HistoryReader();

    long eraSplitA = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
        .ToUnixTimeMilliseconds();

    bool Checklist((double resultR, double feeR, FullSetup s) t) =>
        t.s.FvgSizeRatio >= 0.35 &&
        Math.Abs(t.s.SweepExtreme - t.s.Raid.Level) / t.s.Raid.Level >= 0.0013;

    foreach (bool variantB in new[] { false, true })
    {
        SetupDetector.PreBreachTriggerOnly = variantB;

        var poolA = new List<(double resultR, double feeR, FullSetup s)>();

        foreach (string mkt in provenA)
        {
            var a15 = rda.GetAllCandles(SourceName, mkt, "15m");
            var a5 = rda.GetAllCandles(SourceName, mkt, "5m");

            var araids = SweepDetector.FindCandidates(mkt, a15);

            var asetups = araids
                .Select(rc => SetupDetector.Build(rc, a5))
                .ToList();

            BacktestScorer.Profile(mkt, asetups, a5, poolA);
        }

        Console.WriteLine();
        Console.WriteLine(variantB
            ? "VARIANT B — trigger must form BEFORE first breach"
            : "VARIANT A — trigger from TRUE deepest wick (current)");

        foreach (bool heldOut in new[] { false, true })
        {
            var era = poolA
                .Where(t => (t.s.Raid.SweepTimestamp >= eraSplitA) == heldOut)
                .ToList();

            var filt = era.Where(Checklist).ToList();

            string tag = heldOut ? "held-out 24-26" : "learn 20-23";

            if (filt.Count > 0)
            {
                double net = filt.Sum(t => t.resultR - t.feeR);

                Console.WriteLine(
                    $"   {tag} checklist | trades {filt.Count,5:N0} | " +
                    $"NET exp {net / filt.Count,6:F3}R | NET total {net,7:F1}R");
            }
        }
    }

    SetupDetector.PreBreachTriggerOnly = false;

    Console.WriteLine();
    Console.WriteLine("VERDICT RULE: better NET exp in the HELD-OUT era wins the spec.");
    return;
}

// Verify mode: dotnet run verify
// The exam: candidate filter (real FVG >= 0.35x, breach >= 0.13%)
// learned from the profiler must beat baseline on BOTH the learn
// era (2020-2023) and the held-out era (2024-2026), or it dies.
if (args.Length >= 1 && args[0].Equals("verify", StringComparison.OrdinalIgnoreCase))
{
    string[] provenV = { "BTCUSDT", "ETHUSDT", "XRPUSDT" };

    var rdv = new HistoryReader();
    var pool = new List<(double resultR, double feeR, FullSetup s)>();

    foreach (string mkt in provenV)
    {
        var v15 = rdv.GetAllCandles(SourceName, mkt, "15m");
        var v5 = rdv.GetAllCandles(SourceName, mkt, "5m");

        var vraids = SweepDetector.FindCandidates(mkt, v15);

        var vsetups = vraids
            .Select(rc => SetupDetector.Build(rc, v5))
            .ToList();

        BacktestScorer.Profile(mkt, vsetups, v5, pool);
    }

    long eraSplit = new DateTimeOffset(2024, 1, 1, 0, 0, 0, TimeSpan.Zero)
        .ToUnixTimeMilliseconds();

    bool PassesChecklist((double resultR, double feeR, FullSetup s) t) =>
        t.s.FvgSizeRatio >= 0.35 &&
        Math.Abs(t.s.SweepExtreme - t.s.Raid.Level) / t.s.Raid.Level >= 0.0013;

    void Row(string label, List<(double resultR, double feeR, FullSetup s)> set)
    {
        if (set.Count == 0) { Console.WriteLine($"   {label,-22} | no trades"); return; }

        double net = set.Sum(t => t.resultR - t.feeR);
        int wins = set.Count(t => t.resultR > 0);

        Console.WriteLine(
            $"   {label,-22} | trades {set.Count,5:N0} | win {100.0 * wins / set.Count,5:F1}% | " +
            $"NET exp {net / set.Count,6:F3}R | NET total {net,7:F1}R");
    }

    foreach (bool heldOut in new[] { false, true })
    {
        var era = pool
            .Where(t => (t.s.Raid.SweepTimestamp >= eraSplit) == heldOut)
            .ToList();

        Console.WriteLine();
        Console.WriteLine(heldOut
            ? "HELD-OUT ERA 2024-2026 (the exam)"
            : "LEARN ERA 2020-2023");

        Row("baseline (all)", era);
        Row("checklist filtered", era.Where(PassesChecklist).ToList());
    }

    Console.WriteLine();
    Console.WriteLine("VERDICT RULE: checklist must beat baseline NET exp in BOTH eras.");
    return;
}

// Profile mode: dotnet run profile
// Winner profiling across the playbook markets: which measured
// traits separate winning trades from losing ones.
if (args.Length >= 1 && args[0].Equals("profile", StringComparison.OrdinalIgnoreCase))
{
    string[] provenP = { "BTCUSDT", "ETHUSDT", "XRPUSDT" };

    var rdq = new HistoryReader();
    var sink = new List<(double, double, FullSetup)>();

    foreach (string mkt in provenP)
    {
        var q15 = rdq.GetAllCandles(SourceName, mkt, "15m");
        var q5 = rdq.GetAllCandles(SourceName, mkt, "5m");

        var qraids = SweepDetector.FindCandidates(mkt, q15);

        var qsetups = qraids
            .Select(rc => SetupDetector.Build(rc, q5))
            .ToList();

        BacktestScorer.Profile(mkt, qsetups, q5, sink);
        Console.WriteLine($"{mkt}: profiled.");
    }

    BacktestScorer.PrintProfile(sink);
    return;
}

// Playbook mode: dotnet run playbook
// Strategy #1 on the PROVEN markets only (net-positive after fees
// across 6y in the full 9-market test), scored at the 3R target.
if (args.Length >= 1 && args[0].Equals("playbook", StringComparison.OrdinalIgnoreCase))
{
    string[] proven = { "BTCUSDT", "ETHUSDT", "XRPUSDT" };

    var rdp = new HistoryReader();
    var summaries = new List<BacktestScorer.MarketSummary>();

    foreach (string mkt in proven)
    {
        var p15 = rdp.GetAllCandles(SourceName, mkt, "15m");
        var p5 = rdp.GetAllCandles(SourceName, mkt, "5m");

        var praids = SweepDetector.FindCandidates(mkt, p15);

        var psetups = praids
            .Select(rc => SetupDetector.Build(rc, p5))
            .ToList();

        summaries.Add(BacktestScorer.Score(mkt, psetups, p5));
        Console.WriteLine();
    }

    Console.WriteLine("==========================================");
    Console.WriteLine("PLAYBOOK — Strategy #1, proven markets, 3R");

    foreach (var s in summaries)
        Console.WriteLine($"   {s.Market,-9} | trades {s.Trades,5:N0} | net {s.NetR3,7:F1}R | maxDD {s.MaxDd3,6:F1}R");

    Console.WriteLine(
        $"   {"COMBINED",-9} | trades {summaries.Sum(s => s.Trades),5:N0} | " +
        $"net {summaries.Sum(s => s.NetR3),7:F1}R"
    );

    Console.WriteLine("==========================================");
    return;
}

// Score mode: dotnet run score [MARKET|ALL]
if (args.Length >= 1 && args[0].Equals("score", StringComparison.OrdinalIgnoreCase))
{
    string target = args.Length > 1 ? args[1].ToUpperInvariant() : "BTCUSDT";

    string[] scoreMarkets = target == "ALL"
        ? new[] { "BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT",
                  "LINKUSDT", "AVAXUSDT", "SUIUSDT", "HYPEUSDT" }
        : new[] { target };

    var rd0 = new HistoryReader();

    foreach (string mkt in scoreMarkets)
    {
        var k15 = rd0.GetAllCandles(SourceName, mkt, "15m");
        var k5 = rd0.GetAllCandles(SourceName, mkt, "5m");

        if (k15.Count == 0 || k5.Count == 0)
        {
            Console.WriteLine($"{mkt}: no data, skipped.");
            continue;
        }

        var raids0 = SweepDetector.FindCandidates(mkt, k15);

        var setups0 = raids0
            .Select(rc => SetupDetector.Build(rc, k5))
            .ToList();

        BacktestScorer.Score(mkt, setups0, k5);
        Console.WriteLine();
    }

    return;
}

// Full-setup mode: dotnet run setups [MARKET] [COUNT]
// Chains steps 2-5 on every detected raid, prints the funnel,
// and emits a review page of COMPLETE setups for eyeballing.
if (args.Length >= 1 &&
    (args[0].Equals("setups", StringComparison.OrdinalIgnoreCase) ||
     args[0].Equals("dump", StringComparison.OrdinalIgnoreCase)))
{
    string m2 = args.Length > 1 ? args[1].ToUpperInvariant() : "BTCUSDT";
    int count2 = args.Length > 2 && int.TryParse(args[2], out int c2) ? c2 : 12;

    var rd = new HistoryReader();

    Console.WriteLine();
    Console.WriteLine("TRADEDECK FULL-SETUP BUILDER");
    Console.WriteLine($"Market: {m2}");

    var m15 = rd.GetAllCandles(SourceName, m2, "15m");
    var m5 = rd.GetAllCandles(SourceName, m2, "5m");

    Console.WriteLine($"Loaded {m15.Count:N0} 15m / {m5.Count:N0} 5m candles.");

    var raids = SweepDetector.FindCandidates(m2, m15);

    var setups = raids
        .Select(rc => SetupDetector.Build(rc, m5))
        .ToList();

    Console.WriteLine();
    Console.WriteLine("STRATEGY #1 FUNNEL");

    foreach (var g in setups
        .GroupBy(s => s.Stage)
        .OrderBy(g => g.Key))
    {
        Console.WriteLine($"{g.Key,-24} {g.Count(),6:N0}");
    }

    var complete = setups
        .Where(s => s.Stage == SetupStage.Complete)
        .ToList();

    Console.WriteLine($"{"TOTAL RAIDS",-24} {setups.Count,6:N0}");
    Console.WriteLine($"{"COMPLETE SETUPS",-24} {complete.Count,6:N0}");

    if (complete.Count == 0)
    {
        Console.WriteLine("No complete setups to review.");
        return;
    }

    var picked = new List<FullSetup>();

    if (complete.Count <= count2)
    {
        picked.AddRange(complete);
    }
    else
    {
        double st = (double)complete.Count / count2;

        for (int i = 0; i < count2; i++)
            picked.Add(complete[(int)(i * st)]);
    }

    string folder2 = Path.GetFullPath(Path.Combine(
        AppContext.BaseDirectory, "..", "..", "..", "..", "reviews"));

    Directory.CreateDirectory(folder2);

    bool dump = args[0].Equals("dump", StringComparison.OrdinalIgnoreCase);

    string out2 = Path.Combine(
        folder2,
        dump ? $"setups-dump-{m2}.json" : $"setups-{m2}.html");

    File.WriteAllText(
        out2,
        dump
            ? SetupReviewPage.BuildJson(m2, picked, m5)
            : SetupReviewPage.Build(m2, picked, m5));

    Console.WriteLine();
    Console.WriteLine(dump
        ? "UPLOAD THIS FILE TO CLAUDE:"
        : "OPEN THIS FILE IN YOUR BROWSER:");
    Console.WriteLine(out2);

    return;
}

string market =
    args.Length > 0
        ? args[0].ToUpperInvariant()
        : "BTCUSDT";

int sampleCount =
    args.Length > 1 && int.TryParse(args[1], out int parsed)
        ? parsed
        : DefaultSampleCount;

int round =
    args.Length > 2 && int.TryParse(args[2], out int r)
        ? r
        : 1;

var reader = new HistoryReader();

Console.WriteLine();
Console.WriteLine("TRADEDECK SWEEP REVIEW BUILDER");
Console.WriteLine($"Database: {reader.DbPath}");
Console.WriteLine($"Market:   {market} {Interval}");
Console.WriteLine();

List<ResearchCandle> candles =
    reader.GetAllCandles(SourceName, market, Interval);

if (candles.Count == 0)
{
    Console.WriteLine(
        "NO CANDLES FOUND. Run TradeDeck.History first."
    );

    return;
}

Console.WriteLine(
    $"Loaded {candles.Count:N0} candles."
);

List<SweepCandidate> all =
    SweepDetector.FindCandidates(market, candles);

Console.WriteLine(
    $"Candidate raids found: {all.Count:N0} " +
    $"({all.Count(c => c.Direction == "HIGH"):N0} high-side, " +
    $"{all.Count(c => c.Direction == "LOW"):N0} low-side)"
);

if (all.Count == 0)
{
    Console.WriteLine("Nothing to review.");
    return;
}

// Sample evenly across the whole history so the review
// covers every year and market regime, not just one stretch.
var sampled = new List<SweepCandidate>();

if (all.Count <= sampleCount)
{
    sampled.AddRange(all);
}
else
{
    // Still evenly spread across history, but each round starts at
    // a different phase inside the stride, so round 2 shows a fresh
    // batch instead of repeating round 1. Deterministic per round.
    double step = (double)all.Count / sampleCount;

    double phase =
        step * (((round - 1) * 0.37) % 1.0);

    for (int i = 0; i < sampleCount; i++)
    {
        int idx =
            (int)(i * step + phase) % all.Count;

        sampled.Add(all[idx]);
    }
}

string html =
    ReviewPage.Build(market, sampled);

// Review pages live in <repo>\reviews, next to the projects.
string reviewFolder = Path.Combine(
    AppContext.BaseDirectory,
    "..", "..", "..", "..",
    "reviews"
);

reviewFolder = Path.GetFullPath(reviewFolder);

Directory.CreateDirectory(reviewFolder);

string outPath = Path.Combine(
    reviewFolder,
    $"review-{market}-round{round}.html"
);

File.WriteAllText(outPath, html);

Console.WriteLine();
Console.WriteLine(
    $"Review page: {sampled.Count} candidates sampled across " +
    $"{ToUtc(sampled[0].SweepTimestamp)} -> " +
    $"{ToUtc(sampled[^1].SweepTimestamp)}"
);

Console.WriteLine();
Console.WriteLine($"OPEN THIS FILE IN YOUR BROWSER:");
Console.WriteLine(outPath);

static string ToUtc(long unixMs) =>
    DateTimeOffset
        .FromUnixTimeMilliseconds(unixMs)
        .ToString("yyyy-MM-dd");
