using System.Text.Json;

namespace TradeDeck.Research;

/// <summary>
/// Takes an exported grades JSON, re-runs the (deterministic)
/// detector, matches each graded candidate back to its candles,
/// and measures what separates REAL from NAH in plain numbers:
/// sweep momentum, sweep volume, breach depth, reclaim speed,
/// level age. No opinions — measurements.
/// </summary>
public static class GradeAnalyzer
{
    private const int BaselineWindow = 20; // candles before the sweep used as "normal" reference

    private sealed record GradeRow(
        string Direction,
        string PivotUtc,
        string SweepUtc,
        string? Grade
    );

    private sealed record Measured(
        string Grade,
        string SweepUtc,
        string Direction,
        int AgeBars,
        double BreachPct,
        int ReclaimBars,
        double DispRatio,
        double VolRatio
    );

    public static void Run(
        string gradesPath,
        string source,
        string interval)
    {
        string json = File.ReadAllText(gradesPath);

        var rows = JsonSerializer.Deserialize<List<GradeRow>>(
            json,
            new JsonSerializerOptions
            {
                PropertyNameCaseInsensitive = true
            }
        )!;

        rows = rows
            .Where(r => r.Grade != null)
            .ToList();

        if (rows.Count == 0)
        {
            Console.WriteLine("No graded rows in that file.");
            return;
        }

        // All grades in one file are one market.
        string market = MarketFromFileName(gradesPath);

        var reader = new HistoryReader();

        var candles =
            reader.GetAllCandles(source, market, interval);

        var candidates =
            SweepDetector.FindCandidates(market, candles);

        var byKey = candidates.ToDictionary(
            c => Key(c.Direction, ToUtcText(c.PivotTimestamp), ToUtcText(c.SweepTimestamp)),
            c => c
        );

        var measured = new List<Measured>();
        int unmatched = 0;

        foreach (GradeRow row in rows)
        {
            if (!byKey.TryGetValue(
                Key(row.Direction, row.PivotUtc, row.SweepUtc),
                out SweepCandidate? c))
            {
                unmatched++;
                continue;
            }

            measured.Add(Measure(row.Grade!, c, candles));
        }

        Console.WriteLine();
        Console.WriteLine($"GRADE ANALYSIS — {market} ({measured.Count} matched, {unmatched} unmatched)");
        Console.WriteLine();
        Console.WriteLine(
            "grade  | sweep (UTC)        | dir  | age | breach% | reclaim | disp x | vol x"
        );
        Console.WriteLine(new string('-', 84));

        foreach (Measured m in measured)
        {
            Console.WriteLine(
                $"{m.Grade,-6} | {m.SweepUtc,-18} | {m.Direction,-4} | " +
                $"{m.AgeBars,3} | {m.BreachPct,7:F3} | {m.ReclaimBars,7} | " +
                $"{m.DispRatio,6:F2} | {m.VolRatio,5:F2}"
            );
        }

        Console.WriteLine();
        Console.WriteLine("GROUP AVERAGES");
        Console.WriteLine(
            "grade  |   n | age | breach% | reclaim | disp x | vol x"
        );
        Console.WriteLine(new string('-', 60));

        foreach (var g in measured
            .GroupBy(m => m.Grade)
            .OrderBy(g => g.Key))
        {
            Console.WriteLine(
                $"{g.Key,-6} | {g.Count(),3} | " +
                $"{g.Average(m => m.AgeBars),3:F0} | " +
                $"{g.Average(m => m.BreachPct),7:F3} | " +
                $"{g.Average(m => m.ReclaimBars),7:F2} | " +
                $"{g.Average(m => m.DispRatio),6:F2} | " +
                $"{g.Average(m => m.VolRatio),5:F2}"
            );
        }

        Console.WriteLine();
        Console.WriteLine(
            "disp x = sweep candle range vs avg range of prior " +
            $"{BaselineWindow} candles. vol x = same for volume."
        );
        Console.WriteLine(
            "If REAL and NAH averages clearly separate on a column, " +
            "that column is the next detector rule."
        );
    }

    private static Measured Measure(
        string grade,
        SweepCandidate c,
        IReadOnlyList<ResearchCandle> candles)
    {
        int sweep = c.SweepIndex;
        bool isHigh = c.Direction == "HIGH";

        double breach = isHigh
            ? candles[sweep].High - c.Level
            : c.Level - candles[sweep].Low;

        int reclaimBars = 0;

        for (int r = sweep; r < candles.Count; r++)
        {
            bool inside = isHigh
                ? candles[r].Close < c.Level
                : candles[r].Close > c.Level;

            if (inside)
            {
                reclaimBars = r - sweep;
                break;
            }
        }

        double sweepRange =
            candles[sweep].High - candles[sweep].Low;

        double avgRange = 0;
        double avgVol = 0;
        int n = 0;

        for (int k = 1; k <= BaselineWindow; k++)
        {
            int idx = sweep - k;
            if (idx < 0) break;

            avgRange += candles[idx].High - candles[idx].Low;
            avgVol += candles[idx].Volume;
            n++;
        }

        if (n > 0)
        {
            avgRange /= n;
            avgVol /= n;
        }

        return new Measured(
            grade,
            ToUtcText(c.SweepTimestamp),
            c.Direction,
            c.SweepIndex - c.PivotIndex,
            breach / c.Level * 100.0,
            reclaimBars,
            avgRange > 0 ? sweepRange / avgRange : 0,
            avgVol > 0 ? candles[sweep].Volume / avgVol : 0
        );
    }

    private static string Key(string d, string p, string s) =>
        $"{d}|{p}|{s}";

    private static string MarketFromFileName(string path)
    {
        // sweep-grades-BTCUSDT.json -> BTCUSDT
        string name =
            Path.GetFileNameWithoutExtension(path);

        int dash = name.LastIndexOf('-');

        return dash >= 0
            ? name[(dash + 1)..].ToUpperInvariant()
            : name.ToUpperInvariant();
    }

    private static string ToUtcText(long unixMs) =>
        DateTimeOffset
            .FromUnixTimeMilliseconds(unixMs)
            .ToString("yyyy-MM-dd HH:mm 'UTC'");
}
