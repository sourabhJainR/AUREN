const invoke = window.__TAURI__.core.invoke;

const launcher = document.getElementById("launcher");
const workspace = document.getElementById("workspace");
const projectRoot = document.getElementById("project-root");
const startButton = document.getElementById("start");
const stopButton = document.getElementById("stop");
const runtimeState = document.getElementById("runtime-state");
const errorMessage = document.getElementById("error");
const consoleFrame = document.getElementById("console");
const mcpUrl = document.getElementById("mcp-url");

function setBusy(busy) {
  startButton.disabled = busy;
  stopButton.disabled = busy;
  startButton.textContent = busy ? "Starting..." : "Start AUREN";
}

function showError(message) {
  errorMessage.textContent = message;
  errorMessage.hidden = !message;
}

startButton.addEventListener("click", async () => {
  showError("");
  const root = projectRoot.value.trim();
  if (!root) {
    showError("Select or enter a project folder first.");
    return;
  }
  setBusy(true);
  runtimeState.textContent = "Starting local services";
  runtimeState.classList.remove("ready");
  try {
    const result = await invoke("start_local", { projectRoot: root });
    consoleFrame.src = result.dashboardUrl;
    mcpUrl.textContent = result.mcpUrl;
    launcher.hidden = true;
    workspace.hidden = false;
    runtimeState.textContent = "Running locally";
    runtimeState.classList.add("ready");
  } catch (error) {
    runtimeState.textContent = "Not running";
    showError(String(error));
  } finally {
    setBusy(false);
  }
});

stopButton.addEventListener("click", async () => {
  setBusy(true);
  try {
    await invoke("stop_local");
    consoleFrame.src = "about:blank";
    workspace.hidden = true;
    launcher.hidden = false;
    runtimeState.textContent = "Stopped";
    runtimeState.classList.remove("ready");
    showError("");
  } catch (error) {
    showError(String(error));
  } finally {
    setBusy(false);
  }
});
