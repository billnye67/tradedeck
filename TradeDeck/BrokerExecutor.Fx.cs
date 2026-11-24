using System.Text.Json.Nodes;

namespace TradeDeck;

/// <summary>
/// Forex Midnight execution (strategy id "fx-midnight"). Separate from Strategy #1:
///   own switch BROKER_FX_MODE=off|dry|live (default off), own markets BROKER_FX_MARKETS, fixed risk BROKER_FX_RISK_USD (default 25),
///   does NOT use Strategy #1's crypto slots (BROKER_MAX_OPEN), one position per pair, one trade per pair+side+NY day.
/// Order shape: resting STOP entry at the trigger (sell stop below / buy stop above), on fill a full-close protective STOP,
/// no take-profit, MARKET close at 16:55 New York (before the 17:00 NY rollover, so no swap). Shares the account-level
/// daily stop and drawdown floor with Strategy #1.
/// </summary>
public sealed partial class BrokerExecutor
{
    public const string FxStrategy = "fx-midnight";
    public string FxMode { get; private set; } = "off";
    public HashSet<string> FxMarkets { get; private set; } = new() { "EURUSD", "USDJPY", "AUDUSD" };
    public double FxRiskUsd { get; private set; } = 25;
    private readonly Dictionary<string, Trade> _fxArmed = new();     // pair|S or pair|B -> armed (unfilled) entry
    private readonly HashSet<string> _fxDone = new();                // pair|S|yyyy-MM-dd -> traded today on this side

    private void LoadFxConfig(Dictionary<string, string> kv)
    {
        FxMode = kv.GetValueOrDefault("BROKER_FX_MODE", "off").ToLowerInvariant();
        if (kv.TryGetValue("BROKER_FX_MARKETS", out var fm)) FxMarkets = fm.Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries).ToHashSet();
        if (double.TryParse(kv.GetValueOrDefault("BROKER_FX_RISK_USD", "25"), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out var fr) && fr > 0) FxRiskUsd = fr;
        if (Mode == "off") FxMode = "off";                              // no account connection, no forex either
        if (FxMode == "live" && Mode != "live") FxMode = "dry";         // never live on forex while the account side is dry
    }

    public void LogEvent(object o) => Log(o);   // instance since 2026-10-25 (Log tags rows with the account)
    private static string FxSlot(string pair, bool isShort) => pair + "|" + (isShort ? "S" : "B");
    private static string FxDay(string pair, bool isShort, DateTime nyDay) => FxSlot(pair, isShort) + "|" + nyDay.ToString("yyyy-MM-dd");
    public bool FxDoneToday(string pair, bool isShort, DateTime nyDay) { lock (_gate) return _fxDone.Contains(FxDay(pair, isShort, nyDay)); }

    /// <summary>Pull an armed (unfilled) forex entry: outside the kill zone, or the setup is no longer valid.</summary>
    public void OnFxDisarm(string pair, bool isShort, string why)
    {
        lock (_gate) if (_fxArmed.TryGetValue(FxSlot(pair, isShort), out var t) && t.PositionCode == null && t.DeadWhy == null) t.DeadWhy = why;
    }

    /// <summary>Arm (or move) the resting entry for this pair and side. Same key = already armed = nothing to do.</summary>
    public void OnFxArm(string pair, bool isShort, double trigger, double stop, long deadlineMs, DateTime nyDay, string key, double lastPrice)
    {
        if (FxMode == "off" || !FxMarkets.Contains(pair)) return;
        string slot = FxSlot(pair, isShort);
        lock (_gate)
        {
            if (_fxDone.Contains(FxDay(pair, isShort, nyDay))) return;
            if (_open.Any(t => t.Market == pair && t.PositionCode != null)) return;              // one position per pair
            if (_fxArmed.TryGetValue(slot, out var old))
            {
                if (old.OpenCode.EndsWith(key.GetHashCode().ToString("x"))) return;                // same setup, already working
                if (old.PositionCode == null && old.DeadWhy == null) old.DeadWhy = "moved to a newer order block";
                return;                                                                              // re-arm on the next tick once the old one is gone
            }
            var t2 = new Trade
            {
                Market = pair, Symbol = pair, Side = isShort ? "SELL" : "BUY", Strategy = FxStrategy, SweepTs = nyDay.Ticks,
                Entry = trigger, Stop = stop, Target = 0, Armed = true, DeadlineMs = deadlineMs,
                OpenCode = Code("fxo", pair) + "-" + key.GetHashCode().ToString("x"), OpenedMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                ExitAtMs = new DateTimeOffset(TimeZoneInfo.ConvertTimeToUtc(nyDay.AddHours(16).AddMinutes(55), FxMidnightEngine.NyZone)).ToUnixTimeMilliseconds()
            };
            _fxArmed[slot] = t2;
            _ = Task.Run(() => FxOpen(t2, slot, nyDay, lastPrice));
        }
    }

    private async Task FxOpen(Trade t, string slot, DateTime nyDay, double lastPrice)
    {
        bool isShort = t.Side == "SELL";
        try
        {
            if (_token == null) await Login(CancellationToken.None);
            var (equity, _) = await Metrics(); RollDay(equity);
            double ddPct = 100.0 * (_startEquity - equity) / _startEquity, dayPct = 100.0 * (_dayStartEquity - equity) / _dayStartEquity;
            if (ddPct >= FloorPct) { Log(new { ev = "fx-skip", t.Market, why = $"drawdown {ddPct:F2}% >= floor {FloorPct}%" }); return; }
            if (dayPct >= DailyStopPct) { Log(new { ev = "fx-skip", t.Market, why = $"today -{dayPct:F2}% >= daily stop {DailyStopPct}%" }); return; }

            var inst = await InstrumentFor(t.Symbol);
            if (inst.Status != "FULL") { Log(new { ev = "fx-skip", t.Market, why = $"{t.Symbol} status {inst.Status}" }); return; }
            double perUnit = Math.Abs(t.Entry - t.Stop);
            double perUnitUsd = t.Market.StartsWith("USD") ? perUnit / t.Entry : perUnit;         // USDJPY: P&L is in JPY, convert at the entry price
            if (perUnitUsd <= 0) { Log(new { ev = "fx-skip", t.Market, why = "zero risk" }); return; }
            double units = FxRiskUsd / perUnitUsd;
            bool lots = inst.MinSize < 1m;                                                          // DXtrade forex: 0.01-type minimums = lots of 100,000
            decimal qty = RoundDown((decimal)(lots ? units / 100_000.0 : units), inst.MinSize, inst.Increment);
            if (qty < inst.MinSize) { Log(new { ev = "fx-skip", t.Market, why = $"qty {qty} below min {inst.MinSize}" }); return; }
            double unitsFinal = (double)qty * (lots ? 100_000.0 : 1.0);
            double riskCheck = unitsFinal * perUnitUsd, notionalUsd = unitsFinal * (t.Market.StartsWith("USD") ? 1.0 : t.Entry);
            if (riskCheck > FxRiskUsd * 1.5 || riskCheck < FxRiskUsd * 0.5 || notionalUsd > 2 * Math.Max(equity, 1))
            { Log(new { ev = "fx-skip", t.Market, why = "size sanity check failed", qty, lots, unitsFinal, riskCheck, notionalUsd }); return; }
            t.Qty = qty; t.UnitsPerQty = lots ? 100_000.0 : 1.0;

            bool through = lastPrice > 0 && (isShort ? lastPrice <= t.Entry : lastPrice >= t.Entry);
            var req = new JsonObject
            {
                ["orderCode"] = t.OpenCode, ["type"] = through ? "MARKET" : "STOP", ["instrument"] = t.Symbol,
                ["quantity"] = qty.ToString(System.Globalization.CultureInfo.InvariantCulture),
                ["positionEffect"] = "OPEN", ["side"] = t.Side, ["tif"] = "GTC"
            };
            if (!through) req["stopPrice"] = FxPx(t.Market, t.Entry);
            Log(new { ev = "fx-intent", t.Market, strategy = FxStrategy, side = t.Side, qty, lots, unitsFinal, entry = t.Entry, stop = t.Stop, riskUsd = Math.Round(riskCheck, 2), through, untilUtc = DateTimeOffset.FromUnixTimeMilliseconds(t.DeadlineMs).ToString("HH:mm"), mode = FxMode });

            if (FxMode != "live")
            {
                Log(new { ev = "fx-dry-armed", t.Market, request = req.ToJsonString(), instrument = inst });
                // dry: wait out the kill zone or a replacement, then free the slot. No orders are sent.
                while (t.DeadWhy == null && DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < t.DeadlineMs) await Task.Delay(10_000);
                Log(new { ev = "fx-dry-disarmed", t.Market, why = t.DeadWhy ?? "kill zone ended" });
                return;
            }

            var ack = await Post($"/accounts/{_accountPath}/orders", req);
            t.OpenOrderId = ack?["orderId"]?.ToString();
            Log(new { ev = "fx-armed", t.Market, orderId = t.OpenOrderId, orderCode = t.OpenCode, type = req["type"]!.ToString(), entry = t.Entry, stop = t.Stop });

            JsonNode? pos = null;
            while (pos == null && t.DeadWhy == null && DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() < t.DeadlineMs)
            {
                await Task.Delay(5_000);
                var positions = (await Get($"/accounts/{_accountPath}/positions"))?["positions"]?.AsArray();
                pos = positions?.FirstOrDefault(p => (p?["symbol"]?.ToString() == t.Symbol || p?["instrument"]?.ToString() == t.Symbol) &&
                        (p?["positionCode"]?.ToString() == t.OpenOrderId || !_open.Any(o => o != t && o.PositionCode == p?["positionCode"]?.ToString())));
            }
            if (pos == null)
            {
                try { await Delete($"/accounts/{_accountPath}/orders/{Uri.EscapeDataString(t.OpenCode)}"); } catch { }
                Log(new { ev = "fx-cancelled", t.Market, why = t.DeadWhy ?? "kill zone ended, not triggered" });
                return;
            }
            lock (_gate) { _open.Add(t); _fxDone.Add(FxDay(t.Market, isShort, nyDay)); }
            t.PositionCode = pos["positionCode"]!.ToString();
            t.FillPrice = TryDouble(pos["openPrice"]) ?? TryDouble(pos["price"]) ?? t.Entry;
            string opp = isShort ? "BUY" : "SELL";
            t.StopCode = Code("fxs", t.Market);
            await Post($"/accounts/{_accountPath}/orders", new JsonObject
            {
                ["orderCode"] = t.StopCode, ["type"] = "STOP", ["instrument"] = t.Symbol, ["positionEffect"] = "CLOSE",
                ["positionCode"] = t.PositionCode, ["side"] = opp, ["stopPrice"] = FxPx(t.Market, t.Stop), ["tif"] = "GTC"
            });
            for (int i = 0; i < 5 && !t.Protected; i++)
            {
                await Task.Delay(1500);
                var orders = (await Get($"/accounts/{_accountPath}/orders"))?["orders"]?.AsArray() ?? new JsonArray();
                t.Protected = orders.Any(o => (o?["orderCode"]?.ToString() ?? "").EndsWith(t.StopCode));
            }
            if (!t.Protected)
            {
                Log(new { ev = "UNPROTECTED-emergency-close", t.Market, strategy = FxStrategy });
                await Post($"/accounts/{_accountPath}/orders", new JsonObject
                {
                    ["orderCode"] = Code("emerg", t.Market), ["type"] = "MARKET", ["instrument"] = t.Symbol,
                    ["positionEffect"] = "CLOSE", ["positionCode"] = t.PositionCode, ["side"] = opp, ["tif"] = "GTC"
                });
                lock (_gate) _open.Remove(t);
                return;
            }
            t.FilledMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
            Log(new { ev = "protected", t.Market, strategy = FxStrategy, positionCode = t.PositionCode, fill = t.FillPrice, stop = t.Stop, target = (double?)null, qty = t.Qty, exitAtUtc = DateTimeOffset.FromUnixTimeMilliseconds(t.ExitAtMs).ToString("HH:mm") });
            Emit(new { type = "exec-open", market = t.Market, side = t.Side, fill = t.FillPrice, stop = t.Stop, target = (double?)null, qty = t.Qty });
        }
        catch (Exception ex) { Log(new { ev = "fx-open-error", t.Market, error = ex.Message }); }
        finally { lock (_gate) if (_fxArmed.TryGetValue(slot, out var cur) && cur == t) _fxArmed.Remove(slot); }
    }

    private static string FxPx(string pair, double p) => p.ToString(pair.EndsWith("JPY") ? "F3" : "F5", System.Globalization.CultureInfo.InvariantCulture);
}

public sealed partial class BrokerExecutor
{
    /// <summary>Today's 16:55 New York in UTC ms (the forex flat time).</summary>
    private static long FxFlatTimeToday()
    {
        var ny = TimeZoneInfo.ConvertTimeFromUtc(DateTime.UtcNow, FxMidnightEngine.NyZone).Date;
        return new DateTimeOffset(TimeZoneInfo.ConvertTimeToUtc(ny.AddHours(16).AddMinutes(55), FxMidnightEngine.NyZone)).ToUnixTimeMilliseconds();
    }
}
