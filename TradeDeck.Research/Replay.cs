using System.IO;
using System.Text.Json;
namespace TradeDeck.Research;

/// <summary>
/// Forward-record audit: run the SAME detectors over a folder of
/// 5m candle JSON files ([ts,o,h,l,c,v] arrays, one per market) and
/// print every checklist-complete setup as CSV so it can be lined up
/// against reviews/vault/journal.jsonl trade by trade.
/// dotnet run replay &lt;dir&gt; &lt;prefix&gt;   e.g. replay C:\...\sept bn
/// Columns: market,direction,sweepUtc,pivotUtc,ageHours,level,sweepExtreme,entry,stop,netR
/// </summary>
public static class Replay
{
    private static readonly string[] All =
        { "BTCUSDT", "ETHUSDT", "XRPUSDT", "SOLUSDT", "DOGEUSDT", "LINKUSDT", "HYPEUSDT" };

    public static void Run(string dir, string prefix)
    {
        Console.WriteLine("market,direction,sweepUtc,pivotUtc,ageHours,level,sweepExtreme,entry,stop,netR,fvg,breachPct,entryUtc,exitUtc");
        foreach (string mkt in All)
        {
            string path = Path.Combine(dir, $"{prefix}_{mkt}_5m.json");
            if (!File.Exists(path)) continue;
            var arr = JsonSerializer.Deserialize<double[][]>(File.ReadAllText(path))!;
            var five = arr.Select(k => new ResearchCandle((long)k[0], k[1], k[2], k[3], k[4], k[5]))
                          .OrderBy(k => k.Timestamp).ToList();
            var fifteen = Scale.Aggregate(five, 15 * 60_000L);

            SweepDetector.LevelMode = SweepDetector.LevelModeKind.D_ONLY;
            var raids = SweepDetector.FindCandidates(mkt, fifteen);
            var setups = raids.Select(r => SetupDetector.Build(r, five)).ToList();
            var sink = new List<(double resultR, double feeR, FullSetup s)>();
            BacktestScorer.Profile(mkt, setups, five, sink);

            foreach (var t in sink.OrderBy(t => t.s.Raid.SweepTimestamp))
            {
                var s = t.s;
                double breach = Math.Abs(s.SweepExtreme - s.Raid.Level) / s.Raid.Level;
                bool pass = s.FvgSizeRatio >= 0.35 && breach >= 0.0013;
                if (!pass) continue;
                string dirn = s.Raid.Direction == "HIGH" ? "SHORT" : "LONG";
                double age = (s.Raid.SweepTimestamp - s.Raid.PivotTimestamp) / 3_600_000.0;
                Console.WriteLine(string.Join(",",
                    mkt, dirn,
                    DateTimeOffset.FromUnixTimeMilliseconds(s.Raid.SweepTimestamp).ToString("yyyy-MM-ddTHH:mm"),
                    DateTimeOffset.FromUnixTimeMilliseconds(s.Raid.PivotTimestamp).ToString("yyyy-MM-ddTHH:mm"),
                    age.ToString("F1"),
                    s.Raid.Level.ToString("G8"), s.SweepExtreme.ToString("G8"),
                    s.EntryPrice.ToString("G8"), s.SweepExtreme.ToString("G8"),
                    (t.resultR - t.feeR).ToString("F3"),
                    s.FvgSizeRatio.ToString("F2"), (breach * 100).ToString("F3"),
                    DateTimeOffset.FromUnixTimeMilliseconds(five[s.EntryIndex5m].Timestamp).ToString("yyyy-MM-ddTHH:mm"),
                    DateTimeOffset.FromUnixTimeMilliseconds(five[BacktestScorer.ExitIndex(s, five)].Timestamp).ToString("yyyy-MM-ddTHH:mm")));
            }
        }
    }
}
