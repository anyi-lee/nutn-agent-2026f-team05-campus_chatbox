// Capture public Week 08 evidence in an isolated, guest-only browser context.
const { spawn } = require('node:child_process');
const { mkdir, writeFile } = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const cases = [
  ['normal', '許世昌老師的信箱和電機系辦電話', 'BOTH_VERIFIED', 2],
  ['unknown_teacher', '不存在老師的信箱和電機系辦電話', 'MAS_PARTIAL', 1],
];

async function startFixture() {
  const child = spawn(process.env.WEEK08_PYTHON || 'python3',
    ['scripts/run_week08_comparison.py', '--serve', '--port', '0'],
    { cwd: root, stdio: ['ignore', 'pipe', 'inherit'] });
  try {
    const base = await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Offline fixture did not start')), 10000);
      let output = '';
      child.once('error', err => { clearTimeout(timer); reject(err); });
      child.once('exit', () => { clearTimeout(timer); reject(new Error('Offline fixture exited')); });
      child.stdout.on('data', chunk => {
        output += chunk;
        const match = output.match(/http:\/\/127\.0\.0\.1:\d+/);
        if (match) { clearTimeout(timer); resolve(match[0]); }
      });
    });
    return { child, base };
  } catch (error) {
    if (child.pid && child.exitCode === null) child.kill();
    throw error;
  }
}

async function main() {
  const [output, mode] = process.argv.slice(2);
  if (!output || (mode && mode !== '--live')) throw new Error('Usage: capture_week08_browser.cjs OUTPUT_DIRECTORY [--live]');
  const live = mode === '--live';
  const dest = path.resolve(output);
  const { child, base } = live ? { base: 'http://127.0.0.1:8000' } : await startFixture();
  let browser;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 1350 }, locale: 'zh-TW' });
    const config = await (await ctx.request.get(`${base}/api/config`)).json();
    const settings = config.chat_workflow;
    if (settings?.backend !== 'mas' || (live && (settings.provider !== 'gemini' || !settings.configured))) {
      throw new Error('Expected a configured Week 08 MAS backend');
    }
    await mkdir(dest);
    const report = { recorded_at: new Date().toISOString(), checkout_head: process.env.WEEK08_CHECKOUT_HEAD || null,
      version_note: 'Checkout HEAD is not the uncommitted candidate SHA. See the comparison report for worktree hashes.',
      evidence_mode: live ? 'LIVE' : 'TEST_PROVIDER', cases: [] };
    let failed = false;
    for (const [name, query, expected, expectedSources] of cases) {
      const page = await ctx.newPage();
      await page.goto(base);
      await page.locator('#message-input').fill(query);
      const [response] = await Promise.all([
        page.waitForResponse(r => r.url() === `${base}/api/chat` && r.request().method() === 'POST', { timeout: 60000 }),
        page.locator('#send-button').click(),
      ]);
      const result = await response.json();
      const workflow = result.workflow;
      const message = page.locator('.message-row.assistant').last();
      await message.waitFor();
      if (workflow?.name === 'mas') await message.locator('.mas-trace summary').click();
      await message.screenshot({ path: path.join(dest, `${name}.png`) });
      const passed = response.ok() && workflow?.stop_reason === expected && workflow.write_actions === 0 &&
        result.citations?.length === expectedSources && (live ? workflow.live_status === 'LIVE_PASS' : workflow.live_calls === 0);
      failed ||= !passed;
      report.cases.push({ name, query, passed, expected, http_status: response.status(), status: result.status,
        answer: result.answer, citations: result.citations, error: result.error, workflow });
      console.log(`${name}: ${passed ? 'PASS' : 'FAIL'} ${workflow?.stop_reason || 'NO_TRACE'} ${workflow?.live_status || 'UNKNOWN'} ${workflow?.run_id || ''}`);
      await page.close();
    }
    await writeFile(path.join(dest, 'browser_trace.json'), JSON.stringify(report, null, 2) + '\n');
    if (failed) process.exitCode = 1;
  } finally {
    try { if (browser) await browser.close(); }
    finally { if (child && child.exitCode === null) child.kill(); }
  }
}

main().catch(err => { console.error(err.message); process.exitCode = 1; });
