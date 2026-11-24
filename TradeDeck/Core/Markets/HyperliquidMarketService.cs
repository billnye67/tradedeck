using System.Net.Http;
using System.Text;
using System.Text.Json;

namespace TradeDeck.Core.Markets;

public static class HyperliquidMarketService
{
    private static readonly HttpClient Http = new();

    public static async Task<List<MarketDefinition>> GetMarketsAsync()
    {
        using var content = new StringContent(
            """{"type":"meta"}""",
            Encoding.UTF8,
            "application/json"
        );

        using var response = await Http.PostAsync(
            "https://api.hyperliquid.xyz/info",
            content
        );

        response.EnsureSuccessStatusCode();

        var json = await response.Content.ReadAsStringAsync();

        using var document = JsonDocument.Parse(json);

        var universe = document.RootElement.GetProperty("universe");

        var markets = new List<MarketDefinition>();

        foreach (var item in universe.EnumerateArray())
        {
            var symbol = item.GetProperty("name").GetString();

            if (string.IsNullOrWhiteSpace(symbol))
                continue;

            markets.Add(new MarketDefinition
            {
                Symbol = symbol,
                DisplayName = symbol,
                AssetClass = "Crypto Perpetual",
                DataSource = "Hyperliquid",
                ScanEnabled = true
            });
        }

        return markets;
    }
}
