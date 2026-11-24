using System.Net.Http.Json;
using System.Text.Json.Nodes;
using TradeDeck;

var http = new HttpClient { BaseAddress = new Uri("http://127.0.0.1:8899/") };
string vault = AppJournal.VaultFolder; Directory.CreateDirectory(vault); string execLog = Path.Combine(vault, "executions.jsonl");
async Task Scenario(object body) { var r = await http.PostAsync("/__scenario", new StringContent(System.Text.Json.JsonSerializer.Serialize(body), System.Text.Encoding.UTF8, "application/json")); r.EnsureSuccessStatusCode(); }
async Task Reset() { await http.PostAsync("/__reset", null); File.WriteAllText(execLog, ""); File.WriteAllText(Path.Combine(vault, "journal.jsonl"), ""); foreach (var f in Directory.GetFiles(vault, "traded_slot*.txt")) File.Delete(f); }
async Task<JsonNode> State() => (await http.GetFromJsonAsync<JsonNode>("/__state"))!;
string ReadShared(string p) { using var fs = new FileStream(p, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete); using var sr = new StreamReader(fs); return sr.ReadToEnd(); }
List<string> Events() => File.Exists(execLog) ? ReadShared(execLog).Split('\n').Select(l => l.TrimEnd('\r')).Where(l => l.Length > 0).Select(l => JsonNode.Parse(l)!["ev"]!.ToString()).ToList() : new();
int Count(List<string> ev, string name) => ev.Count(e => e == name);
async Task<bool> WaitFor(Func<bool> cond, int secs) { for (int i = 0; i < secs * 10; i++) { if (cond()) { await Task.Delay(700); return true; } await Task.Delay(100); } return cond(); }   // settle so the last log line is flushed before reading
int fails = 0;
string Orders(JsonNode s) => string.Join(" ; ", s["orders"]!.AsArray().Select(o => $"{o!["type"]}/{o["side"]}/{o["positionEffect"]}/{o["status"]}/lp={o["limitPrice"]}"));
void Check(string name, bool ok, string detail) { Console.WriteLine($"{(ok ? "PASS" : "FAIL")}  {name}  |  {detail}"); if (!ok) fails++; }
int scen = 0;
async Task<BrokerExecutor> Fresh(BrokerExecutor? old)
{
    if (File.Exists(execLog)) File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true); scen++;
    old?.Dispose(); await Reset();
    var e = new BrokerExecutor(); e.Start();
    if (!await WaitFor(() => e.Status.Contains("logged in"), 15)) throw new Exception("executor never logged in: " + e.Status);
    File.WriteAllText(execLog, ""); return e;
}
long sweep = 1_790_000_000_000; long fvg = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() - 5 * 60_000;   // FVG bar five minutes ago: retrace window open
var ex = await Fresh(null);

// A. double arm: two OnArm calls 50 ms apart must produce ONE order and ONE position
await Scenario(new { market = 85000.0, latency_ms = 250 });   // DXtrade-like round trips so the race window is real
ex.OnArm("BTCUSDT", true, 85737, 86687, sweep, 950, fvg, "raid-fvg", 85000);   // sell limit above market: rests
await Task.Delay(50);
ex.OnArm("BTCUSDT", true, 85737, 86687, sweep + 900_000, 950, fvg, "raid-fvg", 85000);   // same level re-swept 15 min later: different sweepTs, same market
await Task.Delay(4000);
var st = await State(); int entryOrders = st["orders"]!.AsArray().Count(o => o!["positionEffect"]!.ToString() == "OPEN");
Check("A1 one entry order after a double arm", entryOrders == 1, $"entry orders at broker = {entryOrders}");
await Scenario(new { market = 85800.0 });   // touch: the sell limit at 85737 fills
Console.WriteLine("fake after touch: " + (await State())["market"] + " latency " + (await State())["latency_ms"]);
bool prot = await WaitFor(() => Count(Events(), "protected") >= 1, 30);
st = await State(); int pos = st["positions"]!.AsArray().Count; var ev = Events();
Check("A2 exactly one position and one protected", prot && pos == 1 && Count(ev, "protected") == 1, $"positions={pos} protected={Count(ev, "protected")} events={string.Join(",", ev)} orders={Orders(st)}");
// A3 (2026-10-19): the broker fill writes the journal FILLED row itself (scanner may have killed the setup after the fill)
{
    string jp = Path.Combine(vault, "journal.jsonl");
    var jrows = File.Exists(jp) ? ReadShared(jp).Split('\n').Where(l => l.Trim().Length > 0).Select(l => JsonNode.Parse(l)!).ToList() : new();
    var filled = jrows.Where(r => r["status"]?.ToString() == "FILLED" && r["market"]?.ToString() == "BTCUSDT" && r["source"]?.ToString() == "broker-fill").ToList();
    Check("A3 broker fill journals one FILLED row", filled.Count == 1 && (long)filled[0]["sweepTs"]! == sweep && filled[0]["direction"]!.ToString() == "SHORT",
        $"filled rows={filled.Count} " + string.Join(" | ", filled.Select(r => r.ToJsonString())));
}

// B. rejected through-market LIMIT -> MARKET fallback -> protected, one position
ex = await Fresh(ex);
await Scenario(new { market = 1.5210, reject_through_market = true });
ex.OnArm("XRPUSDT", false, 1.5213, 1.4997, sweep + 1, 0.0216, fvg, "raid-fvg", 1.5210);   // buy limit above market: broker rejects
bool ok = await WaitFor(() => Count(Events(), "protected") >= 1 || Count(Events(), "open-error") > 0 || Count(Events(), "UNPROTECTED-emergency-close") > 0, 30);
ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count;
Check("B rejected limit -> market fallback -> protected", ok && Count(ev, "entry-REJECTED") == 1 && Count(ev, "market-fallback") == 1 && Count(ev, "protected") == 1 && pos == 1, $"positions={pos} events={string.Join(",", ev)}");

// C. armed then dead -> cancelled, no position, no working order left
ex = await Fresh(ex);
await Scenario(new { market = 85000.0 });
ex.OnArm("ETHUSDT", true, 85737, 86687, sweep + 2, 950, fvg, "raid-fvg", 85000);
await Task.Delay(2500); ex.OnDead("ETHUSDT", sweep + 2, "wick retaken");
ok = await WaitFor(() => Count(Events(), "armed-cancelled") >= 1, 25); st = await State(); pos = st["positions"]!.AsArray().Count;
int working = st["orders"]!.AsArray().Count(o => o!["status"]!.ToString() == "WORKING");
Check("C dead setup cancels the resting order", ok && pos == 0 && working == 0, $"positions={pos} working={working} events={string.Join(",", Events())}");

// D. normal arm -> touch -> protected; then the scanner's FILLED call for the same setup is ignored (already armed)
ex = await Fresh(ex);
await Scenario(new { market = 85000.0 });
ex.OnArm("BTCUSDT", true, 85737, 86687, sweep + 3, 950, fvg, "raid-fvg", 85000);
await Task.Delay(2000); await Scenario(new { market = 85800.0 });
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 30);
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 3, 950);
await Task.Delay(3000); st = await State(); pos = st["positions"]!.AsArray().Count; ev = Events();
Check("D normal arm -> touch -> protected; FILLED call ignored", ok && pos == 1 && Count(ev, "protected") == 1 && Count(ev, "open-intent") == 1, $"positions={pos} events={string.Join(",", ev)}");

// E. the direct FILLED path (no arm) with a rejected through-market limit -> market fallback
ex = await Fresh(ex);
await Scenario(new { market = 85800.0, reject_through_market = true });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 4, 950);   // sell at 85737 while market is 85800: through -> rejected -> market
ok = await WaitFor(() => Count(Events(), "protected") >= 1 || Count(Events(), "open-error") > 0, 30); ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count;
Check("E direct call, rejected limit -> market fallback -> protected", ok && Count(ev, "entry-REJECTED") == 1 && Count(ev, "market-fallback") == 1 && pos == 1, $"positions={pos} events={string.Join(",", ev)}");

// F. position closed by hand in the platform -> bot logs closed with how = manual/other (not TP)
ex = await Fresh(ex);
await Scenario(new { market = 85800.0 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 5, 950);
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 30);
var pc = (await State())["positions"]!.AsArray()[0]!["positionCode"]!.ToString();
await Scenario(new { close_position = pc });
ok = ok && await WaitFor(() => Count(Events(), "closed") >= 1, 45);
var closedRow = File.ReadAllLines(execLog).FirstOrDefault(l => l.Contains("\"closed\"")) ?? "";
Check("F manual close labelled manual/other", ok && closedRow.Contains("manual/other"), closedRow[..Math.Min(160, closedRow.Length)]);
// F2 (2026-11-22): the hand close carries the broker's settled $ (open COMMISSION -1.25 + SETTLEMENT -16.57 + close COMMISSION -1.25 on the fake), not the open commission alone
{
    var j = closedRow.Length > 0 ? JsonNode.Parse(closedRow) : null; double? fp = j?["pnl"]?.GetValue<double>(), fr = j?["r"]?.GetValue<double>();
    Check("F2 manual close pnl = broker settlement + commission, r from it", ok && fp.HasValue && Math.Abs(fp.Value - (-19.07)) < 0.011 && fr.HasValue && fr.Value < 0 && fr.Value > -0.2, closedRow[..Math.Min(220, closedRow.Length)]);
}

// G. RESTART SAFETY: a position already open at the broker (with STOP and TP resting) before the bot starts -> adopted, slot counted,
//    a new arm on that market is skipped, and when the stop is crossed the bot journals closed/STOP and the position is gone.
if (File.Exists(execLog)) File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true); scen++;
ex.Dispose(); await Reset();
await Scenario(new { market = 85500.0, seed_position = new { symbol = "BTCUSD", side = "SELL", qty = 0.263, openPrice = 85737.8, stop = 86687.0, tp = 82887.0 } });
ex = new BrokerExecutor(); ex.Start();
if (!await WaitFor(() => ex.Status.Contains("logged in"), 15)) throw new Exception("executor never logged in: " + ex.Status);
ok = await WaitFor(() => Count(Events(), "adopted") >= 1, 15); ev = Events();
Check("G1 existing position adopted on startup, protected", ok && Count(ev, "adopted") == 1 && Count(ev, "adopted-UNPROTECTED") == 0, $"events={string.Join(",", ev)}");
// G1b (2026-10-29): the fake now serves orders in the real DXtrade shape (positionCode + price only inside legs[]); the adopted row must carry the real stop and target
{
    var arow = File.ReadAllLines(execLog).FirstOrDefault(l => l.Contains("\"adopted\"")) ?? "";
    Check("G1b adopted row carries the broker's STOP and TP prices (read through legs[])", arow.Contains("\"stop\":86687") && arow.Contains("\"target\":82887"), arow[..Math.Min(220, arow.Length)]);
}
ex.OnArm("BTCUSDT", true, 85737, 86687, sweep + 6, 950, fvg, "raid-fvg", 85500);
await Task.Delay(3000); st = await State(); int entries = st["orders"]!.AsArray().Count(o => o!["positionEffect"]!.ToString() == "OPEN");
Check("G2 new arm on the adopted market is skipped", entries == 0 && Count(Events(), "skip") >= 1, $"entry orders={entries} events={string.Join(",", Events())}");
await Scenario(new { market = 86700.0 });   // through the stop
ok = await WaitFor(() => Count(Events(), "closed") >= 1, 45); st = await State(); pos = st["positions"]!.AsArray().Count;
closedRow = File.ReadAllLines(execLog).FirstOrDefault(l => l.Contains("\"closed\"")) ?? "";
Check("G3 adopted position stopped out -> closed/STOP journaled, no position left", ok && pos == 0 && closedRow.Contains("\"how\":\"STOP\""), $"positions={pos} row={closedRow[..Math.Min(200, closedRow.Length)]}");

// G5 (2026-10-29 review): two positions on the same symbol, one with its own STOP/TP, one naked -> the naked one must be adopted-UNPROTECTED
//    (never borrow the other position's stop/TP codes and be marked Protected).
if (File.Exists(execLog)) File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true); scen++;
ex.Dispose(); await Reset();
await Scenario(new { market = 85500.0, seed_position = new { symbol = "BTCUSD", side = "SELL", qty = 0.263, openPrice = 85737.8, stop = 86687.0, tp = 82887.0 } });
await Scenario(new { market = 85500.0, seed_position = new { symbol = "BTCUSD", side = "SELL", qty = 0.100, openPrice = 85600.0, stop = 0.0, tp = 0.0, naked = true } });
ex = new BrokerExecutor(); ex.Start();
if (!await WaitFor(() => ex.Status.Contains("logged in"), 15)) throw new Exception("executor never logged in: " + ex.Status);
ok = await WaitFor(() => Count(Events(), "adopted") + Count(Events(), "adopted-UNPROTECTED") >= 2, 15); ev = Events();
{
    var rows = File.ReadAllLines(execLog).Where(l => l.Contains("\"adopted")).Select(l => JsonNode.Parse(l)!).ToList();
    var protRow = rows.FirstOrDefault(r => r["ev"]!.ToString() == "adopted"); var naked = rows.FirstOrDefault(r => r["ev"]!.ToString() == "adopted-UNPROTECTED");
    Check("G5 two positions one symbol: the one with STOP/TP adopted with its prices, the naked one adopted-UNPROTECTED (no borrowed codes)",
          ok && Count(ev, "adopted") == 1 && Count(ev, "adopted-UNPROTECTED") == 1 && protRow?["stop"]?.GetValue<double>() == 86687 && naked?["stop"] == null && naked?["qty"]?.GetValue<double>() == 0.1,
          $"events={string.Join(",", ev)} rows={string.Join(" | ", rows.Select(r => r.ToJsonString()[..Math.Min(150, r.ToJsonString().Length)]))}");
}

// H. TIME STOP: a protected position older than the time stop (rig env: 1 minute) gets one MARKET close; journaled closed/TIME with an exit price; siblings cancelled.
if (File.Exists(execLog)) File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true); scen++;
ex = await Fresh(ex);
await Scenario(new { market = 85800.0 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 7, 950);
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 30);
await Scenario(new { market = 85600.0 });   // drifts in our favour but hits neither stop nor target
ok = ok && await WaitFor(() => Count(Events(), "closed") >= 1, 150);
st = await State(); pos = st["positions"]!.AsArray().Count; working = st["orders"]!.AsArray().Count(o => o!["status"]!.ToString() == "WORKING");
closedRow = File.ReadAllLines(execLog).FirstOrDefault(l => l.Contains("\"closed\"")) ?? "";
Check("H time stop -> market close -> closed/TIME with exit, no position, no working orders", ok && pos == 0 && working == 0 && Count(Events(), "time-exit-sent") == 1 && closedRow.Contains("\"how\":\"TIME\"") && closedRow.Contains("\"exit\":85600"), $"positions={pos} working={working} events={string.Join(",", Events())} row={closedRow[..Math.Min(220, closedRow.Length)]}");

// V. v2 DIRECTION FILTER (patch_v2_filter): negative / missing / stale score -> v2-skip and no order; positive -> v2-ok and armed; filter off -> silent.
if (File.Exists(execLog)) File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true); scen++;
ex = await Fresh(ex);
string v2csv = Path.Combine(vault, "rig_scores.csv");
string Row(string coin, DateTime dec, double l, double s) => $"{coin},{dec:yyyy-MM-dd HH:mm:ss}+00:00,{dec:yyyy-MM-dd HH:mm:ss}+00:00,0.1,0.1,{l.ToString(System.Globalization.CultureInfo.InvariantCulture)},{s.ToString(System.Globalization.CultureInfo.InvariantCulture)},{DateTime.UtcNow:yyyy-MM-dd HH:mm:ss}+00:00\n";
File.WriteAllText(v2csv, "coin,bar,dec,pL,pS,v2L,v2S,run\n" + Row("BTCUSDT", DateTime.UtcNow.AddMinutes(-10), 0.12, -0.20));
ex.V2Filter = true; ex.V2ScoresPath = v2csv; ex.V2MaxAgeMin = 120;
await Scenario(new { market = 85000.0 });
ex.OnArm("BTCUSDT", true, 85737, 86687, sweep + 20, 950, fvg, "raid-fvg", 85000);   // SHORT, v2S = -0.20 -> skip
await Task.Delay(2500); st = await State(); ev = Events();
Check("V1 negative v2 score -> v2-skip, no order", Count(ev, "v2-skip") == 1 && st["orders"]!.AsArray().Count == 0 && Count(ev, "open-intent") == 0, $"events={string.Join(",", ev)}");
ex.OnArm("BTCUSDT", false, 84500, 83800, sweep + 21, 700, fvg, "raid-fvg", 85000);   // LONG, v2L = +0.12 -> allowed: buy limit below market rests
ok = await WaitFor(() => Count(Events(), "armed") >= 1, 20); st = await State();
Console.WriteLine("V2 diag: status=" + ex.Status + " | fake=" + st.ToJsonString()[..Math.Min(600, st.ToJsonString().Length)] + " | log=" + File.ReadAllText(execLog).Replace(Environment.NewLine, " "));
Check("V2 positive v2 score -> v2-ok, order armed", ok && Count(Events(), "v2-ok") == 1 && st["orders"]!.AsArray().Count(o => o!["positionEffect"]!.ToString() == "OPEN") == 1, $"events={string.Join(",", Events())}");
File.WriteAllText(v2csv, "coin,bar,dec,pL,pS,v2L,v2S,run\n" + Row("BTCUSDT", DateTime.UtcNow.AddMinutes(-200), 0.5, 0.5));   // 200 min old: stale
ex.OnArm("ETHUSDT", false, 84500, 83800, sweep + 22, 700, fvg, "raid-fvg", 85000);   // ETH has no rows -> skip
ex.OnArm("XRPUSDT", false, 1.4000, 1.3800, sweep + 23, 0.02, fvg, "raid-fvg", 1.42);   // XRP no rows either -> skip
await Task.Delay(2500); ev = Events();
Check("V3 missing scores -> skipped (3 skips, still 1 open-intent)", Count(ev, "v2-skip") == 3 && Count(ev, "open-intent") == 1, $"events={string.Join(",", ev)}");
ex.OnCall("XRPUSDT", true, 1.4000, 1.4200, sweep + 24, 0.02);   // direct call path on a NEW setup: BTC rows are stale, XRP none -> skip
await Task.Delay(2000); ev = Events();
Check("V4 OnCall new setup gated too", Count(ev, "v2-skip") == 4 && Count(ev, "open-intent") == 1, $"events={string.Join(",", ev)}");
ex.V2Filter = false;
ex.OnArm("ETHUSDT", false, 84500, 83800, sweep + 25, 700, fvg, "raid-fvg", 85000);
ok = await WaitFor(() => Count(Events(), "armed") >= 2, 20);
Check("V5 filter off -> no gate, silent", ok && Count(Events(), "v2-skip") == 4 && Count(Events(), "v2-ok") == 1, $"events={string.Join(",", Events())}");

// P. PRICE GATE + ONE TRADE PER SETUP (2026-10-23, the XRP double loss)
// P1: direct FILLED call, broker rejects the LIMIT, live price already THROUGH the stop -> fallback SKIPPED, trade cancelled, no position, no market order
if (File.Exists(execLog)) File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true); scen++;
ex = await Fresh(ex);
await Scenario(new { market = 1.3120, reject_through_market = true });
ex.OnCall("XRPUSDT", false, 1.3164, 1.3134, sweep + 90, 0.003);   // long: entry 1.3164, stop 1.3134, live 1.3120 = below the stop
ok = await WaitFor(() => Count(Events(), "entry-NOFILL-cancelled") >= 1 || Count(Events(), "market-fallback") >= 1 || Count(Events(), "open-error") >= 1, 90);
ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count; int mkts = st["orders"]!.AsArray().Count(o => o!["type"]!.ToString() == "MARKET");
Check("P1 price through the stop -> market fallback SKIPPED, no position, no market order", ok && Count(ev, "entry-REJECTED") == 1 && Count(ev, "market-fallback-SKIPPED") == 1 && Count(ev, "market-fallback") == 0 && Count(ev, "entry-NOFILL-cancelled") == 1 && pos == 0 && mkts == 0,
      $"positions={pos} marketOrders={mkts} events={string.Join(",", ev)}");
// P1c: the 2026-10-21 shape: SHORT, broker rejects, live price already ABOVE the stop -> SKIPPED
ex = await Fresh(ex);
await Scenario(new { market = 86800.0, reject_through_market = true });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 93, 950);   // short: entry 85737, stop 86687, live 86800 = above the stop
ok = await WaitFor(() => Count(Events(), "entry-NOFILL-cancelled") >= 1 || Count(Events(), "market-fallback") >= 1, 90);
ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count; mkts = st["orders"]!.AsArray().Count(o => o!["type"]!.ToString() == "MARKET");
Check("P1c short with price above the stop -> SKIPPED, no position, no market order", ok && Count(ev, "market-fallback-SKIPPED") == 1 && Count(ev, "market-fallback") == 0 && pos == 0 && mkts == 0, $"positions={pos} marketOrders={mkts} events={string.Join(",", ev)}");
// P1b: same call with the live price BETWEEN entry and stop -> fallback still fires and gets protected (the Oct 5 path survives)
ex = await Fresh(ex);
await Scenario(new { market = 1.3150, reject_through_market = true });
ex.OnCall("XRPUSDT", false, 1.3164, 1.3134, sweep + 91, 0.003);
ok = await WaitFor(() => Count(Events(), "protected") >= 1 || Count(Events(), "UNPROTECTED-emergency-close") > 0, 40);   // a disposed previous executor's last poll logs open-error: not this scenario
ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count;
Check("P1b price between stop and entry -> fallback fires -> protected", ok && Count(ev, "market-fallback") == 1 && Count(ev, "market-fallback-SKIPPED") == 0 && Count(ev, "protected") == 1 && pos == 1, $"positions={pos} events={string.Join(",", ev)}");
// P2: today's sequence. Fill at the touch, stop crossed -> closed; the scanner re-calls the SAME sweep -> skip-traded, no new order, no position
ex = await Fresh(ex);
await Scenario(new { market = 1.3170 });
ex.OnArm("XRPUSDT", false, 1.3164, 1.3134, sweep + 92, 0.003, fvg, "raid-fvg", 1.3170);   // buy limit below market: rests
await Task.Delay(2500); await Scenario(new { market = 1.3160 });   // touch
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 40);
await Scenario(new { market = 1.3120 });   // through the stop
ok = ok && await WaitFor(() => Count(Events(), "closed") >= 1, 60);
int ordersBefore = st != null ? (await State())["orders"]!.AsArray().Count : 0;
ex.OnCall("XRPUSDT", false, 1.3164, 1.3134, sweep + 92, 0.003);   // same sweep, 3 minutes later in real life
await Task.Delay(4000); ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count; int ordersAfter = st["orders"]!.AsArray().Count;
Check("P2 stopped-out setup re-called -> skip-traded, no order, no position", ok && Count(ev, "skip-traded") == 1 && Count(ev, "open-intent") == 1 && ordersAfter == ordersBefore && pos == 0,
      $"positions={pos} orders {ordersBefore}->{ordersAfter} events={string.Join(",", ev)}");

async Task<bool> WaitPos(int n, int secs) { for (int i = 0; i < secs * 2; i++) { if ((await State())["positions"]!.AsArray().Count == n) return true; await Task.Delay(500); } return false; }
// Q. EMERGENCY CLOSE HARDENING + GATE EDGE (2026-10-23 review findings 4 and 11)
// Q1: protection orders rejected by the broker -> emergency close; the first close attempt fails (500), the retry succeeds -> no position
if (File.Exists(execLog)) File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true); scen++;
ex = await Fresh(ex);
await Scenario(new { market = 85800.0, drop_protection = true, fail_close_n = 1 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 94, 950);
ok = await WaitFor(() => Count(Events(), "UNPROTECTED-emergency-close") >= 1, 60);
ok = ok && await WaitPos(0, 30);
ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count;
Check("Q1 emergency close fails once -> retried -> position closed, no alarm", ok && pos == 0 && Count(ev, "emergency-close-error") == 1 && Count(ev, "EMERGENCY-CLOSE-FAILED") == 0, $"positions={pos} events={string.Join(",", ev)}");
// Q2: every attempt fails -> EMERGENCY-CLOSE-FAILED alarm, trade kept; when the broker answers again, Reconcile closes it
ex = await Fresh(ex);
await Scenario(new { market = 85800.0, drop_protection = true, fail_close_n = 3 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 95, 950);
ok = await WaitFor(() => Count(Events(), "EMERGENCY-CLOSE-FAILED") >= 1, 60);
st = await State(); int posAfterFail = st["positions"]!.AsArray().Count;
ok = ok && await WaitFor(() => Count(Events(), "emergency-close-retry-sent") >= 1, 90);
ok = ok && await WaitPos(0, 30);
ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count;
Check("Q2 all attempts fail -> alarm, position kept and tracked, closed by Reconcile once the broker answers", ok && posAfterFail == 1 && pos == 0 && Count(ev, "emergency-close-error") == 3 && Count(ev, "EMERGENCY-CLOSE-FAILED") == 1,
      $"posAfterFail={posAfterFail} positions={pos} events={string.Join(",", ev)}");
// Q3: gate edge: live price one tick above the stop (long) is refused; price in the entry-side half passes (P1b covers the pass)
ex = await Fresh(ex);
await Scenario(new { market = 1.3135, reject_through_market = true });   // stop 1.3134, entry 1.3164, mid 1.3149: 1.3135 is in the stop-side half
ex.OnCall("XRPUSDT", false, 1.3164, 1.3134, sweep + 96, 0.003);
ok = await WaitFor(() => Count(Events(), "entry-NOFILL-cancelled") >= 1 || Count(Events(), "market-fallback") >= 1, 90);
ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count;
Check("Q3 price one tick above the stop -> refused (entry-side half rule)", ok && Count(ev, "market-fallback-SKIPPED") == 1 && Count(ev, "market-fallback") == 0 && pos == 0, $"positions={pos} events={string.Join(",", ev)}");
// R1: one trade per setup survives a restart: trade, stop out, NEW executor (fresh process state), same sweep re-called -> skip-traded
ex = await Fresh(ex);
await Scenario(new { market = 85800.0 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 97, 950);
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 40);
await Scenario(new { market = 86700.0 });   // through the stop
ok = ok && await WaitFor(() => Count(Events(), "closed") >= 1, 60);
ex.Dispose(); ex = new BrokerExecutor(); ex.Start();   // restart WITHOUT resetting the fake or the vault files
if (!await WaitFor(() => ex.Status.Contains("logged in"), 15)) throw new Exception("executor never logged in: " + ex.Status);
await Scenario(new { market = 85800.0 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 97, 950);
await Task.Delay(4000); ev = Events(); st = await State(); pos = st["positions"]!.AsArray().Count;
Check("R1 traded setup survives a restart -> skip-traded, no position", ok && Count(ev, "skip-traded") >= 1 && Count(ev, "open-intent") == 1 && pos == 0, $"positions={pos} events={string.Join(",", ev)}");
File.Delete(Path.Combine(vault, "traded_slot1.txt"));

// M. TWO ACCOUNTS, ONE LOGIN (2026-10-22): a second executor from broker2.env shares the session, trades its own account
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
int loginsBefore = (int)(await State())["logins"]!;
var ex2 = new BrokerExecutor("broker2.env") { Slot = 2 }; int slot2States = 0; ex2.Message += o => { var j = JsonNode.Parse(System.Text.Json.JsonSerializer.Serialize(o))!; if (j["type"]?.ToString() == "exec-state" && (int?)j["slot"] == 2) slot2States++; };
ex2.Start();
ok = await WaitFor(() => ex2.Status.Contains("logged in"), 15);
int loginsAfter = (int)(await State())["logins"]!;
List<JsonNode> MRows() => ReadShared(execLog).Split('\n').Where(l => l.Trim().Length > 0).Select(l => JsonNode.Parse(l)!).ToList();
var mRows = MRows();
Check("M1 second account logs in on the SAME session (no second /login)", ok && loginsAfter == loginsBefore && mRows.Any(r => r["ev"]?.ToString() == "session-shared" && (int?)r["slot"] == 2),
      $"logged in={ok} logins {loginsBefore}->{loginsAfter} events={string.Join(",", Events())}");
Check("M2 the second executor logs into ITS account (…0002), tagged", mRows.Count(r => r["ev"]?.ToString() == "login" && r["account"]?.ToString() == "…0002" && r["acct"]?.ToString() == "…0002") == 1 && ex.Status.Contains("logged in"),
      string.Join(" | ", mRows.Where(r => r["ev"]?.ToString() == "login").Select(r => r["account"] + "/" + r["acct"])));
ex.OnArm("BTCUSDT", true, 85737, 86687, sweep + 77, 950, fvg, "raid-fvg", 85000);
ex2.OnArm("BTCUSDT", true, 85737, 86687, sweep + 77, 950, fvg, "raid-fvg", 85000);
await Task.Delay(4000);
st = await State(); var mEntries = st["orders"]!.AsArray().Where(o => o!["positionEffect"]!.ToString() == "OPEN").ToList();
Check("M3 one resting entry per account", mEntries.Count == 2 && mEntries.Select(o => o!["account"]!.ToString()).Distinct().Count() == 2,
      string.Join(" ; ", mEntries.Select(o => o!["account"] + " " + o["status"])));
await Scenario(new { market = 85800.0 });
ok = await WaitFor(() => Count(Events(), "protected") >= 2, 40);
st = await State(); mRows = MRows();
var mProt = mRows.Where(r => r["ev"]?.ToString() == "protected").Select(r => r["acct"]?.ToString()).ToList();
Check("M4 both accounts filled and protected, mRows tagged per account", ok && st["positions"]!.AsArray().Count == 2 && st["positions"]!.AsArray().Select(p => p!["account"]!.ToString()).Distinct().Count() == 2 && mProt.Contains("…0001") && mProt.Contains("…0002"),
      $"positions={st["positions"]!.AsArray().Count} protected acct tags={string.Join(",", mProt)}");
Check("M5 second account reports exec-state with slot 2", slot2States >= 1, $"slot-2 states={slot2States}");
await Task.Delay(1500); mRows = MRows();
Check("M6 no error rows from either account (ignoring the disposed previous executor's last poll)", !mRows.Any(r => (r["ev"]?.ToString() ?? "").Contains("error") && !(r["error"]?.ToString() ?? "").Contains("disposed object")), string.Join(" | ", mRows.Where(r => (r["ev"]?.ToString() ?? "").Contains("error")).Select(r => r.ToJsonString()[..Math.Min(160, r.ToJsonString().Length)])));
ex2.Dispose();

// N. THREE ACCOUNTS, ONE LOGIN (2026-10-25): a third executor from broker3.env (account …0003) shares the session, sizes off ITS OWN equity
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
loginsBefore = (int)(await State())["logins"]!;
var ex2n = new BrokerExecutor("broker2.env") { Slot = 2 };
var ex3 = new BrokerExecutor("broker3.env") { Slot = 3 }; int slot3States = 0; ex3.Message += o => { var j = JsonNode.Parse(System.Text.Json.JsonSerializer.Serialize(o))!; if (j["type"]?.ToString() == "exec-state" && (int?)j["slot"] == 3) slot3States++; };
ex2n.Start(); ex3.Start();
ok = await WaitFor(() => ex2n.Status.Contains("logged in") && ex3.Status.Contains("logged in"), 20);
loginsAfter = (int)(await State())["logins"]!;
mRows = MRows();
Check("N1 third account logs in on the SAME session (no extra /login)", ok && loginsAfter == loginsBefore && mRows.Any(r => r["ev"]?.ToString() == "session-shared" && (int?)r["slot"] == 3),
      $"logged in={ok} logins {loginsBefore}->{loginsAfter} events={string.Join(",", Events())}");
var n2 = mRows.Where(r => r["ev"]?.ToString() == "login" && r["account"]?.ToString() == "…0003").ToList();
Check("N2 third executor logs into ITS account (…0003) and reads its own equity", n2.Count == 1 && n2[0]["acct"]?.ToString() == "…0003" && (double?)n2[0]["equity"] == 100000.0,
      string.Join(" | ", mRows.Where(r => r["ev"]?.ToString() == "login").Select(r => r["account"] + "/" + r["acct"] + "/" + r["equity"])));
ex.OnArm("BTCUSDT", true, 85737, 86687, sweep + 78, 950, fvg, "raid-fvg", 85000);
ex2n.OnArm("BTCUSDT", true, 85737, 86687, sweep + 78, 950, fvg, "raid-fvg", 85000);
ex3.OnArm("BTCUSDT", true, 85737, 86687, sweep + 78, 950, fvg, "raid-fvg", 85000);
await Task.Delay(4000);
st = await State(); mEntries = st["orders"]!.AsArray().Where(o => o!["positionEffect"]!.ToString() == "OPEN").ToList();
Check("N3 one resting entry per account, three accounts", mEntries.Count == 3 && mEntries.Select(o => o!["account"]!.ToString()).Distinct().Count() == 3,
      string.Join(" ; ", mEntries.Select(o => o!["account"] + " " + o["status"])));
mRows = MRows();
double RiskOf(string acct) => mRows.Where(r => r["ev"]?.ToString() == "open-intent" && r["acct"]?.ToString() == acct).Select(r => (double?)r["riskDollars"] ?? -1).LastOrDefault(-1);
Check("N4 sizing per account: each of the three risks 0.5% of its own equity (START_BALANCE in fake_dx.py)", Math.Abs(RiskOf("…0003") - 500) < 0.01 && Math.Abs(RiskOf("…0001") - 500) < 0.01 && Math.Abs(RiskOf("…0002") - 500) < 0.01,
      $"0003={RiskOf("…0003")} 0001={RiskOf("…0001")} 0002={RiskOf("…0002")}");
await Scenario(new { market = 85800.0 });
ok = await WaitFor(() => Count(Events(), "protected") >= 3, 40);
st = await State(); mRows = MRows();
mProt = mRows.Where(r => r["ev"]?.ToString() == "protected").Select(r => r["acct"]?.ToString()).ToList();
Check("N5 all three accounts filled and protected, rows tagged per account", ok && st["positions"]!.AsArray().Count == 3 && st["positions"]!.AsArray().Select(p => p!["account"]!.ToString()).Distinct().Count() == 3 && mProt.Contains("…0001") && mProt.Contains("…0002") && mProt.Contains("…0003"),
      $"positions={st["positions"]!.AsArray().Count} protected acct tags={string.Join(",", mProt)}");
Check("N6 third account reports exec-state with slot 3", slot3States >= 1, $"slot-3 states={slot3States}");
await Task.Delay(1500); mRows = MRows();
Check("N7 no error rows from any of the three accounts", !mRows.Any(r => (r["ev"]?.ToString() ?? "").Contains("error") && !(r["error"]?.ToString() ?? "").Contains("disposed object")), string.Join(" | ", mRows.Where(r => (r["ev"]?.ToString() ?? "").Contains("error")).Select(r => r.ToJsonString()[..Math.Min(160, r.ToJsonString().Length)])));
ex2n.Dispose(); ex3.Dispose();

// O. 2026-10-25 rig finding (three-account run): an HTTP error between the fill and the STOP post left a position open with no stop and
// nothing retrying. O1: the STOP post fails once -> Reconcile places stop + TP late. O2: protection keeps failing -> emergency close.
ex = await Fresh(ex);
await Scenario(new { market = 85800.0, fail_stop_n = 1 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 96, 950);
ok = await WaitFor(() => Count(Events(), "open-error") >= 1, 60);
ok = ok && await WaitFor(() => Count(Events(), "protected-late") >= 1, 90);
st = await State(); ev = Events();
var oClose = st["orders"]!.AsArray().Where(o => o!["status"]!.ToString() == "WORKING" && o["positionEffect"]!.ToString() == "CLOSE").ToList();
Check("O1 STOP post fails once after the fill -> Reconcile protects late (stop + TP working), no emergency close",
      ok && st["positions"]!.AsArray().Count == 1 && oClose.Count == 2 && Count(ev, "UNPROTECTED-emergency-close") == 0,
      $"positions={st["positions"]!.AsArray().Count} working close orders={oClose.Count} events={string.Join(",", ev)}");
ex = await Fresh(ex);
await Scenario(new { market = 85800.0, fail_stop_n = 1 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 97, 950);
ok = await WaitFor(() => Count(Events(), "open-error") >= 1, 60);
await Scenario(new { drop_protection = true });
ok = ok && await WaitFor(() => Count(Events(), "UNPROTECTED-emergency-close") >= 1, 90);
ok = ok && await WaitPos(0, 30);
ev = Events(); st = await State();
Check("O2 late protection rejected too -> emergency close, no position left, no alarm",
      ok && st["positions"]!.AsArray().Count == 0 && Count(ev, "EMERGENCY-CLOSE-FAILED") == 0,
      $"positions={st["positions"]!.AsArray().Count} events={string.Join(",", ev)}");

// S. 2026-10-29 close labels against the real history shape (broker ignores ?with-order-code; fill price in legs[0].averagePrice).
//    Live bug: XRP stop-outs 2026-10-28/29 logged how=TP, exit/r/pnl null. S1 stop -> STOP + exit + pnl; S2 target -> TP; S3 history down -> unknown.
string ClosedRow() => File.ReadAllLines(execLog).FirstOrDefault(l => l.Contains("\"closed\"")) ?? "";
ex = await Fresh(ex);
await Scenario(new { market = 85800.0 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 101, 950);
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 30);
await Scenario(new { market = 86700.0 });   // through the stop
ok = ok && await WaitFor(() => Count(Events(), "closed") >= 1, 45);
{
    var row = ClosedRow(); var j = row.Length > 0 ? JsonNode.Parse(row) : null;
    double? exitPx = j?["exit"]?.GetValue<double>(), pnl = j?["pnl"]?.GetValue<double>(), rr = j?["r"]?.GetValue<double>();
    Check("S1 bot trade stopped out -> closed/STOP with exit = stop price, r = -1, pnl negative", ok && j?["how"]?.ToString() == "STOP" && exitPx == 86687 && rr == -1 && pnl < 0, row[..Math.Min(220, row.Length)]);
}
ex = await Fresh(ex);
await Scenario(new { market = 85800.0 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 102, 950);
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 30);
await Scenario(new { market = 82800.0 });   // through the 3R target
ok = ok && await WaitFor(() => Count(Events(), "closed") >= 1, 45);
{
    var row = ClosedRow(); var j = row.Length > 0 ? JsonNode.Parse(row) : null;
    double? exitPx = j?["exit"]?.GetValue<double>(), pnl = j?["pnl"]?.GetValue<double>(), rr = j?["r"]?.GetValue<double>();
    Check("S2 bot trade hits target -> closed/TP with exit = target, r = 3, pnl positive", ok && j?["how"]?.ToString() == "TP" && exitPx == 82887 && rr == 3 && pnl > 0, row[..Math.Min(220, row.Length)]);
}
ex = await Fresh(ex);
await Scenario(new { market = 85800.0 });
ex.OnCall("BTCUSDT", true, 85737, 86687, sweep + 103, 950);
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 30);
await Scenario(new { market = 86700.0, fail_history = true });   // stopped out while the history endpoint is down
ok = ok && await WaitFor(() => Count(Events(), "closed") >= 1, 45);
{
    var row = ClosedRow();
    Check("S3 history unreadable -> closed/unknown (never a guessed TP), position gone", ok && row.Contains("\"how\":\"unknown\"") && (await State())["positions"]!.AsArray().Count == 0, row[..Math.Min(220, row.Length)]);
}

// T. FIRST TO FILL WINS (2026-11-22, the user: ETH skipped behind two resting orders that never filled). Three BUY entries at 84900 / 84800 / 84700
//    on BTC / ETH / XRP (one shared fake price), cap 2.
int OpenWorking(JsonNode s) => s["orders"]!.AsArray().Count(o => o!["status"]!.ToString() == "WORKING" && o["positionEffect"]!.ToString() == "OPEN");
void Arm3(long b) { ex.OnArm("BTCUSDT", false, 84900, 84500, b, 400, fvg, "raid-fvg", 85000); ex.OnArm("ETHUSDT", false, 84800, 84400, b + 1, 400, fvg, "raid-fvg", 85000); ex.OnArm("XRPUSDT", false, 84700, 84300, b + 2, 400, fvg, "raid-fvg", 85000); }
// T1: two resting entries no longer block a third
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
Arm3(sweep + 200);
ok = await WaitFor(() => Count(Events(), "armed") >= 3, 20); st = await State(); ev = Events();
Check("T1 three resting entries armed (resting orders hold no slot)", ok && OpenWorking(st) == 3 && Count(ev, "skip") == 0, $"working entries={OpenWorking(st)} events={string.Join(",", ev)}");
// T2: fills one at a time -> first two protected, the third cancelled when the cap is reached
await Scenario(new { market = 84850.0 });
ok = await WaitFor(() => Count(Events(), "protected") >= 1, 40);
await Scenario(new { market = 84750.0 });
ok = ok && await WaitFor(() => Count(Events(), "protected") >= 2 && Count(Events(), "armed-cancelled") >= 1, 60);
st = await State(); ev = Events(); pos = st["positions"]!.AsArray().Count;
Check("T2 sequential fills: BTC + ETH protected, XRP entry cancelled at the cap, nothing resting", ok && pos == 2 && Count(ev, "protected") == 2 && Count(ev, "cap-cancel-sent") == 1 && Count(ev, "armed-cancelled") == 1 && OpenWorking(st) == 0 && Count(ev, "over-cap-fill-closed") == 0 && Count(ev, "CANCEL-FAILED") == 0 && Count(ev, "cancel-error") == 0,
      $"positions={pos} working entries={OpenWorking(st)} events={string.Join(",", ev)}");
// T5: two FILLED -> a new arm is skipped (cap counts fills)
ex.OnArm("XRPUSDT", false, 84700, 84300, sweep + 210, 400, fvg, "raid-fvg", 84750);
await Task.Delay(3000); st = await State(); ev = Events();
Check("T5 two filled -> new arm skipped, no new entry", Count(ev, "skip") >= 1 && OpenWorking(st) == 0 && Count(ev, "open-intent") == 3, $"working entries={OpenWorking(st)} events={string.Join(",", ev)}");
// T3: all three cross at once -> exactly two kept and protected, the extra fill closed at market, no alarm
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
Arm3(sweep + 220);
ok = await WaitFor(() => Count(Events(), "armed") >= 3, 20);
await Scenario(new { market = 84650.0 });
ok = ok && await WaitFor(() => Count(Events(), "protected") >= 2 && Count(Events(), "over-cap-fill-closed") >= 1, 60);
ok = ok && await WaitPos(2, 30);
st = await State(); ev = Events(); pos = st["positions"]!.AsArray().Count;
ok = ok && await WaitFor(() => File.ReadAllText(execLog).Contains("\"how\":\"over-cap\""), 90);
var ocRow = File.ReadAllLines(execLog).FirstOrDefault(l => l.Contains("\"how\":\"over-cap\"")) ?? "";
st = await State(); ev = Events(); pos = st["positions"]!.AsArray().Count;
Check("T3b the over-cap trade is journaled closed/over-cap with its settled $ once the broker shows it gone", ok && ocRow.Contains("\"pnl\":-") && pos == 2, ocRow[..Math.Min(220, ocRow.Length)]);
Check("T3 three fill at once -> 2 protected, 1 closed at market, 2 positions left, nothing resting, no alarm", ok && pos == 2 && Count(ev, "protected") == 2 && Count(ev, "over-cap-fill-closed") == 1 && OpenWorking(st) == 0 && Count(ev, "EMERGENCY-CLOSE-FAILED") == 0,
      $"positions={pos} working entries={OpenWorking(st)} events={string.Join(",", ev)}");
// T7 (review finding 1): BTC filled then closed by hand; before the bot journals that close, ETH and XRP fill -> both kept (the closed BTC holds no slot)
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
Arm3(sweep + 250);
ok = await WaitFor(() => Count(Events(), "armed") >= 3, 20);
await Scenario(new { market = 84850.0 });
ok = ok && await WaitFor(() => Count(Events(), "protected") >= 1, 40);
var btcPc = (await State())["positions"]!.AsArray()[0]!["positionCode"]!.ToString();
await Scenario(new { close_position = btcPc }); await Scenario(new { market = 84650.0 });
ok = ok && await WaitFor(() => Count(Events(), "protected") >= 3 || Count(Events(), "over-cap-fill-closed") >= 1, 60);
st = await State(); ev = Events(); pos = st["positions"]!.AsArray().Count;
Check("T7 a position closed but not yet journaled holds no slot: ETH + XRP both protected, none closed over-cap", ok && Count(ev, "protected") == 3 && Count(ev, "over-cap-fill-closed") == 0 && pos == 2, $"positions={pos} events={string.Join(",", ev)}");
// T8 (review finding 2): the positions read fails twice while the entry rests -> keeps waiting, then fills and is protected
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
ex.OnArm("BTCUSDT", false, 84900, 84500, sweep + 260, 400, fvg, "raid-fvg", 85000);
ok = await WaitFor(() => Count(Events(), "armed") >= 1, 20);
await Scenario(new { fail_positions_n = 6 });
ok = ok && await WaitFor(() => Count(Events(), "entry-poll-error") >= 1, 40);
await Scenario(new { market = 84850.0 });
ok = ok && await WaitFor(() => Count(Events(), "protected") >= 1, 60);
st = await State(); ev = Events();
Check("T8 positions read fails while resting -> still watched, fills, protected", ok && Count(ev, "protected") == 1 && st["positions"]!.AsArray().Count == 1 && Count(ev, "open-error") == 0 && Count(ev, "adopted-UNPROTECTED") == 0, $"events={string.Join(",", ev)}");
// T9 (second review B): the entry fills while the positions read is down and the setup dies -> not dropped: watched until the fill is found, protected
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
ex.OnArm("BTCUSDT", false, 84900, 84500, sweep + 270, 400, fvg, "raid-fvg", 85000);
ok = await WaitFor(() => Count(Events(), "armed") >= 1, 20);
await Scenario(new { fail_positions_n = 8 }); await Scenario(new { market = 84850.0 }); ex.OnDead("BTCUSDT", sweep + 270, "wick retaken");
ok = ok && await WaitFor(() => Count(Events(), "protected") >= 1 || Count(Events(), "armed-cancelled") >= 1, 150);
st = await State(); ev = Events(); pos = st["positions"]!.AsArray().Count;
Check("T9 fill hidden by a failing positions read at the cancel -> kept watching, found, protected (never dropped)", ok && Count(ev, "protected") == 1 && Count(ev, "armed-cancelled") == 0 && pos == 1, $"positions={pos} events={string.Join(",", ev)}");
// T4: the entry fills in the same moment the scanner kills the setup -> found by the last look and protected, never left untracked
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
ex.OnArm("BTCUSDT", false, 84900, 84500, sweep + 230, 400, fvg, "raid-fvg", 85000);
ok = await WaitFor(() => Count(Events(), "armed") >= 1, 20);
await Scenario(new { market = 84850.0 }); ex.OnDead("BTCUSDT", sweep + 230, "wick retaken");
ok = ok && await WaitFor(() => Count(Events(), "protected") >= 1 || Count(Events(), "armed-cancelled") >= 1, 40);
st = await State(); ev = Events(); pos = st["positions"]!.AsArray().Count;
int closeWorking = st["orders"]!.AsArray().Count(o => o!["status"]!.ToString() == "WORKING" && o["positionEffect"]!.ToString() == "CLOSE");
Check("T4 fill at the moment of the cancel -> filled-at-cancel, protected (stop + TP working)", ok && pos == 1 && Count(ev, "filled-at-cancel") == 1 && Count(ev, "protected") == 1 && closeWorking == 2 && Count(ev, "armed-cancelled") == 0,
      $"positions={pos} close orders={closeWorking} events={string.Join(",", ev)}");
// T6: the broker refuses every cancel -> CANCEL-FAILED logged (alarm on screen), no crash, no position
ex = await Fresh(ex);
await Scenario(new { market = 85000.0, latency_ms = 150 });
ex.OnArm("BTCUSDT", false, 84900, 84500, sweep + 240, 400, fvg, "raid-fvg", 85000);
ok = await WaitFor(() => Count(Events(), "armed") >= 1, 20);
await Scenario(new { fail_delete_n = 3 }); ex.OnDead("BTCUSDT", sweep + 240, "wick retaken");
ok = ok && await WaitFor(() => Count(Events(), "armed-cancelled") >= 1, 40);
ev = Events(); st = await State();
Check("T6 cancel refused 3 times -> CANCEL-FAILED alarm, trade released, no position", ok && Count(ev, "CANCEL-FAILED") == 1 && Count(ev, "cancel-error") == 3 && st["positions"]!.AsArray().Count == 0, $"events={string.Join(",", ev)}");

File.Copy(execLog, Path.Combine(vault, $"scenario_{scen}.log"), true);
ex.Dispose();
foreach (var l in File.ReadAllLines(execLog).Where(l => l.Contains("error"))) Console.WriteLine("  err: " + l[..Math.Min(220, l.Length)]);
Console.WriteLine(fails == 0 ? "ALL PASS" : $"{fails} FAIL");
return fails == 0 ? 0 : 1;
