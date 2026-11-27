// Launches headless Chromium. Uses Playwright's own browser when installed
// (`npx playwright install chromium`), otherwise any Chromium already in its cache.
import { chromium } from 'playwright';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function cachedChrome() {
  const dir = path.join(os.homedir(), '.cache', 'ms-playwright');
  if (!fs.existsSync(dir)) return undefined;
  const hits = fs.readdirSync(dir).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()
    .map((d) => path.join(dir, d, 'chrome-linux64', 'chrome')).filter((p) => fs.existsSync(p));
  return hits[0];
}

export async function launch() {
  const own = chromium.executablePath();
  const executablePath = fs.existsSync(own) ? undefined : cachedChrome();
  return chromium.launch({ executablePath, args: ['--force-color-profile=srgb', '--font-render-hinting=none'] });
}

/** A page whose screenshots are width*scale by height*scale pixels. */
export async function newPage(browser, width = 1920, height = 1080, scale = 1, colorScheme = 'light') {
  const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: scale, colorScheme });
  return context.newPage();
}
