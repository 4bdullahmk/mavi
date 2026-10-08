// Run with the bundled Node runtime and Playwright. Provider API calls are
// mocked; this covers the real Online models settings UI without credentials.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
let appSource = fs.readFileSync(path.join(root, "portable", "web", "app.js"), "utf8");
const startup = "  start();\n})();";
assert.equal(appSource.split(startup).length, 2, "expected one app startup hook");
appSource = appSource.replace(startup, `  window.__onlineModelsTest = {
    init() {
      state = { chats: [], models: [], settings: { theme: "system" }, capabilities: { chat: { available: true }, dictation: { available: false } } };
      wireEvents();
      renderOnline({ config: { mode: "local", routes: [], allow_paid: false }, providers: [] }, { force: true });
      renderModelStatus();
    },
    async save() { await saveOnlineSettings(false); },
    async clearKey() { await saveOnlineSettings(true); },
    async loadCatalog(index) { await loadOnlineCatalog(index); }
  };\n})();`);

const snapshot = (config, configuredProviders = []) => ({
  config,
  mode: config.mode,
  routes: config.routes.map((route) => ({ ...route, key_configured: configuredProviders.includes(route.provider), cooldown_seconds: 0, auth_blocked: false, ready: configuredProviders.includes(route.provider) })),
  providers: ["nvidia", "openrouter", "groq", "google", "gateway"].map((id) => ({
    id, name: id, configured: configuredProviders.includes(id), key_configured: configuredProviders.includes(id),
    auth_blocked: false, cooldown_seconds: 0, status: configuredProviders.includes(id) ? "Ready" : "Not configured"
  })),
  allow_paid: false,
  gateway_configured: Boolean(config.gateway_url)
});

(async () => {
  const browser = await chromium.launch({
    headless: true,
    chromiumSandbox: true,
    executablePath: process.env.MAVI_TEST_BROWSER || "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  });
  try {
    const page = await browser.newPage();
    const html = fs.readFileSync(path.join(root, "portable", "web", "index.html"), "utf8")
      .replace('<script src="app.js" defer></script>', "");
    await page.route("http://mavi.test/**", (route) => route.fulfill({
      status: 200,
      contentType: route.request().url().endsWith("/") ? "text/html" : "text/plain",
      body: route.request().url().endsWith("/") ? html : ""
    }));
    await page.goto("http://mavi.test/");
    await page.addScriptTag({ content: appSource });
    await page.evaluate(() => {
      window.__onlineCalls = [];
      window.__onlineKeys = new Set();
      window.fetch = async (url, options = {}) => {
        const pathname = new URL(url, window.location.href).pathname;
        const body = options.body ? JSON.parse(options.body) : null;
        window.__onlineCalls.push({ pathname, body });
        if (pathname.endsWith("/online/configure")) {
          for (const id of body.clear_keys || []) window.__onlineKeys.delete(id);
          for (const id of Object.keys(body.keys || {})) window.__onlineKeys.add(id);
          return new Response(JSON.stringify(window.__onlineSnapshot(body.config, [...window.__onlineKeys])), {
            status: 200, headers: { "Content-Type": "application/json" }
          });
        }
        if (pathname.endsWith("/online/catalog")) {
          return new Response(JSON.stringify({ models: [{ id: "nvidia/test-chat", name: "Test chat model" }] }), {
            status: 200, headers: { "Content-Type": "application/json" }
          });
        }
        throw new Error(`Unexpected UI test request: ${pathname}`);
      };
    });
    await page.evaluate(`window.__onlineSnapshot = ${snapshot.toString()}`);
    await page.evaluate(() => window.__onlineModelsTest.init());

    const preflight = await page.evaluate(async () => {
      const mode = document.querySelector("#online-mode");
      mode.value = "hybrid";
      mode.dispatchEvent(new Event("change", { bubbles: true }));
      await window.__onlineModelsTest.save();
      return {
        defaultMode: document.querySelector("#online-mode").options[0].value,
        attemptedMode: mode.value,
        disclosureVisible: !document.querySelector("#online-disclosure").classList.contains("hidden"),
        requestCount: window.__onlineCalls.length,
        feedback: document.querySelector("#online-feedback").textContent
      };
    });
    assert.deepEqual(preflight, {
      defaultMode: "local", attemptedMode: "hybrid", disclosureVisible: true,
      requestCount: 0, feedback: "Acknowledge the task data disclosure before enabling hybrid mode."
    });

    const configured = await page.evaluate(async () => {
      document.querySelector("#online-consent").checked = true;
      document.querySelector("#online-api-key").value = "nvidia-secret-test-value";
      await window.__onlineModelsTest.save();
      const call = window.__onlineCalls.find((item) => item.pathname.endsWith("/online/configure"));
      return {
        call,
        keyFieldCleared: document.querySelector("#online-api-key").value === "",
        consentVisible: !document.querySelector("#online-disclosure").classList.contains("hidden"),
        hiddenFromChatState: !Object.prototype.hasOwnProperty.call(window.__onlineCalls[0].body, "text")
      };
    });
    assert.equal(configured.call.body.config.mode, "hybrid");
    assert.deepEqual(configured.call.body.config.routes, []);
    assert.equal(configured.call.body.config.allow_paid, false);
    assert.equal(configured.call.body.consent, true);
    assert.deepEqual(configured.call.body.keys, { nvidia: "nvidia-secret-test-value" });
    assert.equal(configured.keyFieldCleared, true);
    assert.equal(configured.consentVisible, true);
    assert.equal(configured.hiddenFromChatState, true);

    const catalogFlow = await page.evaluate(async () => {
      document.querySelector("#online-add-route").click();
      await window.__onlineModelsTest.loadCatalog(0);
      const catalog = document.querySelector("#online-routes [data-field='catalog']");
      const catalogLoaded = [...catalog.options].some((option) => option.value === "nvidia/test-chat");
      catalog.value = "nvidia/test-chat";
      catalog.dispatchEvent(new Event("change", { bubbles: true }));
      const firstModel = document.querySelector("#online-routes .online-route[data-route-index='0'] [data-field='model']").value;
      const roles = [...document.querySelectorAll("#online-routes .online-route[data-route-index='0'] [data-field='role']")];
      for (const role of roles) { role.checked = role.value === "chat"; role.dispatchEvent(new Event("input", { bubbles: true })); }

      document.querySelector("#online-key-provider").value = "openrouter";
      document.querySelector("#online-key-provider").dispatchEvent(new Event("change", { bubbles: true }));
      document.querySelector("#online-add-route").click();
      const second = document.querySelector("#online-routes .online-route[data-route-index='1']");
      second.querySelector("[data-field='provider']").value = "openrouter";
      second.querySelector("[data-field='provider']").dispatchEvent(new Event("change", { bubbles: true }));
      const secondCard = document.querySelector("#online-routes .online-route[data-route-index='1']");
      const secondModel = secondCard.querySelector("[data-field='model']");
      secondModel.value = "openrouter/test-free";
      secondModel.dispatchEvent(new Event("input", { bubbles: true }));
      for (const role of secondCard.querySelectorAll("[data-field='role']")) { role.checked = role.value === "analysis"; role.dispatchEvent(new Event("input", { bubbles: true })); }
      document.querySelector("#online-api-key").value = "openrouter-secret-test-value";
      secondCard.querySelector("[data-action='up']").click();
      await window.__onlineModelsTest.save();
      const calls = window.__onlineCalls;
      const catalogIndex = calls.findIndex((call) => call.pathname.endsWith("/online/catalog"));
      const configureCalls = calls.filter((call) => call.pathname.endsWith("/online/configure"));
      return {
        catalogLoaded, firstModel, roleCheckboxValues: roles.map((role) => role.value),
        catalogCall: calls[catalogIndex], temporaryConfigure: configureCalls[1], finalConfigure: configureCalls.at(-1),
        keyFieldCleared: document.querySelector("#online-api-key").value === "",
        providerStatus: document.querySelector("#online-provider-meta").textContent
      };
    });
    assert.equal(catalogFlow.catalogLoaded, true);
    assert.equal(catalogFlow.firstModel, "nvidia/test-chat");
    assert.deepEqual(catalogFlow.roleCheckboxValues, ["chat", "analysis", "code", "files"]);
    assert.deepEqual(catalogFlow.catalogCall.body, { provider: "nvidia" });
    assert.deepEqual(catalogFlow.temporaryConfigure.body.config.routes, []);
    assert.equal(catalogFlow.temporaryConfigure.body.consent, true);
    assert.deepEqual(catalogFlow.finalConfigure.body.keys, { openrouter: "openrouter-secret-test-value" });
    assert.deepEqual(catalogFlow.finalConfigure.body.config.routes, [
      { provider: "openrouter", model: "openrouter/test-free", roles: ["analysis"] },
      { provider: "nvidia", model: "nvidia/test-chat", roles: ["chat"] }
    ]);
    assert.equal(catalogFlow.finalConfigure.body.config.allow_paid, false);
    assert.equal(catalogFlow.keyFieldCleared, true);
    assert.match(catalogFlow.providerStatus, /key configured for this session/);

    const clearKey = await page.evaluate(async () => {
      await window.__onlineModelsTest.clearKey();
      const calls = window.__onlineCalls;
      return calls.filter((call) => call.pathname.endsWith("/online/configure")).at(-1);
    });
    assert.deepEqual(clearKey.body.clear_keys, ["openrouter"]);
    assert.equal(clearKey.body.keys, undefined);
    await page.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
