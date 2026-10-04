import type { DatabaseSync } from 'node:sqlite';

export const UNLIMITED_SERIES_CODE = 'unlimited_series';
export const UNLIMITED_SERIES_NAME = 'Unlimited Series';
export const UNLIMITED_SERIES_MONTHLY_CREDITS = 5000;
export const CREDITS_PER_VIDEO_EXPORT = 25;

export interface UnlimitedSeriesFeatures {
  autoPostVideos: boolean;
  voiceovers: boolean;
  aiGeneratedContent: boolean;
  scriptAndHookGeneration: boolean;
  backgroundMusic: boolean;
  aiEffectsZoomsTransitions: boolean;
  cinematicCaptions: boolean;
  noWatermark: boolean;
  downloadVideos: boolean;
  unlimitedExports: boolean;
  unlimitedCustomTemplates: boolean;
}

export interface UnlimitedSeriesSnapshot {
  planCode: string;
  planName: string;
  monthlyCredits: number;
  creditsUsedThisMonth: number;
  creditsRemaining: number;
  monthKey: string;
  features: UnlimitedSeriesFeatures;
  activeTemplateCount: number;
}

export const UNLIMITED_SERIES_FEATURES: UnlimitedSeriesFeatures = {
  autoPostVideos: true,
  voiceovers: true,
  aiGeneratedContent: true,
  scriptAndHookGeneration: true,
  backgroundMusic: true,
  aiEffectsZoomsTransitions: true,
  cinematicCaptions: true,
  noWatermark: true,
  downloadVideos: true,
  unlimitedExports: true,
  unlimitedCustomTemplates: true,
};

function currentMonthKey(now: Date = new Date()): string {
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, '0');
  return `${y}-${m}`;
}

function ensureCurrentMonthUsageRow(db: DatabaseSync): { monthKey: string; creditsUsed: number } {
  const monthKey = currentMonthKey();
  const now = new Date().toISOString();

  db.prepare(`
    INSERT INTO monthly_credit_usage (month_key, credits_used, updated_at)
    VALUES (?, 0, ?)
    ON CONFLICT(month_key) DO NOTHING
  `).run(monthKey, now);

  const row = db
    .prepare('SELECT credits_used FROM monthly_credit_usage WHERE month_key = ?')
    .get(monthKey) as { credits_used?: number } | undefined;

  return { monthKey, creditsUsed: Number(row?.credits_used ?? 0) };
}

function getPlanRow(db: DatabaseSync): { monthly_credits: number } {
  const row = db
    .prepare('SELECT monthly_credits FROM plan_subscription WHERE id = 1')
    .get() as { monthly_credits?: number } | undefined;

  return { monthly_credits: Number(row?.monthly_credits ?? UNLIMITED_SERIES_MONTHLY_CREDITS) };
}

export function getUnlimitedSeriesSnapshot(db: DatabaseSync): UnlimitedSeriesSnapshot {
  const usage = ensureCurrentMonthUsageRow(db);
  const plan = getPlanRow(db);

  const templateRow = db
    .prepare(`
      SELECT COUNT(DISTINCT template_name) AS cnt
      FROM social_jobs
      WHERE template_name IS NOT NULL AND trim(template_name) <> ''
    `)
    .get() as { cnt?: number } | undefined;

  const monthlyCredits = Number(plan.monthly_credits || UNLIMITED_SERIES_MONTHLY_CREDITS);
  const creditsUsedThisMonth = Math.max(0, Number(usage.creditsUsed || 0));
  const creditsRemaining = Math.max(0, monthlyCredits - creditsUsedThisMonth);

  return {
    planCode: UNLIMITED_SERIES_CODE,
    planName: UNLIMITED_SERIES_NAME,
    monthlyCredits,
    creditsUsedThisMonth,
    creditsRemaining,
    monthKey: usage.monthKey,
    features: UNLIMITED_SERIES_FEATURES,
    activeTemplateCount: Number(templateRow?.cnt ?? 0),
  };
}

export function canConsumeCredits(
  db: DatabaseSync,
  creditsNeeded: number,
): { ok: boolean; creditsRemaining: number; monthlyCredits: number } {
  const snapshot = getUnlimitedSeriesSnapshot(db);
  return {
    ok: snapshot.creditsRemaining >= creditsNeeded,
    creditsRemaining: snapshot.creditsRemaining,
    monthlyCredits: snapshot.monthlyCredits,
  };
}

export function consumeCredits(
  db: DatabaseSync,
  credits: number,
): { creditsUsedThisMonth: number; creditsRemaining: number; monthKey: string } {
  const usage = ensureCurrentMonthUsageRow(db);
  const now = new Date().toISOString();

  db.prepare(`
    UPDATE monthly_credit_usage
    SET credits_used = credits_used + ?, updated_at = ?
    WHERE month_key = ?
  `).run(credits, now, usage.monthKey);

  const snapshot = getUnlimitedSeriesSnapshot(db);
  return {
    creditsUsedThisMonth: snapshot.creditsUsedThisMonth,
    creditsRemaining: snapshot.creditsRemaining,
    monthKey: snapshot.monthKey,
  };
}
