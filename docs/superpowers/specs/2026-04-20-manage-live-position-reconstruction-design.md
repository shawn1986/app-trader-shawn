# Manage Live Position Reconstruction Design

Date: 2026-04-20  
Workspace: `D:\Codes\trader-shawn`  
Status: Draft for review

## 1. Summary

This subproject adds real `manage` support for live positions, but only for spreads opened by this system and persisted locally at entry time. The system will not attempt general broker-first reconstruction of arbitrary IBKR option positions.

`manage` will be strict and state-driven:

- local SQLite state decides which positions are system-owned and eligible for automated management
- IBKR validates whether those positions still exist and provides current pricing
- any unknown or inconsistent broker option position causes the whole manage cycle to fail closed

The first version will support automatic exit evaluation and close-order submission for:

- `take_profit`
- `stop_loss`
- `dte_exit`
- `short_strike_proximity`
- `event_risk_exit`

## 2. Goals

- Reconstruct open system-owned credit spreads from local state plus live broker verification
- Detect and reject unknown or inconsistent broker option positions
- Rebuild `PositionSnapshot` instances with enough information to run exit rules
- Submit close combo orders for qualifying exits
- Persist state transitions and lifecycle events in SQLite
- Update dashboard state after each manage cycle

## 3. Non-Goals

- No management of hand-entered or externally opened positions
- No broker-first spread pairing for arbitrary option legs
- No partial anomaly tolerance in version one
- No auto-roll logic
- No automatic adoption of orphaned broker positions
- No multi-account reconciliation logic in version one

## 4. Ownership Model

Only positions that satisfy all of the following are considered manageable:

- opened by this system
- successfully persisted to local SQLite state
- still present on IBKR in a form that uniquely matches the saved position identity

If IBKR reports option positions that do not map to saved system-owned records, the manage cycle stops immediately and no close order is submitted.

## 5. Data Storage

SQLite becomes the authoritative data source for managed open spreads. JSON state remains only for dashboard and transient run output.

### 5.1 `managed_positions`

This table stores currently system-owned spreads and their latest known state.

Minimum columns:

- `position_id`
- `ticker`
- `strategy`
- `expiry`
- `short_strike`
- `long_strike`
- `quantity`
- `entry_credit`
- `entry_order_id`
- `mode`
- `status`
- `opened_at`
- `closed_at`
- `last_known_debit`
- `last_evaluated_at`
- `broker_fingerprint`
- `decision_reason`
- `risk_note`

### 5.2 `position_events`

This table stores lifecycle history for each managed position.

Minimum columns:

- `id`
- `position_id`
- `event_type`
- `payload_json`
- `created_at`

Event examples:

- `opened`
- `reconcile_failed`
- `exit_triggered`
- `close_submitted`
- `closed`
- `anomaly_detected`

## 6. Broker Reconciliation

Each manage cycle performs this strict sequence:

1. Load all `managed_positions` with open-like states such as `open` or `closing`
2. Query IBKR for current option positions
3. Match each saved position against live broker legs using the saved spread identity
4. Reject the cycle if any live broker option position is unknown to local state
5. Reject the cycle if any saved position cannot be uniquely matched to broker state
6. For each matched position, fetch live pricing needed to estimate current debit
7. Rebuild a `PositionSnapshot` and run exit evaluation
8. Submit close combo orders for triggered exits
9. Persist state/event updates and dashboard output

### 6.1 Matching Rules

The first version will match using strict spread identity, not fuzzy inference.

Required matching attributes:

- `ticker`
- `strategy`
- `expiry`
- `short_strike`
- `long_strike`
- `quantity`
- `broker_fingerprint`

If the match is not unique, the position is treated as anomalous and the whole cycle fails closed.

## 7. Fail-Closed Conditions

The entire manage cycle must stop without submitting any close order when any of these occur:

- IBKR connection failure
- SQLite load failure or corrupted local state
- broker option positions exist with no corresponding `managed_positions` record
- a saved open position cannot be found on the broker
- one saved position maps to multiple possible broker positions
- reconstructed position data is missing required exit fields
- live pricing is insufficient to estimate `current_debit`
- close order construction fails
- close submission fails for a position after anomaly detection has already invalidated the cycle

Version one chooses account safety over partial progress. It will not continue managing the "good" positions after seeing a bad one.

## 8. Runtime Model

### 8.1 Position Reconstruction

For each matched position, the system rebuilds a `PositionSnapshot` containing at least:

- `ticker`
- `strategy`
- `expiry`
- `short_strike`
- `long_strike`
- `quantity`
- `entry_credit`
- `current_debit`
- `dte`
- `short_leg_distance_pct`
- `side`

`entry_credit` comes from SQLite state, not from broker reconstruction.

`current_debit` comes from live market data for the same spread.

### 8.2 Exit Evaluation

The existing exit rules remain the source of truth:

- profit target based on entry credit
- stop loss based on entry credit multiple
- DTE threshold exit
- short-strike proximity exit
- event-risk exit

When an exit rule triggers, `manage` will create a close combo order and send it through the IBKR executor.

## 9. Module Changes

### 9.1 `monitoring/audit_logger.py`

Extend the SQLite schema and add helpers for:

- inserting and updating `managed_positions`
- appending `position_events`
- querying open managed positions for the manage cycle

### 9.2 `monitoring/state_store.py`

No new durable authority is added here. JSON remains for dashboard/transient run state only.

### 9.3 `execution/ibkr_executor.py`

Ensure the close-order path returns enough broker identifiers and submission details to persist manageable position identity and exit events.

### 9.4 `market_data/ibkr_market_data.py`

Add the minimum live helpers needed for manage:

- list current option positions
- compute or fetch spread debit for a known spread
- provide data required for `dte` and strike-distance evaluation

### 9.5 `positions/manager.py`

Expand from pure rule evaluation to workflow orchestration helpers:

- load manageable positions from SQLite
- reconcile local positions with broker positions
- rebuild `PositionSnapshot`
- invoke `evaluate_exit`
- return close intents and anomaly results

### 9.6 `app.py`

Replace the current fail-closed placeholder `manage` runtime with a real state-driven manage workflow.

## 10. Testing Strategy

### 10.1 Unit Tests

- SQLite schema and CRUD tests for `managed_positions` and `position_events`
- reconciliation success and failure cases
- unknown broker position detection
- missing-state and multi-match anomaly cases
- close-order submission payload generation

### 10.2 Integration Tests

- `manage` command with fake runtime and broker data
- full cycle: open managed position -> trigger exit -> submit close -> persist updates
- dashboard updates for successful manage runs and anomaly-driven failures

### 10.3 Manual Validation

Run against IBKR paper trading before any live use:

- verify a system-opened spread is persisted into SQLite
- verify `manage` sees and matches the live paper position
- verify profit/stop/DTE paths generate the expected close orders
- verify an injected unknown broker option position stops the whole cycle

## 11. Success Criteria

This subproject is complete when:

- system-opened spreads are persisted in SQLite as manageable positions
- `manage` reconstructs only system-owned positions
- unknown broker option positions cause a full fail-closed stop
- exit rules run against reconstructed live snapshots
- close combo orders are submitted for triggered exits
- SQLite and dashboard state reflect the resulting lifecycle events

## 12. Risks

- If entry-time persistence is incomplete, otherwise valid system positions may become unmanaged
- Broker-side leg representation may require small normalization logic for reliable matching
- Strict fail-closed behavior may halt automation frequently during early rollout, but this is intentional for version one
