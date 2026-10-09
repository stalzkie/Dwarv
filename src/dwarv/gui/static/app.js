// Step 10A: the session-transparency panel's client logic. Hard constraint
// (DWARV_PLAN.md Step 10A): model output and repo content are untrusted --
// every piece of session data lands on the page only via the DOM text-node
// API (never HTML-parsing APIs), so a decision's reason/narration/feedback
// containing literal "<script>...</script>" renders as inert plain text.

const state = {
  samples: [],
  flowData: { nodes: [] },
  currentFlowNode: null,
};

function setText(id, text) {
  document.getElementById(id).textContent = text;
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text; // never innerHTML
  return node;
}

function renderSummary(data) {
  if (!data || !data.active) {
    setText("model-summary", "No active session.");
    setText("explanation", "");
    setText("sandbox-summary", "");
    setText("repo-summary", "");
    return;
  }
  setText("model-summary", `Model: ${data.model_id} (ctx ${data.ctx_size})`);
  setText("explanation", data.explanation || "");
  setText(
    "sandbox-summary",
    `Sandbox tier ${data.sandbox_tier}: ${data.sandbox_tier_detail || ""}`
  );
  setText(
    "repo-summary",
    `Repo: ${data.repo_root || "unknown"} (test command: ${
      (data.test_command || []).join(" ") || "none discovered"
    })`
  );
}

function appendDecision(ev) {
  const list = document.getElementById("decision-list");
  const item = el("li", "decision");
  const headClass = "decision-head" + (ev.event ? ` event-${ev.event}` : "");
  const label = ev.action ? `${ev.event}: ${ev.action} -- ${ev.reason || ""}` : `${ev.event}`;
  item.appendChild(el("div", headClass, label));
  if (ev.narration) item.appendChild(el("div", "decision-narration", ev.narration));
  if (ev.inputs_snapshot) {
    item.appendChild(el("pre", "decision-snapshot", JSON.stringify(ev.inputs_snapshot, null, 2)));
  }
  list.appendChild(item);
  list.scrollTop = list.scrollHeight;
  while (list.children.length > 200) {
    list.removeChild(list.firstChild);
  }
}

function pushSample(ev) {
  state.samples.push(ev);
  if (state.samples.length > 200) state.samples.shift();
  drawChart();
}

function drawChart() {
  const canvas = document.getElementById("rss-chart");
  const ctx = canvas.getContext("2d");
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  if (state.samples.length < 2) return;

  const rssVals = state.samples.map((s) => s.server_rss_mb || 0);
  const maxVal = Math.max(...rssVals, 1);
  const stepX = w / (state.samples.length - 1);

  ctx.strokeStyle = "#4a9eff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  rssVals.forEach((v, i) => {
    const x = i * stepX;
    const y = h - (v / maxVal) * h;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderFlow() {
  const container = document.getElementById("flow-nodes");
  container.textContent = "";
  for (const node of state.flowData.nodes || []) {
    const active = node.id === state.currentFlowNode;
    container.appendChild(el("span", "flow-node" + (active ? " flow-node-active" : ""), node.label || node.id));
  }
}

function handleLiveEvent(ev) {
  if (ev.flow_node) {
    state.currentFlowNode = ev.flow_node;
    renderFlow();
  }
  if (ev.event === "decision") appendDecision(ev);
  else if (ev.event === "monitor_sample") pushSample(ev);
  else if (ev.event === "budget_warn" || ev.event === "budget_violation" || ev.event === "budget_change") {
    appendDecision(ev);
  } else if (ev.event === "model_loaded") {
    setText("model-summary", `Model: ${ev.model_id} (ctx ${ev.ctx_size})`);
    setText("explanation", ev.explanation || "");
  } else if (ev.event === "run_started") {
    setText("sandbox-summary", `Sandbox tier ${ev.sandbox_tier}: ${ev.sandbox_tier_detail || ""}`);
    setText(
      "repo-summary",
      `Repo: ${ev.repo_root || "unknown"} (test command: ${
        (ev.test_command || []).join(" ") || "none discovered"
      })`
    );
  }
}

async function loadSession() {
  try {
    const res = await fetch("/api/session");
    const data = await res.json();
    renderSummary(data);
    for (const d of data.decisions || []) appendDecision(d);
    if (data.latest_sample) pushSample(data.latest_sample);
  } catch {
    setText("model-summary", "No active session.");
  }
}

async function loadFlow() {
  try {
    const res = await fetch("/flow.json");
    state.flowData = await res.json();
    renderFlow();
  } catch {
    // flow.json missing is non-fatal -- the rest of the panel still works.
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
    if (ev.event === "no_active_session") {
      renderSummary({ active: false });
      return;
    }
    handleLiveEvent(ev);
  };
}

loadSession();
loadFlow();
connectLive();
