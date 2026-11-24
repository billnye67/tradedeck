#!/bin/bash
# Copies the executor source into the rig so the harness compiles the same code the app uses.
# The executor's server addresses already point at the fake broker (127.0.0.1:8899).
set -e
R="$(cd "$(dirname "$0")" && pwd)"
SRC="$R/../../TradeDeck/BrokerExecutor.cs"
mkdir -p "$R/patched/TradeDeck"
cp "$SRC" "$R/patched/TradeDeck/BrokerExecutor.cs"
cp "${SRC%.cs}.Fx.cs" "$R/patched/TradeDeck/BrokerExecutor.Fx.cs"
sed -i 's#private const string PriceUrl = "https://api.hyperliquid.xyz/info";#private const string PriceUrl = "http://127.0.0.1:8899/info";#' "$R/patched/TradeDeck/BrokerExecutor.cs"
grep -q '127.0.0.1:8899/dxsca-web' "$R/patched/TradeDeck/BrokerExecutor.cs"
grep -q '127.0.0.1:8899/info' "$R/patched/TradeDeck/BrokerExecutor.cs"
echo "prepared"
