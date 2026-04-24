const POLL_INTERVAL_MS = 5000;
const COMMAND_STATUS_POLL_MS = 350;

let pendingTrade = null;
let busyCommand = null;
let isArmed = false;
let activeCommandJobId = null;

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
    const commandDisabled = !isArmed || busyCommand !== null;
    document.querySelectorAll("[data-command]").forEach((button) => {
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
    const command = result.command ? String(result.command).toUpperCase() : "SYSTEM";
    const status = result.status ? String(result.status) : "ok";
    item.textContent = `${command} ${status}`;
    missionLog.prepend(item);
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
    hideTradeConfirm();
    hideCommandOverlay();
}

async function commandResultFromResponse(response, commandName) {
    if (response.ok) {
        return await response.json();
    }

    let reason = "failed";
    try {
        const payload = await response.json();
        if (payload && typeof payload.reason === "string") {
            reason = payload.reason;
        }
    } catch {
        reason = "failed";
    }

    if (reason === "armed_mode_required") {
        relockWarRoom();
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
        pendingTrade = {command: "trade"};
        showTradeConfirm();
        return;
    }

    showCommandOverlay(commandName);
    try {
        const response = await fetch(`/api/war-room/commands/${commandName}`, {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({confirmed: true, async: true}),
        });
        const accepted = await commandResultFromResponse(response, commandName);
        const result =
            response.status === 202 && accepted.job_id
                ? await monitorCommandJob(accepted.job_id, commandName)
                : accepted;
        prependMissionResult(result);
        await fetchSnapshot();
    } finally {
        hideCommandOverlay();
    }
}

async function confirmTrade() {
    if (!pendingTrade) {
        return;
    }

    showCommandOverlay("trade");
    try {
        const response = await fetch("/api/war-room/commands/trade", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify({confirmed: true, async: true}),
        });
        const accepted = await commandResultFromResponse(response, "trade");
        const result =
            response.status === 202 && accepted.job_id
                ? await monitorCommandJob(accepted.job_id, "trade")
                : accepted;

        prependMissionResult(result);
        pendingTrade = null;
        hideTradeConfirm();
        await fetchSnapshot();
    } finally {
        hideCommandOverlay();
    }
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

    fetchSnapshot().catch(() => prependMissionResult({command: "snapshot", status: "failed"}));
    window.setInterval(() => {
        fetchSnapshot().catch(() => undefined);
    }, POLL_INTERVAL_MS);
});
