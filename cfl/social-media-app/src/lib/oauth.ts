import crypto from 'crypto';
import { getDb } from './db';

export const OAUTH_PROVIDERS = [
  'instagram',
  'facebook',
  'twitter',
  'tiktok',
  'youtube',
  'linkedin',
  'pinterest',
] as const;

export type OAuthProvider = (typeof OAUTH_PROVIDERS)[number];
export function isOAuthProvider(value: string): value is OAuthProvider {
  return (OAUTH_PROVIDERS as readonly string[]).includes(value);
}

interface ProviderConfig {
  displayName: string;
  authEndpoint: string;
  tokenEndpoint: string;
  scopeSeparator: ' ' | ',';
  scopes: string[];
  clientIdEnvKeys: string[];
  clientSecretEnvKeys: string[];
  usesPkce?: boolean;
  extraAuthParams?: Record<string, string>;
}

export interface OAuthConnectionStatus {
  provider: OAuthProvider;
  displayName: string;
  connected: boolean;
  configured: boolean;
  accountLabel: string | null;
  connectedAt: string | null;
  reason: string | null;
}

interface TokenResult {
  ok: boolean;
  accessToken?: string;
  refreshToken?: string;
  expiresIn?: number;
  reason?: string;
}

const PROVIDER_CONFIGS: Record<OAuthProvider, ProviderConfig> = {
  instagram: {
    displayName: 'Instagram',
    authEndpoint: 'https://www.facebook.com/v19.0/dialog/oauth',
    tokenEndpoint: 'https://graph.facebook.com/v19.0/oauth/access_token',
    scopeSeparator: ',',
    scopes: [
      'instagram_basic',
      'instagram_content_publish',
      'pages_show_list',
      'pages_read_engagement',
    ],
    clientIdEnvKeys: ['INSTAGRAM_APP_ID', 'FACEBOOK_APP_ID'],
    clientSecretEnvKeys: ['INSTAGRAM_APP_SECRET', 'FACEBOOK_APP_SECRET'],
  },
  facebook: {
    displayName: 'Facebook',
    authEndpoint: 'https://www.facebook.com/v19.0/dialog/oauth',
    tokenEndpoint: 'https://graph.facebook.com/v19.0/oauth/access_token',
    scopeSeparator: ',',
    scopes: ['pages_manage_posts', 'pages_read_engagement'],
    clientIdEnvKeys: ['FACEBOOK_APP_ID'],
    clientSecretEnvKeys: ['FACEBOOK_APP_SECRET'],
  },
  twitter: {
    displayName: 'X (Twitter)',
    authEndpoint: 'https://twitter.com/i/oauth2/authorize',
    tokenEndpoint: 'https://api.twitter.com/2/oauth2/token',
    scopeSeparator: ' ',
    scopes: ['tweet.read', 'tweet.write', 'users.read', 'offline.access'],
    clientIdEnvKeys: ['TWITTER_CLIENT_ID'],
    clientSecretEnvKeys: ['TWITTER_CLIENT_SECRET'],
    usesPkce: true,
    extraAuthParams: {
      response_type: 'code',
    },
  },
  tiktok: {
    displayName: 'TikTok',
    authEndpoint: 'https://www.tiktok.com/v2/auth/authorize/',
    tokenEndpoint: 'https://open.tiktokapis.com/v2/oauth/token/',
    scopeSeparator: ',',
    scopes: ['user.info.basic', 'video.publish'],
    clientIdEnvKeys: ['TIKTOK_CLIENT_KEY'],
    clientSecretEnvKeys: ['TIKTOK_CLIENT_SECRET'],
    extraAuthParams: {
      response_type: 'code',
    },
  },
  youtube: {
    displayName: 'YouTube',
    authEndpoint: 'https://accounts.google.com/o/oauth2/v2/auth',
    tokenEndpoint: 'https://oauth2.googleapis.com/token',
    scopeSeparator: ' ',
    scopes: [
      'https://www.googleapis.com/auth/youtube.upload',
      'https://www.googleapis.com/auth/youtube.readonly',
    ],
    clientIdEnvKeys: ['GOOGLE_CLIENT_ID', 'YOUTUBE_CLIENT_ID'],
    clientSecretEnvKeys: ['GOOGLE_CLIENT_SECRET', 'YOUTUBE_CLIENT_SECRET'],
    extraAuthParams: {
      response_type: 'code',
      access_type: 'offline',
      prompt: 'consent',
    },
  },
  linkedin: {
    displayName: 'LinkedIn',
    authEndpoint: 'https://www.linkedin.com/oauth/v2/authorization',
    tokenEndpoint: 'https://www.linkedin.com/oauth/v2/accessToken',
    scopeSeparator: ' ',
    scopes: ['openid', 'profile', 'w_member_social'],
    clientIdEnvKeys: ['LINKEDIN_CLIENT_ID'],
    clientSecretEnvKeys: ['LINKEDIN_CLIENT_SECRET'],
    extraAuthParams: {
      response_type: 'code',
    },
  },
  pinterest: {
    displayName: 'Pinterest',
    authEndpoint: 'https://www.pinterest.com/oauth/',
    tokenEndpoint: 'https://api.pinterest.com/v5/oauth/token',
    scopeSeparator: ',',
    scopes: ['pins:read', 'pins:write', 'boards:read'],
    clientIdEnvKeys: ['PINTEREST_CLIENT_ID'],
    clientSecretEnvKeys: ['PINTEREST_CLIENT_SECRET'],
    usesPkce: true,
    extraAuthParams: {
      response_type: 'code',
    },
  },
};

function envFirst(keys: string[]): string | null {
  for (const key of keys) {
    const value = process.env[key];
    if (value && value.trim() !== '') {
      return value.trim();
    }
  }
  return null;
}

function getAppBaseUrl(): string {
  return process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
}

function getConfig(provider: OAuthProvider): ProviderConfig {
  return PROVIDER_CONFIGS[provider];
}

function getClientId(provider: OAuthProvider): string | null {
  return envFirst(getConfig(provider).clientIdEnvKeys);
}

function getClientSecret(provider: OAuthProvider): string | null {
  return envFirst(getConfig(provider).clientSecretEnvKeys);
}

function base64UrlEncode(buffer: Buffer): string {
  return buffer
    .toString('base64')
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/g, '');
}

function randomState(): string {
  return base64UrlEncode(crypto.randomBytes(24));
}

function randomVerifier(): string {
  return base64UrlEncode(crypto.randomBytes(48));
}

function pkceChallenge(verifier: string): string {
  const digest = crypto.createHash('sha256').update(verifier).digest();
  return base64UrlEncode(digest);
}

function redirectUriFor(provider: OAuthProvider): string {
  return `${getAppBaseUrl()}/api/oauth/${provider}/callback`;
}

export function getProviderDisplayName(provider: OAuthProvider): string {
  return getConfig(provider).displayName;
}

export function isProviderConfigured(provider: OAuthProvider): {
  ok: boolean;
  reason: string | null;
} {
  const cfg = getConfig(provider);
  const clientId = getClientId(provider);
  const clientSecret = getClientSecret(provider);
  if (!clientId || !clientSecret) {
    return {
      ok: false,
      reason:
        `N/A — ${cfg.displayName} OAuth is not configured on the server ` +
        `(missing client id/secret environment variables).`,
    };
  }
  return { ok: true, reason: null };
}

export function createOAuthAuthorizationUrl(provider: OAuthProvider): {
  url: string;
  state: string;
  codeVerifier: string | null;
} {
  const cfg = getConfig(provider);
  const state = randomState();
  const codeVerifier = cfg.usesPkce ? randomVerifier() : null;
  const challenge = codeVerifier ? pkceChallenge(codeVerifier) : null;

  const params = new URLSearchParams({
    client_id: getClientId(provider) ?? '',
    redirect_uri: redirectUriFor(provider),
    scope: cfg.scopes.join(cfg.scopeSeparator),
    state,
  });

  if (cfg.extraAuthParams) {
    for (const [k, v] of Object.entries(cfg.extraAuthParams)) {
      params.set(k, v);
    }
  }

  if (!params.has('response_type')) {
    params.set('response_type', 'code');
  }

  if (challenge) {
    params.set('code_challenge', challenge);
    params.set('code_challenge_method', 'S256');
  }

  return {
    url: `${cfg.authEndpoint}?${params.toString()}`,
    state,
    codeVerifier,
  };
}

export function saveOAuthState(
  provider: OAuthProvider,
  state: string,
  codeVerifier: string | null,
): void {
  const db = getDb();
  const now = new Date().toISOString();
  db.prepare(`
    INSERT INTO oauth_states (state, provider, code_verifier, created_at)
    VALUES (?, ?, ?, ?)
  `).run(state, provider, codeVerifier, now);
}

export function consumeOAuthState(
  provider: OAuthProvider,
  state: string,
): { found: boolean; codeVerifier: string | null } {
  const db = getDb();
  const row = db
    .prepare('SELECT provider, code_verifier FROM oauth_states WHERE state = ?')
    .get(state) as { provider?: string; code_verifier?: string | null } | undefined;

  db.prepare('DELETE FROM oauth_states WHERE state = ?').run(state);
  if (!row || row.provider !== provider) {
    return { found: false, codeVerifier: null };
  }
  return { found: true, codeVerifier: row.code_verifier ?? null };
}

async function fetchJsonSafe(
  url: string,
  init?: RequestInit,
): Promise<{ ok: boolean; data: Record<string, unknown>; reason?: string }> {
  try {
    const res = await fetch(url, init);
    const text = await res.text();
    let parsed: Record<string, unknown> = {};
    try {
      parsed = text ? (JSON.parse(text) as Record<string, unknown>) : {};
    } catch {
      parsed = { raw: text };
    }
    if (!res.ok) {
      return {
        ok: false,
        data: parsed,
        reason: `N/A — OAuth token exchange failed (${res.status}).`,
      };
    }
    return { ok: true, data: parsed };
  } catch (err) {
    return {
      ok: false,
      data: {},
      reason: `N/A — OAuth token exchange request failed: ${
        err instanceof Error ? err.message : String(err)
      }`,
    };
  }
}

function expiresAtFromNow(expiresIn: number | undefined): string | null {
  if (!expiresIn || Number.isNaN(expiresIn)) return null;
  return new Date(Date.now() + expiresIn * 1000).toISOString();
}

export async function exchangeOAuthCode(
  provider: OAuthProvider,
  code: string,
  codeVerifier: string | null,
): Promise<TokenResult> {
  const clientId = getClientId(provider);
  const clientSecret = getClientSecret(provider);
  if (!clientId || !clientSecret) {
    return {
      ok: false,
      reason: 'N/A — provider OAuth credentials are not configured on the server.',
    };
  }

  const redirectUri = redirectUriFor(provider);

  if (provider === 'instagram' || provider === 'facebook') {
    const url = new URL(getConfig(provider).tokenEndpoint);
    url.searchParams.set('client_id', clientId);
    url.searchParams.set('client_secret', clientSecret);
    url.searchParams.set('redirect_uri', redirectUri);
    url.searchParams.set('code', code);
    const exchanged = await fetchJsonSafe(url.toString());
    if (!exchanged.ok) {
      return { ok: false, reason: exchanged.reason };
    }
    return {
      ok: true,
      accessToken: String(exchanged.data.access_token ?? ''),
      expiresIn: Number(exchanged.data.expires_in ?? 0),
    };
  }

  if (provider === 'twitter') {
    const body = new URLSearchParams({
      code,
      grant_type: 'authorization_code',
      client_id: clientId,
      redirect_uri: redirectUri,
      code_verifier: codeVerifier ?? '',
    });
    const authHeader = Buffer.from(`${clientId}:${clientSecret}`).toString('base64');
    const exchanged = await fetchJsonSafe(getConfig(provider).tokenEndpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
        Authorization: `Basic ${authHeader}`,
      },
      body: body.toString(),
    });
    if (!exchanged.ok) return { ok: false, reason: exchanged.reason };
    return {
      ok: true,
      accessToken: String(exchanged.data.access_token ?? ''),
      refreshToken: String(exchanged.data.refresh_token ?? ''),
      expiresIn: Number(exchanged.data.expires_in ?? 0),
    };
  }

  if (provider === 'youtube' || provider === 'linkedin' || provider === 'tiktok') {
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      redirect_uri: redirectUri,
    });
    if (provider === 'tiktok') {
      body.set('client_key', clientId);
      body.set('client_secret', clientSecret);
    } else {
      body.set('client_id', clientId);
      body.set('client_secret', clientSecret);
    }

    const exchanged = await fetchJsonSafe(getConfig(provider).tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!exchanged.ok) return { ok: false, reason: exchanged.reason };

    return {
      ok: true,
      accessToken: String(exchanged.data.access_token ?? ''),
      refreshToken: String(exchanged.data.refresh_token ?? ''),
      expiresIn: Number(exchanged.data.expires_in ?? 0),
    };
  }

  if (provider === 'pinterest') {
    const body = new URLSearchParams({
      grant_type: 'authorization_code',
      code,
      redirect_uri: redirectUri,
      client_id: clientId,
      client_secret: clientSecret,
      code_verifier: codeVerifier ?? '',
    });
    const exchanged = await fetchJsonSafe(getConfig(provider).tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: body.toString(),
    });
    if (!exchanged.ok) return { ok: false, reason: exchanged.reason };
    return {
      ok: true,
      accessToken: String(exchanged.data.access_token ?? ''),
      refreshToken: String(exchanged.data.refresh_token ?? ''),
      expiresIn: Number(exchanged.data.expires_in ?? 0),
    };
  }

  return {
    ok: false,
    reason: 'N/A — unsupported OAuth provider.',
  };
}

export function upsertOAuthConnection(
  provider: OAuthProvider,
  accessToken: string,
  refreshToken: string | undefined,
  expiresIn: number | undefined,
): void {
  const db = getDb();
  const now = new Date().toISOString();
  const accountLabel = `${getProviderDisplayName(provider)} account`;
  db.prepare(`
    INSERT INTO social_connections
      (provider, account_label, access_token, refresh_token, token_expires_at, metadata_json, connected_at, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(provider) DO UPDATE SET
      account_label = excluded.account_label,
      access_token = excluded.access_token,
      refresh_token = excluded.refresh_token,
      token_expires_at = excluded.token_expires_at,
      metadata_json = excluded.metadata_json,
      connected_at = excluded.connected_at,
      updated_at = excluded.updated_at
  `).run(
    provider,
    accountLabel,
    accessToken,
    refreshToken ?? null,
    expiresAtFromNow(expiresIn),
    JSON.stringify({ provider }),
    now,
    now,
  );
}

export function deleteOAuthConnection(provider: OAuthProvider): void {
  const db = getDb();
  db.prepare('DELETE FROM social_connections WHERE provider = ?').run(provider);
}

export function getOAuthConnectionStatuses(): OAuthConnectionStatus[] {
  const db = getDb();
  const rows = db.prepare(`
    SELECT provider, account_label, connected_at
    FROM social_connections
  `).all() as Array<{
    provider: string;
    account_label: string | null;
    connected_at: string | null;
  }>;

  const map = new Map(rows.map((r) => [r.provider, r]));

  return OAUTH_PROVIDERS.map((provider) => {
    const connection = map.get(provider);
    const configured = isProviderConfigured(provider);
    return {
      provider,
      displayName: getProviderDisplayName(provider),
      connected: Boolean(connection),
      configured: configured.ok,
      accountLabel: connection?.account_label ?? null,
      connectedAt: connection?.connected_at ?? null,
      reason: configured.reason,
    };
  });
}
