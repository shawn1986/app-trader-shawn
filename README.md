# trader-shawn

Trader Shawn 是一個以 IBKR 為 broker 的 credit spread 自動交易工具。它目前提供 CLI 操作、持倉管理、audit/runtime 快照，以及本機 War Room 戰情室 UI。

這個專案預設以 `paper` 模式執行。任何會送單的流程都應先在 paper account 驗證，確認 IBKR 連線、market data、AI provider、risk guard、audit log 都正常後，才考慮 live mode。

## 目錄

- [環境需求](#環境需求)
- [安裝與啟動方式](#安裝與啟動方式)
- [設定檔](#設定檔)
- [IBKR 前置檢查](#ibkr-前置檢查)
- [CLI 操作](#cli-操作)
- [War Room 戰情室](#war-room-戰情室)
- [Runtime 與 audit 檔案](#runtime-與-audit-檔案)
- [測試](#測試)
- [Live mode 注意事項](#live-mode-注意事項)
- [常見問題排查](#常見問題排查)

## 環境需求

- Windows PowerShell
- Python `3.12+`
- IBKR TWS 或 IB Gateway
- 已啟用 IBKR API access
- Paper 或 live account 的 US options market data 權限
- 如果使用 AI 決策，需確保設定的 AI provider 可在本機執行

先確認本機 Python：

```powershell
py --list
```

專案目前驗證主要使用：

```powershell
py -3.12
```

## 安裝與啟動方式

從 repo root 執行：

```powershell
cd D:\Codes\trader-shawn
```

本專案建議使用 repo 外部的 venv，避免污染全域 Python installation。不要在 repo 或 worktree 內建立 `.venv`。

```powershell
py -3.12 -m venv C:\Users\Shawn\.venvs\trader-shawn
C:\Users\Shawn\.venvs\trader-shawn\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .[dev]
```

確認目前使用的是 venv Python：

```powershell
python -c "import sys; print(sys.executable); print(sys.prefix != sys.base_prefix)"
```

預期會看到：

```text
C:\Users\Shawn\.venvs\trader-shawn\Scripts\python.exe
True
```

安裝後可以用 module 方式執行：

```powershell
python -m trader_shawn.app --help
```

也可以使用 editable install 提供的 console script：

```powershell
trader-shawn --help
```

注意：目前 runtime 會從目前工作目錄尋找 `config/`。如果你在 repo root 以外的路徑執行，可能會找不到設定檔。日常操作建議固定從 `D:\Codes\trader-shawn` 執行。

如果 PowerShell 不允許執行 `Activate.ps1`，可以不啟用 venv，直接用完整 Python 路徑：

```powershell
C:\Users\Shawn\.venvs\trader-shawn\Scripts\python.exe -m trader_shawn.app --help
```

## 設定檔

設定檔位於 `config/`。

### `config/app.yaml`

```yaml
mode: paper
live_enabled: false
market_data_type: delayed
ibkr:
  host: 127.0.0.1
  port: 4002
  client_id: 7
scan_inputs:
  min_dte: 5
  max_dte: 35
  strike_window_pct: 0.25
  fallback_strike_count: 12
  max_expiries: 3
audit_db_path: runtime/audit.db
```

重點：

- `mode`: `paper` 或 `live`
- `live_enabled`: live mode 的第二道保險，`mode: live` 時必須是 `true`
- `market_data_type`: `paper` 可用 `delayed` 降低 IBKR 10197 即時行情 session 衝突；`live` mode 只能使用 `live`
- `ibkr.host`: 通常是 `127.0.0.1`
- `ibkr.port`: 需符合 TWS/IB Gateway 的 API socket port
- `ibkr.client_id`: market data client ID，execution client 會使用 `client_id + 1`
- `scan_inputs`: IBKR option quote 掃描範圍，例如 DTE、strike window、fallback strike 數量、expiry 數量
- `audit_db_path`: audit SQLite DB 位置，若是相對路徑，會以 repo root 為基準

### `config/symbols.yaml`

目前掃描 universe：

```yaml
symbols:
  - SPY
  - QQQ
  - GOOG
  - AMD
  - NVDA
```

### `config/risk.yaml`

風控限制在這裡：

```yaml
max_risk_per_trade_pct: 0.02
max_daily_loss_pct: 0.04
max_new_positions_per_day: 6
max_open_risk_pct: 0.20
max_spreads_per_symbol: 2
profit_take_pct: 0.50
stop_loss_multiple: 2.0
exit_dte_threshold: 5
```

這些限制會在交易流程中作為 hard guard。AI 決策不能繞過 risk guard。

### `config/providers.yaml`

```yaml
provider_mode: claude_primary
primary_provider: claude_cli
secondary_provider: codex
provider_timeout_seconds: 15
secondary_timeout_seconds: 10
```

如果 provider CLI 沒有安裝、沒有登入、或 timeout，交易決策會失敗或被拒絕。第一次送單前請先用 `decide` 驗證 AI 決策流程。

### `config/events.yaml`

```yaml
events: []
```

此檔案保留給事件或財報日等外部限制。若日後加入 earnings/event block，應從這裡維護。

### 環境變數覆蓋

以下環境變數會覆蓋 `config/app.yaml`：

```powershell
$env:TRADER_SHAWN_MODE='paper'
$env:TRADER_SHAWN_LIVE_ENABLED='false'
$env:TRADER_SHAWN_MARKET_DATA_TYPE='delayed'
$env:TRADER_SHAWN_IBKR_HOST='127.0.0.1'
$env:TRADER_SHAWN_IBKR_PORT='4002'
$env:TRADER_SHAWN_IBKR_CLIENT_ID='7'
```

布林值可用：`1`, `true`, `yes`, `on`, `0`, `false`, `no`, `off`。

## IBKR 前置檢查

執行任何 broker 相關命令前，先確認：

- TWS 或 IB Gateway 已啟動並登入正確 account
- API access 已啟用
- Socket port 和 `config/app.yaml` 一致
- Paper account 通常使用 paper API port，例如 `4002`
- Live account port 依你的 TWS/IB Gateway 設定為準
- 本機防火牆沒有擋 `127.0.0.1:<port>`
- client ID 沒有被其他程式佔用

本專案會用兩個相鄰 client ID：

- market data / account / position path: `client_id`
- order execution path: `client_id + 1`

如果同時開 War Room 和另外的 CLI 命令，仍可能和相同 client ID 競爭。盤中操作建議以 War Room 為主控台，避免多個程序同時打 IBKR。

`scan`, `decide`, `trade`, `manage`, `trade-cycle` 這類單次 CLI 命令結束時會主動呼叫 IBKR `disconnect()`。War Room 則是長駐服務，會維持自己的連線直到程序停止。

## CLI 操作

以下範例假設你在 repo root：

```powershell
cd D:\Codes\trader-shawn
C:\Users\Shawn\.venvs\trader-shawn\Scripts\Activate.ps1
```

### 查看可用指令

```powershell
python -m trader_shawn.app --help
```

### `scan`

掃描 symbol universe，建立候選 credit spreads。會連 IBKR 抓 option quote，但不做 AI 決策，也不送單。

```powershell
python -m trader_shawn.app scan
```

適合用來確認：

- IBKR market data 是否連得上
- options quote 是否可讀
- symbols / expiry / spread builder 是否正常

補充：

- `scan` 一律回傳正式候選欄位：`candidate_count` / `candidates`
- `paper` mode 下，如果資料不足以通過正式交易門檻，`scan` 會額外回傳 `watchlist_count` / `watchlist`
- `watchlist` 是觀察名單，不會被 `decide` 或 `trade` 當成正式可交易候選
- `watchlist.flags` 會指出常見問題，例如 `missing_delta`、`low_open_interest`、`low_volume`、`missing_market_prices`

### `decide`

執行 scan，然後呼叫 AI provider 做交易決策。不送單。

```powershell
python -m trader_shawn.app decide
```

適合用來確認：

- AI provider 是否可用
- 決策 JSON 是否可解析
- 候選交易是否能進入 decision flow

### `trade`

執行完整開倉流程。若 AI approve 且 risk guard 允許，會透過 IBKR submit combo order。

```powershell
python -m trader_shawn.app trade
```

注意：

- `paper` mode 仍會送到 paper account
- `live` mode 會送到 live account，必須先確認 `mode: live` 且 `live_enabled: true`
- order submission 前會經過 risk guard
- 不要在不了解當前設定時執行此命令

### `manage`

管理現有 open positions，例如評估 profit take、stop loss、DTE exit，必要時 submit closing order。

```powershell
python -m trader_shawn.app manage
```

適合盤中或收盤前固定執行。它會讀 audit DB 中的 managed positions，並使用 IBKR market data / execution path 評估與處理部位。

### `trade-cycle`

執行 entry workflow 並更新 dashboard snapshot。它是完整交易循環入口之一，具備送單能力。

```powershell
python -m trader_shawn.app trade-cycle
```

日常使用建議優先使用較明確的 `scan`, `decide`, `trade`, `manage`。除非你確定要跑完整 entry cycle，否則不要隨手執行。

### `dashboard`

讀取 runtime dashboard snapshot：

```powershell
python -m trader_shawn.app dashboard runtime/dashboard.json
```

這是 JSON 快照檢視，不是完整 UI。完整監控請用 War Room。

### `collect-quotes`

收集 option quote snapshots，存進 `runtime/audit.db`，之後可作為 replay scan / backtest 的資料基礎。這個命令不做 AI 決策，也不送單。

先跑一次可以確認 IBKR 與設定正常：

```powershell
python -m trader_shawn.app collect-quotes --once
```

注意：`--once` 在非交易時段通常只適合做連線檢查，不代表能收集到有回測價值的 option data。要建立可 replay 的資料，應該在美股 options 有報價的盤中時段長駐收集。

確認成功後，可以用 interval 在盤中長駐收集。例如每 5 分鐘收一次：

```powershell
python -m trader_shawn.app collect-quotes --interval 300
```

重點：

- 會使用 `config/symbols.yaml` 的 symbol universe
- 會套用 `config/app.yaml` 的 `scan_inputs`
- 會把每個 symbol 的 quote batch 寫入 SQLite
- 單一 symbol 失敗時不會中斷整批，結果會回傳 `partial`
- 目前只是收集資料，War Room 尚未接上 stored snapshot replay

時段判斷：

- 最有價值：美股 options 盤中，bid/ask、delta、volume、open interest 較可能可用
- 盤前：通常只能拿來測 IBKR 連線，資料可能缺 bid/ask 或 greeks
- 收盤後：snapshot 可能 stale、bid/ask 失真或空掉
- 台灣時間約為夏令 21:30-04:00、冬令 22:30-05:00

## War Room 戰情室

War Room 是本機戰情室 UI，用來集中查看：

- IBKR broker health
- dashboard last cycle
- account / risk deck
- managed positions
- hot positions
- threat rail
- mission log
- armed controls

### 啟動

```powershell
cd D:\Codes\trader-shawn
C:\Users\Shawn\.venvs\trader-shawn\Scripts\Activate.ps1
python -m trader_shawn.app war-room --host 127.0.0.1 --port 8787
```

打開：

```text
http://127.0.0.1:8787/war-room
```

`war-room` 只允許綁定 loopback host：

- `127.0.0.1`
- `localhost`

以下會被拒絕：

```powershell
python -m trader_shawn.app war-room --host 0.0.0.0
```

原因：目前 `ARM` 只是本機操作 gate，不是完整身份驗證。不要把 War Room 暴露到區網或網際網路。

### Monitoring mode

War Room 預設是 monitoring mode。此模式下：

- 可以看 broker / risk / position / threat 狀態
- command buttons 會被鎖住
- 不會因為打開頁面就送交易指令

### Armed mode

輸入：

```text
ARM
```

解鎖目前 browser session 的操作控制。

Armed mode 下：

- `scan`, `decide`, `manage` 可直接執行
- `trade` 仍需要第二次確認
- session cookie 遺失或過期時會自動回到 monitoring mode

### Trade confirmation

即使已進入 Armed mode，`trade` 仍會跳出確認流程。這是避免誤觸的最後一道 UI gate。

### War Room 資料來源

War Room 後端會整理：

- `runtime/dashboard.json`
- `runtime/audit.db`
- IBKR broker health probe
- active managed positions
- recent position events

如果 IBKR 連不上，War Room 仍會開啟，但 broker 狀態會降級，並顯示 degraded snapshot。此時看到的部位資料可能是 last known local state，不應當成即時 broker truth。

## Runtime 與 audit 檔案

常見 runtime 檔案：

```text
runtime/dashboard.json
runtime/audit.db
```

用途：

- `dashboard.json`: 最近一次 cycle / command 狀態摘要
- `audit.db`: managed positions、position events、order uncertainty、quote snapshots 等 audit/runtime record

不要手動刪除 `audit.db`，除非你確定要重置本地 managed position 記錄。刪除 audit DB 會讓系統失去對既有部位的本地追蹤脈絡。

`collect-quotes` 會在 `audit.db` 建立並寫入：

- `quote_snapshots`: 每次 symbol quote batch 的時間、market data type、scan inputs、quote count
- `option_quotes`: 每個 option contract 的 expiry、strike、right、bid、ask、delta、last、mark、volume、open interest

快速檢查最近收集結果：

```powershell
sqlite3 runtime/audit.db "select symbol, collected_at, quote_count from quote_snapshots order by id desc limit 10;"
```

## 測試

先確認 Python：

```powershell
py --list
```

以下命令假設已經啟用 venv：

```powershell
cd D:\Codes\trader-shawn
C:\Users\Shawn\.venvs\trader-shawn\Scripts\Activate.ps1
```

跑完整測試：

```powershell
python -m pytest -q
```

常用 targeted tests：

```powershell
python -m pytest tests\unit\test_settings.py -q
python -m pytest tests\integration\test_cli_commands.py -q
python -m pytest tests\integration\test_war_room_api.py tests\integration\test_war_room_ui.py -q
```

如果 Playwright Chromium 不存在，UI browser test 可能會 skip 或提示需要安裝瀏覽器。這不影響一般 CLI 測試，但會影響完整 UI 驗證。

## Live mode 注意事項

Live mode 必須同時設定：

```yaml
mode: live
live_enabled: true
```

或使用環境變數：

```powershell
$env:TRADER_SHAWN_MODE='live'
$env:TRADER_SHAWN_LIVE_ENABLED='true'
```

操作原則：

- 先用 `paper` 跑完整流程
- 先用 `scan` 驗證 market data
- 再用 `decide` 驗證 AI provider
- 再用 `manage` 驗證持倉管理不會誤判
- 最後才考慮 `trade`
- 每次 live 前確認 IBKR account、port、client ID、symbol universe、risk limits

特別注意：

- `trade`, `trade-cycle`, `manage` 都可能觸發 IBKR order submission
- `manage` 可能送 closing order
- AI provider 的輸出不是最終授權，risk guard 才是硬限制
- War Room 的 `ARM` 不是安全認證，只是本機操作 gate

## 常見問題排查

### `ConnectionRefusedError`

通常代表 TWS/IB Gateway 沒開、API port 不對、或 API access 未啟用。

檢查：

```powershell
Get-NetTCPConnection -LocalPort 4002 -ErrorAction SilentlyContinue
```

並確認 `config/app.yaml` 的 `ibkr.port`。

### `config_load_failed`

通常代表目前工作目錄下找不到 `config/`，或 YAML 格式/欄位驗證失敗。

處理：

```powershell
cd D:\Codes\trader-shawn
C:\Users\Shawn\.venvs\trader-shawn\Scripts\Activate.ps1
python -m trader_shawn.app scan
```

### AI decision timeout 或 invalid output

檢查：

- `config/providers.yaml`
- provider CLI 是否可執行
- provider 是否已登入
- timeout 是否太短

先跑：

```powershell
python -m trader_shawn.app decide
```

### War Room 顯示 degraded

可能原因：

- IBKR broker health probe 失敗
- runtime config 建立失敗
- `runtime/dashboard.json` 不存在或尚未更新
- `runtime/audit.db` 沒有 active positions

先跑：

```powershell
python -m trader_shawn.app manage
python -m trader_shawn.app dashboard runtime/dashboard.json
```

### 指令卡住或很慢

可能原因：

- IBKR market data response 慢
- options chain 太大
- AI provider timeout
- TWS/IB Gateway 連線不穩

先用 `scan` 和 `decide` 分開定位，不要直接跑 `trade`。

### IBKR `Error 10197` 或 underlying spot price 取不到

這通常是 IBKR 即時行情 session 衝突，例如同時開了 TWS、手機、Client Portal 或其他 API session。`paper` mode 預設使用：

```yaml
market_data_type: delayed
```

處理順序：

- 先關掉其他會吃即時行情的 session，再重新跑 `scan`
- paper 測試可維持 `market_data_type: delayed`
- live mode 不能使用 delayed；設定成 live 時，系統會拒絕 `market_data_type: delayed`
- 單次 CLI command 跑完會主動斷線；War Room 是長駐服務，需要停止 War Room 程序才會釋放連線

## 日常建議流程

以下命令假設已經在 repo root 並啟用 venv：

```powershell
cd D:\Codes\trader-shawn
C:\Users\Shawn\.venvs\trader-shawn\Scripts\Activate.ps1
```

盤前：

```powershell
python -m pytest -q
python -m trader_shawn.app scan
python -m trader_shawn.app decide
```

盤中資料收集：

```powershell
python -m trader_shawn.app collect-quotes --interval 300
```

`collect-quotes --interval 300` 建議在美股 options 盤中長駐執行。盤前若要跑 `collect-quotes --once`，用途是檢查 IBKR 連線與設定，不是建立有效回測資料。

盤中監控：

```powershell
python -m trader_shawn.app war-room --host 127.0.0.1 --port 8787
```

持倉管理：

```powershell
python -m trader_shawn.app manage
```

需要進場時：

```powershell
python -m trader_shawn.app trade
```

或在 War Room 中輸入 `ARM` 後使用控制區，`trade` 仍需二次確認。
