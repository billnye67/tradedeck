using System.IO;
using System.Net.Http;
using System.Text.Json;
using TradeDeck.Research;

namespace TradeDeck;

/// <summary>
/// In-app live scanner. Same locked Strategy #1 spec + Playbook
/// Rule #1 as TradeDeck.Research (referenced project — single
/// source of truth). Runs on a background task; reports through
/// callbacks so the WebView UI can render it.
/// </summary>
public sealed class ScannerService : IDisposable
{
    // ALL markets stay scanned — a market is never globally cut,
    // because future strategies may verify on it. Alerting is
    // per-strategy: Strategy #1 is muted on markets where its own
    // backtest proved negative edge. The setup panel still tracks
    // every market.
    private static readonly Dictionary<string, string> MarketTiers = new()
    {
        ["BTCUSDT"] = "PLAYBOOK",
        ["ETHUSDT"] = "PLAYBOOK",
        ["XRPUSDT"] = "PLAYBOOK",
        ["SOLUSDT"] = "RETIRED (tryout ended 2026-10-13: -10.6R over 60 paper trades, backtest flat)",
        ["DOGEUSDT"] = "RETIRED (tryout ended 2026-10-13: -10.6R over 60 paper trades, backtest flat)",
        ["LINKUSDT"] = "RETIRED (tryout ended 2026-10-13: -10.6R over 60 paper trades, backtest flat)",
        ["HYPEUSDT"] = "RETIRED (tryout ended 2026-10-13: -10.6R over 60 paper trades, backtest flat)",
        ["AVAXUSDT"] = "NEGATIVE EDGE (backtest loses)",
        ["SUIUSDT"] = "NEGATIVE EDGE (backtest loses)"
    };

    private static readonly string[] Markets =
        MarketTiers.Keys.ToArray();

    private const int PollSeconds = 60;
    private const double LookbackDays = 3.5;
    private const long FiveMs = 5 * 60 * 1000;
    private const long FifteenMs = 15 * 60 * 1000;

    // Playbook Rule #1 (out-of-sample verified)
    private const double MinFvgRatio = 0.35;
    private const double MinBreachPct = 0.0013;

    private readonly HttpClient _http = new()
    {
        Timeout = TimeSpan.FromMinutes(1)
    };

    private readonly HashSet<string> _alerted = new();
    private readonly long _startedUtcMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
    private readonly HashSet<string> _suppressed = new();
    private DateTime _lastGrade = DateTime.MinValue;

    // Vault: every alert saved as data (journal.jsonl) + an
    // annotated chart snapshot, so the machine can evaluate its
    // own calls later (dotnet run journal in TradeDeck.Research).
    private static readonly string VaultFolder =
        Path.GetFullPath(Path.Combine(
            AppContext.BaseDirectory,
            "..", "..", "..", "..", "reviews", "vault"));

    private CancellationTokenSource? _cts;

    public double RiskDollars { get; set; } = 100;
    public BrokerExecutor? Executor { get; set; }
    /// <summary>2026-10-22: every live account gets every call (two Broker accounts, one login). Empty = just Executor.</summary>
    public List<BrokerExecutor> Executors { get; } = new();
    private IEnumerable<BrokerExecutor> Execs => Executors.Count > 0 ? Executors : (Executor != null ? new[] { Executor } : Array.Empty<BrokerExecutor>());

    // A7 risk engine caps (set from the UI)
    public double DailyLossCapR { get; set; } = 3.0;
    public int MaxOpenCalls { get; set; } = 2;

    private (bool locked, string reason) LockState()
    {
        var (todayNetR, openCalls) = AppJournal.GetRiskState();
        // with the executor on, the cap is about the ACCOUNT: count open calls on its markets only (a SOL paper trade held a slot, 2026-10-06)
        if (Executor != null && Executor.Mode != "off")
            openCalls = Executor.Markets.Count(m => AppJournal.HasOpenCall(m));

        if (todayNetR <= -DailyLossCapR)
            return (true, $"DAILY CAP: {todayNetR:+0.0;-0.0}R today (cap -{DailyLossCapR:0.0}R)");

        if (openCalls >= MaxOpenCalls)
            return (true, $"OPEN CAP: {openCalls} open calls (max {MaxOpenCalls})");

        return (false, $"today {todayNetR:+0.0;-0.0}R | open {openCalls}/{MaxOpenCalls}");
    }

    /// <summary>JSON payloads for the web UI.</summary>
    public event Action<string>? Message;

    /// <summary>Global voice kill-switch (topbar speaker button).</summary>
    public static volatile bool VoiceMuted;

    private static System.Speech.Synthesis.SpeechSynthesizer? _tts;
    public static void StopSpeaking()
    {
        try { _tts?.SpeakAsyncCancelAll(); } catch { }
    }

    private static void Speak(string text)
    {
        if (VoiceMuted) return;
        try
        {
            _tts ??= new System.Speech.Synthesis.SpeechSynthesizer { Rate = 1 };
            _tts.SpeakAsyncCancelAll();
            _tts.SpeakAsync(text);
        }
        catch { /* voice is best-effort */ }
    }

    public bool IsRunning => _cts != null;

    public void Start(double riskDollars)
    {
        if (IsRunning) return;

        // Survive restarts: re-seed dedup keys from the journal so
        // the same setup is never re-alerted or re-journaled after
        // an app relaunch (was double-counting losses in RECORD).
        try
        {
            string jp = Path.Combine(VaultFolder, "journal.jsonl");

            if (File.Exists(jp))
            {
                foreach (string line in File.ReadAllLines(jp))
                {
                    if (string.IsNullOrWhiteSpace(line)) continue;

                    try
                    {
                        using var doc = JsonDocument.Parse(line);
                        var e = doc.RootElement;

                        // rows written before sweepTs existed can't be
                        // seeded; skip them, never abort the whole load
                        if (!e.TryGetProperty("sweepTs", out var st)) continue;

                        string k =
                            $"{e.GetProperty("market").GetString()}|" +
                            $"{(e.GetProperty("direction").GetString() == "SHORT" ? "HIGH" : "LOW")}|" +
                            $"{st.GetInt64()}|" +
                            $"{e.GetProperty("status").GetString()}";

                        _alerted.Add(k);
                    }
                    catch { /* one bad line must not kill seeding */ }
                }
            }
        }
        catch { /* journal unreadable = start fresh */ }

        RiskDollars = riskDollars;
        _cts = new CancellationTokenSource();

        _ = Task.Run(() => Loop(_cts.Token));

        Emit(new { type = "scanner-state", running = true, risk = RiskDollars });
    }

    public void Stop()
    {
        _cts?.Cancel();
        _cts = null;

        Emit(new { type = "scanner-state", running = false });
    }

    private async Task Loop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            foreach (string market in Markets)
            {
                if (ct.IsCancellationRequested) break;

                try
                {
                    await ScanMarket(market, ct);
                }
                catch (Exception ex)
                {
                    Emit(new
                    {
                        type = "scanner-status",
                        market,
                        status = "ERROR",
                        detail = ex.Message
                    });
                }
            }

            // AUTO-GRADE every scan: the moment a stop or 3R is hit on a
            // closed candle, the trade grades and the slot frees. No
            // ghost positions, no waiting on the RECORD button.
            if ((DateTime.UtcNow - _lastGrade).TotalSeconds >= 55)
            {
                _lastGrade = DateTime.UtcNow;
                try { await AppJournal.GetRecordJsonAsync(_http); }
                catch { }
            }

            var (lk, why) = LockState();

            Emit(new
            {
                type = "scanner-heartbeat",
                utc = DateTime.UtcNow.ToString("HH:mm:ss"),
                seen = _alerted.Count,
                locked = lk,
                risk = why
            });

            try
            {
                await Task.Delay(
                    TimeSpan.FromSeconds(PollSeconds), ct);
            }
            catch (TaskCanceledException)
            {
                break;
            }
        }
    }

    private async Task ScanMarket(string market, CancellationToken ct)
    {
        long start = DateTimeOffset.UtcNow
            .AddDays(-LookbackDays)
            .ToUnixTimeMilliseconds();

        List<ResearchCandle> five =
            await FetchClosed5m(market, start, ct);

        if (five.Count < 400)
        {
            Emit(new { type = "scanner-status", market, status = "NO DATA" });
            return;
        }

        List<ResearchCandle> fifteen = BuildFifteen(five);

        // frozen candidate (D_ONLY) drives detection; RAW and OLD_8H are
        // computed as shadow labels so the forward record can later
        // compare level definitions on the SAME future (GPT audit).
        SweepDetector.LevelMode = SweepDetector.LevelModeKind.D_ONLY;
        var raids = SweepDetector.FindCandidates(market, fifteen);

        SweepDetector.LevelMode = SweepDetector.LevelModeKind.RAW;
        var rawKeys = SweepDetector.FindCandidates(market, fifteen)
            .Select(r => $"{r.Direction}|{r.Level}|{r.SweepTimestamp}").ToHashSet();

        SweepDetector.LevelMode = SweepDetector.LevelModeKind.OLD_8H;
        var oldKeys = SweepDetector.FindCandidates(market, fifteen)
            .Select(r => $"{r.Direction}|{r.Level}|{r.SweepTimestamp}").ToHashSet();

        SweepDetector.LevelMode = SweepDetector.LevelModeKind.D_ONLY;

        bool anyLive = false;

        // Track the most advanced FRESH setup for the live panel.
        FullSetup? best = null;
        int bestScore = -1;
        long freshCutoff = DateTimeOffset.UtcNow
            .AddHours(-6).ToUnixTimeMilliseconds();

        foreach (SweepCandidate raid in raids)
        {
            FullSetup s = SetupDetector.Build(raid, five);

            // Dead setups leave the panel: wick retaken before
            // entry, breakout (no reclaim), or FVG expired ~3h
            // unfilled. Only live progressions are shown.
            // wick retaken at ANY pre-entry stage = dead
            bool wickRetaken =
                s.Stage != SetupStage.Complete &&
                five.Count > 0 &&
                (s.Raid.Direction == "HIGH"
                    ? five[^1].High > s.SweepExtreme
                    : five[^1].Low < s.SweepExtreme);

            // displacement window expired with no displacement = dead
            bool dispExpired =
                s.DisplacementIndex5m < 0 &&
                s.TriggerIndex5m >= 0 &&
                DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                    - s.Raid.SweepTimestamp > 4 * 60 * 60 * 1000;

            bool dead =
                wickRetaken ||
                dispExpired ||
                s.Stage == SetupStage.BecameBreakout ||
                s.Stage == SetupStage.FvgInverted ||
                s.Stage == SetupStage.InvalidatedBeforeEntry ||
                (s.FvgMiddleIndex5m >= 0 &&
                 s.Stage != SetupStage.Complete &&
                 DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                     - five[s.FvgMiddleIndex5m].Timestamp
                     > 3 * 60 * 60 * 1000);

            // an armed resting entry on a setup that just died gets pulled
            if (dead)
                foreach (var __e in Execs) __e.OnDead(market, s.Raid.SweepTimestamp,
                    wickRetaken ? "sweep wick retaken before entry"
                    : s.Stage == SetupStage.FvgInverted ? "FVG inverted"
                    : s.Stage == SetupStage.BecameBreakout ? "became breakout"
                    : s.Stage == SetupStage.InvalidatedBeforeEntry ? "invalidated before entry"
                    : "FVG expired unfilled");

            // Panel (chart pins + stage header) only shows setups the strategy could actually take:
            // once an FVG exists it must pass the same size/breach rules as a call, and a COMPLETE
            // setup stays on the panel only if it was really called (otherwise a filtered setup sat
            // there as "7/7 waiting" for six hours — the user caught this on XRP, 2026-10-03).
            bool panelOk = true; string panelWhy = "";
            {
                // breach and tier are known at sweep time: filter at EVERY stage (ETH 2026-10-06: a 10bp breach showed as 4/7 twice, then vanished)
                double bp = Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level;
                if (bp < MinBreachPct) { panelOk = false; panelWhy = $"breach {bp * 1e4:F0}bp < {MinBreachPct * 1e4:F0}bp minimum"; }
                if (MarketTiers[market].StartsWith("NEGATIVE")) { panelOk = false; panelWhy = "negative-edge market"; }
                if (MarketTiers[market].StartsWith("RETIRED")) { panelOk = false; panelWhy = "tryout retired"; }
            }
            if (s.FvgMiddleIndex5m >= 0)
            {
                if (s.FvgSizeRatio < MinFvgRatio) { panelOk = false; panelWhy = $"FVG {s.FvgSizeRatio:F2} < {MinFvgRatio:F2} minimum"; }
                if (s.Stage == SetupStage.Complete && panelOk &&
                    s.EntryIndex5m < five.Count - 3 &&          // (the completing scan itself journals it a few lines below)
                    !AppJournal.CallInfo(market, s.Raid.Direction == "HIGH" ? "SHORT" : "LONG", s.Raid.SweepTimestamp).called)
                    panelOk = false;
            }

            // record why a setup that had reached the trigger stage left the panel (once per sweep)
            if ((dead || !panelOk) && s.TriggerIndex5m >= 0 && s.Raid.SweepTimestamp >= freshCutoff)
            {
                string kd = $"{market}|{s.Raid.Direction}|{s.Raid.SweepTimestamp}|DEAD";
                if (_alerted.Add(kd))
                    AppJournal.Append(new
                    {
                        epochMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                        sweepTs = s.Raid.SweepTimestamp,
                        utcIso = DateTime.UtcNow.ToString("O"),
                        market,
                        status = "DEAD",
                        direction = s.Raid.Direction == "HIGH" ? "SHORT" : "LONG",
                        level = s.Raid.Level,
                        sweepExtreme = s.SweepExtreme,
                        stage = s.Stage.ToString(),
                        reason = dead
                            ? (wickRetaken ? "sweep wick retaken before entry"
                               : s.Stage == SetupStage.FvgInverted ? "FVG inverted"
                               : s.Stage == SetupStage.BecameBreakout ? "became breakout"
                               : s.Stage == SetupStage.InvalidatedBeforeEntry ? "invalidated before entry"
                               : dispExpired ? "no displacement within 4h" : "FVG expired unfilled")
                            : "filtered: " + panelWhy
                    });
            }

            if (!dead && panelOk && s.Raid.SweepTimestamp >= freshCutoff)
            {
                int score =
                    (s.TriggerIndex5m >= 0 ? 1 : 0) +
                    (s.DisplacementIndex5m >= 0 ? 1 : 0) +
                    (s.FvgMiddleIndex5m >= 0 ? 1 : 0) +
                    (s.Stage == SetupStage.Complete ? 1 : 0);

                if (score > bestScore)
                {
                    bestScore = score;
                    best = s;
                }
            }

            bool actionable =
                s.Stage == SetupStage.Complete ||
                s.Stage == SetupStage.NoRetrace;

            if (!actionable) continue;

            if (s.FvgSizeRatio < MinFvgRatio) continue;

            double breachPct =
                Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level;

            if (breachPct < MinBreachPct) continue;

            // Strategy #1 alert mute on proven-negative markets:
            // tracked in the panel (above), never beeped, never
            // vaulted as a call AND never journaled as MISSED.
            // (Was checked after the MISSED block: every muted
            // AVAX/SUI setup leaked into the record as MISSED.)
            if (MarketTiers[market].StartsWith("NEGATIVE") || MarketTiers[market].StartsWith("RETIRED"))
                continue;

            long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            long fvgAgeMs = nowMs - five[s.FvgMiddleIndex5m].Timestamp;

            // Backtest waits up to 40h for the retrace to fill the
            // entry; live must too. A stale FVG only skips if the
            // entry touch is ALSO old (happened while app was off) —
            // and that gets journaled as MISSED, never silently dropped.
            bool entryFresh =
                s.Stage == SetupStage.Complete &&
                s.EntryIndex5m >= 0 &&
                s.EntryIndex5m >= five.Count - 3;

            if (fvgAgeMs > 3 * 60 * 60 * 1000 && !entryFresh)
            {
                if (s.Stage == SetupStage.Complete)
                {
                    // already called as FILLED or WATCH (this run or a
                    // previous one via seeding)? Then it was NOT missed.
                    string kF = $"{market}|{s.Raid.Direction}|{s.Raid.SweepTimestamp}|FILLED";
                    string kW = $"{market}|{s.Raid.Direction}|{s.Raid.SweepTimestamp}|WATCH";
                    if (_alerted.Contains(kF) || _alerted.Contains(kW)) continue;

                    string mk = $"{market}|{s.Raid.Direction}|{s.Raid.SweepTimestamp}|MISSED";
                    if (_alerted.Add(mk))
                        AppJournal.Append(new
                        {
                            epochMs = nowMs,
                            sweepTs = s.Raid.SweepTimestamp,
                            utcIso = DateTime.UtcNow.ToString("O"),
                            market,
                            status = "MISSED",
                            direction = s.Raid.Direction == "HIGH" ? "SHORT" : "LONG",
                            entry = s.EntryPrice,
                            stop = s.StopLoss,
                            reason = five[s.EntryIndex5m].Timestamp < _startedUtcMs
                                ? "app was not running when the entry filled"
                                : "scan did not see the entry within 15 min (feed or scan gap)"
                        });
                }
                continue;
            }

            anyLive = true;

            string status = s.Stage == SetupStage.Complete
                ? "FILLED"
                : "WATCH";

            // GLOBAL CAP (promise line = 2 open calls) + DAILY LOSS CAP.
            // Was computed only for the panel badge; FILLED calls were
            // journaled regardless (27 of the first 73 fills happened
            // with 2+ already open). Now enforced the same way as the
            // per-market rule: journaled as SUPPRESSED, not as a call.
            if (status == "FILLED")
            {
                var (capLocked, capWhy) = LockState();
                if (capLocked && Execs.Any(__e => __e.IsEngaged(market, s.Raid.SweepTimestamp)))
                    capLocked = false;   // the live account already took this one: the journal must say so
                if (capLocked)
                {
                    string ck = $"{market}|{s.Raid.Direction}|{s.Raid.SweepTimestamp}|CAP";
                    if (_suppressed.Add(ck))
                        AppJournal.Append(new
                        {
                            epochMs = nowMs,
                            sweepTs = s.Raid.SweepTimestamp,
                            utcIso = DateTime.UtcNow.ToString("O"),
                            market,
                            status = "SUPPRESSED",
                            direction = s.Raid.Direction == "HIGH" ? "SHORT" : "LONG",
                            entry = s.EntryPrice,
                            stop = s.StopLoss,
                            reason = capWhy
                        });
                    continue;
                }
            }

            string key =
                $"{market}|{s.Raid.Direction}|{s.Raid.SweepTimestamp}|{status}";

            if (_alerted.Contains(key)) continue;

            bool isShort = s.Raid.Direction == "HIGH";

            // PER-MARKET POSITION RULE (frozen, GPT-audited): while a
            // FILLED call is open on this market, new setups are NOT
            // recommended. FIXED: do NOT mark as alerted (the call can
            // fire once the position closes), and journal the
            // suppression so it is visible and auditable.
            if (status == "FILLED" && AppJournal.HasOpenCall(market))
            {
                if (_suppressed.Add(key))
                {
                    var supRow = new
                    {
                        epochMs = nowMs,
                        sweepTs = s.Raid.SweepTimestamp,
                        utcIso = DateTime.UtcNow.ToString("O"),
                        market,
                        status = "SUPPRESSED",
                        direction = isShort ? "SHORT" : "LONG",
                        entry = s.EntryPrice,
                        stop = s.StopLoss,
                        reason = "position open on this market"
                    };
                    AppJournal.Append(supRow);

                    Emit(new
                    {
                        type = "scanner-note",
                        market,
                        text = $"Suppressed {market} {(isShort ? "SHORT" : "LONG")} — position open on this market"
                    });
                }
                continue;
            }

            _alerted.Add(key);

            string coinName = market.Replace("USDT", "");
            Speak(status == "FILLED"
                ? $"{coinName} filled, {(isShort ? "short" : "long")}"
                : $"{coinName} setup, watching, {(isShort ? "short" : "long")}");
            double units = RiskDollars / s.RiskPerUnit;
            var (alertLocked, lockWhy) = LockState();

            // QUALITY BAR — SHADOW LABEL ONLY. The 0.60 gap bar failed
            // the neighborhood test (0.50 collapses), so it is logged
            // for the forward record, never enforced. Do not promote
            // without a plateau on new data.
            bool belowBar = s.FvgSizeRatio < 0.60;

            string rk = $"{s.Raid.Direction}|{s.Raid.Level}|{s.Raid.SweepTimestamp}";
            string levelTags = "D_ONLY" +
                (rawKeys.Contains(rk) ? "|RAW" : "") +
                (oldKeys.Contains(rk) ? "|OLD_8H" : "");

            var alertRow = new
            {
                epochMs = nowMs,
                sweepTs = s.Raid.SweepTimestamp,
                levelTags,
                belowBar,
                utcIso = DateTime.UtcNow.ToString("O"),
                market,
                status,
                direction = isShort ? "SHORT" : "LONG",
                level = s.Raid.Level,
                sweepExtreme = s.SweepExtreme,
                entry = s.EntryPrice,
                stop = s.StopLoss,
                riskPerUnit = s.RiskPerUnit,
                dispRatio = s.DisplacementRatio,
                fvgRatio = s.FvgSizeRatio,
                tier = MarketTiers[market]
            };

            try
            {
                Directory.CreateDirectory(VaultFolder);

                File.AppendAllText(
                    Path.Combine(VaultFolder, "journal.jsonl"),
                    JsonSerializer.Serialize(alertRow) + Environment.NewLine);

                WriteSnapshot(market, s, five, status, nowMs);

                // the hand that places the order (no-op unless broker.env says otherwise)
                if (status == "FILLED")
                    foreach (var __e in Execs) __e.OnCall(market, isShort, s.EntryPrice, s.StopLoss, s.Raid.SweepTimestamp, s.RiskPerUnit);
                else if (status == "WATCH" && s.FvgMiddleIndex5m >= 0)
                    // FVG verified, entry not touched yet: rest the LIMIT at the FVG edge NOW (backtest fill model)
                    foreach (var __e in Execs) __e.OnArm(market, isShort, s.EntryPrice, s.StopLoss, s.Raid.SweepTimestamp, s.RiskPerUnit, five[s.FvgMiddleIndex5m].Timestamp, lastPrice: five[^1].Close);
            }
            catch
            {
                // vault write failure never blocks alerting
            }

            Emit(new
            {
                type = "scanner-alert",
                locked = alertLocked,
                lockReason = lockWhy,
                belowBar,
                levelTags,
                utc = DateTime.UtcNow.ToString("HH:mm"),
                market,
                status,
                direction = isShort ? "SHORT" : "LONG",
                level = s.Raid.Level,
                sweepExtreme = s.SweepExtreme,
                entry = s.EntryPrice,
                stop = s.StopLoss,
                tp3 = isShort
                    ? s.EntryPrice - 3 * s.RiskPerUnit
                    : s.EntryPrice + 3 * s.RiskPerUnit,
                units,
                notional = units * s.EntryPrice,
                dispRatio = Math.Round(s.DisplacementRatio, 2),
                fvgRatio = Math.Round(s.FvgSizeRatio, 2),
                tier = MarketTiers[market]
            });
        }

        Emit(new
        {
            type = "scanner-status",
            market,
            status = anyLive ? "SETUP LIVE" : "SCANNING"
        });

        // Live setup-panel snapshot for this market. If a call is OPEN
        // on this market, the panel/chart pin THAT position instead
        // of chasing new setups (position rule makes them moot).
        var oc = AppJournal.OpenCall(market);

        if (oc.open)
        {
            Emit(new
            {
                type = "scanner-stages",
                market,
                active = false,
                openCall = new
                {
                    direction = oc.direction,
                    entry = oc.entry,
                    stop = oc.stop,
                    tp3 = oc.direction == "SHORT"
                        ? oc.entry - 3 * oc.risk
                        : oc.entry + 3 * oc.risk
                }
            });
        }
        else if (best == null)
        {
            Emit(new { type = "scanner-stages", market, active = false });
        }
        else
        {
            // QA live-dump: the exact setup being drawn, with its
            // candles, so disputes get settled by inspection.
            try
            {
                Directory.CreateDirectory(VaultFolder);

                int ds = Math.Max(0, best.ChartStart5m);
                int de = Math.Min(five.Count - 1, best.ChartEnd5m);

                var dump = new
                {
                    utc = DateTime.UtcNow.ToString("O"),
                    market,
                    direction = best.Raid.Direction,
                    stage = best.Stage.ToString(),
                    level = best.Raid.Level,
                    sweepExtreme = best.SweepExtreme,
                    trigger = best.TriggerIndex5m >= 0 ? best.TriggerPrice : (double?)null,
                    triggerRel = best.TriggerIndex5m - ds,
                    candles = Enumerable.Range(ds, de - ds + 1)
                        .Select(ix => new[]
                        {
                            (double)five[ix].Timestamp,
                            five[ix].Open, five[ix].High,
                            five[ix].Low, five[ix].Close
                        })
                        .ToList()
                };

                File.WriteAllText(
                    Path.Combine(VaultFolder, $"live-{market}.json"),
                    JsonSerializer.Serialize(dump));
            }
            catch { /* dump is best-effort */ }

            bool bShort = best.Raid.Direction == "HIGH";
            bool hasEntry = best.RiskPerUnit > 0 && best.FvgMiddleIndex5m >= 0;
            double bUnits = hasEntry ? RiskDollars / best.RiskPerUnit : 0;

            var ci = AppJournal.CallInfo(
                market,
                bShort ? "SHORT" : "LONG",
                best.Raid.SweepTimestamp);

            Emit(new
            {
                type = "scanner-stages",
                market,
                active = true,
                called = ci.called,
                callResult = ci.result,
                direction = bShort ? "SHORT" : "LONG",
                level = best.Raid.Level,
                sweepWick = best.SweepExtreme,
                triggerPrice = best.TriggerIndex5m >= 0
                    ? best.TriggerPrice
                    : (double?)null,
                liquidity = true,
                raid = true,
                returned = true,
                trigger = best.TriggerIndex5m >= 0,
                displacement = best.DisplacementIndex5m >= 0,
                fvg = best.FvgMiddleIndex5m >= 0,
                retrace = best.Stage == SetupStage.Complete,
                entry = hasEntry ? best.EntryPrice : (double?)null,
                stop = hasEntry ? best.StopLoss : (double?)null,
                tp2r = hasEntry
                    ? (bShort ? best.EntryPrice - 2 * best.RiskPerUnit
                              : best.EntryPrice + 2 * best.RiskPerUnit)
                    : (double?)null,
                tp3r = hasEntry
                    ? (bShort ? best.EntryPrice - 3 * best.RiskPerUnit
                              : best.EntryPrice + 3 * best.RiskPerUnit)
                    : (double?)null,
                units = hasEntry ? bUnits : (double?)null
            });
        }
    }

    private static void WriteSnapshot(
        string market,
        FullSetup s,
        List<ResearchCandle> five,
        string status,
        long nowMs)
    {
        int start = Math.Max(0, s.ChartStart5m);
        int end = Math.Min(five.Count - 1, s.ChartEnd5m);

        var candles = new List<double[]>();

        for (int i = start; i <= end; i++)
            candles.Add(new[]
            {
                (double)five[i].Timestamp,
                five[i].Open, five[i].High, five[i].Low, five[i].Close
            });

        var data = new
        {
            market,
            status,
            direction = s.Raid.Direction == "HIGH" ? "SHORT" : "LONG",
            level = s.Raid.Level,
            stop = s.StopLoss,
            trigger = s.TriggerPrice,
            entry = s.EntryPrice,
            fvgLo = Math.Min(s.FvgProximal, s.FvgDistal),
            fvgHi = Math.Max(s.FvgProximal, s.FvgDistal),
            fvgRel = s.FvgMiddleIndex5m - start,
            candles
        };

        string html = SnapshotTemplate.Replace(
            "__DATA__",
            JsonSerializer.Serialize(data));

        string name =
            $"{DateTime.UtcNow:yyyyMMdd-HHmm}-{market}-{status}.html";

        File.WriteAllText(Path.Combine(VaultFolder, name), html);
    }

    private const string SnapshotTemplate =
    """
    <!DOCTYPE html><html><head><meta charset="utf-8">
    <style>body{background:#0d1117;color:#e6edf3;font-family:Consolas,monospace;margin:12px}
    canvas{background:#161b22;border:1px solid #30363d}</style></head><body>
    <div id="t"></div><canvas id="c" width="1200" height="520"></canvas>
    <script>
    const D=__DATA__;const cv=document.getElementById("c"),x2=cv.getContext("2d");
    document.getElementById("t").textContent=
      D.status+": "+D.market+" "+D.direction+"  level "+D.level+"  entry "+D.entry+"  stop "+D.stop;
    const n=D.candles.length;let hi=-1e18,lo=1e18;
    for(const k of D.candles){if(k[2]>hi)hi=k[2];if(k[3]<lo)lo=k[3];}
    for(const p of[D.level,D.stop,D.trigger,D.fvgHi]){if(p>hi)hi=p;if(p<lo)lo=p;}
    const pad=(hi-lo)*.08;hi+=pad;lo-=pad;
    const L=8,R=80,T=8,B=8,W=cv.width-L-R,H=cv.height-T-B;
    const X=i=>L+W*(i+.5)/n,Y=p=>T+H*(hi-p)/(hi-lo);
    const cw=Math.max(2,Math.floor(W/n*.6));
    x2.fillStyle="rgba(163,113,247,.25)";
    x2.fillRect(X(Math.max(0,D.fvgRel-1)),Y(D.fvgHi),W-X(Math.max(0,D.fvgRel-1))+L,Y(D.fvgLo)-Y(D.fvgHi));
    for(let i=0;i<n;i++){const[,o,h,l,c]=D.candles[i];const up=c>=o;
      x2.strokeStyle=x2.fillStyle=up?"#2ea043":"#f85149";
      x2.beginPath();x2.moveTo(X(i),Y(h));x2.lineTo(X(i),Y(l));x2.stroke();
      x2.fillRect(X(i)-cw/2,Math.min(Y(o),Y(c)),cw,Math.max(1,Math.abs(Y(c)-Y(o))));}
    function hl(p,c,d){x2.strokeStyle=c;x2.setLineDash(d);x2.beginPath();
      x2.moveTo(L,Y(p));x2.lineTo(L+W,Y(p));x2.stroke();x2.setLineDash([]);}
    hl(D.level,"#f85149",[6,4]);hl(D.trigger,"#d29922",[2,3]);
    hl(D.entry,"#2ea043",[2,3]);hl(D.stop,"#f85149",[]);
    x2.fillStyle="#8b949e";x2.font="11px Consolas";
    for(let g=0;g<=5;g++){const p=lo+(hi-lo)*g/5;x2.fillText(p.toPrecision(6),L+W+6,Y(p)+4);}
    </script></body></html>
    """;

    private void Emit(object payload) =>
        Message?.Invoke(JsonSerializer.Serialize(payload));

    // LIVE data source = HYPERLIQUID: the reference venue and
    // the same feed his chart displays, so drawn levels match his
    // wicks exactly. (Backtest history remains Binance-based —
    // structurally equivalent, known small basis; noted in
    // CLAUDE.md and revisited when an HL historical collector
    // exists.)
    private async Task<List<ResearchCandle>> FetchClosed5m(
        string market,
        long startTimestamp,
        CancellationToken ct)
    {
        string coin = market.EndsWith("USDT")
            ? market[..^4]
            : market;

        long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
        long lastClosedOpen = ((nowMs / FiveMs) * FiveMs) - FiveMs;

        var body = JsonSerializer.Serialize(new
        {
            type = "candleSnapshot",
            req = new
            {
                coin,
                interval = "5m",
                startTime = startTimestamp,
                endTime = nowMs
            }
        });

        using var content = new StringContent(
            body,
            System.Text.Encoding.UTF8,
            "application/json");

        using var response = await _http.PostAsync(
            "https://api.hyperliquid.xyz/info", content, ct);

        response.EnsureSuccessStatusCode();

        await using var stream =
            await response.Content.ReadAsStreamAsync(ct);

        using var doc =
            await JsonDocument.ParseAsync(stream, cancellationToken: ct);

        var candles = new List<ResearchCandle>();

        foreach (JsonElement k in doc.RootElement.EnumerateArray())
        {
            long openTime = k.GetProperty("t").GetInt64();

            if (openTime > lastClosedOpen) continue; // still forming

            candles.Add(new ResearchCandle(
                openTime,
                ParseStr(k.GetProperty("o")),
                ParseStr(k.GetProperty("h")),
                ParseStr(k.GetProperty("l")),
                ParseStr(k.GetProperty("c")),
                ParseStr(k.GetProperty("v"))));
        }

        candles.Sort((a, b) => a.Timestamp.CompareTo(b.Timestamp));

        return candles;
    }

    private static double ParseStr(JsonElement e) =>
        double.Parse(
            e.GetString()!,
            System.Globalization.CultureInfo.InvariantCulture);

    private static double Parse(JsonElement e) =>
        double.Parse(
            e.GetString()!,
            System.Globalization.CultureInfo.InvariantCulture);

    private static List<ResearchCandle> BuildFifteen(
        List<ResearchCandle> five)
    {
        var result = new List<ResearchCandle>();

        foreach (var g in five
            .GroupBy(k => k.Timestamp / FifteenMs)
            .OrderBy(g => g.Key))
        {
            var trio = g.OrderBy(k => k.Timestamp).ToList();
            if (trio.Count != 3) continue;

            result.Add(new ResearchCandle(
                trio[0].Timestamp,
                trio[0].Open,
                trio.Max(k => k.High),
                trio.Min(k => k.Low),
                trio[2].Close,
                trio.Sum(k => k.Volume)));
        }

        return result;
    }

    public void Dispose()
    {
        _cts?.Cancel();
        _http.Dispose();
    }
}
