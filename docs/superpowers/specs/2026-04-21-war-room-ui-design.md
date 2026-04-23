# War Room UI Design

Date: 2026-04-21
Workspace: `D:\Codes\trader-shawn`
Status: Draft for review

## 1. Summary

This project adds a real operator-facing war room UI for `trader-shawn`.

The UI is not a generic dashboard. It is a low-density command surface with a military command-center feel. Its first job is to answer two questions immediately:

- can the system operate safely right now
- are current positions drifting into risk or anomaly conditions

The approved direction is:

- single-page command surface
- `Alpha / Central Command` layout
- `Threat Rail` right-side escalation column
- default `Monitoring Mode`
- session-local `Armed Mode` unlocked by typing `ARM`
- health and position risk first
- controls visible but subordinate until `Armed Mode`

The UI should feel severe, legible, and deliberate rather than decorative or crowded.

## 2. Goals

- Give the operator a single surface that summarizes broker health, current trading readiness, risk posture, hot positions, recent actions, and critical anomalies.
- Preserve the project's fail-closed posture by making degraded and stale states visually explicit.
- Allow `scan`, `decide`, `manage`, and `trade` to be triggered from the UI without bypassing the existing runtime and safety rules.
- Keep the first viewport focused on operational awareness rather than tables, admin tooling, or audit back-office detail.
- Create a clear separation between passive monitoring and active command execution.

## 3. Non-Goals

- No mobile-first design target in version one. The UI should remain usable on smaller screens, but desktop command use is the primary target.
- No full historical analytics suite.
- No replacement of the existing CLI internals. The UI should wrap and reuse existing command paths where possible.
- No one-click unrestricted execution surface.
- No attempt to show every persisted audit record in the first viewport.

## 4. Operators and Primary Use Cases

### 4.1 Operator

The primary operator is the local trader running `trader-shawn` on a Windows workstation connected to IBKR paper or live infrastructure.

### 4.2 Primary Use Cases

1. Open the war room and determine if the system is healthy enough to operate.
2. See whether existing positions need attention before looking at anything else.
3. Notice active anomalies and manual intervention requirements without reading logs.
4. Review recent automated or manual actions from a mission-log style feed.
5. Intentionally unlock an armed state and trigger supported commands.

## 5. Visual Direction

### 5.1 Visual Thesis

The UI should feel like a military command display adapted for professional trading: dark, restrained, cold, angular, and status-driven.

### 5.2 Style Rules

- Base palette: deep navy and near-black with cold cyan structure.
- Escalation colors: amber for warning, red for critical states, green only when it communicates operational readiness.
- Typography: bold condensed or technical display face for major state labels, paired with a highly legible body face.
- Surfaces: panel-based, but not card-mosaic based. The layout should read as one integrated console.
- Motion: restrained, purposeful, and mostly tied to state changes, reveal sequences, and mode shifts.

### 5.3 Density Rule

The UI should be low-density. The first viewport is a cockpit, not a spreadsheet.

Complete position lists, full audit tables, and detailed logs belong in lower sections or drill-down areas, not the first visual tier.

## 6. Information Architecture

The first screen contains six fixed modules with strict priority.

### 6.1 Command Status

Location: central top band

Purpose: immediate operating readiness

Required content:

- IBKR connectivity state
- `paper` or `live` mode
- API latency or most recent health timing
- last `manage` result
- last refresh time
- armed state

This module answers whether the system is presently usable.

### 6.2 Risk Deck

Location: central second band

Purpose: immediate portfolio pressure summary

Required content:

- open risk
- unrealized PnL
- new positions today
- active managed position count
- nearest expiry pressure

This module answers whether the book is drifting toward danger.

### 6.3 Hot Positions

Location: central lower primary area

Purpose: highlight the few positions most likely to require action

Required behavior:

- show only the highest-priority 3-5 positions by default
- rank by anomaly, loss pressure, expiry pressure, or stuck lifecycle state
- allow expansion into a full table or detail surface

The first screen must emphasize triage rather than completeness.

### 6.4 Threat Rail

Location: fixed right rail

Purpose: pressure, escalation, and priority ordering

Required content in order:

- threat level
- active anomalies
- manual intervention requirements
- recent failures
- armed gate state

The right rail is intentionally severe. It exists to make critical conditions impossible to miss.

### 6.5 Mission Log

Location: right rail lower-middle region

Purpose: show what the system just did

Required content:

- recent `scan`, `decide`, `trade`, and `manage` outcomes
- success, rejection, failure, and pending states
- terse reasons that can be understood without opening raw logs

### 6.6 Armed Controls

Location: lower-right control bay

Purpose: intentional operator execution

Default state:

- locked
- visible
- copy explains that typing `ARM` is required

Unlocked state:

- expose `scan`, `decide`, `manage`, and `trade`
- `trade` remains separately confirm-gated

## 7. Layout

### 7.1 Approved Layout

The approved layout is `Alpha / Central Command`.

Structure:

- full-width top command frame
- central primary theater occupying roughly 70 percent width
- fixed right-side `Threat Rail` occupying roughly 30 percent width
- central content stacked in operational order: health first, risk second, hot positions third

### 7.2 Rejected Layout Characteristics

The UI must not devolve into:

- symmetric dashboard-card grids
- analytics-homepage hero patterns
- dense monitoring walls in the first viewport
- right-side rails that behave like generic sidebars instead of escalation spines

## 8. Modes and Interaction

### 8.1 Monitoring Mode

This is the default state.

Behavior:

- all monitoring modules visible
- controls visible but not executable
- visual tone remains cold and disciplined
- system is treated as observable but not armed

### 8.2 Armed Mode

This is a session-local UI state, not a durable backend mode.

Unlock rule:

- operator must type `ARM`

Behavior after unlock:

- `scan`, `decide`, and `manage` become executable
- `trade` becomes available but still requires final confirmation
- visual treatment becomes more alert and dangerous without obscuring central monitoring information

Armed mode should reset on page reload, session expiry, or explicit return to monitoring mode.

### 8.3 Trade Confirmation

Even inside `Armed Mode`, `trade` must not fire from a single click.

The final confirmation surface must show:

- current runtime mode
- ticker
- strategy
- limit credit
- key risk check outcome
- explicit confirmation action

## 9. Data Model and Data Flow

### 9.1 Snapshot-First Design

The UI should load from a unified backend snapshot instead of assembling its own state from multiple frontend fetches.

The unified snapshot should combine:

- normalized dashboard state from `runtime/dashboard.json`
- active managed positions from `runtime/audit.db`
- recent position events from `runtime/audit.db`
- broker health probe results
- UI-oriented aggregates such as threat level, hot position ranking, and mission log entries

### 9.2 Polling Strategy

Recommended update cadence:

- `Command Status` and `Threat Rail`: every 5 seconds
- `Risk Deck`, `Hot Positions`, and `Mission Log`: every 10-15 seconds
- while a command is in flight: temporarily increase polling cadence until the state settles

### 9.3 Broker Contact Rule

The UI must not independently scatter broker requests across modules.

Broker health and broker-derived status should be collected server-side and exposed as a coherent snapshot. This avoids contradictory panel states and reduces broker noise.

### 9.4 Command Execution Rule

UI commands should call explicit backend command endpoints that reuse the existing runtime behavior for:

- `scan`
- `decide`
- `manage`
- `trade`

Results must flow back into:

- `Mission Log`
- `Threat Rail`
- `Command Status`
- `Hot Positions` when relevant

## 10. Failure and Degradation Behavior

### 10.1 Broker Down

If IBKR is unavailable:

- the war room still loads
- `Command Status` clearly marks the system as degraded
- `Threat Rail` escalates
- broker-dependent values are labeled as unavailable or stale
- position and dashboard data may still render from the last known local snapshot with visible timestamps

The UI must never present stale local data as live market truth.

### 10.2 Partial Snapshot Failure

If only some data sources fail:

- unaffected modules continue rendering
- failed modules show scoped degraded states
- the page does not collapse into a single generic error view

### 10.3 Command In Flight

When a command is running:

- related controls enter a busy state
- duplicate triggering is blocked
- `Mission Log` shows a pending entry immediately

### 10.4 Alert Escalation

The entire page should not turn red for every fault.

Escalation should concentrate in:

- `Threat Rail`
- high-priority numbers in `Risk Deck`
- critical labels in `Command Status`

This preserves contrast and ensures truly critical states remain legible.

## 11. Backend Additions Required

The current project already has CLI commands and lightweight dashboard snapshot support, but it does not yet expose a real war room UI surface.

Version one of this design requires:

1. a backend snapshot assembler for the war room
2. HTTP endpoints for snapshot retrieval and supported commands
3. a frontend command surface that consumes those endpoints
4. UI-specific aggregation logic for threat level, hot positions, and mission log formatting

The backend should prefer adapting existing runtime and monitoring primitives over inventing parallel execution logic.

## 12. Testing Strategy

### 12.1 Snapshot Assembly Tests

Verify stable snapshot output for:

- healthy runtime
- broker unavailable
- partial source failure
- active anomaly state
- manual intervention state

### 12.2 Command Endpoint Tests

Verify:

- mode gating
- `ARM` gate enforcement
- pending state behavior
- successful command execution
- failed command execution
- `trade` confirmation requirements

### 12.3 UI Integration Tests

Verify:

- initial war room load
- monitoring-to-armed mode transition
- threat escalation rendering
- hot position ranking display
- command busy states
- degraded-state rendering when broker connectivity fails

## 13. Acceptance Criteria

The design is complete when all of the following are true:

- opening the war room immediately communicates broker readiness and portfolio pressure
- the first viewport does not require reading a full table to understand the situation
- anomalies and manual intervention requirements are impossible to miss
- controls are present but operationally subordinate until `ARM` is entered
- backend failures degrade honestly and locally rather than masking problems
- UI command execution reuses existing safety-sensitive runtime logic

## 14. Open Implementation Notes

- The UI should remain desktop-first but not desktop-only.
- The visual system should avoid default SaaS tropes even if the implementation framework is minimal.
- Full audit history and full position inventory should be accessible, but must not displace the first-screen command hierarchy.
