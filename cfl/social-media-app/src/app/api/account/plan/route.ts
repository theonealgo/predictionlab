import { NextResponse } from 'next/server';
import { getDb } from '@/lib/db';
import { getUnlimitedSeriesSnapshot } from '@/lib/unlimitedSeries';

export const dynamic = 'force-dynamic';

// ── GET /api/account/plan ─────────────────────────────────────────────────────
export async function GET() {
  try {
    const db = getDb();
    const snapshot = getUnlimitedSeriesSnapshot(db);
    return NextResponse.json(snapshot);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
