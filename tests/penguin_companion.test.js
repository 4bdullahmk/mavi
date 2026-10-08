// Run with bundled Node + Playwright. Uses the real renderer and localStorage;
// network requests are mocked so no backend or provider is contacted.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
let appSource = fs.readFileSync(path.join(root, "portable", "web", "app.js"), "utf8");
const startup = "  start();\n})();";
assert.equal(appSource.split(startup).length, 2, "expected one app startup hook");
appSource = appSource.replace(startup, `  window.__penguinTest = {
    init() {
      state = {
        chats: [], models: [], profile: "", settings: { theme: "system", project_path: "" },
        discord: {}, capabilities: {
          chat: { available: true }, workers: { available: true },
          dictation: { available: false }, computer: { available: true }
        }
      };
      wireEvents();
      setShowPenguin(showPenguin);
    },
    setView,
    chooseMode,
    setShowPenguin,
    setPenguinAnimations,
    renderPenguinMotion
  };\n})();`);

(async () => {
  const browser = await chromium.launch({
    headless: true,
    chromiumSandbox: true,
    executablePath: process.env.MAVI_TEST_BROWSER || "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  });
  try {
    const context = await browser.newContext({ viewport: { width: 900, height: 700 } });
    const page = await context.newPage();
    const html = fs.readFileSync(path.join(root, "portable", "web", "index.html"), "utf8")
      .replace('<script src="app.js" defer></script>', "");
    const css = fs.readFileSync(path.join(root, "portable", "web", "app.css"), "utf8");
    await page.route("http://mavi.test/**", (route) => {
      const pathname = new URL(route.request().url()).pathname;
      return route.fulfill({
        status: 200,
        contentType: pathname === "/app.css" ? "text/css" : "text/html",
        body: pathname === "/" ? html : pathname === "/app.css" ? css : ""
      });
    });
    await page.goto("http://mavi.test/");
    await page.addScriptTag({ content: appSource });
    await page.evaluate(() => {
      window.fetch = async (url) => {
        const pathname = new URL(url, window.location.href).pathname;
        if (pathname.endsWith("/online")) {
          return new Response(JSON.stringify({ config: { mode: "local", routes: [] }, providers: [], routes: [], allow_paid: false }), {
            status: 200, headers: { "Content-Type": "application/json" }
          });
        }
        if (pathname.endsWith("/artifacts")) {
          return new Response(JSON.stringify({ artifacts: [] }), {
            status: 200, headers: { "Content-Type": "application/json" }
          });
        }
        return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
      };
      window.__penguinTest.init();
    });

    const base = await page.evaluate(() => ({
      visible: !document.querySelector("#penguin-dock").classList.contains("hidden"),
      outsideChat: !document.querySelector("#chat-view").contains(document.querySelector("#penguin-dock")),
      showSettingChecked: document.querySelector("#show-penguin-setting").checked,
      position: JSON.parse(localStorage.getItem("mavi-penguin-position"))
    }));
    assert.equal(base.visible, true, "companion is on by default");
    assert.equal(base.outsideChat, true, "floating dock is a global app-level layer");
    assert.equal(base.showSettingChecked, true);
    assert.ok(base.position && base.position.x >= 0 && base.position.x <= 1 && base.position.y >= 0 && base.position.y <= 1);

    for (const view of ["settings", "discord", "gallery", "chat"]) {
      await page.evaluate((name) => window.__penguinTest.setView(name), view);
      await page.waitForTimeout(20);
      assert.equal(await page.locator("#penguin-dock").isVisible(), true, `companion visible in ${view}`);
    }
    await page.evaluate(() => window.__penguinTest.chooseMode("workers"));
    assert.equal(await page.locator("#workspace-name").textContent(), "Agent team");
    assert.equal(await page.locator("#penguin-dock").isVisible(), true, "companion visible in agent-team workspace");

    await page.locator("#penguin-companion").click();
    assert.equal(await page.locator("#penguin-companion").evaluate((node) => node.classList.contains("is-greeting")), true,
      "clicking the penguin plays the existing wave/wink greeting");

    const box = await page.locator("#penguin-companion").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.mouse.move(-80, 950, { steps: 4 });
    await page.mouse.up();
    const dragged = await page.evaluate(() => {
      const dock = document.querySelector("#penguin-dock");
      const rect = dock.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height, vw: innerWidth, vh: innerHeight,
        saved: JSON.parse(localStorage.getItem("mavi-penguin-position")) };
    });
    assert.ok(dragged.x >= 0 && dragged.y >= 0, "dragging clamps the companion to the viewport origin");
    assert.ok(dragged.x + dragged.width <= dragged.vw + 1 && dragged.y + dragged.height <= dragged.vh + 1,
      "dragging cannot place the companion outside the viewport");
    assert.ok(dragged.saved.x <= 0.01 && dragged.saved.y >= 0.99, "drag position is saved as normalized coordinates");

    await page.setViewportSize({ width: 500, height: 420 });
    const resized = await page.locator("#penguin-dock").boundingBox();
    assert.ok(resized.x >= 0 && resized.y >= 0 && resized.x + resized.width <= 501 && resized.y + resized.height <= 421,
      `saved normalized position remains bounded after resize: ${JSON.stringify(resized)}`);

    const dragPosition = await page.evaluate(() => JSON.parse(localStorage.getItem("mavi-penguin-position")));
    await page.reload();
    await page.addScriptTag({ content: appSource });
    await page.evaluate(() => window.__penguinTest.init());
    const restoredDrag = await page.evaluate(() => ({
      position: JSON.parse(localStorage.getItem("mavi-penguin-position")),
      rect: document.querySelector("#penguin-dock").getBoundingClientRect().toJSON()
    }));
    assert.deepEqual(restoredDrag.position, dragPosition, "normalized drag position persists across reload");
    assert.ok(restoredDrag.rect.x >= 0 && restoredDrag.rect.y >= 0 && restoredDrag.rect.right <= 501 && restoredDrag.rect.bottom <= 421,
      "restored position is clamped to the resized viewport");
    await page.evaluate(() => {
      window.fetch = async (url) => {
        const pathname = new URL(url, window.location.href).pathname;
        if (pathname.endsWith("/online")) return new Response(JSON.stringify({ config: { mode: "local", routes: [] }, providers: [], routes: [], allow_paid: false }), { status: 200, headers: { "Content-Type": "application/json" } });
        if (pathname.endsWith("/artifacts")) return new Response(JSON.stringify({ artifacts: [] }), { status: 200, headers: { "Content-Type": "application/json" } });
        return new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } });
      };
    });

    await page.evaluate(() => window.__penguinTest.setView("settings"));
    await page.waitForTimeout(30);
    await page.evaluate(() => {
      const setting = document.querySelector("#show-penguin-setting");
      setting.checked = false;
      setting.dispatchEvent(new Event("change", { bubbles: true }));
    });
    assert.equal(await page.locator("#penguin-dock").isVisible(), false, "settings can hide the companion globally");
    assert.equal(await page.evaluate(() => localStorage.getItem("mavi-show-penguin")), "0");
    await page.evaluate(() => {
      const setting = document.querySelector("#show-penguin-setting");
      setting.checked = true;
      setting.dispatchEvent(new Event("change", { bubbles: true }));
    });
    assert.equal(await page.locator("#penguin-dock").isVisible(), true, "settings can restore the companion");
    assert.equal(await page.evaluate(() => localStorage.getItem("mavi-show-penguin")), "1");

    await page.locator("#penguin-options-toggle").click();
    assert.equal(await page.locator("#penguin-menu").isVisible(), true);
    assert.equal(await page.evaluate(() => document.activeElement.id), "penguin-menu-say-hi", "opening the options menu moves focus into it");
    await page.keyboard.press("ArrowDown");
    assert.equal(await page.evaluate(() => document.activeElement.id), "penguin-menu-animation", "menu supports arrow-key navigation");
    await page.keyboard.press("Escape");
    assert.equal(await page.locator("#penguin-menu").isVisible(), false);
    assert.equal(await page.evaluate(() => document.activeElement.id), "penguin-options-toggle", "Escape closes the menu and restores focus");

    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.locator("#penguin-options-toggle").click();
    const reducedMotion = await page.evaluate(() => ({
      disabled: document.querySelector("#penguin-menu-animation").disabled,
      classOff: document.querySelector("#penguin-dock").classList.contains("animations-off"),
      label: document.querySelector("#penguin-menu-animation").textContent
    }));
    assert.deepEqual(reducedMotion, { disabled: true, classOff: true, label: "Animation: off (reduced motion)" });
    await page.keyboard.press("Escape");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.locator("#penguin-options-toggle").click();
    await page.locator("#penguin-menu-animation").click();
    assert.equal(await page.evaluate(() => localStorage.getItem("mavi-penguin-animations")), "0");
    assert.equal(await page.locator("#penguin-dock").evaluate((node) => node.classList.contains("animations-off")), true);

    await page.locator("#penguin-menu-reset").click();
    const reset = await page.evaluate(() => JSON.parse(localStorage.getItem("mavi-penguin-position")));
    assert.deepEqual(reset, { x: 0.98, y: 0.1 }, "options menu restores the default position");

    await page.locator("#penguin-companion").click({ button: "right" });
    assert.equal(await page.locator("#penguin-menu").isVisible(), true, "right-click opens the options menu");
    const menuBounds = await page.locator("#penguin-menu").boundingBox();
    assert.ok(menuBounds.x >= 0 && menuBounds.y >= 0 && menuBounds.x + menuBounds.width <= 501 && menuBounds.y + menuBounds.height <= 421,
      `options menu stays inside the viewport: ${JSON.stringify(menuBounds)}`);
    await page.locator("#penguin-menu-hide").click();
    assert.equal(await page.locator("#penguin-dock").isVisible(), false, "options menu can hide the companion");
    assert.equal(await page.locator("#show-penguin-setting").isChecked(), false);
    await page.locator("#show-penguin-setting").check();
    assert.equal(await page.locator("#penguin-dock").isVisible(), true, "settings can show a menu-hidden companion again");

    // Reload the real HTML at the same origin and re-inject the test hook. All
    // persisted state must survive without relying on backend data.
    await page.reload();
    await page.addScriptTag({ content: appSource });
    await page.evaluate(() => window.__penguinTest.init());
    assert.equal(await page.locator("#penguin-dock").isVisible(), true);
    assert.equal(await page.locator("#show-penguin-setting").isChecked(), true);
    assert.equal(await page.locator("#penguin-dock").evaluate((node) => node.classList.contains("animations-off")), true,
      "animation preference persists across reload");
    await context.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
