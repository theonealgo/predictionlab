import { NextResponse } from 'next/server';
import {
  consumeOAuthState,
  exchangeOAuthCode,
  getProviderDisplayName,
  isOAuthProvider,
  upsertOAuthConnection,
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
        setTimeout(function () { window.close(); }, 600);
      })();
    </script>
  </body>
</html>`;
}

// ── GET /api/oauth/[provider]/callback ────────────────────────────────────────
export async function GET(
  request: Request,
  { params }: { params: { provider: string } },
) {
  const providerRaw = params.provider.toLowerCase();
  if (!isOAuthProvider(providerRaw)) {
    return NextResponse.json({ error: 'Provider not supported' }, { status: 404 });
  }

  const url = new URL(request.url);
  const oauthError = url.searchParams.get('error');
  const state = url.searchParams.get('state');
  const code = url.searchParams.get('code');

  if (oauthError) {
    const message = `N/A — ${getProviderDisplayName(providerRaw)} authorization was canceled or denied (${oauthError}).`;
    return new NextResponse(popupResultHtml(providerRaw, false, message), {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }

  if (!state || !code) {
    const message = `N/A — ${getProviderDisplayName(providerRaw)} callback is missing required state/code values.`;
    return new NextResponse(popupResultHtml(providerRaw, false, message), {
      status: 400,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }

  const consumed = consumeOAuthState(providerRaw, state);
  if (!consumed.found) {
    const message = `N/A — OAuth state validation failed for ${getProviderDisplayName(providerRaw)}. Please retry connect.`;
    return new NextResponse(popupResultHtml(providerRaw, false, message), {
      status: 400,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }

  const exchanged = await exchangeOAuthCode(providerRaw, code, consumed.codeVerifier);
  if (!exchanged.ok || !exchanged.accessToken) {
    const message =
      exchanged.reason ??
      `N/A — ${getProviderDisplayName(providerRaw)} token exchange failed.`;
    return new NextResponse(popupResultHtml(providerRaw, false, message), {
      status: 200,
      headers: { 'Content-Type': 'text/html; charset=utf-8' },
    });
  }

  upsertOAuthConnection(
    providerRaw,
    exchanged.accessToken,
    exchanged.refreshToken,
    exchanged.expiresIn,
  );

  const successMessage = `${getProviderDisplayName(providerRaw)} connected successfully.`;
  return new NextResponse(popupResultHtml(providerRaw, true, successMessage), {
    status: 200,
    headers: { 'Content-Type': 'text/html; charset=utf-8' },
  });
}
