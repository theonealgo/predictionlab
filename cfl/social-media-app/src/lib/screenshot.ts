import puppeteer from 'puppeteer';
import path from 'path';
import fs from 'fs';

export interface CaptureResult {
  screenshotPath: string | null;
  extractedText: string | null;
  error?: string;
}

export async function captureScreenshotAndText(
  url: string,
  screenshotSelector: string,
  textSelector: string,
): Promise<CaptureResult> {
  const browser = await puppeteer.launch({
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-dev-shm-usage',
      '--disable-gpu',
    ],
  });

  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });

    await page.goto(url, {
      waitUntil: 'networkidle2',
      timeout: 30_000,
    });

    // ── Screenshot the selected element ──────────────────────────────────────
    let screenshotPath: string | null = null;
    try {
      await page.waitForSelector(screenshotSelector, { timeout: 10_000 });
      const element = await page.$(screenshotSelector);
      if (element) {
        const screenshotsDir = path.join(process.cwd(), 'public', 'screenshots');
        fs.mkdirSync(screenshotsDir, { recursive: true });

        const filename = `screenshot_${Date.now()}.png`;
        const fullPath = path.join(screenshotsDir, filename);
        await element.screenshot({ path: fullPath });
        screenshotPath = `/screenshots/${filename}`;
        console.log(`[Screenshot] Saved ${filename}`);
      }
    } catch (err) {
      console.warn(`[Screenshot] Could not capture "${screenshotSelector}":`, err);
    }

    // ── Extract text ──────────────────────────────────────────────────────────
    let extractedText: string | null = null;
    try {
      await page.waitForSelector(textSelector, { timeout: 5_000 });
      extractedText = await page.$eval(
        textSelector,
        (el: Element) =>
          (el as HTMLElement).innerText?.trim() ||
          el.textContent?.trim() ||
          '',
      );
    } catch (err) {
      console.warn(`[Screenshot] Could not extract text from "${textSelector}":`, err);
    }

    return { screenshotPath, extractedText };
  } catch (err) {
    const error = err instanceof Error ? err.message : String(err);
    console.error('[Screenshot] Page load error:', error);
    return { screenshotPath: null, extractedText: null, error };
  } finally {
    await browser.close();
  }
}
