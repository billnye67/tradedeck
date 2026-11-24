namespace TradeDeck.Strategy1;

public enum SetupDirection
{
    Long,
    Short
}

public enum SetupStage
{
    SearchingForStructure,
    WatchingForSweep,
    Swept,
    ReturnedInside,
    TriggerIdentified,
    DisplacementConfirmed,
    FvgConfirmed,
    WaitingForRetrace,
    EntryReady,
    Invalidated
}

public sealed class Strategy1Setup
{
    public SetupDirection Direction { get; set; }

    public SetupStage Stage { get; set; }
        = SetupStage.SearchingForStructure;

    // 15m structure
    public decimal StructuralLevel { get; set; }
    public DateTime StructuralLevelTime { get; set; }

    // Actual extreme created by liquidity sweep
    public decimal SweepPrice { get; set; }
    public DateTime SweepTime { get; set; }

    // 5m structural pullback that produced final push
    public decimal TriggerPrice { get; set; }
    public DateTime TriggerTime { get; set; }

    // Displacement candle
    public DateTime? DisplacementTime { get; set; }

    // FVG zone
    public decimal? FvgLow { get; set; }
    public decimal? FvgHigh { get; set; }

    // Trade levels
    public decimal? EntryPrice { get; set; }
    public decimal? StopLoss { get; set; }
    public decimal? TargetPrice { get; set; }

    // Debugging / research
    public string? InvalidReason { get; set; }
    public string? Notes { get; set; }
}