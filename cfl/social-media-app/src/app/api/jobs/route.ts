import { NextRequest, NextResponse } from 'next/server';
import { getDb } from '@/lib/db';
import { initializeScheduler, scheduleJob } from '@/lib/scheduler';
import { v4 as uuidv4 } from 'uuid';

export const dynamic = 'force-dynamic';

// Boot the scheduler on the first API call
initializeScheduler();

// ── GET /api/jobs ─────────────────────────────────────────────────────────────
export async function GET() {
  try {
    const db = getDb();
    const rows = db
      .prepare('SELECT * FROM social_jobs ORDER BY created_at DESC')
      .all() as Record<string, unknown>[];

    const jobs = rows.map((row) => ({
      ...row,
      platforms: JSON.parse(row.platforms as string) as string[],
      is_active: Boolean(row.is_active),
    }));

    return NextResponse.json(jobs);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}

// ── POST /api/jobs ────────────────────────────────────────────────────────────
export async function POST(request: NextRequest) {
  try {
    const body = (await request.json()) as Record<string, unknown>;
    const {
      name,
      url,
      screenshot_selector,
      text_selector,
      template_name,
      platforms,
      schedule_time,
    } = body;

    if (
      !name ||
      !url ||
      !screenshot_selector ||
      !text_selector ||
      !Array.isArray(platforms) ||
      platforms.length === 0 ||
      !schedule_time
    ) {
      return NextResponse.json(
        { error: 'All fields are required and at least one platform must be selected.' },
        { status: 400 },
      );
    }

    const db = getDb();
    const id = uuidv4();
    const now = new Date().toISOString();

    db.prepare(`
      INSERT INTO social_jobs
        (id, name, url, screenshot_selector, text_selector, template_name, platforms, schedule_time, is_active, created_at)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    `).run(
      id,
      String(name),
      String(url),
      String(screenshot_selector),
      String(text_selector),
      String(template_name ?? 'Cinematic Default'),
      JSON.stringify(platforms),
      String(schedule_time),
      now,
    );

    // Register the new job with the scheduler
    scheduleJob({
      id,
      name: name as string,
      schedule_time: schedule_time as string,
    });

    return NextResponse.json({ id, success: true }, { status: 201 });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
