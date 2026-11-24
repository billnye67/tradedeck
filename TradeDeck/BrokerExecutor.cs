using System.IO;
using System.Threading;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace TradeDeck;

/// <summary>
/// The hand that places the order. Takes a FILLED Strategy #1 call from the
/// scanner and turns it into a protected position on a Broker (DXtrade)
/// account: MARKET open, full-close STOP at the sweep wick, full-close LIMIT
/// at 3R, then a reconcile loop that cancels the sibling order when either
/// side fires and journals the real fill, exit and P&amp;L.
///
/// Config: broker.env in the repo root (gitignored):
///   BROKER_USER=   BROKER_PASS=
///   BROKER_MODE=off|dry|live        dry = log every request, send nothing (default)
///   BROKER_RISK_PCT=0.5             percent of account equity risked per trade
///   BROKER_MARKETS=BTCUSDT,ETHUSDT,XRPUSDT
///   BROKER_MAX_OPEN=2
///   BROKER_DAILY_STOP_PCT=3.0       stop opening when today's loss reaches this (daily limit minus buffer)
///   BROKER_FLOOR_PCT=6.0            stop opening when total drawdown from start reaches this
///
/// Rules this code refuses to break: unique orderCode per request, never a
/// blind retry of a POST, tif always GTC, position is not "protected" until
/// the STOP is seen in working orders, aggregate caps checked before every
/// open, secrets never logged.
/// </summary>
public sealed partial class BrokerExecutor : IDisposable
{
    private const string Base = "http://127.0.0.1:8899/dxsca-web";
    private const string PriceUrl = "https://api.hyperliquid.xyz/info";   // 2026-10-23 price gate: live mid before any MARKET entry
    private readonly HashSet<string> _traded = new();   // 2026-10-23: market|sweep already filled and closed on this account (one trade per setup)
    private string TradedPath => Path.Combine(AppJournal.VaultFolder, $"traded_slot{Slot}.txt");
    /// <summary>One trade per setup survives a restart: every traded market|sweep is appended to a per-slot file and reloaded on Start.</summary>
    private void MarkTraded(string key)
    {
        lock (_gate) { if (!_traded.Add(key)) return; }
        try { Directory.CreateDirectory(AppJournal.VaultFolder); File.AppendAllText(TradedPath, key + "\n"); } catch (Exception ex) { Log(new { ev = "traded-file-error", error = ex.Message }); }
    }
    private void LoadTraded()
    {
        try { if (File.Exists(TradedPath)) lock (_gate) foreach (var l in File.ReadAllLines(TradedPath)) if (l.Trim().Length > 0) _traded.Add(l.Trim()); } catch { }
    }
    private static readonly HttpClient _priceHttp = new() { Timeout = TimeSpan.FromSeconds(5) };   // own client: never sends the broker session header to the price feed
    private static readonly string EnvPath = Path.Combine(
        AppContext.BaseDirectory, "..", "..", "..", "..", "broker.env");

    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(20) };
    private readonly object _gate = new();
    private readonly HashSet<string> _inflight = new();   // market|sweepTs reserved between OnCall/OnArm and the Trade landing in _open
    private readonly HashSet<string> _inflightCall = new();   // 2026-11-22: the subset reserved by OnCall (a call entry holds a slot; an armed one does not)
    private string? _token;
    private string? _accountCode;      // full "clearing:number" code
    private string? _accountPath;      // URL-encoded
    private double _startEquity;       // equity when this process first logged in (static floor reference)
    private double _dayStartEquity;
    private long _dayKey = -1;
    private readonly Dictionary<string, Instrument> _instruments = new();
    private readonly List<Trade> _open = new();
    private CancellationTokenSource? _cts;

    public string Mode { get; private set; } = "off";
    public double RiskPct { get; private set; } = 0.5;
    public HashSet<string> Markets { get; private set; } = new() { "BTCUSDT", "ETHUSDT", "XRPUSDT" };
    public int MaxOpen { get; private set; } = 2;
    public double DailyStopPct { get; private set; } = 3.0;
    public double FloorPct { get; private set; } = 6.0;
    public int EntryWaitMin { get; private set; } = 30;
    public int TimeStopMin { get; private set; } = 2400;   // = WalkWindow 480 x 5m
    public double StartEquityCfg { get; private set; } = 50_000;
    public double LastEquity { get; private set; }
    public double LastBalance { get; private set; }
    public string Status { get; private set; } = "off";

    public event Action<object>? Message;

    private readonly string? _envOverride;
    public int Slot { get; set; } = 1;
    public BrokerExecutor(string? envFile = null)
    {
        _envOverride = envFile;
        // DXtrade answers "403 RBAC: access denied" to any request with no User-Agent.
        _http.DefaultRequestHeaders.UserAgent.ParseAdd("TradeDeck/1.0");
    }

    private sealed record Instrument(string Symbol, string Status, decimal MinSize, decimal Increment, decimal MaxSize);

    private sealed class Trade
    {
        public string Market = ""; public string Symbol = ""; public string Side = "";
        public string Strategy = "raid-fvg"; public long SweepTs; public double Entry; public double Stop; public double Target; public decimal Qty;
        public string OpenCode = ""; public string? OpenOrderId; public string? PositionCode;
        public string? StopCode; public string? TpCode; public double? FillPrice;
        public long OpenedMs; public bool Protected;
        public bool Armed; public long DeadlineMs; public string? DeadWhy;
        public bool Rejected; public bool MarketSent; public string? MarketOrderId;
        public double UnitsPerQty = 1;   // forex: 100,000 when the broker counts in lots
        public long ExitAtMs;   // forex: absolute flat time (16:55 New York); 0 = use the relative BROKER_TIME_STOP_MIN
        public bool EmergencyFailed;   // 2026-10-23: emergency close could not be sent; Reconcile retries every cycle
        public bool OpenDone;   // 2026-10-25: the Open task has returned (normally or by exception); Reconcile may now repair protection
        public bool OverCap; public long OverCapSentMs; public long ClaimMs; public bool CapCancelled;   // 2026-11-22 first to fill wins
        public long FilledMs; public bool TimeSent; public string? TimeCode;   // 40h time stop (backtest WalkWindow)   // broker rejected the resting LIMIT; MARKET fallback at the touch
    }

    // ------------------------------------------------------------ v2 direction filter (registry 171/173; protocol 2026-10-16)
    public bool V2Filter { get; set; } = false;
    public string V2ScoresPath { get; set; } = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "TradeDeck", "Data", "lab", "live_combo", "scores.csv");
    public int V2MaxAgeMin { get; set; } = 120;
    /// <summary>Latest Engine 2 score for market + direction from the paper bot's scores.csv (coin,bar,dec,pL,pS,v2L,v2S,run).
    /// Returns null when there is no score, the file is missing, or the newest decision is older than V2MaxAgeMin.</summary>
    public (double? score, string why) V2Score(string market, bool isShort)
    {
        try
        {
            if (!File.Exists(V2ScoresPath)) return (null, "no scores file " + V2ScoresPath);
            string tail;
            using (var fs = new FileStream(V2ScoresPath, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
            {
                long take = Math.Min(fs.Length, 600_000); fs.Seek(-take, SeekOrigin.End);   // ~2 days of rows; the file only grows
                using var sr = new StreamReader(fs); tail = sr.ReadToEnd();
            }
            DateTime bestDec = DateTime.MinValue; double? best = null; string prefix = market + ",";
            foreach (var raw in tail.Split('\n'))
            {
                if (!raw.StartsWith(prefix, StringComparison.Ordinal)) continue;
                var f = raw.TrimEnd('\r').Split(',');
                if (f.Length < 7) continue;
                if (!DateTime.TryParse(f[2], System.Globalization.CultureInfo.InvariantCulture, System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out var dec)) continue;
                if (!double.TryParse(isShort ? f[6] : f[5], System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var sc)) continue;
                if (dec >= bestDec) { bestDec = dec; best = sc; }
            }
            if (best == null) return (null, "no score rows for " + market);
            double age = (DateTime.UtcNow - bestDec).TotalMinutes;
            if (age > V2MaxAgeMin) return (null, $"stale: newest decision {bestDec:yyyy-MM-dd HH:mm}Z is {age:F0} min old (max {V2MaxAgeMin})");
            return (best, $"decision {bestDec:HH:mm}Z, {age:F0} min old");
        }
        catch (Exception ex) { return (null, "read error: " + ex.Message); }
    }
    /// <summary>True when the trade may proceed. Journals v2-ok / v2-skip. With the filter off, always true and silent.</summary>
    private bool V2Allows(string market, bool isShort, long sweepTs, string path)
    {
        if (!V2Filter) return true;
        var (sc, why) = V2Score(market, isShort); string side = isShort ? "SELL" : "BUY";
        if (sc == null) { Log(new { ev = "v2-skip", market, side, sweepTs, path, why }); return false; }
        if (sc < 0) { Log(new { ev = "v2-skip", market, side, sweepTs, path, score = Math.Round(sc.Value, 4), why }); return false; }
        Log(new { ev = "v2-ok", market, side, sweepTs, path, score = Math.Round(sc.Value, 4), why });
        return true;
    }

    // ------------------------------------------------------------ config
    public void LoadConfig()
    {
        try
        {
            string path = _envOverride != null ? Path.Combine(Path.GetDirectoryName(Path.GetFullPath(EnvPath))!, _envOverride) : Path.GetFullPath(EnvPath);
            if (!File.Exists(path)) path = Path.Combine(Environment.CurrentDirectory, _envOverride ?? "broker.env");   // 2026-10-25 review #1: a slot never falls back to another slot's file
            if (!File.Exists(path)) { Mode = "off"; Status = "no " + (_envOverride ?? "broker.env"); return; }
            var kv = new Dictionary<string, string>();
            foreach (var line in File.ReadAllLines(path))
            {
                int i = line.IndexOf('=');
                if (i <= 0 || line.TrimStart().StartsWith("#")) continue;
                kv[line[..i].Trim()] = line[(i + 1)..].Trim();
            }
            _user = kv.GetValueOrDefault("BROKER_USER", "");
            _accountWanted = kv.GetValueOrDefault("BROKER_ACCOUNT", "").Trim();
            _pass = kv.GetValueOrDefault("BROKER_PASS", "");
            Mode = kv.GetValueOrDefault("BROKER_MODE", "dry").ToLowerInvariant();
            if (double.TryParse(kv.GetValueOrDefault("BROKER_RISK_PCT", "0.5"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var rp)) RiskPct = rp;
            if (kv.TryGetValue("BROKER_TIME_STOP_MIN", out var tsm) && int.TryParse(tsm, out var tsmV) && tsmV > 0) TimeStopMin = tsmV;
            if (kv.TryGetValue("BROKER_MARKETS", out var mk)) Markets = mk.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToHashSet();
            if (int.TryParse(kv.GetValueOrDefault("BROKER_MAX_OPEN", "2"), out var mo)) MaxOpen = mo;
            V2Filter = kv.GetValueOrDefault("BROKER_V2_FILTER", "off").Trim().ToLowerInvariant() == "on";
            if (kv.TryGetValue("BROKER_V2_SCORES", out var v2p) && v2p.Length > 0) V2ScoresPath = v2p;
            if (int.TryParse(kv.GetValueOrDefault("BROKER_V2_MAX_AGE_MIN", "120"), out var v2a) && v2a > 0) V2MaxAgeMin = v2a;
            if (double.TryParse(kv.GetValueOrDefault("BROKER_DAILY_STOP_PCT", "3.0"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var ds)) DailyStopPct = ds;
            if (double.TryParse(kv.GetValueOrDefault("BROKER_FLOOR_PCT", "6.0"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var fp)) FloorPct = fp;
            if (int.TryParse(kv.GetValueOrDefault("BROKER_ENTRY_WAIT_MIN", "30"), out var ew)) EntryWaitMin = ew;
            if (double.TryParse(kv.GetValueOrDefault("BROKER_START_EQUITY", "100000"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var se)) StartEquityCfg = se;
            if (string.IsNullOrEmpty(_user) || string.IsNullOrEmpty(_pass)) { Mode = "off"; Status = "broker.env missing BROKER_USER/BROKER_PASS"; }
            else Status = $"{Mode} | risk {RiskPct}% | {string.Join(",", Markets)}" + (V2Filter ? " | v2 filter ON" : "");
            LoadFxConfig(kv);
            if (FxMode != "off") Status += $" | forex {FxMode} ${FxRiskUsd} {string.Join(",", FxMarkets)}";
        }
        catch (Exception ex) { Mode = "off"; Status = "config error: " + ex.Message; }
    }
    private string _user = "", _pass = "", _accountWanted = "";
    // one DXtrade session per login shared by every executor in this process (two accounts, one user): login once, reuse the token
    private static readonly Dictionary<string, string> _sharedTokens = new();
    private static readonly SemaphoreSlim _loginGate = new(1, 1);
    // every executions.jsonl row carries the account it belongs to (AsyncLocal flows through Task.Run and awaits)
    private static readonly AsyncLocal<string?> _logAcct = new();
    private string _acctTag => _accountCode != null ? Mask(_accountCode) : "slot" + Slot;

    public void Start()
    {
        LoadConfig();
        if (Mode == "off") { Emit(new { type = "exec-state", mode = Mode, status = Status, slot = Slot }); return; }
        LoadTraded();
        _cts?.Cancel();   // 2026-10-25 review #4: a scanner restart must not leave the previous Loop polling alongside the new one
        _cts = new CancellationTokenSource();
        _ = Task.Run(() => Loop(_cts.Token));
    }

    public void Stop() { _cts?.Cancel(); }
    public void Dispose() { Stop(); try { Logout().Wait(3000); } catch { } _http.Dispose(); }

    // ------------------------------------------------------------ main loop
    private async Task Loop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested)
        {
            try
            {
                if (_token == null) { await Login(ct); if (Mode == "live") await AdoptExisting(); }
                await Reconcile(ct);
                var (eq, bal) = await Metrics();
                LastEquity = eq; LastBalance = bal; RollDay(eq);
                List<object> openList;
                lock (_gate) openList = _open.Select(t => (object)new { t.Market, t.Side, fill = t.FillPrice, t.Stop, t.Target, qty = t.Qty, t.Protected }).ToList();
                Emit(new
                {
                    type = "exec-state", mode = Mode, status = Status, account = Mask(_accountCode ?? ""), slot = Slot,
                    equity = eq, balance = bal, startEquity = _startEquity,
                    todayPL = eq - _dayStartEquity, dailyStop = _startEquity * DailyStopPct / 100.0, dailyLimit = _startEquity * 0.04,
                    totalPL = eq - _startEquity, floorStop = _startEquity * FloorPct / 100.0, floorLimit = _startEquity * 0.07,
                    riskPct = RiskPct, riskDollars = eq * RiskPct / 100.0, maxOpen = MaxOpen, markets = string.Join(",", Markets),
                    open = openList
                });
            }
            catch (Exception ex)
            {
                Status = "error: " + ex.Message;
                Log(new { ev = "loop-error", error = ex.Message });
                _token = null; // force re-login next pass
            }
            try { await Task.Delay(TimeSpan.FromSeconds(30), ct); } catch { break; }
        }
    }

    // ------------------------------------------------------------ the call → order
    /// <summary>Called by the scanner for every FILLED call. Returns quickly; work runs in the background.</summary>
    public void OnCall(string market, bool isShort, double entry, double stop, long sweepTs, double riskPerUnit, string strategy = "raid-fvg")
    {
        _logAcct.Value = _acctTag;
        if (Mode == "off") return;
        if (!Markets.Contains(market)) { Log(new { ev = "skip", market, why = "market not in BROKER_MARKETS" }); return; }
        Trade? existing;
        lock (_gate) existing = _open.FirstOrDefault(t => t.Market == market && t.SweepTs == sweepTs);
        if (existing == null && !V2Allows(market, isShort, sweepTs, "call")) return;
        if (existing != null)
        {
            // the scanner says price touched the entry. If the broker rejected our resting LIMIT, this is the
            // backtest's fill moment: enter at market now instead of sitting on a dead order.
            if (existing.Rejected && !existing.MarketSent && existing.PositionCode == null && existing.DeadWhy == null)
                _ = Task.Run(() => SendMarketEntry(existing, "touch after rejected LIMIT"));
            else Log(new { ev = "already-armed", market, sweepTs });
            return;
        }
        if (!Reserve(market, sweepTs, "call")) return;
        _ = Task.Run(() => Open(market, isShort, entry, stop, sweepTs, riskPerUnit, strategy, false, 0, 0));   // 2026-10-23: no claimed price; the gate fetches the live one
    }

    /// <summary>
    /// Called when the scanner journals a WATCH (FVG verified, entry not yet touched). Places the resting
    /// LIMIT now so it is already sitting at the FVG edge when price comes back: the backtest's fill model.
    /// The order lives until the retrace window ends (36 bars after the FVG) or the scanner says the setup died.
    /// </summary>
    public void OnArm(string market, bool isShort, double entry, double stop, long sweepTs, double riskPerUnit, long fvgMidTs, string strategy = "raid-fvg", double lastPrice = 0)
    {
        _logAcct.Value = _acctTag;
        if (Mode == "off") return;
        if (!Markets.Contains(market)) return;
        lock (_gate) if (_open.Any(t => t.Market == market && t.SweepTs == sweepTs)) return;
        if (!V2Allows(market, isShort, sweepTs, "arm")) return;
        long deadline = fvgMidTs + 38L * 5 * 60 * 1000;   // bar after the FVG + 36-bar retrace window, to its close
        if (deadline <= DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()) return;
        if (!Reserve(market, sweepTs, "arm")) return;
        _ = Task.Run(() => Open(market, isShort, entry, stop, sweepTs, riskPerUnit, strategy, true, deadline, lastPrice));
    }

    /// <summary>Scanner says this setup is dead before entry (wick retaken, FVG inverted, breakout, expired): pull the armed order.</summary>
    public void OnDead(string market, long sweepTs, string why)
    {
        _logAcct.Value = _acctTag;
        lock (_gate)
        {
            var t = _open.FirstOrDefault(x => x.Market == market && x.SweepTs == sweepTs && x.PositionCode == null && x.DeadWhy == null);
            if (t != null) t.DeadWhy = why;
        }
    }

    private async Task Open(string market, bool isShort, double entry, double stop, long sweepTs, double riskPerUnit, string strategy, bool armed, long deadlineMs, double lastPrice)
    {
        try
        {
            if (_token == null) await Login(CancellationToken.None);
            lock (_gate)
            {
                if (_open.Any(t => t.Market == market)) { Log(new { ev = "skip", market, why = "position already open on market" }); return; }
                if (SlotsUsed() >= MaxOpen) { Log(new { ev = "skip", market, why = $"open cap {MaxOpen}" }); return; }
            }
            await OpenReserved(market, isShort, entry, stop, sweepTs, riskPerUnit, strategy, armed, deadlineMs, lastPrice);
        }
        catch (Exception ex)
        {
            Log(new { ev = "open-error", market, error = ex.Message });
            Status = "error: " + ex.Message;
        }
        finally { Release(market, sweepTs); }
    }

    private async Task OpenReserved(string market, bool isShort, double entry, double stop, long sweepTs, double riskPerUnit, string strategy, bool armed, long deadlineMs, double lastPrice)
    {
        Trade? t = null;
        try
        {
            var (equity, _) = await Metrics();
            RollDay(equity);
            double ddPct = 100.0 * (_startEquity - equity) / _startEquity;
            double dayPct = 100.0 * (_dayStartEquity - equity) / _dayStartEquity;
            if (ddPct >= FloorPct) { Log(new { ev = "halt", why = $"drawdown {ddPct:F2}% >= floor {FloorPct}%" }); Status = "HALTED: drawdown floor"; return; }
            if (dayPct >= DailyStopPct) { Log(new { ev = "skip", market, why = $"today -{dayPct:F2}% >= daily stop {DailyStopPct}%" }); return; }

            string symbol = MapSymbol(market);
            var inst = await InstrumentFor(symbol);
            if (inst.Status != "FULL") { Log(new { ev = "skip", market, why = $"{symbol} status {inst.Status}" }); return; }

            double riskDollars = equity * RiskPct / 100.0;
            double perUnit = Math.Abs(entry - stop);
            if (perUnit <= 0) { Log(new { ev = "skip", market, why = "zero risk per unit" }); return; }
            decimal qty = RoundDown((decimal)(riskDollars / perUnit), inst.MinSize, inst.Increment);
            if (qty < inst.MinSize) { Log(new { ev = "skip", market, why = $"qty {qty} below min {inst.MinSize}" }); return; }
            if (inst.MaxSize > 0 && qty > inst.MaxSize) qty = inst.MaxSize;

            double target = isShort ? entry - 3.0 * perUnit : entry + 3.0 * perUnit;
            t = new Trade
            {
                Market = market, Symbol = symbol, Side = isShort ? "SELL" : "BUY", SweepTs = sweepTs, Strategy = strategy,
                Entry = entry, Stop = stop, Target = target, Qty = qty, Armed = armed,
                DeadlineMs = armed ? deadlineMs : DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + EntryWaitMin * 60_000L,
                OpenCode = Code("open", market), OpenedMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
            };
            lock (_gate) _open.Add(t);

            // Entry = resting LIMIT at the retrace price (the backtest's fill model; no entry slippage).
            var openReq = new JsonObject
            {
                ["orderCode"] = t.OpenCode, ["type"] = "LIMIT", ["instrument"] = symbol,
                ["quantity"] = qty.ToString(System.Globalization.CultureInfo.InvariantCulture),
                ["limitPrice"] = Px(entry),
                ["positionEffect"] = "OPEN", ["side"] = t.Side, ["tif"] = "GTC"
            };
            Log(new { ev = "open-intent", market, strategy, armed, symbol, side = t.Side, qty, entry, stop, target, riskDollars = Math.Round(riskDollars, 2), equity = Math.Round(equity, 2), mode = Mode });

            if (Mode != "live")
            {
                t.PositionCode = "DRY"; t.Protected = true; t.FillPrice = entry;
                Log(new { ev = "dry-open", market, request = openReq.ToJsonString() });
                return;
            }

            var ack = await Post($"/accounts/{_accountPath}/orders", openReq);
            t.OpenOrderId = ack?["orderId"]?.ToString();
            Log(new { ev = armed ? "armed" : "open-sent", market, orderId = t.OpenOrderId, orderCode = t.OpenCode, entry, stop, untilUtc = DateTimeOffset.FromUnixTimeMilliseconds(t.DeadlineMs).ToString("HH:mm") });

            // 2026-10-05: the platform REJECTS a LIMIT that is already through the market instead of filling it
            // (XRP 07:15 UTC: placed and rejected in the same second, bot never knew). Read the order back; on a
            // rejection, fall back to MARKET when price is already at the entry (the backtest fills at the touch).
            for (int i = 0; i < 4 && !t.Rejected; i++)
            {
                await Task.Delay(1500);
                var hist = await Get($"/accounts/{_accountPath}/orders/history?with-order-id={t.OpenOrderId}");
                var rec = hist?["orders"]?.AsArray().FirstOrDefault(o => o?["orderId"]?.ToString() == t.OpenOrderId);
                string st = rec?["status"]?.ToString() ?? "";
                if (st == "REJECTED") { t.Rejected = true; Log(new { ev = "entry-REJECTED", market, armed, entry, lastPrice, record = Truncate(rec?.ToJsonString(), 500) }); }
                else if (st is "WORKING" or "FILLED" or "COMPLETED") break;
            }
            if (t.Rejected)
            {
                bool through = lastPrice > 0 && (isShort ? lastPrice >= entry : lastPrice <= entry);
                if (!armed || through) await SendMarketEntry(t, armed ? "LIMIT rejected with price already through the entry" : "LIMIT rejected at the touch");
                else Log(new { ev = "armed-rejected-waiting", market, why = "LIMIT rejected; will enter at MARKET when the scanner reports the touch" });
            }

            // wait for the resting entry to fill: armed = until the retrace window ends or the scanner kills the
            // setup; plain = up to BROKER_ENTRY_WAIT_MIN minutes. Then find the position.
            JsonNode? pos = null;
            HashSet<string> livePos = new(); long fetchMs = 0;   // the broker's open positions as of the last poll, and when it was read
            async Task<JsonNode?> FindMine()
            {
                long asOf = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                var positions = (await Get($"/accounts/{_accountPath}/positions"))?["positions"]?.AsArray();
                livePos = new HashSet<string>((positions ?? new JsonArray()).Select(p => p?["positionCode"]?.ToString() ?? "")); fetchMs = asOf;
                List<Trade> others; lock (_gate) others = _open.Where(o => o != t).ToList();
                return positions?.FirstOrDefault(p =>
                    (p?["symbol"]?.ToString() == symbol || p?["instrument"]?.ToString() == symbol) &&
                    (p?["positionCode"]?.ToString() == t.OpenOrderId || (t.MarketOrderId != null && p?["positionCode"]?.ToString() == t.MarketOrderId) ||
                     !others.Any(o => o.PositionCode == p?["positionCode"]?.ToString())));
            }
            bool pollErr = false;
            while (pos == null && t.DeadWhy == null && DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < t.DeadlineMs)
            {
                await Task.Delay(10_000);
                // 2026-11-22 review: one HTTP error used to end the wait with the LIMIT still live at the broker and nobody watching it. Keep polling.
                try { pos = await FindMine(); if (pollErr) { pollErr = false; Log(new { ev = "entry-poll-ok", market }); } }
                catch (Exception ex) { if (!pollErr) Log(new { ev = "entry-poll-error", market, error = ex.Message, why = "still waiting; the order stays watched" }); pollErr = true; }
            }
            if (pos == null && Mode == "live")
            {
                // cancel the unfilled entry, then look again (2026-11-22): it can fill between the last poll and the cancel (cap cancel,
                // dead setup, deadline). A fill found here is a real position and goes through the slot claim below (protect or close).
                bool cancelled = t.Rejected || t.CapCancelled || await CancelEntry(t);   // a rejected LIMIT was never live; a cap cancel already went through
                await Task.Delay(1500);
                bool looked = false;
                for (int i = 0; i < 3 && !looked; i++)
                {
                    try { pos = await FindMine(); looked = true; }
                    catch (Exception ex) { Log(new { ev = "final-fill-check-error", market, attempt = i + 1, error = ex.Message }); await Task.Delay(2000); }
                }
                if (pos != null) Log(new { ev = "filled-at-cancel", market, why = t.DeadWhy ?? "deadline", positionCode = pos["positionCode"]?.ToString() });
                else if (!cancelled || !looked)
                {
                    // 2026-11-22 review B: the order may still be live, or may have filled while the positions read was down. Only a broker
                    // status of CANCELLED/CANCELED/REJECTED/EXPIRED lets it go; otherwise alarm once and KEEP watching (cancel again, look for
                    // the fill) until it is confirmed dead or its fill is found and claimed. Gives up after 30 min, still alarmed.
                    async Task<string> Status()
                    {
                        try { var h = await Get($"/accounts/{_accountPath}/orders/history?with-order-id={t.OpenOrderId}"); return h?["orders"]?.AsArray().FirstOrDefault(o => o?["orderId"]?.ToString() == t.OpenOrderId)?["status"]?.ToString() ?? "unknown"; }
                        catch { return "unknown"; }
                    }
                    static bool Dead(string x) => x is "CANCELLED" or "CANCELED" or "REJECTED" or "EXPIRED";
                    string st = t.Rejected ? "REJECTED" : await Status();
                    long until = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() + 30 * 60_000L; bool alarmed = false;
                    while (pos == null && !Dead(st) && DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < until)
                    {
                        if (!alarmed)
                        {
                            alarmed = true;
                            Log(new { ev = "CANCEL-FAILED", market, orderCode = t.OpenCode, status = st, why = "could not cancel or confirm the resting entry: still watching it" });
                            Emit(new { type = "exec-alert", market, slot = Slot, account = Mask(_accountCode ?? ""), text = "Could not cancel the resting " + market + " entry. Still watching it; cancel it by hand in Broker if this stays." });
                        }
                        await Task.Delay(10_000);
                        try { pos = await FindMine(); } catch { }
                        if (pos != null) break;
                        if (st is "WORKING" or "unknown") await CancelEntry(t);
                        st = await Status();
                    }
                    if (pos != null) Log(new { ev = "filled-at-cancel", market, why = "found while confirming the cancel", positionCode = pos["positionCode"]?.ToString() });
                    else if (!Dead(st)) Log(new { ev = "CANCEL-UNCONFIRMED-GAVE-UP", market, orderCode = t.OpenCode, status = st, why = "30 min without a confirmed cancel or a fill: CHECK BROKER BY HAND" });
                }
            }
            if (pos == null)
            {
                // unfilled resting entry (already cancelled above): record why
                JsonNode? hist = null;
                try { hist = await Get($"/accounts/{_accountPath}/orders/history?with-order-id={t.OpenOrderId}"); } catch { }
                string why = t.DeadWhy ?? (armed ? "retrace window ended, entry never touched" : $"no fill in {EntryWaitMin} min");
                Log(new { ev = armed ? "armed-cancelled" : "entry-NOFILL-cancelled", market, why, waitedMin = (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - t.OpenedMs) / 60_000, history = Truncate(hist?.ToJsonString(), 300) });
                lock (_gate) _open.Remove(t);
                return;
            }
            bool overCap;
            lock (_gate)   // claim the slot atomically: two entries filling at the same moment cannot both take the last slot
            {
                overCap = t.Strategy != FxStrategy && FilledLive(t, livePos, fetchMs) >= MaxOpen;
                t.PositionCode = pos["positionCode"]!.ToString(); t.ClaimMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(); t.OverCap = overCap;
            }
            t.FillPrice = TryDouble(pos["openPrice"]) ?? TryDouble(pos["price"]) ?? entry;
            string opp = t.Side == "BUY" ? "SELL" : "BUY";
            if (overCap)
            {
                // more fills than slots (entries crossed before the cancel landed): the later fill loses and is closed at market now.
                // The trade stays tracked until the broker shows the position gone: Reconcile re-sends the close and journals it (never protects it).
                Log(new { ev = "over-cap-fill-closed", market, positionCode = t.PositionCode, fill = t.FillPrice, why = $"{MaxOpen} trades already filled: first to fill wins" });
                MarkTraded(market + "|" + sweepTs);
                t.OverCapSentMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                if (!await EmergencyClose(t, 3))
                {
                    t.OverCapSentMs = 0;   // let Reconcile retry on its next pass
                    Log(new { ev = "EMERGENCY-CLOSE-FAILED", market, positionCode = t.PositionCode, why = "over-cap close: 3 attempts failed; retrying every cycle; CLOSE BY HAND if it persists" });
                    Emit(new { type = "exec-alert", market, slot = Slot, account = Mask(_accountCode ?? ""), text = "Extra " + market + " trade (over the trade cap) could not be closed. Retrying. Close it by hand if this stays." });
                }
                return;
            }

            // protective STOP (full close, no quantity)
            t.StopCode = Code("stop", market);
            await Post($"/accounts/{_accountPath}/orders", new JsonObject
            {
                ["orderCode"] = t.StopCode, ["type"] = "STOP", ["instrument"] = symbol, ["positionEffect"] = "CLOSE",
                ["positionCode"] = t.PositionCode, ["side"] = opp, ["stopPrice"] = Px(stop), ["tif"] = "GTC"
            });
            // take profit LIMIT (full close, no quantity)
            t.TpCode = Code("tp", market);
            await Post($"/accounts/{_accountPath}/orders", new JsonObject
            {
                ["orderCode"] = t.TpCode, ["type"] = "LIMIT", ["instrument"] = symbol, ["positionEffect"] = "CLOSE",
                ["positionCode"] = t.PositionCode, ["side"] = opp, ["limitPrice"] = Px(target), ["tif"] = "GTC"
            });

            // confirm protection is working
            for (int i = 0; i < 5 && !t.Protected; i++)
            {
                await Task.Delay(1500);
                var orders = (await Get($"/accounts/{_accountPath}/orders"))?["orders"]?.AsArray() ?? new JsonArray();
                bool stopOk = orders.Any(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(t.StopCode));
                bool tpOk = orders.Any(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(t.TpCode));
                t.Protected = stopOk && tpOk;
            }
            if (!t.Protected)
            {
                Log(new { ev = "UNPROTECTED-emergency-close", market });
                MarkTraded(market + "|" + sweepTs);
                if (await EmergencyClose(t, 3)) { lock (_gate) _open.Remove(t); return; }
                // review finding 4: a failed emergency close must never leave a naked position silently. Alarm, keep the trade, Reconcile retries.
                t.EmergencyFailed = true;
                Log(new { ev = "EMERGENCY-CLOSE-FAILED", market, positionCode = t.PositionCode, why = "3 attempts failed; retrying every cycle; CLOSE BY HAND if it persists" });
                Emit(new { type = "exec-alert", market, slot = Slot, account = Mask(_accountCode ?? ""), text = "EMERGENCY CLOSE FAILED on " + market + ": position has no stop. Retrying. Close it by hand if this stays." });
                return;
            }
            t.FilledMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            Log(new { ev = "protected", market, strategy = t.Strategy, positionCode = t.PositionCode, fill = t.FillPrice, stop, target, qty });
            // 2026-10-19: the broker fill is the truth; make sure the journal has the FILLED row (scanner may have killed the setup after the fill)
            if (t.Strategy == "raid-fvg" && t.SweepTs > 0)
                AppJournal.EnsureFilledFromBroker(market, t.Side == "SELL", t.Entry, t.Stop, t.SweepTs, Math.Abs(t.Entry - t.Stop), t.FillPrice ?? t.Entry);
            Emit(new { type = "exec-open", market, side = t.Side, fill = t.FillPrice, stop, target, qty });
            await CancelRestingIfFull(t, livePos, fetchMs);
        }
        catch (Exception ex)
        {
            Log(new { ev = "open-error", market, error = ex.Message });
            Status = "error: " + ex.Message;
        }
        finally { if (t != null) t.OpenDone = true; }
    }

    // ------------------------------------------------------------ reconcile
    // 2026-10-29: DXtrade order JSON keeps positionCode, positionEffect and the order price inside legs[]; the top level may be null.
    private static JsonNode? Leg0(JsonNode? o) => (o?["legs"] as JsonArray)?.FirstOrDefault();
    private static string? LegPositionCode(JsonNode? o) => o?["positionCode"]?.ToString() ?? Leg0(o)?["positionCode"]?.ToString();
    private static string LegEffect(JsonNode? o) => o?["positionEffect"]?.ToString() ?? Leg0(o)?["positionEffect"]?.ToString() ?? "";
    /// <summary>Order price of a STOP (stopPrice) or LIMIT (limitPrice) row: top level, then legs[0].stopPrice/limitPrice, then legs[0].price
    /// (the live history row of a COMPLETED STOP carries only legs[0].price = the stop price; the WORKING-row shape is not yet observed).</summary>
    private static double? OrderPrice(JsonNode? o, bool isStop)
    {
        if (o == null) return null; string k = isStop ? "stopPrice" : "limitPrice"; var leg = Leg0(o);
        foreach (var v in new[] { TryDouble(o[k]), TryDouble(leg?[k]), TryDouble(leg?["price"]) }) if (v > 0) return v;
        return null;
    }
    /// <summary>Our code = what follows the last ':' of the broker's prefixed orderCode; null when empty (so EndsWith can never match everything).</summary>
    private static string? CodeTail(JsonNode? o) { string c = o?["orderCode"]?.ToString() ?? ""; int i = c.LastIndexOf(':'); c = i >= 0 ? c[(i + 1)..] : c; return c.Length > 0 ? c : null; }
    /// <summary>Fill price of a COMPLETED order: legs[0].averagePrice, else the last execution's lastPrice/averagePrice, else the old flat fields.</summary>
    private static double? FillPriceOf(JsonNode? ord)
    {
        if (ord == null) return null;
        var leg = TryDouble(Leg0(ord)?["averagePrice"]); if (leg > 0) return leg;
        var ex = ord["executions"] as JsonArray;
        if (ex != null) for (int i = ex.Count - 1; i >= 0; i--) { var v = TryDouble(ex[i]?["lastPrice"]) ?? TryDouble(ex[i]?["averagePrice"]) ?? TryDouble(ex[i]?["price"]); if (v > 0) return v; }
        var flat = TryDouble(ord["averagePrice"]) ?? TryDouble(ord["fillPrice"]) ?? TryDouble(ord["price"]); return flat > 0 ? flat : null;
    }
    /// <summary>The history row whose orderCode ends with our code (the broker ignores ?with-order-code and prefixes codes with the session).</summary>
    private static JsonNode? HistoryRow(JsonNode? hist, string? code) => string.IsNullOrEmpty(code) ? null : (hist?["orders"] as JsonArray)?.FirstOrDefault(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(code));

    /// <summary>Net $ the broker settled for one position: its SETTLEMENT row plus every COMMISSION row (open and close), as
    /// tools/trades_cal_backfill.py; overnight FINANCING stays as the backfill's own calendar rows, not here. Read-only.
    /// The opening COMMISSION is booked at the fill (seen on the 0003 account's /transfers 2026-11-22), so this waits for the SETTLEMENT
    /// row: up to 6 reads 10 s apart. Null if it never shows. Runs off the Reconcile loop.</summary>
    private async Task<double?> SettledPnl(string positionCode)
    {
        for (int i = 0; i < 6; i++)
        {
            try
            {
                var tr = await Get($"/accounts/{_accountPath}/transfers");
                double sum = 0; bool settled = false;
                foreach (var x in (tr?["cashTransfers"] as JsonArray) ?? new JsonArray())
                    foreach (var c in (x?["cashTransactions"] as JsonArray) ?? new JsonArray())
                    {
                        string type = c?["type"]?.ToString() ?? "";
                        if ((type != "SETTLEMENT" && type != "COMMISSION") || c?["positionCode"]?.ToString() != positionCode) continue;
                        var v = TryDouble(c?["value"]); if (v.HasValue) { sum += v.Value; if (type == "SETTLEMENT") settled = true; }
                    }
                if (settled) return Math.Round(sum, 2);
            }
            catch (Exception ex) { Log(new { ev = "settled-pnl-error", positionCode, error = ex.Message }); }
            if (i < 5) await Task.Delay(10_000);
        }
        return null;
    }

    /// <summary>Read the broker's open positions on our markets and track the ones we do not already hold (restart safety). Places nothing.</summary>
    private async Task AdoptExisting()
    {
        try
        {
            var positions = (await Get($"/accounts/{_accountPath}/positions"))?["positions"]?.AsArray() ?? new JsonArray();
            var orders = (await Get($"/accounts/{_accountPath}/orders"))?["orders"]?.AsArray() ?? new JsonArray();
            foreach (var p in positions)
            {
              try
              {
                string? pc = p?["positionCode"]?.ToString(); string sym = p?["symbol"]?.ToString() ?? "";
                string? market = Markets.FirstOrDefault(m => MapSymbol(m) == sym) ?? (FxMode != "off" ? FxMarkets.FirstOrDefault(m => m == sym) : null);
                if (pc == null || market == null) continue;
                bool isFx = FxMarkets.Contains(market) && !Markets.Contains(market);
                // 2026-11-22 (rig T8): a resting entry that filled during a re-login is ours, not a stray position: its own wait loop claims it
                lock (_gate) { if (_open.Any(t => t.PositionCode == pc || t.OpenOrderId == pc || (t.MarketOrderId != null && t.MarketOrderId == pc))) continue; }
                string side = (p?["side"]?.ToString() ?? "").ToUpperInvariant();
                decimal qty = (decimal)(TryDouble(p?["quantity"]) ?? 0); if (qty < 0) { qty = -qty; if (side == "") side = "SELL"; }
                if (side == "") side = "BUY";
                double fill = TryDouble(p?["openPrice"]) ?? TryDouble(p?["price"]) ?? 0;
                string? Tail(JsonNode? o) => CodeTail(o);
                // 2026-10-29: the broker's order rows carry positionCode only inside legs[] (top level null); match there. Fallback by
                // symbol + opposite side only when this is the ONLY broker position on the symbol and the order names no position at
                // all (review: otherwise a naked position could borrow another position's stop/TP and be marked Protected).
                string oppSideA = side == "BUY" ? "SELL" : "BUY";
                var working = orders.Where(o => o?["status"]?.ToString() == "WORKING").ToList();
                var mine = working.Where(o => LegPositionCode(o) == pc).ToList();
                int samePos = positions.Count(q => (q?["symbol"]?.ToString() ?? "") == sym);
                if (mine.Count == 0 && samePos == 1)
                    mine = working.Where(o => LegPositionCode(o) == null && (o?["instrument"]?.ToString() ?? o?["symbol"]?.ToString()) == sym
                                              && (o?["side"]?.ToString() ?? "").ToUpperInvariant() == oppSideA && (LegEffect(o) == "CLOSE" || LegEffect(o) == "")).ToList();
                var so = mine.FirstOrDefault(o => (o?["type"]?.ToString() ?? "").StartsWith("STOP"));
                var to = mine.FirstOrDefault(o => o?["type"]?.ToString() == "LIMIT");
                double stop = OrderPrice(so, true) ?? 0, target = OrderPrice(to, false) ?? 0;
                var t = new Trade
                {
                    Market = market, Symbol = sym, Side = side, Strategy = isFx ? FxStrategy : "adopted", SweepTs = 0, Entry = fill, FillPrice = fill,
                    ExitAtMs = isFx ? FxFlatTimeToday() : 0,
                    Stop = stop > 0 ? stop : fill, Target = target > 0 ? target : fill, Qty = qty, PositionCode = pc, Protected = so != null,
                    StopCode = so != null ? Tail(so) : null, TpCode = to != null ? Tail(to) : null, OpenCode = "adopted-" + pc,
                    OpenedMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()
                };
                { double? ot = TryDouble(p?["openTime"]) ?? TryDouble(p?["time"]); t.FilledMs = ot.HasValue && ot.Value > 1e12 ? (long)ot.Value : DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(); }
                lock (_gate) _open.Add(t);
                Log(new { ev = so != null ? "adopted" : "adopted-UNPROTECTED", market, positionCode = pc, side, qty, fill, stop = so != null ? stop : (double?)null, target = to != null ? target : (double?)null, why = so == null ? "no working STOP order found for this position: protect it by hand" : null });
              }
              catch (Exception ex) { Log(new { ev = "adopt-row-error", positionCode = p?["positionCode"]?.ToString(), error = ex.Message }); }   // one bad row must not abort the others
            }
        }
        catch (Exception ex) { Log(new { ev = "adopt-error", error = ex.Message }); }
    }

    private async Task Reconcile(CancellationToken ct)
    {
        List<Trade> snapshot; lock (_gate) snapshot = _open.ToList();
        if (snapshot.Count == 0) return;
        if (Mode != "live")
        {
            // dry mode: trades are closed by the scanner's own grading; keep them until journal grades them
            foreach (var t in snapshot)
                if (AppJournal.IsGraded(t.Market, t.SweepTs)) { lock (_gate) _open.Remove(t); MarkTraded(t.Market + "|" + t.SweepTs); }
            return;
        }
        var positions = (await Get($"/accounts/{_accountPath}/positions"))?["positions"]?.AsArray() ?? new JsonArray();
        var orders = (await Get($"/accounts/{_accountPath}/orders"))?["orders"]?.AsArray() ?? new JsonArray();
        foreach (var t in snapshot)
        {
            if (t.PositionCode == null) continue;
            bool stillOpen = positions.Any(p => p?["positionCode"]?.ToString() == t.PositionCode);
            if (stillOpen)
            {
                if (t.OverCap)
                {
                    // 2026-11-22: an over-cap fill is never protected or kept; re-send the market close until the position is gone
                    long nowOc = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                    if (nowOc - t.OverCapSentMs >= 20_000 && await EmergencyClose(t, 1)) { t.OverCapSentMs = nowOc; Log(new { ev = "over-cap-close-resent", t.Market, positionCode = t.PositionCode }); }
                    continue;
                }
                if (t.EmergencyFailed)
                {
                    if (await EmergencyClose(t, 1)) { t.EmergencyFailed = false; Log(new { ev = "emergency-close-retry-sent", t.Market, positionCode = t.PositionCode }); }
                    continue;
                }
                if (!t.Protected)
                {
                    // 2026-10-25 (rig, three-account run): an HTTP error between the fill and the STOP post left a position open with no
                    // stop and nothing retrying. Once the Open task is done, place whatever protection is missing; if that fails, close.
                    if (!t.OpenDone) continue;   // the normal protection step is still running; do not race it
                    try
                    {
                        string opp = t.Side == "BUY" ? "SELL" : "BUY";
                        bool stopOk = t.StopCode != null && orders.Any(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(t.StopCode));
                        bool tpOk = t.TpCode != null && orders.Any(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(t.TpCode));
                        if (!stopOk)
                        {
                            t.StopCode = Code("stop", t.Market);
                            await Post($"/accounts/{_accountPath}/orders", new JsonObject
                            {
                                ["orderCode"] = t.StopCode, ["type"] = "STOP", ["instrument"] = t.Symbol, ["positionEffect"] = "CLOSE",
                                ["positionCode"] = t.PositionCode, ["side"] = opp, ["stopPrice"] = Px(t.Stop), ["tif"] = "GTC"
                            });
                        }
                        if (!tpOk)
                        {
                            t.TpCode = Code("tp", t.Market);
                            await Post($"/accounts/{_accountPath}/orders", new JsonObject
                            {
                                ["orderCode"] = t.TpCode, ["type"] = "LIMIT", ["instrument"] = t.Symbol, ["positionEffect"] = "CLOSE",
                                ["positionCode"] = t.PositionCode, ["side"] = opp, ["limitPrice"] = Px(t.Target), ["tif"] = "GTC"
                            });
                        }
                        await Task.Delay(1500);
                        var again = (await Get($"/accounts/{_accountPath}/orders"))?["orders"]?.AsArray() ?? new JsonArray();
                        t.Protected = again.Any(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(t.StopCode)) && again.Any(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(t.TpCode));
                    }
                    catch (Exception ex) { Log(new { ev = "late-protect-error", t.Market, error = ex.Message }); }
                    if (t.Protected)
                    {
                        if (t.FilledMs == 0) t.FilledMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                        Log(new { ev = "protected-late", t.Market, strategy = t.Strategy, positionCode = t.PositionCode, fill = t.FillPrice, t.Stop, t.Target, qty = t.Qty });
                        continue;
                    }
                    Log(new { ev = "UNPROTECTED-emergency-close", t.Market, why = "protection could not be placed after the fill" });
                    MarkTraded(t.Market + "|" + t.SweepTs);
                    if (await EmergencyClose(t, 3)) { lock (_gate) _open.Remove(t); continue; }
                    t.EmergencyFailed = true;
                    Log(new { ev = "EMERGENCY-CLOSE-FAILED", t.Market, positionCode = t.PositionCode, why = "3 attempts failed; retrying every cycle; CLOSE BY HAND if it persists" });
                    Emit(new { type = "exec-alert", market = t.Market, slot = Slot, account = Mask(_accountCode ?? ""), text = "EMERGENCY CLOSE FAILED on " + t.Market + ": position has no stop. Retrying. Close it by hand if this stays." });
                    continue;
                }
                long nowMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                bool timeUp = t.ExitAtMs > 0 ? nowMs >= t.ExitAtMs : nowMs - t.FilledMs >= TimeStopMin * 60_000L;
                if (t.Protected && !t.TimeSent && t.FilledMs > 0 && timeUp)
                {
                    t.TimeSent = true; t.TimeCode = Code("time", t.Market);
                    string oppSide = t.Side == "BUY" ? "SELL" : "BUY";
                    try
                    {
                        await Post($"/accounts/{_accountPath}/orders", new JsonObject
                        {
                            ["orderCode"] = t.TimeCode, ["type"] = "MARKET", ["instrument"] = t.Symbol,
                            ["positionEffect"] = "CLOSE", ["positionCode"] = t.PositionCode, ["side"] = oppSide, ["tif"] = "GTC"
                        });
                        Log(new { ev = "time-exit-sent", t.Market, positionCode = t.PositionCode, heldMin = (nowMs - t.FilledMs) / 60_000, limitMin = TimeStopMin });
                    }
                    catch (Exception ex) { Log(new { ev = "time-exit-error", t.Market, error = ex.Message }); t.TimeSent = false; }
                }
                continue;
            }

            // closed: cancel whichever sibling is still working, then journal the result
            foreach (var code in new[] { t.StopCode, t.TpCode })
            {
                var w = orders.FirstOrDefault(o => !string.IsNullOrEmpty(code) && (o?["orderCode"]?.ToString() ?? "").EndsWith(code));
                if (w != null)
                {
                    try { await Delete($"/accounts/{_accountPath}/orders/{Uri.EscapeDataString(w["orderCode"]!.ToString())}"); }
                    catch (Exception ex) { Log(new { ev = "cancel-sibling-error", t.Market, error = ex.Message }); }
                }
            }
            // 2026-10-29: one history read; the broker ignores ?with-order-code (returned the whole list, oldest first) and the fill
            // price lives in legs[0].averagePrice, so every close used to be labelled "TP" with no exit. Label by which order COMPLETED.
            double? exit = null; string how = "unknown";
            try
            {
                var hist = await Get($"/accounts/{_accountPath}/orders/history");
                var stopRow = HistoryRow(hist, t.StopCode); var tpRow = HistoryRow(hist, t.TpCode); var timeRow = HistoryRow(hist, t.TimeCode);
                if (stopRow?["status"]?.ToString() == "COMPLETED") { how = "STOP"; exit = FillPriceOf(stopRow); }
                else if (tpRow?["status"]?.ToString() == "COMPLETED") { how = "TP"; exit = FillPriceOf(tpRow); }
                else if (t.TimeSent && timeRow?["status"]?.ToString() == "COMPLETED") { how = "TIME"; exit = FillPriceOf(timeRow); }
                else if (stopRow != null || tpRow != null) how = "manual/other";   // siblings seen in the history, neither filled: closed by hand or by the broker
                else if (hist?["orders"] == null) how = "unknown";
                else how = "manual/other";
                if (exit == null && how != "unknown") Log(new { ev = "close-no-exit-price", t.Market, how, why = "history row has no fill price; r and pnl left null" });
            }
            catch (Exception ex) { how = "unknown"; Log(new { ev = "close-history-error", t.Market, error = ex.Message }); }
            if (t.OverCap) { how = "over-cap"; t.Strategy = "over-cap"; }   // review C: its own bucket in the record, not a raid-fvg loss
            double risk = Math.Abs((t.FillPrice ?? t.Entry) - t.Stop);
            double? r = exit.HasValue && risk > 0 ? (t.Side == "BUY" ? (exit.Value - (t.FillPrice ?? t.Entry)) : ((t.FillPrice ?? t.Entry) - exit.Value)) / risk : null;
            double? pnl = exit.HasValue ? Math.Round((t.Side == "BUY" ? (exit.Value - (t.FillPrice ?? t.Entry)) : ((t.FillPrice ?? t.Entry) - exit.Value)) * (double)t.Qty * t.UnitsPerQty, 2) : null;
            if (pnl.HasValue && t.Strategy == FxStrategy && t.Market.StartsWith("USD") && exit > 0) pnl = Math.Round(pnl.Value / exit!.Value, 2);   // USDJPY P&L is in yen
            lock (_gate) _open.Remove(t); MarkTraded(t.Market + "|" + t.SweepTs);
            if (pnl.HasValue || string.IsNullOrEmpty(t.PositionCode) || t.PositionCode == "DRY")
            {
                Log(new { ev = "closed", t.Market, strategy = t.Strategy, how, fill = t.FillPrice, exit, r = r.HasValue ? Math.Round(r.Value, 3) : (double?)null, pnl, qty = t.Qty, sweepTs = t.SweepTs });
                Emit(new { type = "exec-close", market = t.Market, how, exit, r });
                continue;
            }
            // 2026-11-22 (the user: hand closes showed $0 on the calendar): no exit price from our own orders -> the broker's settled $ for this
            // position (SETTLEMENT + COMMISSION rows on /transfers, as tools/trades_cal_backfill.py). Read in the background so Reconcile never
            // waits on it (review finding 4); the closed row is written when the SETTLEMENT row is there, or after ~1 min with pnl null.
            var tc = t; string howC = how; double riskC = risk; var acctTag = _logAcct.Value;
            _ = Task.Run(async () =>
            {
                _logAcct.Value = acctTag;
                double? pn = await SettledPnl(tc.PositionCode!); double? rr = null;
                if (pn.HasValue && riskC > 0 && tc.Qty > 0 && tc.Strategy != FxStrategy) rr = pn.Value / (riskC * (double)tc.Qty * tc.UnitsPerQty);
                Log(new { ev = pn.HasValue ? "close-pnl-from-broker" : "close-pnl-missing", tc.Market, positionCode = tc.PositionCode, pnl = pn });
                Log(new { ev = "closed", tc.Market, strategy = tc.Strategy, how = howC, fill = tc.FillPrice, exit = (double?)null, r = rr.HasValue ? Math.Round(rr.Value, 3) : (double?)null, pnl = pn, qty = tc.Qty, sweepTs = tc.SweepTs });
                Emit(new { type = "exec-close", market = tc.Market, how = howC, exit = (double?)null, r = rr });
            });
        }
    }

    /// <summary>Reserve a (market, sweep) slot synchronously so two scanner calls a few ms apart cannot both open. Returns false if taken.</summary>
    private bool Reserve(string market, long sweepTs, string ev)
    {
        string key = market + "|" + sweepTs;
        lock (_gate)
        {
            if (_inflight.Contains(key) || _inflight.Any(k => k.StartsWith(market + "|"))) { Log(new { ev = "skip", market, why = "open already in flight (" + ev + ")" }); return false; }
            if (_open.Any(t => t.Market == market)) { Log(new { ev = "skip", market, why = "position already open on market (" + ev + ")" }); return false; }
            if (_traded.Contains(key) || AppJournal.IsGraded(market, sweepTs)) { Log(new { ev = "skip-traded", market, sweepTs, why = "setup already traded: one trade per setup (" + ev + ")" }); return false; }
            // 2026-10-22 (rig V5): an armed trade sits in BOTH _open and _inflight until Open() returns, so count a reservation only while its market is not yet in _open
            int reserving = _inflightCall.Count(k => !_open.Any(t => k.StartsWith(t.Market + "|")));   // 2026-11-22: arms in flight hold no slot (three setups armed in one scan)
            if (SlotsUsed() + reserving >= MaxOpen) { Log(new { ev = "skip", market, why = $"open cap {MaxOpen} (" + ev + ")" }); return false; }
            _inflight.Add(key); if (ev == "call") _inflightCall.Add(key); return true;
        }
    }
    private void Release(string market, long sweepTs) { string k = market + "|" + sweepTs; lock (_gate) { _inflight.Remove(k); _inflightCall.Remove(k); } }

    // 2026-11-22 FIRST TO FILL WINS (the user: ETH skipped behind two resting orders that never filled). The backtest (GLOBAL 2) counts a
    // trade from its FILL; live used to count a resting armed order as a slot. Now a resting ARMED order holds no slot; a filled position
    // (or a plain call entry, which fills within minutes) does. At the fill the slot is claimed under the lock; a fill beyond MaxOpen is
    // closed at once; when the cap is reached every other resting entry is cancelled. SlotsUsed/FilledCount: call with _gate held.
    private int SlotsUsed() => _open.Count(t => t.Strategy != FxStrategy && ((t.PositionCode != null && !t.OverCap) || !t.Armed || t.MarketSent));
    private int FilledCount(Trade? except) => _open.Count(t => t != except && t.Strategy != FxStrategy && t.PositionCode != null && !t.OverCap);
    /// <summary>Filled trades that still hold a slot, judged on a positions read taken at fetchMs: a trade counts if its position is in that
    /// read, or it was claimed after the read (a fill the read could not see yet). A position that closed but is not yet journaled does NOT
    /// count (review finding 1), so it cannot push a good fill over the cap. Over-cap fills never count. Call with _gate held.</summary>
    private int FilledLive(Trade? except, HashSet<string> live, long fetchMs) =>
        _open.Count(o => o != except && o.Strategy != FxStrategy && !o.OverCap && o.PositionCode != null &&
                         (o.PositionCode == "DRY" || live.Contains(o.PositionCode) || o.ClaimMs >= fetchMs));

    /// <summary>Cancel a resting entry, 3 attempts 2 s apart. False = every attempt failed (the caller checks the order's status before alarming).</summary>
    private async Task<bool> CancelEntry(Trade t)
    {
        for (int i = 0; i < 3; i++)
        {
            try { await Delete($"/accounts/{_accountPath}/orders/{Uri.EscapeDataString(t.OpenCode)}"); return true; }
            catch (Exception ex) { Log(new { ev = "cancel-error", t.Market, attempt = i + 1, error = ex.Message }); }
            if (i < 2) await Task.Delay(2000);
        }
        return false;
    }

    /// <summary>After a fill is protected: if MaxOpen trades are now filled, pull every other resting entry (no slot left for it).</summary>
    private async Task CancelRestingIfFull(Trade filled, HashSet<string> live, long fetchMs)
    {
        List<Trade> pull;
        lock (_gate)
        {
            if (filled.Strategy == FxStrategy || FilledLive(null, live, fetchMs) < MaxOpen) return;
            pull = _open.Where(o => o != filled && o.Strategy != FxStrategy && o.PositionCode == null && o.DeadWhy == null).ToList();
            foreach (var o in pull) o.DeadWhy = $"cap reached: {MaxOpen} trades filled (first to fill wins)";
        }
        foreach (var o in pull)
        {
            if (o.Rejected) continue;   // its LIMIT was never live at the broker
            try { await Delete($"/accounts/{_accountPath}/orders/{Uri.EscapeDataString(o.OpenCode)}"); o.CapCancelled = true; Log(new { ev = "cap-cancel-sent", o.Market, orderCode = o.OpenCode }); }
            catch (Exception ex) { Log(new { ev = "cap-cancel-error", o.Market, error = ex.Message, why = "the entry's own wait loop cancels it again and checks for a fill" }); }
        }
    }
    /// <summary>True when the live side already holds, or is opening, this market's sweep (the scanner must not suppress its journal row).</summary>
    public bool IsEngaged(string market, long sweepTs) { lock (_gate) return _open.Any(t => t.Market == market && t.SweepTs == sweepTs) || _inflight.Contains(market + "|" + sweepTs); }

    /// <summary>MARKET close of an unprotected position, up to n attempts 2 s apart. True when an attempt was accepted.</summary>
    private async Task<bool> EmergencyClose(Trade t, int attempts)
    {
        string opp = t.Side == "BUY" ? "SELL" : "BUY";
        for (int i = 0; i < attempts; i++)
        {
            try
            {
                await Post($"/accounts/{_accountPath}/orders", new JsonObject
                {
                    ["orderCode"] = Code("emerg", t.Market), ["type"] = "MARKET", ["instrument"] = t.Symbol,
                    ["positionEffect"] = "CLOSE", ["positionCode"] = t.PositionCode, ["side"] = opp, ["tif"] = "GTC"
                });
                return true;
            }
            catch (Exception ex) { Log(new { ev = "emergency-close-error", t.Market, attempt = i + 1, error = ex.Message }); }
            if (i + 1 < attempts) await Task.Delay(2000);
        }
        return false;
    }

    /// <summary>MARKET entry used only when the broker rejected the resting LIMIT and price is at the entry (backtest = fill at the touch).</summary>
    private async Task SendMarketEntry(Trade t, string why)
    {
        lock (_gate)
        {
            if (t.MarketSent || t.PositionCode != null || t.DeadWhy != null) return;
            // 2026-11-22 review finding 6: a rejected armed entry holds no slot; never send its MARKET entry when the cap is already full
            if (t.Strategy != FxStrategy && FilledCount(t) >= MaxOpen) { Log(new { ev = "skip", t.Market, why = $"open cap {MaxOpen} (market entry after rejected LIMIT)" }); return; }
            t.MarketSent = true;
        }
        // 2026-10-23 price gate: the backtest fills at the touch of the entry. A MARKET entry is only that fill when the live
        // price is still between the stop and the entry. Past the stop (XRP 2026-10-23 14:16, ETH 2026-10-21) it is a trade
        // the backtest never takes, with a stop that cannot be placed. No live price = no entry.
        double? px = await LivePrice(t.Market);
        double mid = (t.Entry + t.Stop) / 2.0;   // review finding 11: one tick above the stop must not pass; the entry-side half is the structural line
        bool between = px.HasValue && (t.Side == "BUY" ? (px.Value > mid && px.Value <= t.Entry) : (px.Value < mid && px.Value >= t.Entry));
        if (!between)
        {
            string reason = px.HasValue ? "live price is not in the entry-side half between the stop and the entry" : "no live price";
            Log(new { ev = "market-fallback-SKIPPED", t.Market, why, livePrice = px, t.Entry, t.Stop, reason });
            lock (_gate) t.DeadWhy = "market fallback skipped: " + reason + (px.HasValue ? " (live " + px.Value.ToString(System.Globalization.CultureInfo.InvariantCulture) + ")" : "");
            return;
        }
        try
        {
            string code = Code("mkt", t.Market);
            var ack = await Post($"/accounts/{_accountPath}/orders", new JsonObject
            {
                ["orderCode"] = code, ["type"] = "MARKET", ["instrument"] = t.Symbol,
                ["quantity"] = t.Qty.ToString(System.Globalization.CultureInfo.InvariantCulture),
                ["positionEffect"] = "OPEN", ["side"] = t.Side, ["tif"] = "GTC"
            });
            t.MarketOrderId = ack?["orderId"]?.ToString();
            Log(new { ev = "market-fallback", t.Market, why, orderId = t.MarketOrderId, orderCode = code, t.Entry, t.Stop, qty = t.Qty });
        }
        catch (Exception ex) { Log(new { ev = "market-fallback-error", t.Market, error = ex.Message }); lock (_gate) t.DeadWhy ??= "market entry failed: " + ex.Message; }
    }

    /// <summary>Every real action the executor took, from executions.jsonl, for the Trades drawer.</summary>
    private class StratStat { public string Strategy = ""; public int Trades, Wins, Losses; public double R, Pnl; public int Open; }
    public static readonly Dictionary<string, string> StrategyNames = new() { ["raid-fvg"] = "15m Liquidity Raid \u2192 5m FVG", [FxStrategy] = "Forex Midnight (EUR/USD, USD/JPY, AUD/USD)" };

    public string RecordJson()
    {
        var rows = new List<object>();
        var stats = new Dictionary<string, StratStat>();
        try
        {
            string path = Path.Combine(AppJournal.VaultFolder, "executions.jsonl");
            if (File.Exists(path))
                foreach (var line in File.ReadAllLines(path))
                {
                    if (string.IsNullOrWhiteSpace(line)) continue;
                    JsonObject? o; try { o = JsonNode.Parse(line)?.AsObject(); } catch { continue; }
                    if (o == null) continue;
                    string ev = o["ev"]?.ToString() ?? "";
                    if (ev is "protected" or "closed" or "dry-open" or "armed" or "armed-cancelled" or "entry-NOFILL-cancelled" or "entry-REJECTED" or "market-fallback" or "halt" or "UNPROTECTED-emergency-close" || ev.StartsWith("TEST-final"))
                    {
                        string iso = o["utcIso"]?.ToString() ?? "";
                        rows.Add(new
                        {
                            utc = iso[..Math.Min(16, iso.Length)],
                            ev, market = o["market"]?.ToString() ?? o["Market"]?.ToString() ?? "",
                            fill = o["fill"]?.ToString() ?? o["entry"]?.ToString(), stop = o["stop"]?.ToString(), target = o["target"]?.ToString(),
                            exit = o["exit"]?.ToString(), how = o["how"]?.ToString() ?? o["why"]?.ToString(), r = o["r"]?.ToString(), qty = o["qty"]?.ToString(),
                            pnl = o["pnl"]?.ToString(), strategy = o["strategy"]?.ToString() ?? "raid-fvg"
                        });
                        if (ev == "closed")
                        {
                            string sk = o["strategy"]?.ToString() ?? "raid-fvg";
                            if (!stats.TryGetValue(sk, out var st)) stats[sk] = st = new StratStat { Strategy = sk };
                            double rv = double.TryParse(o["r"]?.ToString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var rr) ? rr : 0;
                            double pv = double.TryParse(o["pnl"]?.ToString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var pp) ? pp : 0;
                            st.Trades++; if (rv > 0) st.Wins++; else if (rv < 0) st.Losses++; st.R += rv; st.Pnl += pv;
                        }
                    }
                }
        }
        catch { }
        rows.Reverse();
        lock (_gate) foreach (var t in _open) { if (!stats.TryGetValue(t.Strategy, out var st)) stats[t.Strategy] = st = new StratStat { Strategy = t.Strategy }; st.Open++; }
        foreach (var k in StrategyNames.Keys) if (!stats.ContainsKey(k)) stats[k] = new StratStat { Strategy = k };
        var strategies = stats.Values.Select(s => new { id = s.Strategy, name = StrategyNames.TryGetValue(s.Strategy, out var n) ? n : s.Strategy, s.Trades, s.Wins, s.Losses, r = Math.Round(s.R, 2), pnl = Math.Round(s.Pnl, 2), s.Open }).OrderByDescending(s => s.r).ToList();
        return JsonSerializer.Serialize(new { type = "exec-record", rows, strategies, mode = Mode, equity = LastEquity, balance = LastBalance, startEquity = _startEquity });
    }

    // ------------------------------------------------------------ REST
    private async Task Login(CancellationToken ct)
    {
        _logAcct.Value = _acctTag;
        await _loginGate.WaitAsync(ct);
        try
        {
            string? shared; lock (_sharedTokens) _sharedTokens.TryGetValue(_user, out shared);
            if (shared != null)
            {
                _token = shared; Log(new { ev = "session-shared", slot = Slot });
            }
            else
            {
                var body = new JsonObject { ["username"] = _user, ["domain"] = "default", ["password"] = _pass };
                using var resp = await _http.PostAsync(Base + "/login", new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json"), ct);
                string txt = await resp.Content.ReadAsStringAsync(ct);
                if (!resp.IsSuccessStatusCode) throw new Exception($"login HTTP {(int)resp.StatusCode} {Truncate(txt, 160)}");
                _token = JsonNode.Parse(txt)?["sessionToken"]?.ToString() ?? throw new Exception("login: no sessionToken");
                lock (_sharedTokens) _sharedTokens[_user] = _token;
            }
            _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("DXAPI", _token);
        }
        finally { _loginGate.Release(); }

        var users = await Get("/users");
        var accounts = users?["userDetails"]?.AsArray().SelectMany(u => u?["accounts"]?.AsArray() ?? new JsonArray())
                       ?? users?["accounts"]?.AsArray() ?? new JsonArray();
        var acct = _accountWanted.Length > 0
            ? accounts.FirstOrDefault(a => (a?["account"]?.ToString() ?? "").EndsWith(_accountWanted) && a?["accountStatus"]?.ToString() == "FULL_TRADING")
              ?? throw new Exception($"account {_accountWanted} not found or not FULL_TRADING on this login (set BROKER_ACCOUNT)")
            : accounts.FirstOrDefault(a => a?["accountStatus"]?.ToString() == "FULL_TRADING")
              ?? throw new Exception("no FULL_TRADING account on this login");
        _accountCode = acct["account"]!.ToString();
        _accountPath = Uri.EscapeDataString(_accountCode);
        _logAcct.Value = _acctTag;
        var (equity, balance) = await Metrics();
        if (_startEquity <= 0) _startEquity = StartEquityCfg > 0 ? StartEquityCfg : (balance > 0 ? balance : equity);
        RollDay(equity);
        Status = $"{Mode} | logged in | equity {equity:F2} | risk {RiskPct}%";
        Log(new { ev = "login", account = Mask(_accountCode), equity, balance, mode = Mode });
    }

    private async Task Logout()
    {
        if (_token == null) return;
        lock (_sharedTokens) { if (_sharedTokens.TryGetValue(_user, out var cur) && cur == _token) _sharedTokens.Remove(_user); }
        try { await _http.PostAsync(Base + "/logout", new StringContent("", Encoding.UTF8, "application/json")); } catch { }
        _token = null;
    }

    /// <summary>Live mid from Hyperliquid (the scanner's feed): null on any failure.</summary>
    private async Task<double?> LivePrice(string market)
    {
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(5));
            using var resp = await _priceHttp.PostAsync(PriceUrl, new StringContent("{\"type\":\"allMids\"}", Encoding.UTF8, "application/json"), cts.Token);
            if (!resp.IsSuccessStatusCode) return null;
            var j = JsonNode.Parse(await resp.Content.ReadAsStringAsync(cts.Token));
            string coin = market.EndsWith("USDT") ? market[..^4] : market;
            return double.TryParse(j?[coin]?.ToString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var v) && v > 0 ? v : null;
        }
        catch { return null; }
    }

    private async Task<(double equity, double balance)> Metrics()
    {
        var m = (await Get($"/accounts/{_accountPath}/metrics"))?["metrics"]?.AsArray().FirstOrDefault();
        return (TryDouble(m?["equity"]) ?? 0, TryDouble(m?["balance"]) ?? 0);
    }

    private async Task<Instrument> InstrumentFor(string symbol)
    {
        if (_instruments.TryGetValue(symbol, out var i)) return i;
        var q = await Get($"/accounts/{_accountPath}/instruments/query?symbols={Uri.EscapeDataString(symbol)}");
        var n = (q?["instrumentDetails"] ?? q?["instruments"])?.AsArray().FirstOrDefault(x => x?["symbol"]?.ToString() == symbol)
                ?? throw new Exception($"instrument {symbol} not found on account");
        var inst = new Instrument(symbol, n["tradingStatus"]?.ToString() ?? "?",
            TryDec(n["minOrderSize"]) ?? 0.001m, TryDec(n["minOrderSizeIncrement"]) ?? 0.001m, TryDec(n["maxOrderSize"]) ?? 0m);
        _instruments[symbol] = inst;
        return inst;
    }

    private async Task<JsonNode?> Get(string path)
    {
        using var resp = await _http.GetAsync(Base + path);
        string txt = await resp.Content.ReadAsStringAsync();
        if (resp.StatusCode == System.Net.HttpStatusCode.Unauthorized)
        {
            lock (_sharedTokens) { if (_token != null && _sharedTokens.TryGetValue(_user, out var cur) && cur == _token) _sharedTokens.Remove(_user); }
            _token = null; throw new Exception("session expired");
        }
        if (!resp.IsSuccessStatusCode) throw new Exception($"GET {path} HTTP {(int)resp.StatusCode} {Truncate(txt, 160)}");
        return string.IsNullOrWhiteSpace(txt) ? null : JsonNode.Parse(txt);
    }

    private async Task<JsonNode?> Post(string path, JsonObject body)
    {
        using var resp = await _http.PostAsync(Base + path, new StringContent(body.ToJsonString(), Encoding.UTF8, "application/json"));
        string txt = await resp.Content.ReadAsStringAsync();
        if (!resp.IsSuccessStatusCode) throw new Exception($"POST {path} HTTP {(int)resp.StatusCode} {Truncate(txt, 200)}");
        return string.IsNullOrWhiteSpace(txt) ? null : JsonNode.Parse(txt);
    }

    private async Task Delete(string path)
    {
        using var resp = await _http.DeleteAsync(Base + path);
        if (!resp.IsSuccessStatusCode) throw new Exception($"DELETE {path} HTTP {(int)resp.StatusCode}");
    }

    // ------------------------------------------------------------ helpers
    private static string MapSymbol(string market) => market.EndsWith("USDT") ? market[..^4] + "USD" : market;
    private static string Code(string kind, string market) => $"td-{kind}-{market[..3].ToLowerInvariant()}-{DateTimeOffset.UtcNow:yyyyMMddHHmmss}-{Random.Shared.Next(1000, 9999)}";
    private static string Px(double p) => p.ToString(p >= 100 ? "F2" : p >= 1 ? "F4" : "F6", System.Globalization.CultureInfo.InvariantCulture);
    private static decimal RoundDown(decimal q, decimal min, decimal inc)
    {
        if (inc <= 0) return q;
        if (q < min) return q;
        return min + Math.Floor((q - min) / inc) * inc;
    }
    private static double? TryDouble(JsonNode? n) => n == null ? null : double.TryParse(n.ToString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var d) ? d : null;
    private static decimal? TryDec(JsonNode? n) => n == null ? null : decimal.TryParse(n.ToString(), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var d) ? d : null;
    private static string Truncate(string? s, int n) => s == null ? "" : s.Length <= n ? s : s[..n] + "…";
    private static string Mask(string s) => s.Length <= 4 ? "…" : "…" + s[^4..];
    private void RollDay(double equity)
    {
        long key = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() / 86_400_000L;
        if (key != _dayKey) { _dayKey = key; _dayStartEquity = equity; }
    }
    private void Emit(object o) { try { Message?.Invoke(o); } catch { } }
    private static readonly object _logLock = new();   // 2026-10-25: three executors append to one file; FileMode.Append seeks to the end at open, so unserialised writers can overwrite each other's row (rig: lost open-intent/protected rows)
    private void Log(object o)   // instance since 2026-10-25: rows are tagged with this executor's account
    {
        try
        {
            Directory.CreateDirectory(AppJournal.VaultFolder);
            var row = JsonSerializer.SerializeToNode(o)!.AsObject();
            row["utcIso"] = DateTime.UtcNow.ToString("O");
            row["acct"] = _logAcct.Value ?? _acctTag;   // 2026-10-25 review #2: every row carries its account (Loop/Reconcile rows were untagged)
            var bytes = System.Text.Encoding.UTF8.GetBytes(row.ToJsonString() + Environment.NewLine);
            lock (_logLock)
            {
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    using (var fs = new FileStream(Path.Combine(AppJournal.VaultFolder, "executions.jsonl"), FileMode.Append, FileAccess.Write, FileShare.ReadWrite | FileShare.Delete)) { var b = bytes; fs.Write(b, 0, b.Length); }
                    break;
                }
                catch (IOException) when (attempt < 20) { Thread.Sleep(25); }   // a reader without sharing: retry up to 0.5 s rather than drop the row
            }
            }   // _logLock
        }
        catch { }
    }
}
