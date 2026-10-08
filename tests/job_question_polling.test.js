// Run with the bundled Node runtime and Playwright. This exercises the real
// renderer without starting Mavi or making any API requests.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright");

const root = path.resolve(__dirname, "..");
const appPath = path.join(root, "portable", "web", "app.js");
let appSource = fs.readFileSync(appPath, "utf8");
const startup = "  start();\n})();";
assert.equal(appSource.split(startup).length, 2, "expected one app startup hook");
appSource = appSource.replace(startup, `  window.__jobQuestionTest = {
    renderJobQuestion,
    setView,
    sendMessage,
    selectAutomationPolicy,
    automationPolicy() { return automationPolicyChoice; },
    configureComputer() {
      state = { chats: [], models: [], settings: {}, capabilities: { computer: { available: true }, dictation: { available: false } } };
      currentMode = "computer";
      renderModelStatus();
    },
    configureChat() {
      state = { chats: [], models: [], settings: {}, capabilities: { chat: { available: true }, dictation: { available: false } } };
      currentMode = "chat";
      activeChatId = "chat-test";
      renderModelStatus();
    }
  };\n})();`);

(async () => {
  const browser = await chromium.launch({
    headless: true,
    chromiumSandbox: true,
    executablePath: process.env.MAVI_TEST_BROWSER || "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
  });
  try {
    const page = await browser.newPage();
    await page.setContent('<p id="summary-prompt-text">test</p><div id="job-question" class="hidden"></div>');
    await page.addScriptTag({ content: appSource });
    const result = await page.evaluate(() => {
      const render = window.__jobQuestionTest.renderJobQuestion;
      const freshDefaultPolicy = window.__jobQuestionTest.automationPolicy();
      const question = { id: "job-1", status: "waiting", question: "Which sheet should I update?" };
      render(question);
      const firstInput = document.querySelector("#job-question input");
      firstInput.value = "Budget";
      firstInput.focus();
      render({ ...question, progress: "Still waiting" }); // next one-second poll
      const sameNode = firstInput === document.querySelector("#job-question input");
      const preservedValue = document.querySelector("#job-question input")?.value;
      const preservedFocus = document.activeElement === firstInput;
      render({ ...question, question: "Which month should I use?" });
      const updatedPrompt = document.querySelector("#job-question p")?.textContent;
      const newQuestionInput = document.querySelector("#job-question input");
      const replacedForNewQuestion = newQuestionInput !== firstInput;
      render({ id: "job-2", status: "waiting", question: "Which month should I use?" });
      const replacedForNewJob = document.querySelector("#job-question input") !== newQuestionInput;
      render({ id: "job-3", status: "waiting", question: "Allow Mavi to read and control the Excel window for this task? Mavi will ask before each action and will pause for private sign-ins." });
      const appAccessControls = [...document.querySelectorAll("#job-question button")].map((button) => button.textContent);
      const appAccessHasNoTextInput = !document.querySelector("#job-question input");
      render({ id: "job-3b", status: "waiting", question: "Allow Mavi to capture the current Excel window for this task? Screenshots are sent only to your selected local model and are not saved." });
      const captureApprovalControls = [...document.querySelectorAll("#job-question button")].map((button) => button.textContent);
      render({ id: "job-3c", status: "waiting", question: "Mavi wants to click a visible button in Excel. Allow this action?" });
      const actionApprovalControls = [...document.querySelectorAll("#job-question button")].map((button) => button.textContent);
      render({ id: "job-4", status: "waiting", question: "Which workbook should I use?" });
      const ordinaryQuestionHasTextInput = Boolean(document.querySelector("#job-question input"));
      render({ id: "job-2", status: "working" });
      return {
        freshDefaultPolicy, sameNode, preservedValue, preservedFocus, updatedPrompt,
        replacedForNewQuestion, replacedForNewJob,
        appAccessControls, appAccessHasNoTextInput, captureApprovalControls,
        actionApprovalControls, ordinaryQuestionHasTextInput,
        clearedWhenNotWaiting: document.querySelector("#job-question").classList.contains("hidden") && !document.querySelector("#job-question").dataset.questionKey
      };
    });
    assert.deepEqual(result, {
      freshDefaultPolicy: "ask_each",
      sameNode: true,
      preservedValue: "Budget",
      preservedFocus: true,
      updatedPrompt: "Which month should I use?",
      replacedForNewQuestion: true,
      replacedForNewJob: true,
      appAccessControls: ["Cancel task", "Allow"],
      appAccessHasNoTextInput: true,
      captureApprovalControls: ["Cancel task", "Allow"],
      actionApprovalControls: ["Cancel task", "Allow"],
      ordinaryQuestionHasTextInput: true,
      clearedWhenNotWaiting: true
    });

    const dispatchPage = await browser.newPage();
    const html = fs.readFileSync(path.join(root, "portable", "web", "index.html"), "utf8")
      .replace('<script src="app.js" defer></script>', "");
    await dispatchPage.route("http://mavi.test/**", (route) => route.fulfill({
      status: 200,
      contentType: route.request().url().endsWith("/") ? "text/html" : "text/plain",
      body: route.request().url().endsWith("/") ? html : ""
    }));
    await dispatchPage.addInitScript(() => localStorage.setItem("mavi-automation-policy", "routine_navigation"));
    await dispatchPage.goto("http://mavi.test/");
    await dispatchPage.addScriptTag({ content: appSource });
    const dispatchResult = await dispatchPage.evaluate(async () => {
      let chatRequests = 0;
      let chatPayload = null;
      window.fetch = async (url, options = {}) => {
        const path = new URL(url, window.location.href).pathname;
        if (path.endsWith("/chat")) {
          chatRequests += 1;
          chatPayload = JSON.parse(options.body);
          await new Promise((resolve) => setTimeout(resolve, 75));
          return new Response(JSON.stringify({ job_id: "job-send-once", chat_id: "chat-test" }), {
            status: 200,
            headers: { "Content-Type": "application/json" }
          });
        }
        return new Response(JSON.stringify({ config: { mode: "local", routes: [] }, mode: "local", routes: [], providers: [], allow_paid: false }), {
          status: 200,
          headers: { "Content-Type": "application/json" }
        });
      };
      const savedOnLoad = window.__jobQuestionTest.automationPolicy();
      window.__jobQuestionTest.setView("settings");
      const settingsFocus = document.activeElement?.id;
      window.__jobQuestionTest.setView("discord");
      const discordFocus = document.activeElement?.id;
      const settingsFocusable = document.querySelector("#settings-view")?.getAttribute("tabindex");
      const discordFocusable = document.querySelector("#discord-view")?.getAttribute("tabindex");
      window.__jobQuestionTest.configureComputer();
      const controlRestored = document.querySelector("#automation-policy").value;
      window.__jobQuestionTest.selectAutomationPolicy("ask_each");
      const changedPreferenceSaved = localStorage.getItem("mavi-automation-policy");
      window.__jobQuestionTest.configureChat();
      window.__jobQuestionTest.configureComputer();
      const restoredAfterHide = document.querySelector("#automation-policy").value;
      window.__jobQuestionTest.selectAutomationPolicy("routine_navigation");
      window.__jobQuestionTest.configureChat();
      document.querySelector("#composer-input").value = "Please run one task";
      await Promise.all([window.__jobQuestionTest.sendMessage(), window.__jobQuestionTest.sendMessage()]);
      return {
        chatRequests,
        activeJobID: document.querySelector("#job-card").classList.contains("hidden") ? null : "job-started",
        savedOnLoad, controlRestored, changedPreferenceSaved, restoredAfterHide,
        settingsFocus, discordFocus, settingsFocusable, discordFocusable,
        requestPolicy: chatPayload?.automation_policy
      };
    });
    assert.deepEqual(dispatchResult, {
      chatRequests: 1, activeJobID: "job-started", savedOnLoad: "routine_navigation",
      controlRestored: "routine_navigation", changedPreferenceSaved: "ask_each",
      restoredAfterHide: "ask_each", settingsFocus: "settings-view", discordFocus: "discord-view",
      settingsFocusable: "-1", discordFocusable: "-1", requestPolicy: "routine_navigation"
    });
    await dispatchPage.close();
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
