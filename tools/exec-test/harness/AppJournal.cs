namespace TradeDeck;

// Harness stub: the real AppJournal lives in the app. The executor only needs the vault folder (for executions.jsonl) and IsGraded.
public static class AppJournal
{
    public static string VaultFolder => Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "..", "reviews", "vault"));
    public static bool IsGraded(string market, long sweepTs) => false;
    // 2026-10-19: broker-fill journal row (real one in the app); the rig appends it so a scenario can assert on it
    public static void EnsureFilledFromBroker(string market, bool isShort, double entry, double stop, long sweepTs, double riskPerUnit, double fill)
    {
        try
        {
            Directory.CreateDirectory(VaultFolder);
            string line = System.Text.Json.JsonSerializer.Serialize(new { epochMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(), sweepTs, market, status = "FILLED", direction = isShort ? "SHORT" : "LONG", entry, stop, riskPerUnit, fill, source = "broker-fill" });
            File.AppendAllText(Path.Combine(VaultFolder, "journal.jsonl"), line + Environment.NewLine);
        }
        catch { }
    }
}
