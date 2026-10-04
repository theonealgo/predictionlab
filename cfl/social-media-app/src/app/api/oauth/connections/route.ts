import { NextResponse } from 'next/server';
import { getOAuthConnectionStatuses } from '@/lib/oauth';

export const dynamic = 'force-dynamic';

// ── GET /api/oauth/connections ────────────────────────────────────────────────
export async function GET() {
  try {
    const data = getOAuthConnectionStatuses();
    return NextResponse.json(data);
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
