using System;
using System.Collections.Generic;
using System.IO;
using System.Text.Json;
using System.Threading.Tasks;
using System.Windows;
using Microsoft.Web.WebView2.Core;
using TradeDeck.Core.Models;
using TradeDeck.Strategy1;

namespace TradeDeck;

public partial class MainWindow : Window
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    private readonly ScannerService _scanner = new();
    private readonly BrokerExecutor _executor = new();
    private readonly BrokerExecutor _executor2 = new("broker2.env") { Slot = 2 };   // second account slot
    private readonly BrokerExecutor _executor3 = new("broker3.env") { Slot = 3 };   // third account slot
    private FxMidnightService? _fx;
    private readonly PositioningService _positioning = new();
    private readonly RecorderService _recorder = new();
    private readonly NewsService _news = new();

    // A5: background mode — tray icon keeps the scanner alive when
    // the window is closed; alerts surface as Windows notifications.
    private System.Windows.Forms.NotifyIcon? _tray;
    private bool _reallyExit;

    private static readonly System.Net.Http.HttpClient JournalHttp =
        new() { Timeout = TimeSpan.FromMinutes(1) };

    [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
    private static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

    public MainWindow()
    {
        InitializeComponent();
        Loaded += MainWindow_Loaded;

        // dark title bar (Windows 10 20H1+ / 11): DWMWA_USE_IMMERSIVE_DARK_MODE = 20
        SourceInitialized += (_, _) =>
        {
            try
            {
                var hwnd = new System.Windows.Interop.WindowInteropHelper(this).Handle;
                int dark = 1; DwmSetWindowAttribute(hwnd, 20, ref dark, sizeof(int));
            }
            catch { }
        };

        // close button -> hide to tray, scanner keeps running
        Closing += (_, e) =>
        {
            if (_reallyExit) return;
            e.Cancel = true;
            Hide();
            _tray?.ShowBalloonTip(
                2000, "TradeDeck",
                "Still scanning in the background. Double-click the tray icon to reopen.",
                System.Windows.Forms.ToolTipIcon.Info);
        };

        _positioning.Message += json =>
            Dispatcher.Invoke(() =>
            {
                try { Browser.CoreWebView2?.PostWebMessageAsJson(json); }
                catch { }
            });
        _positioning.Start();

        _recorder.Message += json =>
            Dispatcher.Invoke(() =>
            {
                try { Browser.CoreWebView2?.PostWebMessageAsJson(json); }
                catch { }
            });
        _recorder.Start();

        _news.Message += json =>
            Dispatcher.Invoke(() =>
            {
                try { Browser.CoreWebView2?.PostWebMessageAsJson(json); }
                catch { }
            });
        _news.Start();

        Closed += (_, _) =>
        {
            _news.Dispose();
            _recorder.Dispose();
            _positioning.Dispose();
            _fx?.Dispose();
            _executor.Dispose();
            _executor2.Dispose();
            _executor3.Dispose();
            _scanner.Dispose();
            if (_tray != null) { _tray.Visible = false; _tray.Dispose(); }
        };

        SetupTray();

        _scanner.Executor = _executor;
        _scanner.Executors.Add(_executor); _scanner.Executors.Add(_executor2); _scanner.Executors.Add(_executor3);
        Action<object> toBrowser = o =>
        {
            string json = JsonSerializer.Serialize(o);
            Dispatcher.Invoke(() => { try { Browser.CoreWebView2?.PostWebMessageAsJson(json); } catch { } });
        };
        _executor.Message += toBrowser;
        _executor2.Message += toBrowser;
        _executor3.Message += toBrowser;

        _scanner.Message += json =>
        {
            NotifyIfAlert(json);

            Dispatcher.Invoke(() =>
            {
                try
                {
                    Browser.CoreWebView2?.PostWebMessageAsJson(json);
                }
                catch
                {
                    // window closing; ignore
                }
            });
        };
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        await Browser.EnsureCoreWebView2Async();

        Browser.CoreWebView2.WebMessageReceived += Browser_WebMessageReceived;

        string webFolder = Path.Combine(
            AppDomain.CurrentDomain.BaseDirectory,
            "Web"
        );

        Browser.CoreWebView2.SetVirtualHostNameToFolderMapping(
            "tradedeck.local",
            webFolder,
            CoreWebView2HostResourceAccessKind.Allow
        );

        Browser.Source = new Uri("https://tradedeck.local/index.html");
    }

    private void Browser_WebMessageReceived(
        object? sender,
        CoreWebView2WebMessageReceivedEventArgs e)
    {
        // Scanner control messages first; anything else falls
        // through to the existing structure handler untouched.
        try
        {
            using var probe = JsonDocument.Parse(e.WebMessageAsJson);

            if (probe.RootElement.TryGetProperty("type", out var t))
            {
                string type = t.GetString() ?? "";

                if (type == "scanner-start")
                {
                    double risk =
                        probe.RootElement.TryGetProperty("risk", out var r) &&
                        r.TryGetDouble(out double rv)
                            ? rv
                            : 100;

                    if (probe.RootElement.TryGetProperty("dailyCapR", out var dc) &&
                        dc.TryGetDouble(out double dcv)) _scanner.DailyLossCapR = dcv;

                    if (probe.RootElement.TryGetProperty("maxOpen", out var mo) &&
                        mo.TryGetInt32(out int mov)) _scanner.MaxOpenCalls = mov;

                    _scanner.Start(risk);
                    _executor.Start();
                    _executor2.Start();
                    _executor3.Start();
                    _fx ??= new FxMidnightService(_executor);
                    _fx.Start();                                   // forex brain; does nothing unless BROKER_FX_MODE is dry or live
                    PostAlertHistory();
                    return;
                }

                if (type == "scanner-stop")
                {
                    _scanner.Stop();
                    _fx?.Stop();
                    return;
                }

                if (type == "history-request")
                {
                    var hr = probe.RootElement;
                    string hm = hr.GetProperty("market").GetString() ?? "";
                    string hi = hr.GetProperty("interval").GetString() ?? "5m";
                    long beforeSec = hr.GetProperty("beforeSec").GetInt64();

                    _ = Task.Run(() =>
                    {
                        string json = HistoryBridge.Slice(hm, hi, beforeSec, 3000);

                        Dispatcher.Invoke(() =>
                        {
                            try { Browser.CoreWebView2?.PostWebMessageAsJson(json); }
                            catch { }
                        });
                    });

                    return;
                }

                if (type == "record-open")
                {
                    var ro = probe.RootElement;

                    string mkt = ro.GetProperty("market").GetString() ?? "";
                    long ep = ro.GetProperty("epochMs").GetInt64();
                    string dir = ro.GetProperty("direction").GetString() ?? "";
                    double en = ro.GetProperty("entry").GetDouble();
                    double st = ro.GetProperty("stop").GetDouble();
                    double rk = ro.GetProperty("riskPerUnit").GetDouble();

                    _ = Task.Run(async () =>
                    {
                        try
                        {
                            string json = await AppJournal.BuildPlayoutAsync(
                                JournalHttp, mkt, ep, dir, en, st, rk);

                            Dispatcher.Invoke(() =>
                            {
                                try
                                {
                                    Browser.CoreWebView2?
                                        .PostWebMessageAsJson(json);
                                }
                                catch { }
                            });
                        }
                        catch { }
                    });

                    return;
                }

                if (type == "open-url")
                {
                    try
                    {
                        string? url = probe.RootElement.GetProperty("url").GetString();
                        if (url != null && url.StartsWith("http"))
                            System.Diagnostics.Process.Start(
                                new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true });
                    }
                    catch { }
                    return;
                }

                if (type == "replay-request")
                {
                    try
                    {
                        string json = AppJournal.RecentAlertsJson(12);
                        Browser.CoreWebView2?.PostWebMessageAsJson(json);
                    }
                    catch { }
                    return;
                }

                if (type == "voice-toggle")
                {
                    try
                    {
                        bool muted = probe.RootElement.TryGetProperty("muted", out var mu) && mu.GetBoolean();
                        ScannerService.VoiceMuted = muted;
                        if (muted)
                        {
                            ScannerService.StopSpeaking();
                            NewsService.StopSpeaking();
                        }
                    }
                    catch { }
                    return;
                }

                if (type == "coverage-request")
                {
                    try
                    {
                        Browser.CoreWebView2?.PostWebMessageAsJson(NewsService.CoverageJson());
                    }
                    catch { }
                    return;
                }

                if (type == "news-request")
                {
                    _ = Task.Run(() =>
                    {
                        string json = NewsService.BuildFeedJson();

                        Dispatcher.Invoke(() =>
                        {
                            try
                            {
                                Browser.CoreWebView2?
                                    .PostWebMessageAsJson(json);
                            }
                            catch { }
                        });
                    });

                    return;
                }

                if (type == "data-request")
                {
                    _ = Task.Run(() =>
                    {
                        string json = DataInventory.BuildJson();

                        Dispatcher.Invoke(() =>
                        {
                            try
                            {
                                Browser.CoreWebView2?
                                    .PostWebMessageAsJson(json);
                            }
                            catch { }
                        });
                    });

                    return;
                }

                if (type == "exec-record-request")
                {
                    try { Browser.CoreWebView2?.PostWebMessageAsJson(_executor.RecordJson()); } catch { }
                    return;
                }

                if (type == "record-request")
                {
                    _ = Task.Run(async () =>
                    {
                        string json =
                            await AppJournal.GetRecordJsonAsync(JournalHttp);

                        Dispatcher.Invoke(() =>
                        {
                            try
                            {
                                Browser.CoreWebView2?
                                    .PostWebMessageAsJson(json);
                            }
                            catch { }
                        });
                    });

                    return;
                }
            }
        }
        catch
        {
            // not scanner traffic; continue to structure handler
        }

        try
        {
            var request = JsonSerializer.Deserialize<StructureRequest>(
                e.WebMessageAsJson,
                JsonOptions
            );

            if (
                request == null ||
                request.Type != "strategy1-structure" ||
                request.Interval != "15m" ||
                request.Candles.Count == 0
            )
            {
                return;
            }

            StructuralLevel? high =
                Strategy1StructureDetector.FindCurrentHigh(request.Candles);

            StructuralLevel? low =
                Strategy1StructureDetector.FindCurrentLow(request.Candles);

            var response = new
            {
                type = "strategy1-structure-result",

                high = high == null
                    ? null
                    : new
                    {
                        price = high.Price,
                        timestamp = high.Timestamp,
                        atr = high.Atr,
                        moveAway = high.MoveAway
                    },

                low = low == null
                    ? null
                    : new
                    {
                        price = low.Price,
                        timestamp = low.Timestamp,
                        atr = low.Atr,
                        moveAway = low.MoveAway
                    }
            };

            string json = JsonSerializer.Serialize(
                response,
                JsonOptions
            );

            Browser.CoreWebView2.PostWebMessageAsJson(json);
        }
        catch (Exception ex)
        {
            Console.WriteLine(
                "Strategy #1 structure error: " + ex.Message
            );
        }
    }

    private void SetupTray()
    {
        _tray = new System.Windows.Forms.NotifyIcon
        {
            Icon = System.Drawing.SystemIcons.Application,
            Text = "TradeDeck — scanning",
            Visible = true
        };

        var menu = new System.Windows.Forms.ContextMenuStrip();
        menu.Items.Add("Open TradeDeck", null, (_, _) => RestoreWindow());
        menu.Items.Add("Exit (stops scanner)", null, (_, _) =>
        {
            _reallyExit = true;
            Close();
        });
        _tray.ContextMenuStrip = menu;
        _tray.DoubleClick += (_, _) => RestoreWindow();
    }

    private void RestoreWindow()
    {
        Show();
        WindowState = WindowState.Normal;
        Activate();
    }

    private void NotifyIfAlert(string json)
    {
        try
        {
            using var doc = JsonDocument.Parse(json);
            var e = doc.RootElement;

            if (e.GetProperty("type").GetString() != "scanner-alert") return;

            string status = e.GetProperty("status").GetString() ?? "";
            string market = e.GetProperty("market").GetString() ?? "";
            string dir = e.GetProperty("direction").GetString() ?? "";
            string tier = e.GetProperty("tier").GetString() ?? "";
            double entry = e.GetProperty("entry").GetDouble();
            double stop = e.GetProperty("stop").GetDouble();

            Dispatcher.Invoke(() =>
                _tray?.ShowBalloonTip(
                    8000,
                    $"TradeDeck {status}: {market} {dir}",
                    $"entry {entry}  stop {stop}\n{tier}",
                    tier.StartsWith("PLAYBOOK")
                        ? System.Windows.Forms.ToolTipIcon.Info
                        : System.Windows.Forms.ToolTipIcon.Warning));
        }
        catch { }
    }

    private void PostAlertHistory()
    {
        _ = Task.Run(() =>
        {
            try
            {
                string jp = System.IO.Path.Combine(
                    AppJournal.VaultFolder, "journal.jsonl");

                if (!System.IO.File.Exists(jp)) return;

                var rows = System.IO.File.ReadAllLines(jp)
                    .Where(l => !string.IsNullOrWhiteSpace(l))
                    .Select(l => JsonDocument.Parse(l).RootElement.Clone())
                    .GroupBy(e =>
                        $"{e.GetProperty("market").GetString()}|" +
                        $"{e.GetProperty("direction").GetString()}|" +
                        $"{e.GetProperty("sweepTs").GetInt64()}|" +
                        $"{e.GetProperty("status").GetString()}")
                    .Select(g => g.First())
                    .OrderByDescending(e => e.GetProperty("epochMs").GetInt64())
                    .Take(30)
                    .Select(e => new
                    {
                        utc = DateTimeOffset
                            .FromUnixTimeMilliseconds(
                                e.GetProperty("epochMs").GetInt64())
                            .UtcDateTime.ToString("HH:mm"),
                        market = e.GetProperty("market").GetString(),
                        status = e.GetProperty("status").GetString(),
                        direction = e.GetProperty("direction").GetString(),
                        entry = e.GetProperty("entry").GetDouble(),
                        stop = e.GetProperty("stop").GetDouble(),
                        tier = e.GetProperty("tier").GetString()
                    })
                    .ToList();

                string json = JsonSerializer.Serialize(new
                {
                    type = "alert-history",
                    rows
                });

                Dispatcher.Invoke(() =>
                {
                    try
                    {
                        Browser.CoreWebView2?.PostWebMessageAsJson(json);
                    }
                    catch { }
                });
            }
            catch { /* no history yet */ }
        });
    }

    private sealed class StructureRequest
    {
        public string Type { get; set; } = "";
        public string Interval { get; set; } = "";
        public List<Candle> Candles { get; set; } = new();
    }
}
