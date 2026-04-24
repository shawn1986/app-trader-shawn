const POLL_INTERVAL_MS = 5000;
const COMMAND_STATUS_POLL_MS = 1500;

let pendingTrade = null;
let busyCommand = null;
let isArmed = false;
let activeCommandJobId = null;
let tradeReady = false;

const COMMAND_COPY_DEFAULT = "Core systems online. Monitoring live operations.";
const THREAT_COPY_DEFAULT = "Type ARM to unlock";

function commandLabel(value) {
    return String(value || "scan").trim().toUpperCase();
}

function commandDetail(value) {
    const normalized = String(value || "").trim().toLowerCase();
    if (normalized === "scan") {
        return "Sweeping broker state, option surfaces, and watchlist signals.";
    }
    if (normalized === "decide") {
        return "Cross-checking candidate spreads against the decision stack.";
    }
    if (normalized === "manage") {
        return "Reconciling active positions, exits, and threat conditions.";
    }
    if (normalized === "trade") {
        return "Routing the order payload through the broker execution rail.";
    }
    return "Running War Room command.";
}

function syncCommandAvailability() {
    document.querySelectorAll("[data-command]").forEach((button) => {
        const commandName = button.dataset.command || "";
        let commandDisabled = !isArmed || busyCommand !== null;
        if (commandName === "trade" && !tradeReady) {
            commandDisabled = true;
        }
        button.disabled = commandDisabled;
        button.dataset.busy = busyCommand === button.dataset.command ? "true" : "false";
    });

    const armInput = document.querySelector("[data-arm-input]");
    const armSubmit = document.querySelector("[data-arm-submit]");
    if (armInput) {
        armInput.disabled = busyCommand !== null;
    }
    if (armSubmit) {
        armSubmit.disabled = busyCommand !== null;
    }

    const tradeConfirm = document.querySelector("[data-trade-confirm-submit]");
    if (tradeConfirm) {
        tradeConfirm.disabled = busyCommand !== null;
    }
}

function sleep(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function titleCase(value) {
    if (!value) {
        return "Nominal";
    }
    const source = String(value).trim().toLowerCase();
    return source.charAt(0).toUpperCase() + source.slice(1);
}

function resultSeverity(result) {
    const status = String(result?.status || "ok").toLowerCase();
    if (Number(result?.symbol_error_count || 0) > 0) {
        return "warning";
    }
    if (["ok", "ready", "accepted", "submitted"].includes(status)) {
        return "ok";
    }
    return "error";
}

function firstSymbolError(result) {
    const errors = Array.isArray(result?.symbol_errors) ? result.symbol_errors : [];
    return errors.length > 0 ? errors[0] : null;
}

function scanCounts(result) {
    return {
        candidates: Number(result?.candidate_count || 0),
        watchlist: Number(result?.watchlist_count || 0),
    };
}

function summarizeScanResult(result) {
    if (String(result?.command || "").toLowerCase() !== "scan") {
        return "";
    }
    if (typeof result?.candidate_count !== "number") {
        return "";
    }

    const counts = scanCounts(result);
    const parts = [`${counts.candidates} candidates`];
    if (typeof result?.watchlist_count === "number") {
        parts.push(`${counts.watchlist} watchlist`);
    }

    const summaries = Array.isArray(result?.symbol_summaries) ? result.symbol_summaries : [];
    const quoteSummaries = summaries
        .filter((summary) => typeof summary?.quotes_count === "number")
        .slice(0, 3)
        .map((summary) => `${summary.symbol || "symbol"} ${summary.quotes_count} quotes`);

    let detail =
        counts.candidates === 0
            ? `No tradable candidates (${parts.join(", ")}).`
            : `${parts.join(", ")}.`;
    if (quoteSummaries.length > 0) {
        detail = `${detail} ${quoteSummaries.join("; ")}.`;
    }
    if (summaries.length > quoteSummaries.length) {
        detail = `${detail} +${summaries.length - quoteSummaries.length} symbols.`;
    }
    return detail;
}

function isApprovedDecision(result) {
    if (String(result?.command || "").toLowerCase() !== "decide") {
        return false;
    }
    const action = String(result?.decision?.action || "").toLowerCase();
    return result?.status === "ok" && action === "approve";
}

function nextActionModel(result) {
    const command = String(result?.command || "").toLowerCase();
    if (command === "scan" && result?.status === "ok") {
        const counts = scanCounts(result);
        if (counts.candidates === 0) {
            return {
                title: "No tradable candidates",
                summary: `${counts.watchlist} watchlist observations need better pricing, liquidity, or filter settings before a decision.`,
                actions: [
                    "Review watchlist observations for symbols that almost qualified.",
                    "Relax filters or widen scan inputs if the strategy is too strict.",
                    "Run Scan again after quotes refresh or market conditions move.",
                ],
            };
        }
        return {
            title: "Candidates ready",
            summary: `${counts.candidates} candidates are ready for decision review.`,
            actions: [
                "Run Decide to choose the candidate and verify risk.",
                "Keep Trade locked until Decide returns an approved action.",
            ],
        };
    }
    if (command === "decide" && result?.status === "no_candidates") {
        return {
            title: "Decision blocked",
            summary: "No candidates are available for the decision stack.",
            actions: [
                "Run Scan again after quotes refresh.",
                "Relax filters or widen scan inputs if watchlist observations look acceptable.",
            ],
        };
    }
    if (isApprovedDecision(result)) {
        return {
            title: "Trade ready",
            summary: "Decision approved. Trade can now be staged for confirmation.",
            actions: [
                "Review the approved candidate and limit credit.",
                "Use Trade only if the order still matches the current market.",
            ],
        };
    }
    return null;
}

function renderNextActions(result) {
    const root = document.querySelector("[data-next-actions]");
    if (!root) {
        return;
    }
    const title = root.querySelector("[data-next-title]");
    const summary = root.querySelector("[data-next-summary]");
    const list = root.querySelector("[data-next-list]");
    const model = nextActionModel(result);

    if (!model) {
        root.hidden = true;
        if (list) {
            list.replaceChildren();
        }
        return;
    }

    if (title) {
        title.textContent = model.title;
    }
    if (summary) {
        summary.textContent = model.summary;
    }
    if (list) {
        list.replaceChildren();
        model.actions.forEach((action) => {
            const item = document.createElement("li");
            item.textContent = action;
            list.appendChild(item);
        });
    }
    root.hidden = false;
}

function applyCommandOutcome(result) {
    const command = String(result?.command || "").toLowerCase();
    if (command === "scan" || command === "manage") {
        pendingTrade = null;
        tradeReady = false;
        hideTradeConfirm();
    }
    if (command === "decide") {
        pendingTrade = null;
        tradeReady = isApprovedDecision(result);
        hideTradeConfirm();
    }
    if (command === "trade") {
        pendingTrade = null;
        tradeReady = false;
        hideTradeConfirm();
    }
    renderNextActions(result);
    syncCommandAvailability();
}

function resultHeadline(result) {
    const command = result?.command ? String(result.command).toUpperCase() : "SYSTEM";
    const severity = resultSeverity(result);
    if (severity === "warning") {
        return `${command} warning`;
    }
    const status = result?.status ? String(result.status) : "ok";
    return `${command} ${status}`;
}

function resultDetail(result) {
    if (Number(result?.symbol_error_count || 0) > 0) {
        const count = Number(result.symbol_error_count || 0);
        const firstError = firstSymbolError(result);
        const firstErrorText = firstError
            ? `${firstError.symbol || "symbol"}: ${firstError.message || firstError.error_type || "failed"}`
            : "Review symbol errors.";
        return `${count} symbol errors. ${firstErrorText}`;
    }
    if (result?.reason) {
        return String(result.reason);
    }
    return summarizeScanResult(result);
}

function renderPersistentResultSummary(result) {
    const commandCopy = document.querySelector("[data-command-copy]");
    const threatCopy = document.querySelector("[data-threat-copy]");
    const severity = resultSeverity(result);
    const detail = resultDetail(result);
    const command = result?.command ? String(result.command).toUpperCase() : "SYSTEM";

    if (commandCopy) {
        if (severity === "warning") {
            commandCopy.textContent = `${command} completed with ${detail}`;
        } else if (severity === "error") {
            commandCopy.textContent = `${command} ended with ${result?.status || "error"}. ${detail}`.trim();
        } else if (detail) {
            commandCopy.textContent = `${command} completed. ${detail}`;
        } else {
            commandCopy.textContent = COMMAND_COPY_DEFAULT;
        }
    }
    if (threatCopy) {
        if (severity === "warning") {
            threatCopy.textContent = detail;
        } else if (severity === "error") {
            threatCopy.textContent = detail || `${command} needs review.`;
        } else {
            threatCopy.textContent = isArmed ? "Weapons free. Confirm trade before execution." : THREAT_COPY_DEFAULT;
        }
    }
}

function setArmedMode(armed) {
    isArmed = armed;
    document.body.dataset.mode = armed ? "armed" : "monitoring";
    syncCommandAvailability();
}

function prependMissionResult(result) {
    const missionLog = document.querySelector("[data-mission-log]");
    if (!missionLog) {
        return;
    }

    const item = document.createElement("li");
    const headline = document.createElement("span");
    const detail = resultDetail(result);
    item.dataset.severity = resultSeverity(result);
    headline.className = "mission-log__headline";
    headline.textContent = resultHeadline(result);
    item.appendChild(headline);
    if (detail) {
        const detailEl = document.createElement("span");
        detailEl.className = "mission-log__detail";
        detailEl.textContent = detail;
        item.appendChild(detailEl);
    }
    missionLog.prepend(item);
    applyCommandOutcome(result);
    renderPersistentResultSummary(result);
}

function showTradeConfirm() {
    const tray = document.querySelector("[data-trade-confirm]");
    if (!tray) {
        return;
    }
    tray.hidden = false;
}

function hideTradeConfirm() {
    const tray = document.querySelector("[data-trade-confirm]");
    if (!tray) {
        return;
    }
    tray.hidden = true;
}

function showCommandOverlay(commandName) {
    busyCommand = commandName;
    document.body.dataset.busy = "true";
    const overlay = document.querySelector("[data-command-overlay]");
    const overlayCommand = document.querySelector("[data-overlay-command]");
    const overlayDetail = document.querySelector("[data-overlay-detail]");
    const commandCopy = document.querySelector("[data-command-copy]");
    const threatCopy = document.querySelector("[data-threat-copy]");

    if (overlayCommand) {
        overlayCommand.textContent = `${commandLabel(commandName)} IN FLIGHT`;
    }
    if (overlayDetail) {
        overlayDetail.textContent = commandDetail(commandName);
    }
    const progressLabel = document.querySelector("[data-overlay-progress-label]");
    if (progressLabel) {
        progressLabel.textContent = "Awaiting broker response.";
    }
    if (commandCopy) {
        commandCopy.textContent = `${commandLabel(commandName)} command running. Hold the rail.`;
    }
    if (threatCopy) {
        threatCopy.textContent = `Active command: ${commandLabel(commandName)}`;
    }
    if (overlay) {
        overlay.hidden = false;
    }
    renderNextActions(null);
    renderOverlayEvents([]);
    setOverlayProgress(null, null, null);
    syncCommandAvailability();
}

function hideCommandOverlay() {
    busyCommand = null;
    activeCommandJobId = null;
    document.body.dataset.busy = "false";
    const overlay = document.querySelector("[data-command-overlay]");
    const commandCopy = document.querySelector("[data-command-copy]");
    const threatCopy = document.querySelector("[data-threat-copy]");
    const progressLabel = document.querySelector("[data-overlay-progress-label]");

    if (overlay) {
        overlay.hidden = true;
    }
    if (progressLabel) {
        progressLabel.textContent = "Awaiting broker response.";
    }
    if (commandCopy) {
        commandCopy.textContent = COMMAND_COPY_DEFAULT;
    }
    if (threatCopy) {
        threatCopy.textContent = isArmed ? "Weapons free. Confirm trade before execution." : THREAT_COPY_DEFAULT;
    }
    renderOverlayEvents([]);
    setOverlayProgress(null, null, null);
    syncCommandAvailability();
}

function relockWarRoom() {
    setArmedMode(false);
    pendingTrade = null;
    tradeReady = false;
    hideTradeConfirm();
    renderNextActions(null);
    hideCommandOverlay();
}

async function commandResultFromResponse(response, commandName) {
    if (response.ok) {
        return await response.json();
    }

    let reason = "failed";
    let payload = null;
    try {
        payload = await response.json();
        if (payload && typeof payload.reason === "string") {
            reason = payload.reason;
        }
    } catch {
        reason = "failed";
    }

    if (reason === "armed_mode_required") {
        relockWarRoom();
    }

    if (reason === "command_in_progress" && payload?.status === "running" && payload?.job_id) {
        return {
            ...payload,
            command: payload.command || commandName,
            reason,
        };
    }

    return {command: commandName, status: reason};
}

function setOverlayProgress(current, total, unit) {
    const progressRoot = document.querySelector("[data-overlay-progress]");
    const progressLabel = document.querySelector("[data-overlay-progress-label]");
    if (!progressRoot) {
        return;
    }

    if (
        Number.isInteger(current) &&
        Number.isInteger(total) &&
        total > 0
    ) {
        const ratio = Math.max(0, Math.min(1, current / total));
        progressRoot.dataset.mode = "determinate";
        progressRoot.style.setProperty("--progress-value", `${Math.max(10, Math.round(ratio * 100))}%`);
        if (progressLabel) {
            progressLabel.textContent = `${current}/${total} ${unit || "steps"} complete`;
        }
        return;
    }

    delete progressRoot.dataset.mode;
    progressRoot.style.removeProperty("--progress-value");
    if (progressLabel) {
        progressLabel.textContent = "Awaiting broker response.";
    }
}

function renderOverlayEvents(events) {
    const list = document.querySelector("[data-overlay-events]");
    if (!list) {
        return;
    }
    list.replaceChildren();
    const recentEvents = Array.isArray(events) ? events.slice(-6) : [];
    recentEvents.forEach((event) => {
        const item = document.createElement("li");
        item.textContent = event?.message || "Running command step.";
        list.appendChild(item);
    });
}

function updateOverlayFromStatus(status) {
    const commandName = status?.command || busyCommand;
    const overlayCommand = document.querySelector("[data-overlay-command]");
    const overlayDetail = document.querySelector("[data-overlay-detail]");
    const commandCopy = document.querySelector("[data-command-copy]");
    const threatCopy = document.querySelector("[data-threat-copy]");

    if (overlayCommand && commandName) {
        overlayCommand.textContent = `${commandLabel(commandName)} IN FLIGHT`;
    }
    if (overlayDetail) {
        overlayDetail.textContent = status?.current_message || commandDetail(commandName);
    }
    if (commandCopy && commandName) {
        commandCopy.textContent = `${commandLabel(commandName)} command running. Hold the rail.`;
    }
    if (threatCopy && commandName) {
        threatCopy.textContent = `Active command: ${commandLabel(commandName)}`;
    }
    setOverlayProgress(
        status?.progress?.current,
        status?.progress?.total,
        status?.progress?.unit,
    );
    renderOverlayEvents(status?.events);
}

async function fetchCommandStatus() {
    const response = await fetch("/api/war-room/commands/status", {
        cache: "no-store",
    });
    if (!response.ok) {
        throw new Error("command_status_request_failed");
    }
    return await response.json();
}

async function monitorCommandJob(jobId, commandName) {
    activeCommandJobId = jobId;
    while (activeCommandJobId === jobId) {
        const status = await fetchCommandStatus();
        if (status?.job_id === jobId) {
            updateOverlayFromStatus(status);
        }
        if (status?.job_id === jobId && status?.status === "completed") {
            return status.result || {command: commandName, status: "ok"};
        }
        if (status?.job_id === jobId && status?.status === "failed") {
            return {
                command: commandName,
                status: "failed",
                reason: status?.error?.message || "command_failed",
            };
        }
        await sleep(COMMAND_STATUS_POLL_MS);
    }
    return {command: commandName, status: "cancelled"};
}

function attachToRunningCommand(status) {
    if (status?.status !== "running" || !status?.job_id) {
        return;
    }

    const commandName = status.command || busyCommand || "scan";
    if (activeCommandJobId === status.job_id) {
        updateOverlayFromStatus(status);
        return;
    }

    showCommandOverlay(commandName);
    activeCommandJobId = status.job_id;
    updateOverlayFromStatus(status);
    monitorCommandJob(status.job_id, commandName)
        .then(async (result) => {
            if (activeCommandJobId === status.job_id) {
                hideCommandOverlay();
            }
            prependMissionResult(result);
            await fetchSnapshot();
        })
        .catch(() => {
            if (activeCommandJobId === status.job_id) {
                hideCommandOverlay();
            }
            prependMissionResult({command: commandName, status: "failed"});
        });
}

async function syncCommandStatus() {
    if (activeCommandJobId !== null) {
        return null;
    }
    const status = await fetchCommandStatus();
    attachToRunningCommand(status);
    return status;
}

async function fetchSnapshot() {
    const response = await fetch("/api/war-room/snapshot");
    if (!response.ok) {
        throw new Error("snapshot_request_failed");
    }

    const snapshot = await response.json();
    const threat = snapshot.threat_level || "nominal";
    const threatEl = document.querySelector("[data-threat-level]");
    if (threatEl) {
        threatEl.textContent = titleCase(threat);
    }
    document.body.dataset.threat = String(threat).toLowerCase();
    return snapshot;
}

async function pollSnapshot() {
    if (busyCommand !== null) {
        return null;
    }
    return await fetchSnapshot();
}

async function armWarRoom() {
    const armInput = document.querySelector("[data-arm-input]");
    if (!armInput) {
        return;
    }

    const response = await fetch("/api/war-room/arm", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({phrase: armInput.value}),
    });

    if (!response.ok) {
        prependMissionResult({command: "arm", status: "denied"});
        return;
    }

    setArmedMode(true);
    armInput.value = "";
    const threatCopy = document.querySelector("[data-threat-copy]");
    if (threatCopy) {
        threatCopy.textContent = "Weapons free. Confirm trade before execution.";
    }
    prependMissionResult({command: "arm", status: "ready"});
}

async function runCommand(commandName) {
    if (commandName === "trade") {
        if (!tradeReady) {
            prependMissionResult({
                command: "trade",
                status: "blocked",
                reason: "Run Decide and wait for an approved candidate before staging a trade.",
            });
            return;
        }
        pendingTrade = {command: "trade"};
        showTradeConfirm();
        return;
    }

    pendingTrade = null;
    tradeReady = false;
    hideTradeConfirm();
    showCommandOverlay(commandName);
    let result = null;
    try {
        const response = await fetch(`/api/war-room/commands/${commandName}`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({confirmed: true, async: true}),
        });
        const accepted = await commandResultFromResponse(response, commandName);
        result =
            accepted.job_id && (response.status === 202 || accepted.reason === "command_in_progress")
                ? await monitorCommandJob(accepted.job_id, accepted.command || commandName)
                : accepted;
        await fetchSnapshot();
    } finally {
        hideCommandOverlay();
    }
    prependMissionResult(result);
}

async function confirmTrade() {
    if (!pendingTrade) {
        return;
    }

    showCommandOverlay("trade");
    let result = null;
    try {
        const response = await fetch("/api/war-room/commands/trade", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({confirmed: true, async: true}),
        });
        const accepted = await commandResultFromResponse(response, "trade");
        result =
            accepted.job_id && (response.status === 202 || accepted.reason === "command_in_progress")
                ? await monitorCommandJob(accepted.job_id, accepted.command || "trade")
                : accepted;

        pendingTrade = null;
        hideTradeConfirm();
        await fetchSnapshot();
    } finally {
        hideCommandOverlay();
    }
    prependMissionResult(result);
}

document.addEventListener("DOMContentLoaded", () => {
    setArmedMode(false);

    const armSubmit = document.querySelector("[data-arm-submit]");
    const armInput = document.querySelector("[data-arm-input]");
    if (armSubmit) {
        armSubmit.addEventListener("click", () => {
            armWarRoom().catch(() => prependMissionResult({command: "arm", status: "failed"}));
        });
    }
    if (armInput) {
        armInput.addEventListener("keydown", (event) => {
            if (event.key === "Enter") {
                event.preventDefault();
                armWarRoom().catch(() => prependMissionResult({command: "arm", status: "failed"}));
            }
        });
    }

    document.querySelectorAll("[data-command]").forEach((button) => {
        button.addEventListener("click", () => {
            runCommand(button.dataset.command).catch(() =>
                prependMissionResult({command: button.dataset.command, status: "failed"}),
            );
        });
    });

    const tradeConfirmSubmit = document.querySelector("[data-trade-confirm-submit]");
    if (tradeConfirmSubmit) {
        tradeConfirmSubmit.addEventListener("click", () => {
            confirmTrade().catch(() => prependMissionResult({command: "trade", status: "failed"}));
        });
    }

    syncCommandStatus()
        .catch(() => undefined)
        .finally(() => {
            pollSnapshot().catch(() => prependMissionResult({command: "snapshot", status: "failed"}));
        });
    window.setInterval(() => {
        syncCommandStatus()
            .catch(() => undefined)
            .finally(() => {
                pollSnapshot().catch(() => undefined);
            });
    }, POLL_INTERVAL_MS);
});
