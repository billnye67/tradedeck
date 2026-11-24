namespace TradeDeck.Core.Models;

public sealed class Candle
{
    public string Market { get; set; } = "";
    public long Timestamp { get; set; }

    public double Open { get; set; }
    public double High { get; set; }
    public double Low { get; set; }
    public double Close { get; set; }
    public double Volume { get; set; }
}
