# TradeDeck

A market research terminal for Windows, written in C# on .NET 8 (WPF with a WebView2 front end), with a Python lab for backtesting ideas.

It is a research tool. Nothing here is a claim that any strategy makes money, and most of the ideas tested in it failed. Those failures are kept on purpose.

## What it does

- **Terminal** (`TradeDeck/`): a desktop app that records market data, scans crypto markets for one rule-based setup, shows charts and a news feed, and keeps a journal of every setup it saw.
- **Research CLI** (`TradeDeck.Research/`): a command-line backtester in C#. It replays recorded history through the setup detector and through alternative entry, stop and exit rules.
- **History store** (`TradeDeck.History/`): downloads and stores candles in a local SQLite database.
- **Python lab** (`tools/`, and the scripts in `specs/`): about 165 scripts that test other ideas (momentum, session effects, published papers, strategies from videos) with NumPy, pandas, numba and scikit-learn.
- **Order executor** (`TradeDeck/BrokerExecutor.cs`): places and manages orders over a DXtrade-style REST API. In this copy it talks only to the fake broker described below.

Size: about 17,000 lines of C# and 17,500 lines of Python.

## The test registry

`specs/REGISTRY.md` is one table of every idea that was tested, with its result and verdict.

- **Written down first.** Each test had a spec written before any result was looked at: the rules, the data, the costs and what counts as a pass.
- **Two eras.** Data before 2024 is the learn era, where rules can be studied. Data from 2024 on is the exam era, which only judges. Rules are not changed after seeing exam results.
- **Costs included.** Results are reported after commission, slippage and financing, in R (multiples of the amount risked per trade).
- **Controls.** Many tests run a coin-flip or placebo version next to the real rule, to see whether the rule beats chance.
- **Failures stay.** Most rows end in "failed", "rejected" or "killed", and they are kept in the table.

This public copy shows 133 of the original 172 registry rows. The other rows, and the individual spec and result files the rows point to, are not included.

## The fake-broker test rig

`tools/exec-test/` tests the order executor without a real broker.

- `fake_dx.py` is a small local server that imitates the broker's REST API on `127.0.0.1:8899`. Scenarios can make it reject orders, add latency, drop protective orders or fail requests. Every test account starts with the same balance, set by `START_BALANCE` at the top of the file.
- `harness/` is a C# program that compiles the same executor source the app uses and runs it through scripted scenarios: rejected limit orders, two orders racing, several accounts on one login, reconnects in the middle of a fill. Each scenario ends in pass/fail checks.
- `prepare.sh` copies the executor source into the rig.

The executor's server address in this copy is the fake broker. With no `broker.env` file the executor is off, and the default mode in `broker.env.example` is `dry`, which logs requests and sends nothing.

## Building

Requirements: Windows, the .NET 8 SDK, and the WebView2 runtime (included with current Windows).

```
dotnet build TradeDeck/TradeDeck.csproj
dotnet run --project TradeDeck/TradeDeck.csproj
```

Research CLI:

```
dotnet run --project TradeDeck.Research -- <command>
```

The commands are listed at the top of `TradeDeck.Research/Program.cs`. They need the local history database, which the History project builds from public exchange data.

Test rig (Git Bash, Python 3):

```
bash tools/exec-test/prepare.sh
python tools/exec-test/fake_dx.py &
dotnet run --project tools/exec-test/harness
```

Python lab: the scripts need `numpy pandas numba scikit-learn scipy`. Many expect data files that are not in this repo and use placeholder paths (`C:\Users\you\...`), so treat them as a record of how each test was run more than as something that runs out of the box.

## What is not here

- Market data files, the local databases and any trading records.
- The per-test spec and result files.
- Account details and configuration for a real broker.
- Two side windows of the app that were not part of the research tool.

The app in this copy builds. The test rig and the Python scripts were not re-run after names, paths and the rig's starting balance were changed for this copy.

## Licence

MIT. See `LICENSE`.
