import { TwitterApi } from 'twitter-api-v2';
import type { PlatformResult } from '@/types';

export interface SocialPostPayload {
  caption: string;
  hashtags: string;
  screenshotPath: string | null;
  videoPath: string | null;
}

function buildPublicAssetUrl(assetPath: string | null): string | null {
  if (!assetPath) return null;
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
  return `${baseUrl}${assetPath}`;
}

function composePostText(payload: SocialPostPayload): string {
  const lines = [payload.caption, payload.hashtags].filter(Boolean);
  const videoUrl = buildPublicAssetUrl(payload.videoPath);
  if (videoUrl) {
    lines.push(`🎬 Watch & download: ${videoUrl}`);
  }
  return lines.join('\n\n').trim();
}

function trimForTwitter(text: string): string {
  if (text.length <= 280) return text;
  return `${text.slice(0, 276)}…`;
}

// ─── Twitter / X ──────────────────────────────────────────────────────────────
export async function postToTwitter(
  payload: SocialPostPayload,
): Promise<PlatformResult> {
  const {
    TWITTER_API_KEY,
    TWITTER_API_SECRET,
    TWITTER_ACCESS_TOKEN,
    TWITTER_ACCESS_SECRET,
  } = process.env;

  if (
    !TWITTER_API_KEY ||
    !TWITTER_API_SECRET ||
    !TWITTER_ACCESS_TOKEN ||
    !TWITTER_ACCESS_SECRET
  ) {
    return {
      platform: 'twitter',
      success: false,
      message:
        'N/A — Twitter API credentials are incomplete; set TWITTER_API_KEY, TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN, and TWITTER_ACCESS_SECRET.',
    };
  }

  try {
    const client = new TwitterApi({
      appKey: TWITTER_API_KEY,
      appSecret: TWITTER_API_SECRET,
      accessToken: TWITTER_ACCESS_TOKEN,
      accessSecret: TWITTER_ACCESS_SECRET,
    });

    const tweetText = trimForTwitter(composePostText(payload));
    const tweet = await client.v2.tweet(tweetText);

    return {
      platform: 'twitter',
      success: true,
      message: 'Posted to X successfully.',
      postId: tweet.data.id,
      url: `https://x.com/i/web/status/${tweet.data.id}`,
    };
  } catch (err) {
    return {
      platform: 'twitter',
      success: false,
      message: `N/A — Twitter/X publish failed: ${
        err instanceof Error ? err.message : String(err)
      }`,
    };
  }
}

// ─── Instagram ────────────────────────────────────────────────────────────────
export async function postToInstagram(
  _payload: SocialPostPayload,
): Promise<PlatformResult> {
  const { INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_BUSINESS_ACCOUNT_ID } = process.env;

  if (!INSTAGRAM_ACCESS_TOKEN || !INSTAGRAM_BUSINESS_ACCOUNT_ID) {
    return {
      platform: 'instagram',
      success: false,
      message:
        'N/A — Instagram API is not configured (missing INSTAGRAM_ACCESS_TOKEN / INSTAGRAM_BUSINESS_ACCOUNT_ID).',
    };
  }

  return {
    platform: 'instagram',
    success: false,
    message:
      'N/A — Instagram auto-post video endpoint wiring is not implemented yet in src/lib/social.ts.',
  };
}

// ─── Facebook ─────────────────────────────────────────────────────────────────
export async function postToFacebook(
  _payload: SocialPostPayload,
): Promise<PlatformResult> {
  const { FACEBOOK_ACCESS_TOKEN, FACEBOOK_PAGE_ID } = process.env;

  if (!FACEBOOK_ACCESS_TOKEN || !FACEBOOK_PAGE_ID) {
    return {
      platform: 'facebook',
      success: false,
      message:
        'N/A — Facebook API is not configured (missing FACEBOOK_ACCESS_TOKEN / FACEBOOK_PAGE_ID).',
    };
  }

  return {
    platform: 'facebook',
    success: false,
    message:
      'N/A — Facebook video auto-post endpoint wiring is not implemented yet in src/lib/social.ts.',
  };
}

// ─── Dispatch to all platforms ────────────────────────────────────────────────
export async function postToAllPlatforms(
  platforms: string[],
  payload: SocialPostPayload,
): Promise<PlatformResult[]> {
  const handlers: Record<string, () => Promise<PlatformResult>> = {
    twitter: () => postToTwitter(payload),
    instagram: () => postToInstagram(payload),
    facebook: () => postToFacebook(payload),
  };

  const settled = await Promise.allSettled(
    platforms.map((p) =>
      handlers[p]
        ? handlers[p]()
        : Promise.resolve<PlatformResult>({
            platform: p,
            success: false,
            message: `N/A — unknown platform: ${p}`,
          }),
    ),
  );

  return settled.map((r, i) =>
    r.status === 'fulfilled'
      ? r.value
      : {
          platform: platforms[i],
          success: false,
          message: `N/A — platform dispatch failed: ${
            (r.reason as Error)?.message ?? 'unknown error'
          }`,
        },
  );
}
