import { NextRequest, NextResponse } from 'next/server';
import { getDb } from '@/lib/db';

export const dynamic = 'force-dynamic';

// ── GET /api/posts?jobId=... ──────────────────────────────────────────────────
export async function GET(request: NextRequest) {
  try {
    const { searchParams } = new URL(request.url);
    const jobId = searchParams.get('jobId');
    const limit = Number(searchParams.get('limit') ?? '100');

    const db = getDb();
    let rows: Record<string, unknown>[];

    if (jobId) {
      rows = db
        .prepare(
          'SELECT * FROM post_history WHERE job_id = ? ORDER BY created_at DESC LIMIT ?',
        )
        .all(jobId, limit) as Record<string, unknown>[];
    } else {
      rows = db
        .prepare('SELECT * FROM post_history ORDER BY created_at DESC LIMIT ?')
        .all(limit) as Record<string, unknown>[];
    }

    const posts = rows.map((row) => ({
      ...row,
      platforms: JSON.parse(row.platforms as string) as string[],
      platform_results: row.platform_results
        ? (JSON.parse(row.platform_results as string) as unknown[])
        : null,
      video_notes: row.video_notes
        ? (JSON.parse(row.video_notes as string) as string[])
        : null,
      watermark_applied: Boolean(row.watermark_applied),
      credits_used: Number(row.credits_used ?? 0),
    }));

    return NextResponse.json(posts);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
