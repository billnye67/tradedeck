using System;
using System.IO;
using System.Linq;
using System.Windows;
using TradeDeck.Core.Markets;
using TradeDeck.Data;

namespace TradeDeck;

public partial class App : Application
{
    private static System.Threading.Mutex? _single;

    protected override async void OnStartup(StartupEventArgs e)
    {
        // Single instance: the auto-start/watchdog task may fire while
        // the app is already running; a second copy would double-scan
        // and double-journal.
        _single = new System.Threading.Mutex(true, "TradeDeck.SingleInstance", out bool first);
        if (!first) { Shutdown(); return; }

        base.OnStartup(e);

        TradeDeckDb.Initialize();

        string logPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "TradeDeck",
            "startup.log"
        );

        try
        {
            var markets = await HyperliquidMarketService.GetMarketsAsync();

            File.WriteAllText(
                logPath,
                $"Discovered {markets.Count} Hyperliquid markets.{Environment.NewLine}" +
                $"Sample: {string.Join(", ", markets.Take(30).Select(x => x.Symbol))}"
            );
        }
        catch (Exception ex)
        {
            File.WriteAllText(
                logPath,
                $"MARKET ERROR: {ex}"
            );
        }
    }
}
