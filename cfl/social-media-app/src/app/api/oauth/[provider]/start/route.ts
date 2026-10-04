import { NextResponse } from 'next/server';
import {
  createOAuthAuthorizationUrl,
  isOAuthProvider,
  isProviderConfigured,
  saveOAuthState,
} from '@/lib/oauth';

export const dynamic = 'force-dynamic';

function popupResultHtml(provider: string, success: boolean, message: string): string {
  const payload = JSON.stringify({
    source: 'streamly-oauth',
    provider,
    success,
    message,
  });
  return `<!doctype html>
<html>
  <body style="font-family: -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif; background:#0f172a; color:#e2e8f0; padding:20px;">
    <p>${message}</p>
    <script>
      (function () {
        var payload = ${payload};
        try {
          if (window.opener && !window.opener.closed) {
            window.opener.postMessage(payload, window.location.origin);
          }
        } catch (_err) {}
        setTimeout(function () { window.close(); }, 500);
      })();
    </script>
  </body>
</html>`;
}

// ── GET /api/oauth/[provider]/start ───────────────────────────────────────────
export async function GET(
  _request: Request,
  { params }: { params: { provider: string } },
) {
  const providerRaw = params.provider.toLowerCase();
  if (!isOAuthProvider(providerRaw)) {
    return NextResponse.json({ error: 'Provider not supported' }, { status: 404 });
  }

  const configured = isProviderConfigured(providerRaw);
  if (!configured.ok) {
    return new NextResponse(
      popupResultHtml(providerRaw, false, configured.reason ?? 'N/A — provider not configured.'),
      {
        status: 200,
        headers: { 'Content-Type': 'text/html; charset=utf-8' },
      },
    );
  }

  const auth = createOAuthAuthorizationUrl(providerRaw);
  saveOAuthState(providerRaw, auth.state, auth.codeVerifier);
  return NextResponse.redirect(auth.url);
}
