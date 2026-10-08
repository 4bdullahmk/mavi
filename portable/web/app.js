"use strict";

function maviEncodeWav16k(sampleBlocks) {
  const sampleCount = sampleBlocks.reduce((total, block) => total + block.length, 0);
  const dataBytes = sampleCount * 2;
  if (!sampleCount || dataBytes > 0xffffffff - 36) throw new Error("The recording is empty or too large to encode.");
  const wav = new Uint8Array(44 + dataBytes);
  const view = new DataView(wav.buffer);
  const ascii = (offset, value) => { for (let i = 0; i < value.length; i += 1) wav[offset + i] = value.charCodeAt(i); };
  ascii(0, "RIFF"); view.setUint32(4, 36 + dataBytes, true); ascii(8, "WAVE");
  ascii(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
  view.setUint16(22, 1, true); view.setUint32(24, 16000, true); view.setUint32(28, 32000, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, "data"); view.setUint32(40, dataBytes, true);
  let offset = 44;
  for (const block of sampleBlocks) {
    for (let index = 0; index < block.length; index += 1, offset += 2) view.setInt16(offset, block[index], true);
  }
  return wav;
}

function maviBytesToBase64(bytes) {
  let result = "";
  const chunkSize = 48 * 1024;
  for (let offset = 0; offset < bytes.length; offset += chunkSize) {
    result += btoa(String.fromCharCode(...bytes.subarray(offset, Math.min(bytes.length, offset + chunkSize))));
  }
  return result;
}

if (typeof window !== "undefined") window.MaviAudio = Object.freeze({ encodeWav16k: maviEncodeWav16k, bytesToBase64: maviBytesToBase64 });

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const apiBase = "/api";
  const onboardingPrompt = $("#summary-prompt-text").textContent.trim();
  const modes = [
    { id: "auto", name: "Automatic", icon: "M", cap: "auto", description: "Let Mavi choose the right local workspace" },
    { id: "chat", name: "Chat", icon: "◌", cap: "chat", description: "Ask questions and work through ideas" },
    { id: "workers", name: "Agent team", icon: "⋮", cap: "workers", description: "Independent local analysts and a shared review" },
    { id: "developer", name: "Developer", icon: "⌘", cap: "developer", description: "Inspect and update a code project" },
    { id: "files", name: "Files", icon: "▤", cap: "files", description: "Create or edit workspace files" },
    { id: "images", name: "Images", icon: "▧", cap: "images", description: "Generate or edit images" },
    { id: "3d", name: "3D", icon: "◇", cap: "3d", description: "Create a 3D or CAD artifact" },
    { id: "browser", name: "Browser", icon: "◎", cap: "browser", description: "Work with a website in the local browser" },
    { id: "computer", name: "Computer", icon: "▣", cap: "computer", description: "Inspect a local app and propose actions" },
    { id: "stocks", name: "Stocks", icon: "⌁", cap: "stocks", description: "Analyze supplied market data" },
    { id: "dictation", name: "Dictation", icon: "♪", cap: "dictation", description: "Transcribe a supported WAV recording" },
    { id: "improve_mavi", name: "Improve Mavi", icon: "✳", cap: "improve_mavi", description: "Build a local update candidate" }
  ];
  const aliases = { images: ["image"], "3d": ["cad", "model3d"], improve_mavi: ["update", "improveMavi"] };
  const apiModes = { images: "image", "3d": "cad", improve_mavi: "update" };
  const textExtensions = new Set(["txt", "md", "csv", "json", "html", "css", "js", "py", "swift", "log"]);
  const imageMimes = new Set(["image/png", "image/jpeg"]);
  const wavMimes = new Set(["audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"]);
  const documentMimes = new Map([
    ["pdf", new Set(["application/pdf"])],
    ["docx", new Set(["application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"])],
    ["xlsx", new Set(["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"])],
    ["pptx", new Set(["application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/octet-stream"])]
  ]);
  const documentModes = new Set(["auto", "files", "developer", "workers"]);
  const maxAttachmentBytes = 10 * 1024 * 1024;
  const maxRecordingSeconds = 5 * 60;
  const maxRecordingSamples = 16_000 * maxRecordingSeconds;
  const maxProfileBytes = 32 * 1024;
  const maxProfileChars = 4500;

  let state = null;
  let activeChatId = null;
  let activeJob = null;
  let currentMode = "auto";
  let currentView = "chat";
  let discordStatusPollTimer = 0;
  let discordStatusPollGeneration = 0;
  let discordStatusPollController = null;
  let discordStatusPollTimedOut = false;
  let attachments = [];
  let toastTimer = 0;
  let localProfileDraft = "";
  let theme = "system";
  let discordConfigDirty = false;
  let onlineConfigDirty = false;
  let onlineModeDraft = "local";
  let onlineRoutesDraft = [];
  let onlineGatewayDraft = "";
  let onlineProviders = [];
  let onlineRouteStatuses = [];
  let onlineKeyConfigured = new Set();
  let onlineCatalogs = new Map();
  let recording = null;
  let sendPending = false;
  const automationPolicyStorageKey = "mavi-automation-policy";
  let automationPolicyChoice = (() => {
    try {
      const saved = localStorage.getItem(automationPolicyStorageKey);
      return ["ask_each", "routine_navigation"].includes(saved) ? saved : "ask_each";
    } catch { return "ask_each"; }
  })();
  let agentEventJobId = null;
  let agentEventByID = new Map();
  const showTeamStorageKey = "mavi-show-team";
  let showTeam = (() => { try { return localStorage.getItem(showTeamStorageKey) !== "0"; } catch { return true; } })();
  const showPenguinStorageKey = "mavi-show-penguin";
  let showPenguin = (() => { try { return localStorage.getItem(showPenguinStorageKey) !== "0"; } catch { return true; } })();
  let penguinGreetingTimer = 0;
  let galleryItems = [];

  function apiModeFor(modeID) { return apiModes[modeID] || modeID; }

  function apiURL(path) {
    const url = new URL(`${apiBase}${path}`, window.location.href);
    if (url.origin !== window.location.origin) throw new Error("Mavi only accepts same-origin local requests.");
    return url;
  }

  async function request(path, options = {}) {
    const response = await fetch(apiURL(path), {
      credentials: "same-origin",
      cache: "no-store",
      ...options,
      headers: { ...(options.body ? { "Content-Type": "application/json" } : {}), ...(options.headers || {}) }
    });
    const type = response.headers.get("content-type") || "";
    const payload = type.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = payload && typeof payload === "object" ? (payload.error || payload.detail || payload.message) : payload;
      throw new Error(typeof message === "string" && message ? message : `Local request failed (${response.status}).`);
    }
    return payload;
  }

  function post(path, body) {
    return request(path, { method: "POST", body: JSON.stringify(body) });
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = String(message || "Done");
    toast.classList.remove("hidden");
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.add("hidden"), 3200);
  }

  function setConnection(online, message) {
    const dot = $("#connection-dot");
    dot.classList.toggle("ready", Boolean(online));
    dot.classList.toggle("offline", !online);
    $("#connection-label").textContent = message;
  }

  function capabilityFor(modeID) {
    if (modeID === "chat") {
      const reported = state?.capabilities?.chat;
      if (!reported || typeof reported.available !== "boolean") return { available: false, reason: "Chat readiness has not been reported by the local backend." };
      return reported;
    }
    const mode = modes.find((item) => item.id === modeID);
    const caps = state?.capabilities || {};
    const keys = [mode?.cap, ...(aliases[modeID] || [])].filter(Boolean);
    const capability = keys.map((key) => caps[key]).find((value) => value && typeof value === "object");
    if (!capability || typeof capability.available !== "boolean") return { available: false, reason: "The local backend has not reported this capability." };
    return capability;
  }

  function applyTheme(nextTheme) {
    theme = ["system", "light", "dark"].includes(nextTheme) ? nextTheme : "system";
    document.documentElement.dataset.theme = theme;
    const prefersDark = window.matchMedia?.("(prefers-color-scheme: dark)").matches === true;
    const darkTheme = theme === "dark" || (theme === "system" && prefersDark);
    const themeColor = $('meta[name="theme-color"]');
    if (themeColor) themeColor.content = darkTheme ? "#111210" : "#f5f5f3";
    $$("[data-theme-choice]").forEach((button) => button.classList.toggle("active", button.dataset.themeChoice === theme));
  }

  function renderWorkspaceMenu() {
    const menu = $("#workspace-menu");
    menu.replaceChildren();
    for (const mode of modes) {
      const capability = capabilityFor(mode.id);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "workspace-option";
      button.setAttribute("role", "menuitem");
      button.disabled = !capability.available;
      button.title = capability.available ? mode.description : (capability.reason || "This capability is not ready on this device.");
      const icon = document.createElement("span");
      icon.className = "option-icon";
      icon.textContent = mode.icon;
      const detail = document.createElement("span");
      const name = document.createElement("b");
      name.textContent = mode.name;
      const description = document.createElement("small");
      description.textContent = capability.available ? mode.description : (capability.reason || "Not ready on this device");
      detail.append(name, description);
      const status = document.createElement("span");
      status.className = "option-state";
      status.textContent = capability.available ? "Ready" : "Unavailable";
      button.append(icon, detail, status);
      button.addEventListener("click", () => chooseMode(mode.id));
      menu.append(button);
    }
  }

  function chooseMode(modeID) {
    const capability = capabilityFor(modeID);
    if (!capability.available) {
      showToast(capability.reason || "This capability is not ready on this device.");
      return;
    }
    currentMode = modeID;
    const mode = modes.find((item) => item.id === modeID) || modes[0];
    $("#workspace-name").textContent = mode.name;
    $("#workspace-glyph").textContent = mode.icon;
    $("#composer-mode-name").textContent = mode.name;
    closeWorkspaceMenu();
    $("#composer-input").placeholder = modeID === "chat" ? "Message Mavi…" : `What would you like to do in ${mode.name}?`;
    renderModelStatus();
    $("#composer-input").focus();
  }

  function openWorkspaceMenu() {
    renderWorkspaceMenu();
    $("#workspace-menu").classList.remove("hidden");
    $("#workspace-picker").setAttribute("aria-expanded", "true");
  }

  function closeWorkspaceMenu() {
    $("#workspace-menu").classList.add("hidden");
    $("#workspace-picker").setAttribute("aria-expanded", "false");
  }

  function renderModelStatus() {
    const models = Array.isArray(state?.models) ? state.models : [];
    const ready = capabilityFor(currentMode).available && !(activeJob && !["chat", "browser", "computer"].includes(activeJob.mode));
    const surpriseReady = isLocalSurpriseRequest($("#composer-input")?.value || "");
    renderAutomationApprovalControls();
    renderChatContext();
    $("#send-button").disabled = (!ready && !surpriseReady) || Boolean(recording) || sendPending;
    const recordButton = $("#record-button");
    const dictation = capabilityFor("dictation");
    recordButton.disabled = !recording && (!dictation.available || Boolean(activeJob));
    recordButton.title = recording ? "Stop recording" : (dictation.available ? "Record audio locally for dictation" : (dictation.reason || "Dictation is not ready on this device."));
    const note = $("#composer-note");
    const hybridNotice = onlineModeDraft === "hybrid"
      ? (hasOnlineRoutes() ? "Hybrid is enabled; eligible text tasks try configured online routes and may send task text/context to those providers. Screenshots and computer-control content stay local. " : "Hybrid mode has no configured online routes; eligible tasks use local inference. ")
      : "";
    if (activeJob?.mode === "auto") note.textContent = `${hybridNotice}Mavi is choosing the right workspace…`;
    else if (activeJob && !["chat", "browser", "computer"].includes(activeJob.mode)) note.textContent = `${hybridNotice}A task is already running. Stop it before starting another task.`;
    else if (activeJob) note.textContent = `${hybridNotice}Send a follow-up to steer this task. Attachments can be used on your next task.`;
    else note.textContent = `${hybridNotice}Local AI can make mistakes. Review important details.`;
    const indicator = $("#model-indicator");
    indicator.replaceChildren();
    const dot = document.createElement("span");
    dot.className = `status-dot ${ready ? "ready" : "offline"}`;
    const label = document.createElement("span");
    const selected = state?.settings?.model;
    const selectedPresent = selected && models.some((model) => model.name === selected);
    const inference = activeJob?.inference;
    label.textContent = inference?.location === "online"
      ? `Online · ${providerName(inference.provider)}${inference.model ? ` · ${inference.model}` : ""}`
      : activeJob ? (onlineModeDraft === "hybrid" ? "Hybrid task running" : "Local task running")
        : ready ? (onlineModeDraft === "hybrid" ? (hasOnlineRoutes() ? "Online routes enabled · local fallback" : "Hybrid mode · local only") : (selectedPresent ? selected : `${models.length} local model${models.length === 1 ? "" : "s"} available`))
          : "No ready local chat model";
    indicator.append(dot, label);

    const list = $("#model-list");
    list.replaceChildren();
    if (!models.length) {
      const empty = document.createElement("div");
      empty.className = "empty-models";
      empty.textContent = "No local models are reported. Chat and tool workspaces stay unavailable until the backend confirms a ready model.";
      list.append(empty);
    } else {
      for (const item of models) {
        const row = document.createElement("div");
        row.className = "model-row";
        const dot = document.createElement("span");
        dot.className = "status-dot ready";
        const name = document.createElement("span");
        name.className = "model-name";
        name.textContent = String(item.name || "Local model");
        const size = document.createElement("span");
        size.className = "model-size";
        size.textContent = item.size == null ? "Installed" : formatSize(item.size);
        row.append(dot, name, size);
        list.append(row);
      }
    }

    const select = $("#model-select");
    select.replaceChildren();
    const choose = document.createElement("option");
    choose.value = "";
    choose.textContent = models.length ? "Use local default" : "No model available";
    select.append(choose);
    for (const item of models) {
      const option = document.createElement("option");
      option.value = item.name;
      option.textContent = String(item.name);
      select.append(option);
    }
    select.value = selected || "";
    select.disabled = !models.length;

    const hardware = state?.hardware || {};
    const ram = Number(hardware.ram_gb);
    const gpuValue = hardware.gpu;
    const gpu = typeof gpuValue === "string" ? gpuValue.trim() : String(gpuValue?.name || gpuValue?.model || hardware.gpu_name || hardware.gpu_model || "").trim();
    const vram = Number(hardware.gpu_vram_gb ?? hardware.vram_gb ?? hardware.gpu?.vram_gb);
    const vramMB = Number(hardware.gpu_vram_mb ?? hardware.vram_mb ?? hardware.gpu?.vram_mb);
    const bits = [];
    if (hardware.platform) bits.push(String(hardware.platform));
    if (Number.isFinite(ram) && ram > 0) bits.push(`${ram} GB memory`);
    if (gpu) bits.push(gpu);
    if (Number.isFinite(vram) && vram > 0) bits.push(`${vram} GB VRAM`);
    else if (Number.isFinite(vramMB) && vramMB > 0) bits.push(`${Math.round(vramMB)} MB VRAM`);
    const recommendation = "Many local models work best with 32 GB or more of memory and an NVIDIA GPU.";
    $("#hardware-note").textContent = bits.length ? `Detected device: ${bits.join(" · ")}. ${recommendation} Workspace readiness comes from backend capability checks.` : `Device details are not reported. ${recommendation} Readiness comes from the local backend capability checks.`;
  }

  function normalizedTouchText(value) {
    return String(value || "").trim().replace(/[.!…]+$/, "").replace(/\s+/g, " ").toLocaleLowerCase();
  }

  function isLocalSurpriseRequest(text) {
    const personalTouch = state?.settings?.personal_touch;
    const builtInTouch = { trigger: "I love Abdullah", message: "I love Muzhda" };
    const normalized = normalizedTouchText(text);
    return Boolean(normalized && [personalTouch, builtInTouch].some((touch) =>
      touch?.trigger && touch?.message && normalized === normalizedTouchText(touch.trigger)));
  }

  function selectAutomationPolicy(value) {
    if (!["ask_each", "routine_navigation"].includes(value)) return;
    automationPolicyChoice = value;
    try { localStorage.setItem(automationPolicyStorageKey, value); } catch { /* Keep the selection for this page session. */ }
    const select = $("#automation-policy");
    if (select) select.value = value;
    renderAutomationApprovalControls();
  }

  function renderAutomationApprovalControls() {
    const fieldset = $("#automation-approval");
    const policySelect = $("#automation-policy");
    const scopeFieldset = $("#automation-scope");
    const details = $("#automation-control-details");
    if (!fieldset || !policySelect || !scopeFieldset || !details) return;
    const visible = !activeJob && ["auto", "browser", "computer"].includes(currentMode) && capabilityFor("computer").available;
    details.classList.toggle("hidden", !visible);
    fieldset.classList.toggle("hidden", !visible);
    scopeFieldset.classList.toggle("hidden", !visible);
    policySelect.value = automationPolicyChoice;
    if (!visible) {
      details.open = false;
      const singleApp = scopeFieldset.querySelector('input[name="automation-scope"][value="single_app"]');
      if (singleApp) singleApp.checked = true;
    }
    const scope = scopeFieldset.querySelector('input[name="automation-scope"]:checked')?.value;
    $("#automation-control-summary-state").textContent = `${automationPolicyChoice === "routine_navigation" ? "Routine navigation" : "Ask each action"} · ${scope === "whole_computer" ? "approved app switches" : "selected app"}`;
  }

  function formatSize(value) {
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0) return String(value);
    if (number > 1e9) return `${(number / 1e9).toFixed(1)} GB`;
    if (number > 1e6) return `${(number / 1e6).toFixed(0)} MB`;
    return `${number} B`;
  }

  function renderDiscordStatus() {
    const discord = state?.discord || {};
    const connected = discord.connected === true;
    const configured = discord.configured === true;
    const rawStatus = typeof discord.status === "string" && discord.status ? discord.status : (configured ? (connected ? "Connected" : "Configured · not connected") : "Not configured");
    const status = discordStatusPollTimedOut && configured && !connected && rawStatus === "Connecting…"
      ? "Still connecting · refresh status to check again"
      : rawStatus;
    $("#discord-nav-state").textContent = connected ? "On" : (configured ? "Ready" : "Off");
    $("#discord-status").textContent = status;
    $("#discord-view-title").textContent = configured ? (connected ? "Connected" : "Configured") : "Not configured";
    $("#discord-view-copy").textContent = status;
    $("#discord-connection-value").textContent = connected ? "Connected" : (configured ? "Configured, offline" : "Not configured");
    $("#discord-badge").textContent = connected ? "CONNECTED" : (configured ? "CONFIGURED" : "OPTIONAL");
  }

  const onlineProviderOptions = [
    ["nvidia", "NVIDIA"], ["openrouter", "OpenRouter"], ["groq", "Groq"],
    ["google", "Google"], ["gateway", "Local gateway"]
  ];
  const onlineRoles = [["chat", "Chat"], ["analysis", "Analysis"], ["code", "Code"], ["files", "Files"]];

  function providerName(id) { return onlineProviderOptions.find(([value]) => value === id)?.[1] || String(id || "Provider"); }
  function hasOnlineRoutes() { return onlineRoutesDraft.some((route) => route.model && route.roles.length); }

  function updateHybridCopy() {
    if (onlineModeDraft !== "hybrid") {
      $("#welcome-copy").textContent = "Ask a question, shape an idea, or bring a file into the conversation. Messages are saved in this workspace.";
      return;
    }
    $("#welcome-copy").textContent = hasOnlineRoutes()
      ? "Messages are saved in this workspace. Hybrid mode tries configured online routes for eligible text tasks and may send task text and context to those providers."
      : "Hybrid mode is selected, but no online routes are configured. Tasks still use local inference.";
  }

  function normalizeOnlineConfig(config) {
    const mode = config?.mode === "hybrid" ? "hybrid" : "local";
    const routes = Array.isArray(config?.routes) ? config.routes.slice(0, 8).map((route) => ({
      provider: onlineProviderOptions.some(([id]) => id === route?.provider) ? route.provider : "nvidia",
      model: typeof route?.model === "string" ? route.model : "",
      roles: Array.isArray(route?.roles) ? onlineRoles.map(([id]) => id).filter((id) => route.roles.includes(id)) : ["chat", "analysis", "code", "files"]
    })) : [];
    return { mode, routes, gateway_url: typeof config?.gateway_url === "string" ? config.gateway_url : "", allow_paid: false };
  }

  function renderOnline(payload, { force = false } = {}) {
    if (!payload || typeof payload !== "object") return;
    onlineProviders = Array.isArray(payload.providers) ? payload.providers : onlineProviders;
    onlineRouteStatuses = Array.isArray(payload.routes) ? payload.routes : onlineRouteStatuses;
    for (const item of onlineProviders) if (item?.id) {
      if (item.configured === true) onlineKeyConfigured.add(item.id);
      else onlineKeyConfigured.delete(item.id);
    }
    for (const item of onlineRouteStatuses) if (item?.provider && (item.key_configured === true || item.ready === true)) onlineKeyConfigured.add(item.provider);
    if (onlineConfigDirty) {
      onlineModeDraft = $("#online-mode").value;
      onlineGatewayDraft = $("#online-gateway-url").value;
      onlineRoutesDraft = readOnlineRoutes();
    }
    if (!onlineConfigDirty || force) {
      const config = normalizeOnlineConfig(payload.config || payload.online?.config || payload);
      onlineModeDraft = config.mode;
      onlineRoutesDraft = config.routes;
      onlineGatewayDraft = config.gateway_url;
      onlineConfigDirty = false;
      onlineCatalogs.clear();
    }
    $("#online-mode").value = onlineModeDraft;
    $("#online-gateway-url").value = onlineGatewayDraft;
    $("#online-mode-badge").textContent = onlineModeDraft === "hybrid" ? "HYBRID" : "LOCAL ONLY";
    $("#online-disclosure").classList.toggle("hidden", onlineModeDraft !== "hybrid");
    updateHybridCopy();
    $("#gateway-url-wrap").classList.toggle("hidden", $("#online-key-provider").value !== "gateway");
    const provider = $("#online-key-provider").value;
    const providerStatus = onlineProviders.find((item) => item?.id === provider) || (() => {
      const matches = onlineRouteStatuses.filter((item) => item?.provider === provider);
      return matches.length ? {
        configured: matches.some((item) => item.key_configured === true || item.ready === true),
        cooldown_seconds: Math.max(0, ...matches.map((item) => Number(item.cooldown_seconds) || 0)),
        status: matches.some((item) => item.auth_blocked === true) ? "key needs attention" : ""
      } : onlineKeyConfigured.has(provider) ? { configured: true, status: "" } : null;
    })();
    $("#online-provider-meta").textContent = providerStatus
      ? `${providerName(provider)} · ${provider === "gateway" ? (providerStatus.configured ? "gateway configured" : "not configured") : (providerStatus.configured ? "key configured for this session" : "not configured")}${Number(providerStatus.cooldown_seconds) > 0 ? ` · quota cooldown ${Math.ceil(providerStatus.cooldown_seconds)}s` : ""}${providerStatus.status ? ` · ${providerStatus.status}` : ""}`
      : `${providerName(provider)} key is optional until a route uses this provider.`;
    renderOnlineRoutes();
    const inference = state?.active_job?.inference;
    renderJobInference(inference);
    renderModelStatus();
  }

  function renderOnlineRoutes() {
    const container = $("#online-routes");
    container.replaceChildren();
    if (!onlineRoutesDraft.length) {
      const empty = document.createElement("div");
      empty.className = "online-empty";
      empty.textContent = "No online routes are configured. Local inference remains the only available path.";
      container.append(empty);
    }
    onlineRoutesDraft.forEach((route, index) => {
      const card = document.createElement("div");
      card.className = "online-route";
      card.dataset.routeIndex = String(index);
      const title = document.createElement("div");
      title.className = "online-route-index";
      title.textContent = `Route ${index + 1}`;
      card.append(title);

      const providerLabel = document.createElement("label");
      providerLabel.className = "online-field";
      providerLabel.textContent = "Provider";
      const providerSelect = document.createElement("select");
      providerSelect.dataset.field = "provider";
      for (const [id, name] of onlineProviderOptions) {
        const option = document.createElement("option"); option.value = id; option.textContent = name; providerSelect.append(option);
      }
      providerSelect.value = route.provider;
      providerLabel.append(providerSelect);
      card.append(providerLabel);

      const modelLabel = document.createElement("label");
      modelLabel.className = "online-field";
      modelLabel.textContent = "Model ID";
      const modelInput = document.createElement("input");
      modelInput.type = "text"; modelInput.autocomplete = "off"; modelInput.spellcheck = false;
      modelInput.placeholder = "Provider model ID"; modelInput.value = route.model; modelInput.dataset.field = "model";
      modelLabel.append(modelInput);
      card.append(modelLabel);

      const controls = document.createElement("div"); controls.className = "online-route-controls";
      for (const [label, action, disabled] of [["↑", "up", index === 0], ["↓", "down", index === onlineRoutesDraft.length - 1], ["Remove", "remove", false]]) {
        const button = document.createElement("button"); button.type = "button"; button.className = "quiet-button";
        button.textContent = label; button.dataset.action = action; button.disabled = disabled;
        button.setAttribute("aria-label", action === "remove" ? `Remove route ${index + 1}` : `${action === "up" ? "Move route up" : "Move route down"}`);
        controls.append(button);
      }
      card.append(controls);

      const catalogWrap = document.createElement("div"); catalogWrap.className = "online-route-catalog";
      const catalogLabel = document.createElement("label"); catalogLabel.className = "online-field"; catalogLabel.textContent = "Available models";
      const catalog = document.createElement("select"); catalog.dataset.field = "catalog";
      const placeholder = document.createElement("option"); placeholder.value = ""; placeholder.textContent = onlineCatalogs.has(index) ? "Choose a loaded model" : "Load models to browse"; catalog.append(placeholder);
      for (const item of onlineCatalogs.get(index) || []) {
        const option = document.createElement("option"); option.value = String(item.id || ""); option.textContent = String(item.name || item.id || "Model"); catalog.append(option);
      }
      catalog.value = (onlineCatalogs.get(index) || []).some((item) => item.id === route.model) ? route.model : "";
      catalogLabel.append(catalog);
      const load = document.createElement("button"); load.type = "button"; load.className = "quiet-button"; load.textContent = "Load models"; load.dataset.action = "catalog";
      catalogWrap.append(catalogLabel, load); card.append(catalogWrap);

      const roles = document.createElement("div"); roles.className = "online-role-list"; roles.setAttribute("aria-label", "Task roles for this route");
      for (const [id, name] of onlineRoles) {
        const label = document.createElement("label"); const checkbox = document.createElement("input");
        checkbox.type = "checkbox"; checkbox.value = id; checkbox.dataset.field = "role"; checkbox.checked = route.roles.includes(id);
        label.append(checkbox, document.createTextNode(name)); roles.append(label);
      }
      card.append(roles);
      container.append(card);
    });
    $("#online-add-route").disabled = onlineRoutesDraft.length >= 8;
  }

  function renderJobInference(inference) {
    const notice = $("#job-inference");
    const provider = typeof inference?.provider === "string" ? inference.provider : "";
    const model = typeof inference?.model === "string" ? inference.model : "";
    if (!provider && !model) { notice.classList.add("hidden"); notice.textContent = ""; return; }
    const isOnline = inference?.location === "online";
    notice.textContent = `${isOnline ? "Online model used" : "Local model"}${provider ? ` · ${providerName(provider)}` : ""}${model ? ` · ${model}` : ""}${isOnline ? " · Eligible task text/context sent to provider" : ""}`;
    notice.classList.remove("hidden");
  }

  async function loadOnlineState({ force = false } = {}) {
    try { renderOnline(await request("/online"), { force }); }
    catch (error) {
      if (force || !onlineConfigDirty) $("#online-feedback").textContent = error.message || "Online model setup is unavailable.";
    }
  }

  async function loadOnlineCatalog(index) {
    const route = onlineRoutesDraft[index];
    if (!route) return;
    const button = $(`.online-route[data-route-index="${index}"] [data-action="catalog"]`);
    if (onlineModeDraft !== "hybrid") {
      setOnlineFeedback("Select Hybrid mode and acknowledge the task data disclosure before loading an online catalog.", true);
      return;
    }
    if (!$("#online-consent").checked) {
      setOnlineFeedback("Acknowledge the task data disclosure before configuring an online provider.", true);
      return;
    }
    button.disabled = true; button.textContent = "Loading…";
    try {
      const key = $("#online-api-key").value.trim();
      const keyProvider = $("#online-key-provider").value;
      const routes = readOnlineRoutes().filter((item) => item.model && item.roles.length);
      if (onlineConfigDirty || key) {
        const config = { mode: "hybrid", routes, gateway_url: $("#online-gateway-url").value.trim(), allow_paid: false };
        const body = { config, consent: true, ...(key ? { keys: { [keyProvider]: key } } : {}) };
        try {
          const configured = await post("/online/configure", body);
          if (key) onlineKeyConfigured.add(keyProvider);
          state = state || {}; state.online = configured;
          renderOnline(configured);
        } finally {
          body.keys = undefined;
          $("#online-api-key").value = "";
        }
      }
      const result = await post("/online/catalog", { provider: route.provider });
      onlineCatalogs.set(index, Array.isArray(result?.models) ? result.models : []);
      renderOnlineRoutes();
      $(`#online-routes .online-route[data-route-index="${index}"] [data-field="catalog"]`)?.focus();
      $("#online-feedback").textContent = `${onlineCatalogs.get(index).length} model${onlineCatalogs.get(index).length === 1 ? "" : "s"} loaded for ${providerName(route.provider)}.`;
    } catch (error) {
      button.textContent = "Load models"; button.disabled = false;
      setOnlineFeedback(error.message || "Could not load models.", true);
    }
  }

  function setOnlineFeedback(message, isError = false) {
    const node = $("#online-feedback"); node.textContent = message; node.classList.toggle("is-error", isError);
  }

  function readOnlineRoutes() {
    return $$(".online-route", $("#online-routes")).map((card) => ({
      provider: $("[data-field='provider']", card).value,
      model: $("[data-field='model']", card).value.trim(),
      roles: $$("[data-field='role']:checked", card).map((item) => item.value)
    }));
  }

  async function saveOnlineSettings(clearKey = false) {
    const mode = $("#online-mode").value;
    const consent = $("#online-consent").checked;
    if (mode === "hybrid" && !consent) return setOnlineFeedback("Acknowledge the task data disclosure before enabling hybrid mode.", true);
    const routes = readOnlineRoutes();
    if (routes.length > 8) return setOnlineFeedback("Use no more than eight fallback routes.", true);
    if (routes.some((route) => !route.model || !route.roles.length)) return setOnlineFeedback("Give each route a model ID and at least one task role.", true);
    const gatewayURL = $("#online-gateway-url").value.trim();
    if (gatewayURL) {
      let parsed;
      try { parsed = new URL(gatewayURL); } catch { return setOnlineFeedback("Enter a valid local gateway URL ending in /v1.", true); }
      if (parsed.protocol !== "http:" || parsed.username || parsed.password || parsed.search || parsed.hash || !["localhost", "127.0.0.1"].includes(parsed.hostname) || !parsed.port || parsed.pathname !== "/v1") {
        return setOnlineFeedback("The gateway URL must use http://localhost:<port>/v1 or 127.0.0.1:<port>/v1.", true);
      }
    }
    const provider = $("#online-key-provider").value;
    const key = $("#online-api-key").value.trim();
    const config = { mode, routes, gateway_url: gatewayURL, allow_paid: false };
    const body = { config, consent, ...(key ? { keys: { [provider]: key } } : {}), ...(clearKey ? { clear_keys: [provider] } : {}) };
    const save = $("#online-save"); save.disabled = true; save.setAttribute("aria-busy", "true");
    setOnlineFeedback("Saving online model settings…");
    try {
      const result = await post("/online/configure", body);
      if (key) onlineKeyConfigured.add(provider);
      if (clearKey) onlineKeyConfigured.delete(provider);
      onlineConfigDirty = false;
      state = state || {}; state.online = result;
      renderOnline(result, { force: true });
      setOnlineFeedback("Online model settings saved. API key field cleared; keys remain session-only.");
    } catch (error) {
      setOnlineFeedback(error.message || "Could not save online model settings.", true);
    } finally {
      body.keys = undefined;
      $("#online-api-key").value = "";
      save.disabled = false; save.removeAttribute("aria-busy");
      if (clearKey) loadOnlineState({ force: true });
    }
  }

  function renderDiscord() {
    const discord = state?.discord || {};
    const configured = discord.configured === true;
    renderDiscordStatus();
    if (!discordConfigDirty) {
      $("#discord-enabled").checked = typeof discord.enabled === "boolean" ? discord.enabled : configured;
      $("#discord-allow-tasks").checked = discord.allow_tasks === true;
      // Older local backends omit these safe identifiers. Leave the current
      // fields alone in that case so the setup guide still works in-session.
      for (const [key, selector] of [["application_id", "#discord-application"], ["channel_id", "#discord-channel"]]) {
        if (Object.prototype.hasOwnProperty.call(discord, key) && typeof discord[key] === "string") $(selector).value = discord[key];
      }
      if (Object.prototype.hasOwnProperty.call(discord, "user_ids") && Array.isArray(discord.user_ids)) $("#discord-users").value = discord.user_ids.filter((id) => typeof id === "string").join(", ");
    }
    updateDiscordInviteLink();
    updateDiscordSaveLabel();
  }

  function updateDiscordSaveLabel() {
    const enabled = $("#discord-enabled").checked;
    const connected = state?.discord?.enabled === true || state?.discord?.connected === true;
    $("#save-discord").textContent = enabled
      ? (connected ? "Save changes" : "Connect Discord")
      : (connected ? "Disconnect Discord" : "Save setup");
  }

  function updateDiscordInviteLink() {
    const link = $("#discord-invite-link");
    if (!link) return;
    const copyButton = $("#discord-copy-invite");
    const applicationID = $("#discord-application").value.trim();
    if (!/^\d{17,20}$/.test(applicationID)) {
      link.href = "#";
      link.setAttribute("aria-disabled", "true");
      link.setAttribute("tabindex", "-1");
      link.classList.add("disabled-link");
      if (copyButton) copyButton.disabled = true;
      return;
    }
    const invite = new URL("https://discord.com/oauth2/authorize");
    invite.search = new URLSearchParams({ client_id: applicationID, permissions: "117760", scope: "bot" }).toString();
    link.href = invite.href;
    link.removeAttribute("aria-disabled");
    link.removeAttribute("tabindex");
    link.classList.remove("disabled-link");
    if (copyButton) copyButton.disabled = false;
  }

  function stopDiscordStatusPoll() {
    discordStatusPollGeneration += 1;
    window.clearTimeout(discordStatusPollTimer);
    discordStatusPollTimer = 0;
    if (discordStatusPollController) discordStatusPollController.abort();
    discordStatusPollController = null;
  }

  function startDiscordStatusPoll() {
    stopDiscordStatusPoll();
    discordStatusPollTimedOut = false;
    renderDiscordStatus();
    if (!$("#discord-enabled").checked) return;
    const generation = discordStatusPollGeneration;
    const deadline = Date.now() + 30_000;

    const poll = async () => {
      if (generation !== discordStatusPollGeneration || !["settings", "discord"].includes(currentView) || !$("#discord-enabled").checked) {
        return stopDiscordStatusPoll();
      }
      if (Date.now() >= deadline) {
        discordStatusPollTimedOut = true;
        renderDiscordStatus();
        return stopDiscordStatusPoll();
      }
      const controller = new AbortController();
      discordStatusPollController = controller;
      let requestTimedOut = false;
      const timeoutMs = Math.max(1, Math.min(8_000, deadline - Date.now()));
      const requestTimeout = window.setTimeout(() => {
        requestTimedOut = true;
        controller.abort();
      }, timeoutMs);
      try {
        const result = await request("/state", { signal: controller.signal });
        if (generation !== discordStatusPollGeneration || !["settings", "discord"].includes(currentView)) return;
        if (result && result.discord && typeof result.discord === "object") {
          // Keep the refresh scoped to status so it cannot replace form drafts,
          // model selection, chat history, or the intentionally-cleared token field.
          state = state || {};
          state.discord = result.discord;
          renderDiscordStatus();
          const discord = result.discord;
          const status = typeof discord.status === "string" ? discord.status : "";
          if (discord.connected === true || discord.configured !== true || /^Connection stopped:|^Disconnected\b/.test(status)) {
            return stopDiscordStatusPoll();
          }
        }
      } catch (error) {
        if (generation !== discordStatusPollGeneration) return;
        if (error?.name === "AbortError" && !requestTimedOut) return;
      } finally {
        window.clearTimeout(requestTimeout);
        if (discordStatusPollController === controller) discordStatusPollController = null;
      }
      if (generation === discordStatusPollGeneration) {
        if (Date.now() >= deadline) {
          discordStatusPollTimedOut = true;
          renderDiscordStatus();
          stopDiscordStatusPoll();
        } else {
          discordStatusPollTimer = window.setTimeout(poll, 3_000);
        }
      }
    };

    discordStatusPollTimer = window.setTimeout(poll, 1_000);
  }

  function renderChats() {
    const list = $("#chat-list");
    list.replaceChildren();
    const chats = Array.isArray(state?.chats) ? state.chats : [];
    if (!chats.length) {
      const empty = document.createElement("div");
      empty.className = "chat-list-empty";
      empty.textContent = "Your chats will appear here.";
      list.append(empty);
      return;
    }
    for (const chat of chats) {
      const row = document.createElement("div");
      row.className = `chat-row${chat.id === activeChatId ? " selected" : ""}`;
      const open = document.createElement("button");
      open.type = "button";
      open.className = "chat-open";
      open.textContent = String(chat.title || "New conversation");
      open.title = open.textContent;
      open.addEventListener("click", () => selectChat(chat.id));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "chat-delete";
      remove.textContent = "×";
      remove.title = "Delete this chat";
      remove.setAttribute("aria-label", `Delete ${open.textContent}`);
      remove.addEventListener("click", (event) => { event.stopPropagation(); deleteChat(chat.id); });
      row.append(open, remove);
      list.append(row);
    }
  }

  function renderCurrentChat() {
    const messages = $("#messages");
    messages.replaceChildren();
    const chat = state?.chats?.find((item) => item.id === activeChatId);
    const items = Array.isArray(chat?.messages) ? chat.messages : [];
    $("#welcome-state").classList.toggle("hidden", items.length > 0);
    renderChatContext();
    for (const item of items) appendMessage(item.role, item.content, [], item.status);
    scrollToBottom();
  }

  function selectedChat() {
    return state?.chats?.find((item) => item.id === activeChatId) || null;
  }

  function renderChatContext() {
    const banner = $("#chat-context-banner");
    if (!banner) return;
    const context = selectedChat()?.app_context;
    const name = typeof context?.name === "string" ? context.name.trim().slice(0, 100) : "";
    const task = typeof context?.task === "string" ? context.task.trim().slice(0, 240) : "";
    const visible = Boolean(name && typeof context?.app === "string");
    banner.classList.toggle("hidden", !visible);
    if (!visible) return;
    $("#chat-context-name").textContent = name;
    $("#chat-context-task").textContent = task;
    const continueButton = $("#continue-app-context");
    const ready = capabilityFor("auto").available && !activeJob;
    continueButton.textContent = `Continue in ${name}`;
    continueButton.disabled = !ready;
    continueButton.title = ready ? `Continue the previous task in ${name}` : (activeJob ? "Wait for the active task to finish first." : "A local model is required to continue this task.");
  }

  async function continueAppContext() {
    if (!selectedChat()?.app_context || !activeChatId) return;
    if (activeJob) { showToast("Wait for the active task to finish before continuing this app context."); return; }
    if (!capabilityFor("auto").available) { showToast("A local model is required to continue this task."); return; }
    const input = $("#composer-input");
    if (input.value.trim() || attachments.length) {
      showToast("Send or clear your draft and attachments before continuing the previous task.");
      input.focus();
      return;
    }
    if (currentMode !== "auto") chooseMode("auto");
    if (currentMode !== "auto") return;
    const askEach = $("#automation-approval input[name='automation-policy'][value='ask_each']");
    const singleApp = $("#automation-scope input[name='automation-scope'][value='single_app']");
    if (askEach) askEach.checked = true;
    if (singleApp) singleApp.checked = true;
    await sendMessage("Continue the previous task");
  }

  async function clearAppContext() {
    if (!activeChatId || !selectedChat()?.app_context) return;
    const button = $("#clear-app-context");
    button.disabled = true;
    try {
      await post("/chat-context", { chat_id: activeChatId, clear: true });
      const chat = selectedChat();
      if (chat) chat.app_context = null;
      renderChatContext();
      showToast("App context cleared. This chat will continue as general chat.");
    } catch (error) {
      showToast(error.message || "Could not clear this chat’s app context.");
    } finally {
      button.disabled = false;
    }
  }

  function appendMessage(role, content, files = [], taskStatus = "failed") {
    const wrapper = document.createElement("article");
    const normalizedRole = String(role || "").toLowerCase();
    const isUser = ["user", "you", "human"].includes(normalizedRole);
    const isTaskStatus = normalizedRole === "task_status";
    const wrapperClass = isUser ? "user" : (isTaskStatus ? "task-status" : "assistant");
    wrapper.className = `message ${wrapperClass}`;
    const avatar = document.createElement("div");
    avatar.className = "message-avatar";
    avatar.textContent = isUser ? "Y" : (isTaskStatus ? "!" : "M");
    const body = document.createElement("div");
    body.className = "message-body";
    const label = document.createElement("p");
    label.className = "message-role";
    const statusLabel = String(taskStatus || "failed").toLowerCase();
    label.textContent = isUser ? "You" : (isTaskStatus ? `Task ${statusLabel === "stopped" ? "stopped" : "failed"}` : "Mavi");
    const text = document.createElement("p");
    text.className = "message-content";
    text.textContent = typeof content === "string" ? content : String(content ?? "");
    body.append(label, text);
    if (files.length) {
      const tray = document.createElement("div");
      tray.className = "message-attachments";
      for (const file of files) {
        const pill = document.createElement("span");
        pill.className = "attachment-pill";
        pill.textContent = file.name;
        tray.append(pill);
      }
      body.append(tray);
    }
    wrapper.append(avatar, body);
    $("#messages").append(wrapper);
    $("#welcome-state").classList.add("hidden");
    scrollToBottom();
    return text;
  }

  function showWelcomeIfEmpty() {
    const chat = state?.chats?.find((item) => item.id === activeChatId);
    const hasMessages = (chat?.messages || []).length > 0 || $("#messages").children.length > 0;
    $("#welcome-state").classList.toggle("hidden", hasMessages);
  }

  function scrollToBottom() {
    const scroller = $("#message-scroll");
    requestAnimationFrame(() => { scroller.scrollTop = scroller.scrollHeight; });
  }

  function selectChat(id) {
    activeChatId = id;
    localStorage.setItem("mavi-active-chat", id);
    if (activeJob?.chat_id !== id) renderJobArtifacts([]);
    if (activeJob?.chat_id !== id) renderAgentMap([]);
    currentView = "chat";
    setView("chat");
    renderChats();
    renderCurrentChat();
    closeMobileSidebar();
  }

  function newChat() {
    activeChatId = null;
    localStorage.setItem("mavi-active-chat", "__new__");
    renderJobArtifacts([]);
    renderAgentMap([]);
    currentView = "chat";
    setView("chat");
    $("#messages").replaceChildren();
    $("#welcome-state").classList.remove("hidden");
    closeMobileSidebar();
    $("#composer-input").focus();
  }

  function setView(name) {
    if (!["settings", "discord"].includes(name)) stopDiscordStatusPoll();
    currentView = name;
    $("#chat-view").classList.toggle("hidden", name !== "chat");
    $("#gallery-view").classList.toggle("hidden", name !== "gallery");
    $("#settings-view").classList.toggle("hidden", name !== "settings");
    $("#discord-view").classList.toggle("hidden", name !== "discord");
    $$(".nav-item[data-view]").forEach((button) => button.classList.toggle("active", button.dataset.view === name));
    closeWorkspaceMenu();
    if (name === "settings") renderSettings();
    if (name === "discord") renderDiscord();
    if (name === "gallery") loadGallery();
    if (name === "settings" || name === "discord") {
      const section = document.getElementById(name === "settings" ? "settings-view" : "discord-view");
      section?.focus({ preventScroll: true });
    }
  }

  function renderSettings() {
    $("#settings-profile").value = String(state?.profile || "").slice(0, maxProfileChars);
    localProfileDraft = $("#settings-profile").value;
    updateProfileCount();
    $("#adapter-status").textContent = "No adapter training starts automatically. Use a separate explicit local action if training is enabled.";
    renderDiscord();
    renderOnline(state?.online);
    loadOnlineState();
    $("#project-path").value = String(state?.settings?.project_path || "");
    applyTheme(state?.settings?.theme || theme);
    renderModelStatus();
  }

  function updateProfileCount() {
    const text = $("#settings-profile").value;
    $("#profile-count").textContent = `${text.length.toLocaleString()} / ${maxProfileChars.toLocaleString()} characters`;
  }

  async function savePreferences(profile, onboarded = Boolean(state?.settings?.onboarded)) {
    const modelControl = $("#model-select");
    const modelChoice = modelControl.options.length ? modelControl.value : (state?.settings?.model || "");
    const payload = {
      profile: String(profile || "").slice(0, maxProfileChars),
      model: modelChoice,
      theme,
      onboarded
    };
    const projectPath = $("#project-path")?.value.trim() || state?.settings?.project_path || "";
    if (projectPath) payload.project_path = projectPath;
    await post("/preferences", payload);
    localStorage.setItem("mavi-onboarded", onboarded ? "1" : "0");
    if (state) {
      state.profile = payload.profile;
      state.settings = { ...(state.settings || {}), model: payload.model, theme, onboarded, ...(payload.project_path ? { project_path: payload.project_path } : {}) };
    }
    renderModelStatus();
  }

  async function loadState({ syncJob = true, first = false } = {}) {
    try {
      const result = await request("/state");
      if (!result || typeof result !== "object") throw new Error("Local backend returned an invalid workspace state.");
      state = result;
      theme = result.settings?.theme || theme;
      applyTheme(theme);
      const chats = Array.isArray(state.chats) ? state.chats : [];
      if (activeChatId && !chats.some((chat) => chat.id === activeChatId)) activeChatId = null;
      if (first) {
        const savedChat = localStorage.getItem("mavi-active-chat");
        if (savedChat === "__new__") activeChatId = null;
        else if (savedChat && chats.some((chat) => chat.id === savedChat)) activeChatId = savedChat;
        else if (!activeChatId && chats.length) activeChatId = chats[0].id;
        localStorage.setItem("mavi-active-chat", activeChatId || "__new__");
      }
      renderChats();
      if (first || !$("#messages").children.length) renderCurrentChat();
      discordStatusPollTimedOut = false;
      renderDiscord();
      if (result.online) renderOnline(result.online);
      renderModelStatus();
      renderWorkspaceMenu();
      setConnection(true, "Connected to local Mavi");
      if (syncJob) syncActiveJob(state.active_job);
      if (first) maybeShowOnboarding();
      return true;
    } catch (error) {
      setConnection(false, "Local backend unavailable");
      $("#model-indicator").lastElementChild.textContent = "Waiting for local backend";
      showToast(error.message || "Could not connect to the local backend.");
      return false;
    }
  }

  function maybeShowOnboarding() {
    const serverFlag = state?.settings?.onboarded;
    const localFlag = localStorage.getItem("mavi-onboarded") === "1";
    if (serverFlag === true || (serverFlag == null && localFlag)) return;
    const dialog = $("#onboarding-dialog");
    if (!dialog.open) dialog.showModal();
  }

  function syncActiveJob(job) {
    if (!job || !job.id) {
      if (!activeJob) renderJob(null);
      return;
    }
    if (!activeJob || activeJob.id !== job.id) {
      activeJob = { id: job.id, chat_id: job.chat_id || activeChatId, content: "", status: job.status || "working", progress: job.progress, mode: job.mode || "chat", streamNode: null };
      renderModelStatus();
    }
    renderJob(job);
    pollJob();
  }

  function renderJob(job) {
    const card = $("#job-card");
    const track = $("#job-progress-track");
    const bar = $("#job-progress-bar");
    const spinner = $(".job-spinner", card);
    if (!job) {
      renderJobInference(null);
      renderJobQuestion(null);
      card.classList.add("hidden");
      card.classList.remove("image-generating");
      track.classList.remove("is-indeterminate");
      bar.style.width = "0";
      spinner.classList.add("hidden");
      return;
    }
    renderJobInference(job.inference);
    card.classList.remove("hidden");
    const stopping = Boolean(activeJob?.stopping) || String(job.status || "").toLowerCase() === "stopping";
    const status = stopping ? "Stopping" : String(job.status || "working").replaceAll("_", " ");
    $("#job-status").textContent = status.charAt(0).toUpperCase() + status.slice(1);
    const active = !stopping && ["queued", "running", "working"].includes(String(job.status || "working").toLowerCase());
    const waiting = Boolean(job.question || job.requires_approval) || String(job.status || "").toLowerCase() === "waiting";
    const imageMode = ["image", "images"].includes(String(job.mode || activeJob?.mode || "").toLowerCase());
    card.classList.toggle("image-generating", active && !waiting && imageMode);
    spinner.classList.toggle("hidden", !active || waiting);

    const actualProgress = typeof job.progress_percent === "number" ? job.progress_percent :
      (typeof job.progress === "number" ? job.progress : null);
    const hasProgress = typeof actualProgress === "number" && Number.isFinite(actualProgress);
    track.classList.toggle("is-indeterminate", active && !waiting && !hasProgress);
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", "100");
    if (hasProgress) {
      const boundedProgress = Math.max(0, Math.min(100, actualProgress));
      bar.style.width = `${boundedProgress}%`;
      track.setAttribute("aria-valuenow", String(boundedProgress));
    } else {
      bar.style.width = "0";
      track.removeAttribute("aria-valuenow");
    }

    let progressText = stopping ? "Waiting for the local task to stop…" :
      (typeof job.progress === "string" ? job.progress : "Mavi is working on your request.");
    const elapsed = typeof job.elapsed === "number" && Number.isFinite(job.elapsed) && job.elapsed >= 0 ? Math.round(job.elapsed) :
      (active && !waiting && !stopping && typeof job.started === "number" && Number.isFinite(job.started) ?
        Math.max(0, Math.floor(Date.now() / 1000 - job.started)) : null);
    const terminalStatus = String(job.status || "").toLowerCase();
    if (elapsed !== null && ["complete", "completed", "done", "success", "succeeded"].includes(terminalStatus)) {
      progressText += ` · finished in ${elapsed}s`;
    } else if (elapsed !== null && ["failed", "error", "cancelled", "canceled", "stopped"].includes(terminalStatus)) {
      progressText += ` · after ${elapsed}s`;
    } else if (elapsed !== null && active && !waiting && !stopping) {
      progressText += ` · ${elapsed}s elapsed`;
    }
    if (imageMode && active && !waiting && !stopping && typeof job.eta_seconds === "number" && Number.isFinite(job.eta_seconds) && job.eta_seconds >= 0) {
      progressText += ` · about ${Math.ceil(job.eta_seconds)}s remaining`;
    }
    $("#job-progress").textContent = progressText;
    const jobIsInOpenChat = !job.chat_id || job.chat_id === activeChatId;
    updatePenguinStatus(jobIsInOpenChat ? job : null);
    renderJobArtifacts(jobIsInOpenChat && Array.isArray(job.artifacts) ? job.artifacts : []);
    if (jobIsInOpenChat) updateAgentMap(job);
    else renderAgentMap([]);
    renderJobQuestion(job);
  }

  function updatePenguinStatus(job) {
    const button = $("#team-toggle");
    const status = String(job?.status || "").toLowerCase();
    const state = job ? (job.question || job.requires_approval || status === "waiting" ? "waiting" : ["complete", "completed", "done", "success", "succeeded"].includes(status) ? "done" : "working") : "idle";
    button.dataset.state = state;
    button.title = state === "working" ? "Team activity · working" : state === "waiting" ? "Team activity · waiting for you" : state === "done" ? "Team activity · complete" : "Show team activity";
  }

  function setShowPenguin(visible) {
    showPenguin = Boolean(visible);
    try { localStorage.setItem(showPenguinStorageKey, showPenguin ? "1" : "0"); } catch { /* Keep the preference for this page session. */ }
    const companion = $("#penguin-companion");
    companion.classList.toggle("hidden", !showPenguin);
    $("#show-penguin-setting").checked = showPenguin;
    if (!showPenguin) {
      window.clearTimeout(penguinGreetingTimer);
      companion.classList.remove("is-greeting");
    }
  }

  function setShowTeam(visible, reveal = false) {
    showTeam = Boolean(visible);
    try { localStorage.setItem(showTeamStorageKey, showTeam ? "1" : "0"); } catch { /* Keep the preference for this page session. */ }
    const details = $("#agent-map");
    renderAgentMap([...agentEventByID.values()]);
    if (showTeam && details && reveal) {
      details.open = true;
      details.scrollIntoView({ behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches ? "auto" : "smooth", block: "nearest" });
    }
  }

  function updateAgentMap(job) {
    if (job?.id && agentEventJobId !== job.id) {
      agentEventJobId = job.id;
      agentEventByID = new Map();
    }
    if (Array.isArray(job?.agent_events)) {
      for (const event of job.agent_events) {
        if (!event || typeof event.agent_id !== "string" || !event.agent_id) continue;
        if (!agentEventByID.has(event.agent_id) && agentEventByID.size >= 4) continue;
        agentEventByID.set(event.agent_id, {
          agent_id: event.agent_id,
          parent_id: typeof event.parent_id === "string" ? event.parent_id : null,
          name: typeof event.name === "string" ? event.name.slice(0, 100) : "Role not reported",
          model: typeof event.model === "string" ? event.model.slice(0, 140) : "",
          task: typeof event.task === "string" ? event.task.slice(0, 280) : "",
          result: typeof event.result === "string" ? event.result.slice(0, 280) : "",
          status: ["pending", "running", "completed", "failed", "stopped"].includes(event.status) ? event.status : "pending",
          summary: typeof event.summary === "string" ? event.summary.slice(0, 280) : "",
          time: Number(event.time)
        });
      }
    }
    renderAgentMap([...agentEventByID.values()]);
  }

  function renderAgentMap(events) {
    const details = $("#agent-map");
    const nodes = $("#agent-map-nodes");
    nodes.replaceChildren();
    details.classList.toggle("hidden", !showTeam);
    $("#team-toggle").setAttribute("aria-pressed", String(showTeam));
    $("#team-toggle").setAttribute("aria-expanded", String(showTeam && !details.classList.contains("hidden") && details.open));
    $("#team-toggle").setAttribute("aria-label", `${showTeam ? "Hide" : "Show"} team activity`);
    $("#show-team-setting").checked = showTeam;
    $("#agent-count").textContent = events.length ? `${events.length} / 4 agents` : "Ready";
    const byID = new Map(events.map((event) => [event.agent_id, event]));
    const sorted = [...events].sort((a, b) => Number(Boolean(a.parent_id)) - Number(Boolean(b.parent_id)));
    for (const event of sorted) {
      const card = document.createElement("article");
      card.className = `agent-node${event.parent_id ? " agent-child" : " agent-root"}`;
      const top = document.createElement("div");
      top.className = "agent-node-top";
      const identity = document.createElement("div");
      identity.className = "agent-identity";
      const name = document.createElement("strong");
      name.textContent = event.model || "Model not reported";
      const role = document.createElement("span");
      role.textContent = `Assigned role: ${event.name}`;
      identity.append(name, role);
      const status = document.createElement("span");
      status.className = `agent-status status-${event.status}`;
      status.textContent = event.status === "pending" ? "waiting" : event.status;
      top.append(identity, status);
      card.append(top);
      if (event.task) {
        const task = document.createElement("p");
        task.className = "agent-task";
        task.textContent = `Task: ${event.task}`;
        card.append(task);
      }
      if (event.parent_id) {
        const parent = byID.get(event.parent_id);
        const relation = document.createElement("p");
        relation.className = "agent-parent";
        relation.textContent = `Reports to ${parent ? parent.name : `agent ${event.parent_id.slice(0, 8)}`}`;
        card.append(relation);
      }
      const updateText = event.result || event.summary;
      if (updateText) {
        const summary = document.createElement("p");
        summary.className = "agent-summary";
        const updateLabel = document.createElement("b");
        updateLabel.textContent = event.status === "completed" ? "Result" : "Progress";
        const update = document.createElement("span");
        update.textContent = updateText;
        summary.append(updateLabel, update);
        card.append(summary);
      }
      if (Number.isFinite(event.time) && event.time > 0 && event.time < 8_640_000_000_000) {
        const time = document.createElement("time");
        time.className = "agent-time";
        time.dateTime = new Date(event.time * 1000).toISOString();
        time.textContent = new Date(event.time * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
        card.append(time);
      }
      nodes.append(card);
    }
  }

  function verifiedArtifactURL(rawURL, name) {
    if (typeof rawURL !== "string" || !rawURL.startsWith("/api/artifact?")) return null;
    try {
      const url = new URL(rawURL, window.location.origin);
      if (url.origin !== window.location.origin || url.pathname !== "/api/artifact" || url.username || url.password || url.hash || [...url.searchParams.keys()].length !== 1) return null;
      if (url.searchParams.get("name") !== name) return null;
      return url.href;
    } catch { return null; }
  }

  function renderJobArtifacts(artifacts) {
    const container = $("#job-artifacts");
    container.replaceChildren();
    const safeItems = artifacts.slice(0, 12).filter((item) => item && typeof item.name === "string").map((item) => ({ item, url: verifiedArtifactURL(item.url, item.name) })).filter(({ url }) => url);
    container.classList.toggle("hidden", safeItems.length === 0);
    if (!safeItems.length) return;
    const heading = document.createElement("p");
    heading.className = "artifact-heading";
    heading.textContent = "Files from this task";
    container.append(heading);
    for (const { item, url } of safeItems) {
      const row = document.createElement("div");
      row.className = "artifact-row";
      const link = document.createElement("a");
      link.href = url;
      link.textContent = item.name;
      link.setAttribute("download", item.name);
      link.rel = "noopener";
      const size = document.createElement("span");
      size.textContent = item.size == null ? "Download" : formatSize(item.size);
      row.append(link, size);
      const extension = item.name.split(".").pop().toLowerCase();
      const mime = String(item.mime || item.content_type || "").toLowerCase();
      if (["png", "jpg", "jpeg"].includes(extension) && (!mime || imageMimes.has(mime))) {
        const preview = document.createElement("img");
        preview.className = "artifact-preview";
        preview.src = url;
        preview.alt = `Preview of ${item.name}`;
        preview.loading = "lazy";
        row.append(preview);
      }
      container.append(row);
    }
  }

  async function loadGallery() {
    const grid = $("#gallery-grid");
    grid.replaceChildren();
    const loading = document.createElement("p");
    loading.className = "gallery-empty";
    loading.textContent = "Loading local files…";
    grid.append(loading);
    try {
      const result = await request("/artifacts");
      galleryItems = Array.isArray(result) ? result : (Array.isArray(result?.artifacts) ? result.artifacts : []);
      renderGallery();
    } catch (error) {
      galleryItems = [];
      grid.replaceChildren();
      const message = document.createElement("p");
      message.className = "gallery-empty";
      message.textContent = error.message || "Could not load generated files.";
      grid.append(message);
    }
  }

  function renderGallery() {
    const grid = $("#gallery-grid");
    grid.replaceChildren();
    const safeItems = galleryItems.slice(0, 500).filter((item) => item && typeof item.name === "string")
      .map((item) => ({ item, url: verifiedArtifactURL(item.url, item.name) })).filter(({ url }) => url);
    $("#clear-gallery").disabled = safeItems.length === 0;
    if (!safeItems.length) {
      const empty = document.createElement("p");
      empty.className = "gallery-empty";
      empty.textContent = "No generated files yet. Files created by Mavi will appear here.";
      grid.append(empty);
      return;
    }
    for (const { item, url } of safeItems) {
      const card = document.createElement("article");
      card.className = "gallery-card";
      const extension = item.name.split(".").pop().toLowerCase();
      const mime = String(item.mime || item.content_type || "").toLowerCase();
      if (["png", "jpg", "jpeg"].includes(extension) && (!mime || imageMimes.has(mime))) {
        const image = document.createElement("img");
        image.className = "gallery-preview";
        image.src = url;
        image.alt = `Preview of ${item.name}`;
        image.loading = "lazy";
        card.append(image);
      } else {
        const icon = document.createElement("div");
        icon.className = "gallery-file-icon";
        icon.textContent = extension ? extension.slice(0, 6).toUpperCase() : "FILE";
        card.append(icon);
      }
      const name = document.createElement("a");
      name.className = "gallery-file-name";
      name.href = url;
      name.textContent = item.name;
      name.setAttribute("download", item.name);
      name.rel = "noopener";
      card.append(name);
      const meta = document.createElement("p");
      meta.className = "gallery-file-meta";
      meta.textContent = item.size == null ? extension.toUpperCase() : `${extension.toUpperCase()} · ${formatSize(item.size)}`;
      card.append(meta);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "quiet-button danger-button gallery-delete";
      remove.textContent = "Delete";
      remove.setAttribute("aria-label", `Delete ${item.name}`);
      remove.addEventListener("click", () => deleteGalleryFiles([item.name], false));
      card.append(remove);
      grid.append(card);
    }
  }

  async function deleteGalleryFiles(names, all) {
    const cleanNames = [...new Set(names.filter((name) => typeof name === "string"))];
    if (!cleanNames.length) return;
    const confirmation = all ? `Delete all ${cleanNames.length} generated files from this device? This cannot be undone.` : `Delete “${cleanNames[0]}” from this device? This cannot be undone.`;
    if (!window.confirm(confirmation)) return;
    try {
      await post("/artifacts/delete", { names: cleanNames });
      showToast(all ? "Generated files deleted from this device." : "Generated file deleted from this device.");
      await loadGallery();
      await loadState({ syncJob: false });
    } catch (error) { showToast(error.message || "Could not delete generated files."); }
  }

  function renderJobQuestion(job) {
    const container = $("#job-question");
    const question = job?.question;
    const prompt = typeof question === "string" ? question :
      (typeof question?.text === "string" ? question.text : (typeof question?.prompt === "string" ? question.prompt : ""));
    const requiresApproval = Boolean(job?.requires_approval);
    const appAccessApproval = /^Allow Mavi to (?:read and control|read the window|open or bring|capture)\b/i.test(prompt) ||
      /^Mavi will read and operate\b[\s\S]*\bAllow this task\?$/i.test(prompt);
    const explicitActionApproval = /^(?:Mavi wants to|Mavi is about to|Scroll\b|Press\b)[\s\S]*\bAllow this (?:action|click|text|step)\?/i.test(prompt);
    const showApprovalButtons = requiresApproval || appAccessApproval || explicitActionApproval;
    if (!prompt && !showApprovalButtons) {
      container.classList.add("hidden");
      container.replaceChildren();
      delete container.dataset.questionKey;
      return;
    }
    const jobID = typeof job?.id === "string" ? job.id : (typeof activeJob?.id === "string" ? activeJob.id : "");
    const questionKey = JSON.stringify([jobID, prompt, showApprovalButtons]);
    container.classList.remove("hidden");
    if (container.dataset.questionKey === questionKey) return;
    container.dataset.questionKey = questionKey;
    container.replaceChildren();
    const p = document.createElement("p");
    p.textContent = String(prompt || "Mavi is asking for your approval before continuing.");
    container.append(p);
    const actions = document.createElement("div");
    actions.className = "job-question-actions";
    if (showApprovalButtons) {
      const deny = document.createElement("button");
      deny.className = "quiet-button"; deny.type = "button"; deny.textContent = "Cancel task";
      deny.addEventListener("click", () => answerJob("no"));
      const approve = document.createElement("button");
      approve.className = "primary-button"; approve.type = "button"; approve.textContent = "Allow";
      approve.addEventListener("click", () => answerJob("yes"));
      actions.append(deny, approve);
    } else {
      const input = document.createElement("input");
      input.type = "text"; input.placeholder = "Your answer"; input.setAttribute("aria-label", "Answer Mavi's question");
      input.addEventListener("keydown", (event) => { if (event.key === "Enter") { event.preventDefault(); answerJob(input.value); } });
      const send = document.createElement("button");
      send.className = "primary-button"; send.type = "button"; send.textContent = "Send answer";
      send.addEventListener("click", () => answerJob(input.value));
      actions.append(input, send);
    }
    container.append(actions);
  }

  function pollJob() {
    if (!activeJob || activeJob.polling) return;
    activeJob.polling = true;
    window.setTimeout(async () => {
      const jobID = activeJob?.id;
      if (!jobID) return;
      try {
        const result = await request(`/job?id=${encodeURIComponent(jobID)}`);
        if (!activeJob || activeJob.id !== jobID) return;
        activeJob.polling = false;
        activeJob.status = result.status || activeJob.status;
        activeJob.progress = result.progress;
        activeJob.mode = result.mode || activeJob.mode || "chat";
        if (typeof result.content === "string" && result.content) updateStream(result.content);
        renderJob({ ...result, requires_approval: result.requires_approval === true, question: result.question });
        const status = String(result.status || "").toLowerCase();
        if (["complete", "completed", "done", "success", "succeeded"].includes(status)) {
          if (!activeJob.streamNode && result.content) updateStream(result.content);
          if (!activeJob.streamNode && result.content) activeJob.streamNode = true;
          activeJob = null;
          $("#job-card").classList.add("hidden");
          renderModelStatus();
          await loadState({ syncJob: false });
          renderChats();
          return;
        }
        if (["failed", "error", "cancelled", "canceled", "stopped"].includes(status)) {
          const message = result.error || result.content || `Task ${status}.`;
          activeJob = null;
          $("#job-card").classList.add("hidden");
          renderModelStatus();
          showToast(message);
          await loadState({ syncJob: false });
          if (activeChatId === result.chat_id) renderCurrentChat();
          return;
        }
        pollJob();
      } catch (error) {
        if (activeJob?.id === jobID) {
          activeJob.polling = false;
          $("#job-progress").textContent = error.message || "Could not refresh task status.";
          window.setTimeout(pollJob, 2500);
        }
      }
    }, 1100);
  }

  function updateStream(content) {
    if (!activeJob) return;
    const text = String(content);
    if (!activeJob.streamNode) activeJob.streamNode = appendMessage("assistant", text);
    else if (activeJob.streamNode instanceof HTMLElement) activeJob.streamNode.textContent = text;
    activeJob.content = text;
  }

  async function answerJob(answer) {
    if (!activeJob || !String(answer).trim()) return;
    try {
      await post("/answer", { job_id: activeJob.id, answer: String(answer).trim() });
      $("#job-question").classList.add("hidden");
      showToast("Answer sent to the local task.");
      pollJob();
    } catch (error) { showToast(error.message || "Could not send that answer."); }
  }

  async function stopJob() {
    if (!activeJob) return;
    try {
      await post("/stop", { job_id: activeJob.id });
      activeJob.status = "stopping";
      activeJob.stopping = true;
      renderJob({ ...activeJob, progress: "Waiting for the local task to stop…" });
    } catch (error) { showToast(error.message || "Could not stop the task."); }
  }

  async function steerJob(text) {
    if (!activeJob) return false;
    const jobID = activeJob.id;
    await post("/steer", { job_id: jobID, text });
    appendMessage("user", `Steer task: ${text}`);
    showToast("Steering sent to the active local task.");
    return true;
  }

  function showMaviSurprise(displayText) {
    if (document.querySelector(".mavi-surprise")) return;
    const returnFocus = document.activeElement;
    const dialog = document.createElement("dialog");
    dialog.className = "mavi-surprise";
    dialog.setAttribute("aria-label", displayText);
    const hearts = document.createElement("div");
    hearts.className = "mavi-surprise-hearts";
    hearts.setAttribute("aria-hidden", "true");
    // Bounded, one-shot CSS animation; no render loop, sound, or model call.
    // Keep the hearts visible without movement when reduced motion is enabled.
    for (let index = 0; index < 108; index += 1) {
      const heart = document.createElement("span");
      heart.textContent = "♥";
      heart.style.setProperty("--delay", `${(index % 6) * .045}s`);
      heart.style.setProperty("--tilt", `${(index * 19) % 31 - 15}deg`);
      heart.style.setProperty("--scale", `${.8 + (index % 5) * .12}`);
      heart.style.setProperty("--alpha", `${.6 + (index % 4) * .1}`);
      hearts.append(heart);
    }
    const message = document.createElement("div");
    message.className = "mavi-surprise-message";
    const overline = document.createElement("p");
    overline.textContent = "A LITTLE SOMETHING, JUST FOR YOU";
    const title = document.createElement("h1");
    title.textContent = displayText;
    const close = document.createElement("button");
    close.className = "quiet-button";
    close.textContent = "Close";
    message.append(overline, title, close);
    dialog.append(hearts, message);
    document.body.append(dialog);
    const dismiss = () => dialog.close();
    const timer = window.setTimeout(dismiss, 8500);
    close.addEventListener("click", dismiss);
    dialog.addEventListener("close", () => {
      window.clearTimeout(timer);
      dialog.remove();
      if (returnFocus?.isConnected) returnFocus.focus();
    }, { once: true });
    dialog.showModal();
  }

  async function sendMessage(messageOverride = null) {
    const input = $("#composer-input");
    const text = typeof messageOverride === "string" ? messageOverride.trim() : input.value.trim();
    if (!text && !attachments.length) return;
    if (recording) { showToast("Stop the microphone recording before sending."); return; }
    if (sendPending) return;
    // The author explicitly chose to ship this Easter egg in every public package.
    // It is UI-only: no model call, saved chat, or private setup file is needed.
    const personalTouch = state?.settings?.personal_touch;
    const builtInTouch = { trigger: "I love Abdullah", message: "I love Muzhda" };
    const surprise = [personalTouch, builtInTouch].find((touch) =>
      touch?.trigger && touch?.message && normalizedTouchText(text) === normalizedTouchText(touch.trigger));
    if (surprise) {
      input.value = "";
      resizeComposer();
      renderModelStatus();
      showMaviSurprise(surprise.message);
      return;
    }
    const capability = capabilityFor(currentMode);
    if (!capability.available) { showToast(capability.reason || "This workspace is not ready."); return; }
    sendPending = true;
    renderModelStatus();
    if (activeJob) {
      try {
        if (activeJob.mode === "auto") { showToast("Mavi is choosing the right local workspace. Wait a moment before sending a follow-up."); return; }
        if (activeJob.mode && !["chat", "browser", "computer"].includes(activeJob.mode)) { showToast("A task is already running. Stop it before starting another task."); return; }
        if (!text) { showToast("Add a short instruction to steer the active task."); return; }
        if (attachments.length) { showToast("Active task steering accepts text only. Keep the attachments for your next task."); return; }
        await steerJob(text); input.value = ""; resizeComposer();
      } catch (error) { showToast(error.message || "Could not steer the active task."); }
      finally { sendPending = false; renderModelStatus(); }
      return;
    }
    const requestText = text || (currentMode === "dictation" ? "Please transcribe the attached audio recording." : "Please review the attached files.");
    const wasNew = !activeChatId;
    let draftChatID = null;
    try {
      const sentFiles = attachments.map(({ name, text: content, data_base64, mime }) => ({ name, ...(typeof content === "string" ? { text: content } : {}), ...(data_base64 ? { data_base64, mime } : {}) }));
      appendMessage("user", requestText, attachments);
      if (wasNew) {
        const optimistic = { id: `pending-${Date.now()}`, title: (requestText || attachments[0]?.name || "New conversation").slice(0, 70), messages: [] };
        activeChatId = optimistic.id;
        state = state || {};
        state.chats = [optimistic, ...(state.chats || [])];
      }
      draftChatID = activeChatId;
      input.value = ""; resizeComposer(); clearAttachments(); renderChats();
      const scopeControl = $("#automation-scope");
      const scopeChoice = scopeControl && !scopeControl.classList.contains("hidden")
        ? (scopeControl.querySelector('input[name="automation-scope"]:checked')?.value || "single_app")
        : null;
      const payload = { chat_id: wasNew ? null : draftChatID, text: requestText, attachments: sentFiles, mode: apiModeFor(currentMode), automation_policy: automationPolicyChoice };
      if (scopeChoice) payload.automation_scope = scopeChoice;
      const result = await post("/chat", payload);
      if (result.chat_id) {
        activeChatId = result.chat_id;
        localStorage.setItem("mavi-active-chat", result.chat_id);
        const optimistic = state?.chats?.find((chat) => chat.id === draftChatID);
        if (optimistic) optimistic.id = result.chat_id;
      }
      if (!result.job_id) throw new Error("The local backend accepted no job ID; nothing can be tracked yet.");
      if (scopeControl) {
        const singleApp = scopeControl.querySelector('input[name="automation-scope"][value="single_app"]');
        if (singleApp) singleApp.checked = true;
      }
      activeJob = { id: result.job_id, chat_id: result.chat_id || activeChatId, status: "working", mode: apiModeFor(currentMode), streamNode: null, content: "", polling: false };
      renderModelStatus();
      renderChats();
      renderJob({ status: "working", mode: activeJob.mode });
      pollJob();
    } catch (error) {
      if (wasNew && draftChatID) {
        state.chats = (state.chats || []).filter((chat) => chat.id !== draftChatID);
        activeChatId = null;
      }
      renderChats(); showWelcomeIfEmpty();
      showToast(error.message || "Could not send the request to the local backend.");
    } finally { sendPending = false; renderModelStatus(); }
  }

  async function handleAttachmentFiles(files) {
    for (const file of files) {
      if (attachments.length >= 6) { showToast("Attach up to 6 files per message."); break; }
      const extension = file.name.split(".").pop().toLowerCase();
      const isText = textExtensions.has(extension);
      const isImage = imageMimes.has(file.type) && ["png", "jpg", "jpeg"].includes(extension);
      const isWav = wavMimes.has(file.type) && extension === "wav";
      const allowedDocumentMimes = documentMimes.get(extension);
      const isDocument = Boolean(allowedDocumentMimes && allowedDocumentMimes.has(file.type));
      if (!isText && !isImage && !isWav && !isDocument) { showToast(`${file.name}: choose supported text, PNG/JPEG, WAV, PDF, DOCX, XLSX, or PPTX files.`); continue; }
      if (isDocument && !documentModes.has(currentMode)) { showToast(`${file.name}: switch to Automatic, Files, Developer, or Agent team to attach documents.`); continue; }
      if (file.size > maxAttachmentBytes) { showToast(`${file.name}: keep each attachment under 10 MB.`); continue; }
      if (attachments.reduce((sum, item) => sum + (item.size || 0), 0) + file.size > maxAttachmentBytes) { showToast("Keep all attachments under 10 MB total."); continue; }
      try {
        if (isText) {
          const content = await file.text();
          if (content.includes("\u0000")) { showToast(`${file.name}: the file does not appear to be plain text.`); continue; }
          if (content.length > 50_000) { showToast(`${file.name}: text attachments are limited to 50,000 characters.`); continue; }
          attachments.push({ name: file.name, text: content, size: file.size });
        } else {
          const dataURL = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(String(reader.result || ""));
            reader.onerror = () => reject(new Error("File read failed"));
            reader.readAsDataURL(file);
          });
          const encoded = String(dataURL).split(",", 2)[1] || "";
          attachments.push({ name: file.name, mime: file.type, data_base64: encoded, size: file.size });
        }
      } catch { showToast(`Could not read ${file.name}.`); }
    }
    $("#attachment-input").value = "";
    renderAttachments();
  }

  function renderAttachments() {
    const strip = $("#attachment-strip");
    strip.replaceChildren();
    strip.classList.toggle("hidden", attachments.length === 0);
    attachments.forEach((file, index) => {
      const pill = document.createElement("div"); pill.className = "queued-file";
      const name = document.createElement("span"); name.textContent = file.name;
      const remove = document.createElement("button"); remove.type = "button"; remove.textContent = "×"; remove.setAttribute("aria-label", `Remove ${file.name}`);
      remove.addEventListener("click", () => { attachments.splice(index, 1); renderAttachments(); });
      pill.append(name, remove); strip.append(pill);
    });
  }

  function clearAttachments() { attachments = []; renderAttachments(); }

  function resizeComposer() {
    const input = $("#composer-input");
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  }

  async function deleteChat(chatID) {
    const chat = state?.chats?.find((item) => item.id === chatID);
    if (!window.confirm(`Delete “${chat?.title || "this chat"}” from this local workspace?`)) return;
    try {
      await post("/chat/delete", { chat_id: chatID });
      if (activeChatId === chatID) {
        activeChatId = null;
        localStorage.setItem("mavi-active-chat", "__new__");
      }
      await loadState({ syncJob: false });
      renderCurrentChat();
      showToast("Chat deleted from this workspace.");
    } catch (error) { showToast(error.message || "Could not delete this chat."); }
  }

  async function clearHistory() {
    if (!window.confirm("Clear all chats from this local workspace? This cannot be undone.")) return;
    try {
      await post("/clear-history", {});
      activeChatId = null;
      localStorage.setItem("mavi-active-chat", "__new__");
      await loadState({ syncJob: false });
      renderCurrentChat();
      showToast("Chat history cleared.");
    } catch (error) { showToast(error.message || "Could not clear chat history."); }
  }

  async function saveSettingsProfile() {
    const value = $("#settings-profile").value;
    if (value.length > maxProfileChars) { showToast("Keep saved preferences under 4,500 characters."); return; }
    try { await savePreferences(value, Boolean(state?.settings?.onboarded)); showToast("Local preferences saved."); }
    catch (error) { showToast(error.message || "Could not save preferences."); }
  }

  async function saveDiscordSettings() {
    const tokenField = $("#discord-token");
    const feedback = $("#discord-setup-feedback");
    const fail = (message, selector) => {
      feedback.textContent = message;
      feedback.classList.add("is-error");
      $(selector)?.focus();
    };
    const channelID = $("#discord-channel").value.trim();
    const users = $("#discord-users").value.split(/[\n,]+/).map((item) => item.trim()).filter(Boolean);
    const applicationID = $("#discord-application").value.trim();
    const enabled = $("#discord-enabled").checked;
    const token = tokenField.value.trim();
    if (applicationID && !/^\d{17,20}$/.test(applicationID)) {
      fail("That application ID should contain 17–20 digits. You can leave it blank if your bot is already invited.", "#discord-application");
      return;
    }
    if (enabled && !/^\d{17,20}$/.test(channelID)) {
      fail("Add the 17–20 digit ID for your private Discord channel.", "#discord-channel");
      return;
    }
    if (enabled && (!users.length || users.some((id) => !/^\d{17,20}$/.test(id)))) {
      fail("Add at least one valid 17–20 digit Discord user ID. Separate multiple IDs with commas or new lines.", "#discord-users");
      return;
    }
    const discord = state?.discord || {};
    const tokenRequired = enabled && discord.connected !== true && (
      discord.configured !== true || /enter bot token/i.test(String(discord.status || ""))
    );
    if (tokenRequired && !token) {
      fail("Paste your bot token to connect. Mavi keeps it in memory on this device only.", "#discord-token");
      return;
    }
    const payload = { application_id: applicationID, channel_id: channelID, user_ids: users, enabled, allow_tasks: $("#discord-allow-tasks").checked };
    if (token) payload.token = token;
    stopDiscordStatusPoll();
    feedback.classList.remove("is-error");
    feedback.textContent = "Saving your connection settings…";
    const saveButton = $("#save-discord");
    saveButton.disabled = true;
    saveButton.setAttribute("aria-busy", "true");
    // Clear as soon as the validated credential is about to be sent locally.
    tokenField.value = "";
    try {
      await post("/discord", payload);
      discordConfigDirty = false;
      await loadState({ syncJob: false });
      if ($("#discord-enabled").checked) startDiscordStatusPoll();
      feedback.classList.remove("is-error");
      feedback.textContent = payload.enabled
        ? "Settings saved. Connecting… Keep Mavi open and your computer awake. You’ll need to enter the token again after a restart."
        : "Settings saved on this device. Discord is off until you connect it.";
    } catch (_error) {
      feedback.classList.add("is-error");
      feedback.textContent = "Could not connect. Check the token, channel ID, user ID, and internet connection, then enter the token and try again.";
    } finally {
      saveButton.disabled = false;
      saveButton.removeAttribute("aria-busy");
      if (token) delete payload.token;
    }
  }

  async function copyDiscordInvite() {
    const link = $("#discord-invite-link");
    const feedback = $("#discord-setup-feedback");
    if (!link || link.getAttribute("aria-disabled") === "true") return;
    try {
      await navigator.clipboard.writeText(link.href);
      feedback.classList.remove("is-error");
      feedback.textContent = "Invite link copied. Open it to add your bot to your server.";
    } catch (_error) {
      feedback.classList.add("is-error");
      feedback.textContent = "Could not copy the link. Use Open bot invite instead.";
    }
  }

  function setOnboardingError(message) {
    const error = $("#onboarding-error");
    error.textContent = message;
    error.classList.toggle("hidden", !message);
  }

  async function completeCleanOnboarding() {
    try {
      await savePreferences("", true);
      showDiscordOnboarding();
    } catch (error) { setOnboardingError(error.message || "Could not save onboarding choice."); }
  }

  async function completePersonalOnboarding() {
    const profile = $("#onboarding-profile").value.trim();
    if (!profile) { setOnboardingError("Add or import a preference summary, or choose Continue clean."); return; }
    if (profile.length > maxProfileChars) { setOnboardingError("Keep the summary at or under 4,500 characters."); return; }
    try {
      await savePreferences(profile, true);
      showDiscordOnboarding();
    } catch (error) { setOnboardingError(error.message || "Could not save local preferences."); }
  }

  async function importOnboardingProfile(file) {
    setOnboardingError("");
    if (!file) return;
    const extension = file.name.split(".").pop().toLowerCase();
    if (!["txt", "md"].includes(extension)) { setOnboardingError("Choose a .txt or .md file."); return; }
    if (file.size > maxProfileBytes) { setOnboardingError("Choose a file no larger than 32 KB."); return; }
    try {
      const bytes = await file.arrayBuffer();
      if (bytes.byteLength > maxProfileBytes) { setOnboardingError("The file grew beyond the 32 KB limit. Choose a smaller text file."); return; }
      const text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      $("#onboarding-profile").value = text;
      $("#import-name").textContent = file.name;
      updateOnboardingCount();
      if (text.length > maxProfileChars) setOnboardingError("The preview is longer than 4,500 characters. Edit it before saving.");
    } catch { setOnboardingError("The selected file must be UTF-8 text."); }
  }

  function updateOnboardingCount() {
    const value = $("#onboarding-profile").value;
    $("#onboarding-char-count").textContent = `${value.length.toLocaleString()} / ${maxProfileChars.toLocaleString()}`;
    $("#save-preferences").disabled = !value.trim() || value.length > maxProfileChars;
    if (value.length <= maxProfileChars && $("#onboarding-error").textContent.includes("4,500 characters")) setOnboardingError("");
  }

  function openPersonalizeStep() {
    $("#onboarding-clean-step").classList.add("hidden");
    $("#onboarding-personalize-step").classList.remove("hidden");
    $("#onboarding-dialog").setAttribute("aria-labelledby", "onboarding-personalize-title");
    $("#onboarding-profile").focus();
  }

  function showDiscordOnboarding() {
    $("#onboarding-clean-step").classList.add("hidden");
    $("#onboarding-personalize-step").classList.add("hidden");
    $("#onboarding-discord-step").classList.remove("hidden");
    $("#onboarding-dialog").setAttribute("aria-labelledby", "onboarding-discord-title");
    const dialog = $("#onboarding-dialog");
    if (!dialog.open) dialog.showModal();
  }

  function finishDiscordOnboarding(openSetup = false) {
    const dialog = $("#onboarding-dialog");
    if (dialog.open) dialog.close();
    if (openSetup) setView("discord");
  }

  function openCleanStep() {
    $("#onboarding-personalize-step").classList.add("hidden");
    $("#onboarding-clean-step").classList.remove("hidden");
    $("#onboarding-dialog").setAttribute("aria-labelledby", "onboarding-title");
    setOnboardingError("");
  }

  async function saveSelectedModel() {
    try { await savePreferences(state?.profile || "", Boolean(state?.settings?.onboarded)); showToast("Preferred local model saved."); }
    catch (error) { showToast(error.message || "Could not save model preference."); }
  }

  async function saveProjectPath() {
    const path = $("#project-path").value.trim();
    if (!path) { showToast("Enter an existing local project folder path."); return; }
    try {
      const modelControl = $("#model-select");
      await post("/preferences", { profile: String(state?.profile || "").slice(0, maxProfileChars), model: modelControl.value || state?.settings?.model || "", theme, onboarded: Boolean(state?.settings?.onboarded), project_path: path });
      if (state) state.settings = { ...(state.settings || {}), project_path: path };
      showToast("Developer folder saved on this device.");
    } catch (error) { showToast(error.message || "Could not save the project folder."); }
  }

  function elapsedLabel(milliseconds) {
    const seconds = Math.max(0, Math.floor(milliseconds / 1000));
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function refreshRecordingLabel() {
    if (!recording) return;
    const elapsed = Date.now() - recording.startedAt;
    $("#record-label").textContent = `Stop · ${elapsedLabel(elapsed)} / 05:00`;
    if (elapsed >= maxRecordingSeconds * 1000) stopMicrophoneRecording();
  }

  function releaseRecorder(rec) {
    if (rec.processor) { rec.processor.onaudioprocess = null; try { rec.processor.disconnect(); } catch {} }
    if (rec.source) { try { rec.source.disconnect(); } catch {} }
    if (rec.stream) for (const track of rec.stream.getTracks()) track.stop();
    if (rec.timer) window.clearInterval(rec.timer);
    if (rec.context && rec.context.state !== "closed") rec.context.close().catch(() => {});
  }

  async function startMicrophoneRecording() {
    const capability = capabilityFor("dictation");
    if (!capability.available) { showToast(capability.reason || "Dictation is not ready on this device."); return; }
    if (activeJob) { showToast("Stop the active task before recording dictation."); return; }
    if (attachments.length) { showToast("Send or remove the queued files before recording dictation."); return; }
    if (!navigator.mediaDevices?.getUserMedia || typeof AudioContext === "undefined") {
      showToast("Local microphone recording is unavailable in this browser. Use a supported WAV file instead."); return;
    }
    const button = $("#record-button");
    button.disabled = true;
    let acquiredStream = null;
    let audioContext = null;
    try {
      acquiredStream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (!capabilityFor("dictation").available) { for (const track of acquiredStream.getTracks()) track.stop(); return; }
      audioContext = new AudioContext();
      const source = audioContext.createMediaStreamSource(acquiredStream);
      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      const rec = { stream: acquiredStream, context: audioContext, source, processor, chunks: [], sampleCount: 0, inputFrames: 0, nextSourceFrame: 0, startedAt: Date.now(), stopping: false, timer: 0 };
      const step = audioContext.sampleRate / 16000;
      processor.onaudioprocess = (event) => {
        if (recording !== rec || rec.stopping) return;
        const input = event.inputBuffer.getChannelData(0);
        const startFrame = rec.inputFrames;
        const endFrame = startFrame + input.length;
        const output = [];
        while (rec.nextSourceFrame + 1 < endFrame && rec.sampleCount + output.length < maxRecordingSamples) {
          const local = rec.nextSourceFrame - startFrame;
          const index = Math.floor(local);
          const fraction = local - index;
          const sample = input[index] + (input[index + 1] - input[index]) * fraction;
          const clamped = Math.max(-1, Math.min(1, sample));
          output.push(Math.round(clamped < 0 ? clamped * 32768 : clamped * 32767));
          rec.nextSourceFrame += step;
        }
        rec.inputFrames = endFrame;
        if (output.length) {
          rec.chunks.push(Int16Array.from(output));
          rec.sampleCount += output.length;
        }
        try { event.outputBuffer.getChannelData(0).fill(0); } catch {}
        if (rec.sampleCount >= maxRecordingSamples) stopMicrophoneRecording();
      };
      source.connect(processor);
      processor.connect(context.destination);
      recording = rec;
      for (const track of acquiredStream.getAudioTracks()) track.addEventListener("ended", () => { if (recording === rec) stopMicrophoneRecording(); }, { once: true });
      button.classList.add("is-recording");
      button.setAttribute("aria-label", "Stop microphone recording");
      rec.timer = window.setInterval(refreshRecordingLabel, 250);
      refreshRecordingLabel();
      renderModelStatus();
    } catch (error) {
      if (recording) { const rec = recording; recording = null; releaseRecorder(rec); }
      else {
        if (acquiredStream) for (const track of acquiredStream.getTracks()) track.stop();
        if (audioContext && audioContext.state !== "closed") audioContext.close().catch(() => {});
      }
      button.classList.remove("is-recording");
      $("#record-label").textContent = "Dictate";
      button.setAttribute("aria-label", "Record a local dictation");
      renderModelStatus();
      showToast(error?.name === "NotAllowedError" ? "Microphone permission was not granted. Mavi did not record audio." : (error.message || "Could not start local microphone recording."));
    } finally {
      if (!recording) renderModelStatus();
    }
  }

  async function stopMicrophoneRecording({ discard = false } = {}) {
    const rec = recording;
    if (!rec || rec.stopping) return;
    rec.stopping = true;
    recording = null;
    releaseRecorder(rec);
    const button = $("#record-button");
    button.classList.remove("is-recording");
    button.setAttribute("aria-label", "Record a local dictation");
    $("#record-label").textContent = "Dictate";
    renderModelStatus();
    if (discard) return;
    try {
      if (!rec.sampleCount) { showToast("No microphone audio was captured."); return; }
      const wav = maviEncodeWav16k(rec.chunks);
      if (wav.byteLength > maxAttachmentBytes || attachments.length) { showToast("The recording could not be queued within the 10 MB attachment limit."); return; }
      attachments.push({ name: "dictation.wav", mime: "audio/wav", data_base64: maviBytesToBase64(wav), size: wav.byteLength });
      renderAttachments();
      chooseMode("dictation");
      showToast("Recording is ready. Review the request and press Send to transcribe locally.");
    } catch (error) { showToast(error.message || "Could not encode the recording as WAV."); }
  }

  function discardRecordingOnExit() {
    if (!recording) return;
    const rec = recording;
    recording = null;
    releaseRecorder(rec);
  }

  async function saveThemeChoice(nextTheme) {
    applyTheme(nextTheme);
    try { await savePreferences(state?.profile || "", Boolean(state?.settings?.onboarded)); }
    catch (error) { showToast(error.message || "Could not save appearance preference."); }
  }

  function toggleSidebar(open) {
    $("#sidebar").classList.toggle("open", open);
    $("#mobile-scrim").classList.toggle("hidden", !open);
  }

  function closeMobileSidebar() { toggleSidebar(false); }

  function wireEvents() {
    const systemTheme = window.matchMedia?.("(prefers-color-scheme: dark)");
    const refreshSystemThemeColor = () => { if (theme === "system") applyTheme("system"); };
    if (typeof systemTheme?.addEventListener === "function") systemTheme.addEventListener("change", refreshSystemThemeColor);
    else systemTheme?.addListener?.(refreshSystemThemeColor);
    $("#team-toggle").addEventListener("click", () => setShowTeam(!showTeam, !showTeam));
    $("#show-team-setting").addEventListener("change", (event) => setShowTeam(event.target.checked));
    $("#show-penguin-setting").addEventListener("change", (event) => setShowPenguin(event.target.checked));
    $("#penguin-companion").addEventListener("click", (event) => {
      const companion = event.currentTarget;
      window.clearTimeout(penguinGreetingTimer);
      companion.classList.remove("is-greeting");
      void companion.offsetWidth;
      companion.classList.add("is-greeting");
      penguinGreetingTimer = window.setTimeout(() => companion.classList.remove("is-greeting"), 1100);
    });
    updatePenguinStatus(null);
    setShowPenguin(showPenguin);
    renderAgentMap([...agentEventByID.values()]);
    $("#workspace-picker").addEventListener("click", () => $("#workspace-menu").classList.contains("hidden") ? openWorkspaceMenu() : closeWorkspaceMenu());
    $("#composer-mode-chip").addEventListener("click", openWorkspaceMenu);
    document.addEventListener("click", (event) => { if (!$(".workspace-picker-wrap").contains(event.target)) closeWorkspaceMenu(); });
    $("#menu-toggle").addEventListener("click", () => toggleSidebar(true));
    $("#sidebar-close").addEventListener("click", closeMobileSidebar);
    $("#mobile-scrim").addEventListener("click", closeMobileSidebar);
    $("#new-chat").addEventListener("click", newChat);
    $$(".nav-item[data-view]").forEach((button) => button.addEventListener("click", () => { setView(button.dataset.view); closeMobileSidebar(); }));
    $("#open-settings").addEventListener("click", () => setView("settings"));
    $("#refresh-state").addEventListener("click", () => loadState({ syncJob: false }));
    $("#refresh-models").addEventListener("click", () => loadState({ syncJob: false }));
    $("#refresh-gallery").addEventListener("click", loadGallery);
    $("#clear-gallery").addEventListener("click", () => deleteGalleryFiles(galleryItems.map((item) => item?.name).filter((name) => typeof name === "string"), true));
    $("#theme-cycle").addEventListener("click", () => saveThemeChoice(theme === "system" ? "dark" : theme === "dark" ? "light" : "system"));
    $$("[data-theme-choice]").forEach((button) => button.addEventListener("click", () => saveThemeChoice(button.dataset.themeChoice)));
    $("#model-select").addEventListener("change", saveSelectedModel);
    $("#composer-form").addEventListener("submit", (event) => { event.preventDefault(); sendMessage(); });
    $("#automation-policy").addEventListener("change", (event) => selectAutomationPolicy(event.target.value));
    $("#automation-control-details").addEventListener("change", renderAutomationApprovalControls);
    $("#continue-app-context").addEventListener("click", continueAppContext);
    $("#clear-app-context").addEventListener("click", clearAppContext);
    $("#composer-input").addEventListener("input", () => { resizeComposer(); renderModelStatus(); });
    $("#composer-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); sendMessage(); }
    });
    $("#attach-button").addEventListener("click", () => $("#attachment-input").click());
    $("#attachment-input").addEventListener("change", (event) => handleAttachmentFiles([...event.target.files]));
    $("#record-button").addEventListener("click", () => recording ? stopMicrophoneRecording() : startMicrophoneRecording());
    window.addEventListener("beforeunload", discardRecordingOnExit);
    window.addEventListener("pagehide", discardRecordingOnExit);
    $$(".suggestion").forEach((button) => button.addEventListener("click", () => { $("#composer-input").value = button.dataset.prompt || ""; resizeComposer(); renderModelStatus(); $("#composer-input").focus(); }));
    $("#stop-job").addEventListener("click", stopJob);
    $("#clear-history").addEventListener("click", clearHistory);
    $("#clear-history-settings").addEventListener("click", clearHistory);
    $("#settings-profile").addEventListener("input", updateProfileCount);
    $("#save-settings-profile").addEventListener("click", saveSettingsProfile);
    $("#online-mode").addEventListener("change", () => {
      onlineModeDraft = $("#online-mode").value; onlineConfigDirty = true;
      if (onlineModeDraft === "local") $("#online-consent").checked = false;
      $("#online-disclosure").classList.toggle("hidden", onlineModeDraft !== "hybrid");
      $("#online-mode-badge").textContent = onlineModeDraft === "hybrid" ? "HYBRID" : "LOCAL ONLY";
      updateHybridCopy();
      renderModelStatus();
    });
    $("#online-key-provider").addEventListener("change", () => {
      $("#online-api-key").value = "";
      $("#gateway-url-wrap").classList.toggle("hidden", $("#online-key-provider").value !== "gateway");
      const config = { mode: onlineModeDraft, routes: onlineRoutesDraft, gateway_url: onlineGatewayDraft };
      renderOnline({ config, providers: onlineProviders });
    });
    $("#online-gateway-url").addEventListener("input", (event) => { onlineGatewayDraft = event.target.value; onlineConfigDirty = true; });
    $("#online-add-route").addEventListener("click", () => {
      onlineRoutesDraft = readOnlineRoutes();
      if (onlineRoutesDraft.length >= 8) return;
      onlineRoutesDraft.push({ provider: $("#online-key-provider").value, model: "", roles: ["chat", "analysis", "code", "files"] });
      onlineConfigDirty = true; renderOnlineRoutes(); updateHybridCopy(); renderModelStatus();
    });
    $("#online-routes").addEventListener("input", (event) => {
      if (event.target.matches("[data-field='model'], [data-field='role']")) {
        onlineRoutesDraft = readOnlineRoutes(); onlineConfigDirty = true;
        updateHybridCopy(); renderModelStatus();
      }
    });
    $("#online-routes").addEventListener("change", (event) => {
      const card = event.target.closest(".online-route");
      if (!card) return;
      const index = Number(card.dataset.routeIndex);
      if (event.target.matches("[data-field='provider']")) {
        onlineRoutesDraft = readOnlineRoutes();
        onlineRoutesDraft[index].provider = event.target.value;
        onlineRoutesDraft[index].model = "";
        onlineCatalogs.delete(index);
        onlineConfigDirty = true; renderOnlineRoutes(); updateHybridCopy(); renderModelStatus();
      } else if (event.target.matches("[data-field='catalog']")) {
        onlineRoutesDraft = readOnlineRoutes();
        const picked = event.target.value;
        if (picked) onlineRoutesDraft[index].model = picked;
        renderOnlineRoutes();
        $(`#online-routes .online-route[data-route-index="${index}"] [data-field="model"]`)?.focus();
        onlineConfigDirty = true;
        updateHybridCopy(); renderModelStatus();
      }
    });
    $("#online-routes").addEventListener("click", (event) => {
      const button = event.target.closest("[data-action]");
      const card = event.target.closest(".online-route");
      if (!button || !card) return;
      const index = Number(card.dataset.routeIndex);
      if (button.dataset.action === "catalog") return loadOnlineCatalog(index);
      onlineRoutesDraft = readOnlineRoutes();
      if (button.dataset.action === "remove") onlineRoutesDraft.splice(index, 1);
      else if (button.dataset.action === "up" && index > 0) [onlineRoutesDraft[index - 1], onlineRoutesDraft[index]] = [onlineRoutesDraft[index], onlineRoutesDraft[index - 1]];
      else if (button.dataset.action === "down" && index < onlineRoutesDraft.length - 1) [onlineRoutesDraft[index + 1], onlineRoutesDraft[index]] = [onlineRoutesDraft[index], onlineRoutesDraft[index + 1]];
      onlineConfigDirty = true; onlineCatalogs.clear(); renderOnlineRoutes(); updateHybridCopy(); renderModelStatus();
    });
    $("#online-save").addEventListener("click", () => saveOnlineSettings(false));
    $("#online-clear-key").addEventListener("click", () => saveOnlineSettings(true));
    window.addEventListener("pagehide", () => { $("#online-api-key").value = ""; });
    $("#discord-setup-form").addEventListener("submit", (event) => { event.preventDefault(); saveDiscordSettings(); });
    $("#discord-copy-invite").addEventListener("click", copyDiscordInvite);
    ["#discord-application", "#discord-channel", "#discord-users", "#discord-enabled", "#discord-allow-tasks"].forEach((selector) => {
      $(selector).addEventListener("input", () => { discordConfigDirty = true; updateDiscordInviteLink(); });
      $(selector).addEventListener("change", () => { discordConfigDirty = true; updateDiscordInviteLink(); });
    });
    $("#discord-enabled").addEventListener("change", () => {
      updateDiscordSaveLabel();
      if (!$("#discord-enabled").checked) stopDiscordStatusPoll();
    });
    $("#save-project-path").addEventListener("click", saveProjectPath);
    $("#discord-open-settings").addEventListener("click", () => setView("settings"));
    $("#discord-open-guide").addEventListener("click", () => setView("discord"));
    $("#connect-discord-onboarding").addEventListener("click", () => finishDiscordOnboarding(true));
    $("#skip-discord-onboarding").addEventListener("click", () => finishDiscordOnboarding(false));
    $("#continue-clean").addEventListener("click", completeCleanOnboarding);
    $("#personalize-clean").addEventListener("click", completeCleanOnboarding);
    $("#start-personalize").addEventListener("click", openPersonalizeStep);
    $("#back-onboarding").addEventListener("click", openCleanStep);
    $("#copy-summary-prompt").addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(onboardingPrompt); $("#copy-summary-prompt").textContent = "Copied"; }
      catch { setOnboardingError("Clipboard access is unavailable. Select and copy the prompt text above."); }
    });
    $("#import-profile").addEventListener("click", () => $("#profile-file").click());
    $("#profile-file").addEventListener("change", (event) => importOnboardingProfile(event.target.files[0]));
    $("#onboarding-profile").addEventListener("input", updateOnboardingCount);
    $("#save-preferences").addEventListener("click", completePersonalOnboarding);
    $("#onboarding-dialog").addEventListener("cancel", (event) => {
      if (!$("#onboarding-discord-step").classList.contains("hidden")) return;
      event.preventDefault();
      showToast("Choose Continue clean or Personalize to finish setup.");
    });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeWorkspaceMenu(); });
  }

  async function start() {
    applyTheme("system");
    wireEvents();
    const loaded = await loadState({ syncJob: true, first: true });
    if (loaded) {
      if (capabilityFor(currentMode).available) chooseMode(currentMode);
      const chats = Array.isArray(state?.chats) ? state.chats : [];
      if (!activeChatId && chats.length) activeChatId = chats[0].id;
      renderChats();
      renderCurrentChat();
      renderModelStatus();
    }
  }

  start();
})();
