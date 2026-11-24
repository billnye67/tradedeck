namespace TradeDeck.Core.Markets;

public sealed class MarketDefinition
{
    public string Symbol { get; set; } = "";
    public string DisplayName { get; set; } = "";
    public string AssetClass { get; set; } = "";
    public string DataSource { get; set; } = "";

    public bool ScanEnabled { get; set; } = true;
    public bool Pinned { get; set; } = false;
}
