# IBKR Credit Spread Autotrader Design

Date: 2026-04-20
Workspace: `D:\Codes\trader-shawn`
Status: Draft for review

## 1. Summary

This project will build a first-version automated options trading system for Interactive Brokers (IBKR) focused on short-duration credit spreads on a fixed symbol universe: `SPY`, `QQQ`, `GOOG`, `AMD`, and `NVDA`.

The system will support both `paper` and `live` modes within the same codebase. It will default to `paper`, and `live` mode will require explicit configuration. The decision layer will use an agent-adapter design with `Claude CLI` as the default primary decision provider and `Codex` as a secondary opinion provider. The AI layer may choose `strike`, `DTE`, and `width` within the allowed credit spread framework, but it will never bypass hard risk rules.

The architecture will be a modular monolith. It will be optimized for safety, auditability, and maintainability rather than maximum throughput.

## 2. Goals

- Automatically scan the approved universe for short-duration credit spread opportunities.
- Generate structured candidate spreads based on deterministic pre-filters.
- Use AI to make a constrained trade decision within the credit spread strategy family.
- Enforce hard risk controls before any order is sent.
- Submit combo orders to IBKR in both `paper` and `live` modes.
- Manage open positions with automated take-profit, stop-loss, DTE exit, and event-risk exit logic.
- Persist complete audit trails for data inputs, AI prompts, AI outputs, risk decisions, and order lifecycle events.

## 3. Non-Goals

- No support for arbitrary symbols outside the fixed universe.
- No support for strategy families beyond `bull put credit spreads` and `bear call credit spreads`.
- No auto-roll logic in version one. The system will prioritize closing positions instead.
- No discretionary news interpretation, social sentiment, or NLP-driven macro strategy.
- No mobile app in version one.
- No distributed microservice deployment in version one.
- No high-frequency trading or sub-second decision loops.

## 4. Trading Scope

### 4.1 Allowed Universe

- `SPY`
- `QQQ`
- `GOOG`
- `AMD`
- `NVDA`

### 4.2 Allowed Structures

- `bull_put_credit_spread`
- `bear_call_credit_spread`

### 4.3 Strategy Window

- Preferred expiration window: `7-21 DTE`
- Candidate construction will favor liquid options with usable strike granularity and narrow enough spreads.
- The AI layer may select the final `strike`, `DTE`, and `width` only inside the approved candidate space.

## 5. Operational Assumptions

- Broker: `IBKR`
- Connection target: `IB Gateway` or `TWS`
- Initial deployment environment: local Windows machine
- Future migration path: VPS-hosted deployment
- IBKR account has live market data and Greeks for US equity options
- Runtime mode options: `paper` and `live`
- Default runtime mode: `paper`
- `live` mode requires explicit configuration at startup

## 6. High-Level Architecture

The system will be implemented as a modular monolith with seven main modules:

1. `market_data`
2. `candidate_builder`
3. `ai_decision`
4. `risk_guard`
5. `execution`
6. `position_manager`
7. `audit_monitoring`

This design keeps deployment simple while preserving strict internal boundaries between market data collection, AI decisioning, risk validation, broker execution, and post-trade management.

## 7. Module Design

### 7.1 `market_data`

Responsibilities:

- Connect to IBKR market data endpoints
- Fetch underlying spot prices
- Fetch option chains and Greeks
- Normalize contract and quote data into internal models
- Provide data snapshots for downstream candidate building

Constraints:

- Must fail closed if required quote fields are missing
- Must expose snapshot timestamps so stale data can be rejected

### 7.2 `candidate_builder`

Responsibilities:

- Construct a candidate pool of allowed credit spreads before AI review
- Apply deterministic eligibility checks such as:
  - symbol whitelist
  - DTE bounds
  - minimum liquidity and OI
  - acceptable bid/ask width
  - account risk bounds
  - event exclusions

Output:

- A bounded list of structured candidate spreads with metrics such as:
  - spread type
  - expiration
  - short strike
  - long strike
  - width
  - credit
  - max loss
  - delta
  - estimated POP
  - liquidity metrics

### 7.3 `ai_decision`

Responsibilities:

- Convert the candidate pool and market context into a provider-neutral decision request
- Send the primary decision request to `Claude CLI`
- Send the secondary opinion request to `Codex`
- Parse and validate the provider responses
- Produce a single internal decision object

Provider model:

- Default primary provider: `Claude CLI`
- Secondary opinion provider: `Codex`
- Provider mode must be configurable so the operator can later switch to:
  - `claude_primary`
  - `codex_primary`
  - `single_provider`
  - `manual_switch`

Allowed AI scope:

- The AI may choose among approved credit spread candidates
- The AI may determine final `strike`, `DTE`, and `width` within the constrained candidate space
- The AI may reject trading for a cycle

Disallowed AI behavior:

- Creating naked options positions
- Creating option structures outside approved credit spreads
- Bypassing risk validation
- Sending broker orders directly

### 7.4 `risk_guard`

Responsibilities:

- Evaluate every proposed trade against hard risk rules
- Enforce account-level and trade-level limits
- Reject trades that violate safety conditions

This module has final authority over whether a trade can proceed. If `risk_guard` rejects a trade, no order is sent regardless of AI output.

### 7.5 `execution`

Responsibilities:

- Build IBKR combo contracts for approved spreads
- Submit limit orders
- Track order status and fills
- Cancel or reprice within configured execution rules
- Submit closing orders for exits triggered by the position manager

Constraints:

- Limit orders only in version one
- No market orders
- Repricing must stay within configured slippage bounds

### 7.6 `position_manager`

Responsibilities:

- Monitor open positions
- Trigger exits based on:
  - profit target
  - stop-loss
  - DTE threshold
  - short-strike proximity
  - scheduled event-risk exit

Version one policy:

- Prefer closing over rolling
- Auto-roll is explicitly out of scope

### 7.7 `audit_monitoring`

Responsibilities:

- Persist market snapshots used for each decision
- Persist prompts sent to AI providers
- Persist structured responses from AI providers
- Persist risk approvals and rejections
- Persist order lifecycle events
- Persist position lifecycle events

Auditability is a first-class requirement. Every live or paper trade decision must be replayable after the fact.

## 8. AI Decision Interface

### 8.1 Decision Request Inputs

The AI layer will receive a structured summary, not the full raw option chain dump.

Inputs include:

- `ticker`
- `spot`
- trend snapshot
- candidate spreads
- `expiry`
- `dte`
- `credit`
- `width`
- `max_loss`
- `delta`
- estimated `pop`
- liquidity metrics
- event context
- current account mode
- current account exposure
- current day PnL state
- remaining risk budget
- system constraints

### 8.2 Decision Response Contract

The primary and secondary providers must return strict JSON.

Approved trade response example:

```json
{
  "action": "approve",
  "ticker": "AMD",
  "strategy": "bull_put_credit_spread",
  "expiry": "2026-04-30",
  "short_strike": 160,
  "long_strike": 155,
  "limit_credit": 1.05,
  "confidence": 0.72,
  "reason": "IV elevated, bullish bias, acceptable spread liquidity",
  "risk_note": "Skip if fill worse than 0.95"
}
```

Rejected trade response example:

```json
{
  "action": "reject",
  "reason": "No candidate passes liquidity and event-risk threshold"
}
```

Invalid responses are treated as provider failure.

### 8.3 Provider Failure Handling

- If `Claude CLI` times out, errors, or returns invalid JSON, the cycle rejects the trade.
- If `Codex` fails, the cycle can continue without a second opinion.
- If the secondary provider disagrees with the primary provider, the disagreement is logged but does not override the primary decision in version one.
- If provider latency exceeds the configured threshold for the trade cycle, the cycle rejects the trade rather than chasing price.

## 9. Hard Risk Rules

Version one `live` defaults:

- Max risk per trade: `2%` of account net liquidation value
- Max daily loss: `4%`
- Max new positions per day: `6`
- Max aggregate open risk: `20%`
- Max simultaneous spreads per symbol: `2`

Additional hard blocks:

- No new trades when daily loss limit is breached
- No new trades when aggregate open risk exceeds limit
- No new trades when broker connectivity is unhealthy
- No new trades when market data is incomplete or stale
- No new trades when AI decision output is invalid
- No new trades when configuration mode is inconsistent
- No duplicate trades for the same setup in the same cycle

The exact symbol-correlation rule will be implemented as a concentration check so the system can prevent excessive directional clustering across the allowed universe.

## 10. Order Lifecycle

### 10.1 Entry Flow

1. Scheduler triggers a scan cycle.
2. `market_data` fetches required snapshots.
3. `candidate_builder` constructs a valid candidate pool.
4. `Claude CLI` returns the primary decision.
5. `Codex` returns the secondary opinion.
6. `risk_guard` validates the proposed trade.
7. `execution` submits the combo limit order.
8. Fills and order state transitions are recorded in the audit store.

### 10.2 Fill Policy

- Limit orders only
- Initial price anchored around the mid price or a safer configured price
- Limited repricing attempts allowed
- Trade is canceled if the required fill would exceed configured slippage

## 11. Position Exit Rules

Version one automated exits:

- Take profit at `50%` of collected credit
- Stop loss at `200%` of original credit
- Exit when `DTE <= 5`
- Exit early when spot approaches the short strike according to configured distance thresholds
- Exit before major known events for affected symbols

Version one will not automatically roll positions. If risk rises, the default action is to reduce or close exposure.

## 12. Modes and Safety Controls

Shared behavior:

- `paper` and `live` use the same core strategy and risk logic

Safety controls:

- Startup mode defaults to `paper`
- `live` requires explicit operator configuration
- The active mode is logged at startup and shown in monitoring output
- `live` mode may require an additional environment-based enable flag for protection against accidental activation

## 13. Project Layout

Planned repository structure:

```text
trader-shawn/
  pyproject.toml
  README.md
  .env.example
  config/
    app.yaml
    symbols.yaml
    risk.yaml
    providers.yaml
  src/trader_shawn/
    app.py
    scheduler.py
    domain/
      models.py
      enums.py
    market_data/
      ibkr_market_data.py
    candidate_builder/
      credit_spread_builder.py
    ai/
      base.py
      claude_cli_adapter.py
      codex_adapter.py
      decision_parser.py
    risk/
      guard.py
      rules.py
    execution/
      ibkr_executor.py
      order_builder.py
    positions/
      manager.py
    monitoring/
      audit_logger.py
      state_store.py
      dashboard_api.py
    events/
      earnings_calendar.py
  tests/
    unit/
    integration/
    paper/
  docs/
    superpowers/
      specs/
```

## 14. Configuration Model

Version one configuration will use `YAML` files plus environment variables.

### 14.1 `config/symbols.yaml`

- Fixed trading universe
- Per-symbol enable or disable flags if needed

### 14.2 `config/risk.yaml`

- Trade risk limits
- Daily loss limit
- Aggregate exposure limit
- Entry and exit thresholds

### 14.3 `config/providers.yaml`

- Primary provider
- Secondary provider
- Timeout settings
- Retry behavior
- Provider mode

### 14.4 `.env`

- IBKR connection settings
- Runtime mode
- Provider credentials or local command settings
- Safety enable flags

## 15. Command Entrypoints

Version one will expose distinct execution modes:

- `scan`: build and print candidate opportunities only
- `decide`: run candidate generation and AI decisioning without broker order submission
- `trade`: run the full trade loop with order submission enabled
- `manage`: manage existing open positions only

This separation reduces operational ambiguity and makes validation easier before live deployment.

## 16. Testing Strategy

### 16.1 Unit Tests

- Risk rule evaluation
- Decision parsing
- Order construction
- Position-exit logic

### 16.2 Integration Tests

- IBKR paper connection
- Market-data normalization
- AI adapter execution and JSON parsing
- End-to-end cycle without live order routing

### 16.3 Paper Validation

Before any live deployment, the system should be exercised in paper mode long enough to verify:

- stable scheduler execution
- reliable market data snapshots
- valid candidate generation
- valid AI decision payloads
- risk rejections behave as expected
- orders are placed correctly
- exits trigger correctly
- audit logs are complete

## 17. Success Criteria for Version One

The first version is considered complete when it can:

- Run against `paper` and `live` modes from one codebase
- Scan `SPY`, `QQQ`, `GOOG`, `AMD`, and `NVDA`
- Build valid short-duration credit spread candidates
- Use `Claude CLI` as the primary decision layer
- Use `Codex` as the secondary opinion layer
- Enforce hard risk rules before order submission
- Submit IBKR combo limit orders
- Manage exits automatically
- Produce an audit trail sufficient for replay and diagnosis

## 18. Deferred Work

Not part of version one but intentionally left open for later:

- VPS deployment packaging
- Auto-roll logic
- Rich dashboard or web UI
- More advanced event ingestion
- More complex concentration modeling
- Alternate broker adapters
- Additional AI providers and routing policies

## 19. Design Rationale

This design chooses constrained automation over unconstrained autonomy.

The strategy engine is intentionally narrow. The AI layer has room to decide inside a bounded strategy family, but the system remains deterministic where safety matters most: symbol universe, strategy type, order type, and hard risk enforcement. This balances flexibility with replayability and protects the live account from prompt-level drift.
