// Step 10A: the session-transparency panel's client logic. Hard constraint
// (DWARV_PLAN.md Step 10A): model output and repo content are untrusted --
// every piece of session data lands on the page only via the DOM text-node
// API (never HTML-parsing APIs), so a decision's reason/narration/feedback
// containing literal "<script>...</script>" renders as inert plain text.
//
// Everything is driven by one source of truth: the SSE stream
// (/api/live/stream), which always replays a session's full history before
// going live -- there is no separate REST fetch duplicating that history,
// so there is nothing to keep in sync.

const state = {
  flowLabels: {},
  samples: [],
};

const output = document.getElementById("terminal-output");
let cursorLine = null;

function ensureCursor() {
  if (cursorLine) return cursorLine;
  cursorLine = document.createElement("div");
  cursorLine.className = "line cursor-line";
  const cursor = document.createElement("span");
  cursor.className = "cursor";
  cursorLine.appendChild(cursor);
  output.appendChild(cursorLine);
  return cursorLine;
}

function printLine(text, className) {
  const line = document.createElement("div");
  line.className = "line " + (className || "line--info");
  line.textContent = text;
  const cursor = ensureCursor();
  output.insertBefore(line, cursor);
  output.scrollTop = output.scrollHeight;
  return line;
}

function printJSON(obj) {
  if (!obj || Object.keys(obj).length === 0) return;
  printLine(JSON.stringify(obj, null, 2), "line--json");
}

function setStatus(id, text) {
  document.getElementById(id).textContent = text;
}

async function typeBootLine(command, speedMs) {
  const line = document.createElement("div");
  line.className = "line line--boot";
  const prompt = document.createElement("span");
  prompt.className = "prompt";
  prompt.textContent = "$";
  const typed = document.createElement("span");
  line.appendChild(prompt);
  line.appendChild(typed);
  const cursor = ensureCursor();
  output.insertBefore(line, cursor);

  for (const ch of command) {
    typed.textContent += ch;
    output.scrollTop = output.scrollHeight;
    await new Promise((resolve) => setTimeout(resolve, speedMs));
  }
}

function pushSample(sample) {
  state.samples.push(sample);
  if (state.samples.length > 60) state.samples.shift();
  drawSparkline();
  if (typeof sample.server_rss_mb === "number") {
    const limit = sample.ram_limit_mb ? ` / ${Math.round(sample.ram_limit_mb)}MB` : "";
    setStatus("status-rss", `rss: ${Math.round(sample.server_rss_mb)}MB${limit}`);
  }
}

function drawSparkline() {
  const canvas = document.getElementById("rss-chart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (state.samples.length < 2) return;
  const vals = state.samples.map((s) => s.server_rss_mb || 0);
  const maxVal = Math.max(...vals, 1);
  const stepX = w / (state.samples.length - 1);
  ctx.strokeStyle = "#3b9eff";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  vals.forEach((v, i) => {
    const x = i * stepX;
    const y = h - (v / maxVal) * h;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function flowLabel(id) {
  return (state.flowLabels[id] || id || "").replace(/_/g, " ");
}

function handleEvent(ev) {
  if (ev.event === "no_active_session") {
    printLine("No active session.", "line--note");
    return;
  }
  if (ev.event === "session_ended") {
    printLine("-- session ended --", "line--sep");
    setStatus("status-flow", "flow: --");
    return;
  }
  if (ev.flow_node) {
    setStatus("status-flow", `flow: ${flowLabel(ev.flow_node)}`);
  }

  switch (ev.event) {
    case "run_started":
      printLine(`repo: ${ev.repo_root || "unknown"} (git: ${!!ev.is_git_repo})`, "line--info");
      printLine(
        `test command: ${(ev.test_command || []).join(" ") || "none discovered"}`,
        "line--info"
      );
      printLine(`sandbox tier ${ev.sandbox_tier}: ${ev.sandbox_tier_detail || ""}`, "line--info");
      setStatus("status-tier", `tier: ${ev.sandbox_tier}`);
      break;
    case "model_loaded":
      printLine(`model: ${ev.model_id} (ctx ${ev.ctx_size})`, "line--info");
      if (ev.explanation) printLine(ev.explanation, "line--note");
      setStatus("status-model", `model: ${ev.model_id}`);
      break;
    case "turn_started":
      printLine(`-- turn ${ev.turn_index} --`, "line--sep");
      break;
    case "patch_proposed":
      printLine(`patch proposed: ${(ev.files || []).join(", ")}`, "line--event");
      break;
    case "verified":
      printLine(
        `verified: ${ev.failure_class}${
          ev.failure_class !== "PASS" && ev.feedback ? " -- " + ev.feedback : ""
        }`,
        ev.failure_class === "PASS" ? "line--event" : "line--warn"
      );
      break;
    case "decision":
      printLine(`${ev.action}: ${ev.reason || ""}`, "line--decision");
      if (ev.narration) printLine(ev.narration, "line--narration");
      printJSON(ev.inputs_snapshot);
      break;
    case "budget_change":
      printLine(
        `budget changed: ${ev.reason} -> ${Math.round(ev.new_ram_limit_mb)}MB`,
        "line--warn"
      );
      break;
    case "budget_warn":
      printLine(
        `budget warning: rss ${Math.round(ev.server_rss_mb || 0)}MB / limit ${Math.round(
          ev.ram_limit_mb || 0
        )}MB`,
        "line--warn"
      );
      break;
    case "budget_violation":
      printLine(
        `budget violation: rss ${Math.round(ev.server_rss_mb || 0)}MB / limit ${Math.round(
          ev.ram_limit_mb || 0
        )}MB`,
        "line--violation"
      );
      break;
    case "model_unloaded":
      printLine(`model unloaded: ${ev.model_id || ""}`, "line--event");
      break;
    case "run_finished":
      printLine(`session finished (${ev.turns_completed || 0} turns)`, "line--event");
      break;
    case "monitor_sample":
      pushSample(ev);
      break;
    default:
      break;
  }
}

async function loadFlow() {
  try {
    const res = await fetch("/flow.json");
    const data = await res.json();
    for (const node of data.nodes || []) {
      state.flowLabels[node.id] = node.label || node.id;
    }
  } catch {
    // flow.json missing is non-fatal -- falls back to raw flow_node ids.
  }
}

function connectLive() {
  const source = new EventSource("/api/live/stream");
  source.onmessage = (msg) => {
    let ev;
    try {
      ev = JSON.parse(msg.data);
    } catch {
      return;
    }
    handleEvent(ev);
  };
}

(async function boot() {
  await typeBootLine("dwarv gui", 28);
  printLine("connecting to the active session...", "line--event");
  await loadFlow();
  connectLive();
})();
