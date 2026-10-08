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
appSource = appSource.replace(startup, "  window.__jobQuestionTest = { renderJobQuestion };\n})();");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.MAVI_TEST_BROWSER || "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    args: ["--no-sandbox"]
  });
  try {
    const page = await browser.newPage();
    await page.setContent('<p id="summary-prompt-text">test</p><div id="job-question" class="hidden"></div>');
    await page.addScriptTag({ content: appSource });
    const result = await page.evaluate(() => {
      const render = window.__jobQuestionTest.renderJobQuestion;
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
      render({ id: "job-2", status: "working" });
      return {
        sameNode, preservedValue, preservedFocus, updatedPrompt,
        replacedForNewQuestion, replacedForNewJob,
        clearedWhenNotWaiting: document.querySelector("#job-question").classList.contains("hidden") && !document.querySelector("#job-question").dataset.questionKey
      };
    });
    assert.deepEqual(result, {
      sameNode: true,
      preservedValue: "Budget",
      preservedFocus: true,
      updatedPrompt: "Which month should I use?",
      replacedForNewQuestion: true,
      replacedForNewJob: true,
      clearedWhenNotWaiting: true
    });
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
