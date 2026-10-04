import { NextResponse } from 'next/server';
import { deleteOAuthConnection, isOAuthProvider } from '@/lib/oauth';

export const dynamic = 'force-dynamic';

// ── POST /api/oauth/[provider]/disconnect ─────────────────────────────────────
export async function POST(
  _request: Request,
  { params }: { params: { provider: string } },
) {
  const providerRaw = params.provider.toLowerCase();
  if (!isOAuthProvider(providerRaw)) {
    return NextResponse.json({ error: 'Provider not supported' }, { status: 404 });
  }

  try {
    deleteOAuthConnection(providerRaw);
    return NextResponse.json({ success: true });
  } catch (err) {
    return NextResponse.json({ error: String(err) }, { status: 500 });
  }
}
