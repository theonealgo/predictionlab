import { NextRequest, NextResponse } from 'next/server';
import { v4 as uuidv4 } from 'uuid';
import { getDb } from '@/lib/db';
import { captureScreenshotAndText } from '@/lib/screenshot';
import { generateCaption, generateScriptHookVoiceover } from '@/lib/ai';
import { createVideoExport } from '@/lib/video';
import { postToAllPlatforms } from '@/lib/social';
import {
  canConsumeCredits,
  consumeCredits,
  CREDITS_PER_VIDEO_EXPORT,
  getUnlimitedSeriesSnapshot,
} from '@/lib/unlimitedSeries';

export const dynamic = 'force-dynamic';
// Allow up to 60 s for Puppeteer + OpenAI in production environments
export const maxDuration = 60;

interface DbJob {
  id: string;
  name: string;
  url: string;
  screenshot_selector: string;
  text_selector: string;
  template_name: string;
  platforms: string; // stored as JSON string
}

// ── POST /api/jobs/[id]/run ───────────────────────────────────────────────────
export async function POST(
  _request: NextRequest,
  { params }: { params: { id: string } },
) {
  const db = getDb();
  const job = db
    .prepare('SELECT * FROM social_jobs WHERE id = ?')
    .get(params.id) as DbJob | undefined;

  if (!job) {
    return NextResponse.json({ error: 'Job not found' }, { status: 404 });
  }

  const platforms = JSON.parse(job.platforms) as string[];
  const creditCheck = canConsumeCredits(db, CREDITS_PER_VIDEO_EXPORT);
  if (!creditCheck.ok) {
    return NextResponse.json(
      {
        success: false,
        error: `N/A — monthly credits are exhausted for this cycle. Needed ${CREDITS_PER_VIDEO_EXPORT}, remaining ${creditCheck.creditsRemaining} of ${creditCheck.monthlyCredits}.`,
      },
      { status: 402 },
    );
  }

  try {
    // ── 1. Screenshot + text extraction ────────────────────────────────────
    const capture = await captureScreenshotAndText(
      job.url,
      job.screenshot_selector,
      job.text_selector,
    );

    // ── 2. AI caption + script/hook generation ─────────────────────────────
    const { caption, hashtags } = await generateCaption(
      capture.extractedText ?? job.name,
      job.name,
      platforms,
    );
    const scriptBlock = await generateScriptHookVoiceover(
      capture.extractedText ?? caption,
      job.name,
      job.template_name || 'Cinematic Default',
    );

    // ── 3. Video export generation ──────────────────────────────────────────
    const video = await createVideoExport({
      screenshotPath: capture.screenshotPath,
      caption,
      hook: scriptBlock.hook,
      script: scriptBlock.script,
      voiceoverText: scriptBlock.voiceover,
      templateName: job.template_name || 'Cinematic Default',
      fileStem: `${job.name}_${job.id}`,
    });

    // ── 4. Auto-post to social platforms ────────────────────────────────────
    const platformResults = await postToAllPlatforms(platforms, {
      caption,
      hashtags,
      screenshotPath: capture.screenshotPath,
      videoPath: video.videoPath,
    });

    const videoReady = video.success && Boolean(video.videoPath);
    const allSuccess = platformResults.every((r) => r.success) && videoReady;
    const anySuccess = platformResults.some((r) => r.success) || videoReady;
    const status: string = allSuccess ? 'success' : anySuccess ? 'partial' : 'pending';

    // ── 5. Consume credits only when a downloadable export exists ──────────
    let creditsUsed = 0;
    let creditsRemaining = getUnlimitedSeriesSnapshot(db).creditsRemaining;
    if (videoReady) {
      const creditState = consumeCredits(db, CREDITS_PER_VIDEO_EXPORT);
      creditsUsed = CREDITS_PER_VIDEO_EXPORT;
      creditsRemaining = creditState.creditsRemaining;
    }

    const videoNotes = [
      ...(capture.error ? [`N/A — screenshot note: ${capture.error}`] : []),
      ...(video.reason ? [video.reason] : []),
      ...video.notes,
    ];

    // ── 6. Save to post history ─────────────────────────────────────────────
    const postId = uuidv4();
    const now = new Date().toISOString();

    db.prepare(`
      INSERT INTO post_history
        (id, job_id, job_name, caption, hashtags, screenshot_path, extracted_text,
         video_path, export_path, captions_path, voiceover_path, template_name,
         credits_used, watermark_applied, video_notes, platforms, status,
         platform_results, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    `).run(
      postId,
      job.id,
      job.name,
      caption,
      hashtags,
      capture.screenshotPath,
      capture.extractedText,
      video.videoPath,
      video.exportPath,
      video.captionsPath,
      video.voiceoverPath,
      job.template_name || 'Cinematic Default',
      creditsUsed,
      video.watermarkApplied ? 1 : 0,
      JSON.stringify(videoNotes),
      JSON.stringify(platforms),
      status,
      JSON.stringify(platformResults),
      now,
    );

    // ── 7. Update job's last_run ────────────────────────────────────────────
    db.prepare(
      'UPDATE social_jobs SET last_run = ?, last_status = ? WHERE id = ?',
    ).run(now, status, job.id);

    return NextResponse.json({
      success: true,
      postId,
      hook: scriptBlock.hook,
      script: scriptBlock.script,
      voiceoverText: scriptBlock.voiceover,
      caption,
      hashtags,
      screenshotPath: capture.screenshotPath,
      videoPath: video.videoPath,
      exportPath: video.exportPath,
      captionsPath: video.captionsPath,
      voiceoverPath: video.voiceoverPath,
      extractedText: capture.extractedText,
      creditsUsed,
      creditsRemaining,
      videoNotes,
      platformResults,
      status,
    });
  } catch (err) {
    const errorMessage = err instanceof Error ? err.message : String(err);
    console.error('[Run Job] Error:', errorMessage);

    db.prepare(
      'UPDATE social_jobs SET last_run = ?, last_status = ? WHERE id = ?',
    ).run(new Date().toISOString(), 'error', job.id);

    return NextResponse.json({ error: errorMessage }, { status: 500 });
  }
}
