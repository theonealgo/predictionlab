import { NextRequest, NextResponse } from 'next/server';
import { getDb } from '@/lib/db';
import { unscheduleJob } from '@/lib/scheduler';

export const dynamic = 'force-dynamic';

// ── DELETE /api/jobs/[id] ─────────────────────────────────────────────────────
export async function DELETE(
  _request: NextRequest,
  { params }: { params: { id: string } },
) {
  try {
    const db = getDb();
    const job = db
      .prepare('SELECT id FROM social_jobs WHERE id = ?')
      .get(params.id);

    if (!job) {
      return NextResponse.json({ error: 'Job not found' }, { status: 404 });
    }

    unscheduleJob(params.id);
    // CASCADE delete removes associated post_history rows too
    db.prepare('DELETE FROM social_jobs WHERE id = ?').run(params.id);

    return NextResponse.json({ success: true });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
