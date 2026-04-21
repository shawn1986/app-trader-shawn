const POLL_INTERVAL_MS = 5000;

let pendingTrade = null;

function titleCase(value) {
    if (!value) {
        return "Nominal";
    }
    const source = String(value).trim().toLowerCase();
    return source.charAt(0).toUpperCase() + source.slice(1);
}

function setArmedMode(armed) {
    document.body.dataset.mode = armed ? "armed" : "monitoring";
    document.querySelectorAll("[data-command]").forEach((button) => {
        button.disabled = !armed;
    });
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
    prependMissionResult({command: "arm", status: "ready"});
}

async function runCommand(commandName) {
    if (commandName === "trade") {
        pendingTrade = {command: "trade"};
        showTradeConfirm();
        return;
    }

    const response = await fetch(`/api/war-room/commands/${commandName}`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({confirmed: true}),
    });
    const result = response.ok
        ? await response.json()
        : {command: commandName, status: "failed"};
    prependMissionResult(result);
    await fetchSnapshot();
}

async function confirmTrade() {
    if (!pendingTrade) {
        return;
    }

    const response = await fetch("/api/war-room/commands/trade", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({confirmed: true}),
    });
    const result = response.ok
        ? await response.json()
        : {command: "trade", status: "failed"};

    prependMissionResult(result);
    pendingTrade = null;
    hideTradeConfirm();
    await fetchSnapshot();
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
