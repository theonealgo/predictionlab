'use client';

import { useEffect, useMemo, useState } from 'react';

type BillingCycle = 'monthly' | 'yearly';
type ProviderKey =
  | 'instagram'
  | 'facebook'
  | 'twitter'
  | 'tiktok'
  | 'youtube'
  | 'linkedin'
  | 'pinterest';

interface OAuthConnectionStatus {
  provider: ProviderKey;
  displayName: string;
  connected: boolean;
  configured: boolean;
  accountLabel: string | null;
  connectedAt: string | null;
  reason: string | null;
}

const PROVIDERS: Array<{ key: ProviderKey; icon: string }> = [
  { key: 'instagram', icon: '📸' },
  { key: 'facebook', icon: '📘' },
  { key: 'twitter', icon: '𝕏' },
  { key: 'tiktok', icon: '🎵' },
  { key: 'youtube', icon: '▶️' },
  { key: 'linkedin', icon: '💼' },
  { key: 'pinterest', icon: '📌' },
];

const PLAN_FEATURES = [
  'Voiceovers',
  'AI-assisted content generation',
  'Hook + script builder',
  'Auto background soundtrack',
  'AI edits (zooms, cuts, transitions)',
  'Cinematic caption overlays',
  'No watermark on exports',
  'Downloadable videos',
  'Unlimited exports',
  'Unlimited custom templates',
] as const;

const PRICING = [
  {
    name: 'Starter',
    subtitle: 'For launching your first channel',
    monthly: 29,
    yearlyEquivalent: 19,
    credits: 500,
    teamMembers: 3,
    support: 'Priority chat support',
    highlight: 'Great for first-time creators',
    extra: [],
  },
  {
    name: 'Growth',
    subtitle: 'For creators scaling output',
    monthly: 39,
    yearlyEquivalent: 29,
    credits: 1000,
    teamMembers: 5,
    support: 'Priority chat support',
    highlight: 'Most selected',
    extra: ['AI assistant', 'UGC workflow'],
  },
  {
    name: 'Influencer',
    subtitle: 'For teams publishing daily',
    monthly: 69,
    yearlyEquivalent: 49,
    credits: 2000,
    teamMembers: 7,
    support: 'Priority chat support',
    highlight: 'High-volume creators',
    extra: ['AI assistant', 'UGC workflow'],
  },
  {
    name: 'Ultra',
    subtitle: 'For studios and agencies',
    monthly: 99,
    yearlyEquivalent: 79,
    credits: 5000,
    teamMembers: 10,
    support: 'Premium + call support',
    highlight: 'For advanced teams',
    extra: ['AI assistant', 'UGC workflow', 'API access', 'Early feature access'],
  },
];

export default function HomePage() {
  const [billing, setBilling] = useState<BillingCycle>('monthly');
  const [connections, setConnections] = useState<OAuthConnectionStatus[]>([]);
  const [loadingConnections, setLoadingConnections] = useState(true);
  const [oauthNotice, setOauthNotice] = useState<string | null>(null);
  const [connectingProvider, setConnectingProvider] = useState<ProviderKey | null>(null);
  const [disconnectingProvider, setDisconnectingProvider] = useState<ProviderKey | null>(null);

  async function fetchConnections() {
    try {
      const res = await fetch('/api/oauth/connections');
      if (!res.ok) throw new Error(`Failed to load OAuth statuses (${res.status})`);
      const data = (await res.json()) as OAuthConnectionStatus[];
      setConnections(data);
    } catch (err) {
      setOauthNotice(
        `N/A — could not fetch social connection status: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setLoadingConnections(false);
    }
  }

  useEffect(() => {
    void fetchConnections();
  }, []);

  useEffect(() => {
    function handleOAuthMessage(event: MessageEvent) {
      const data = event.data as
        | { source?: string; provider?: string; success?: boolean; message?: string }
        | undefined;
      if (!data || data.source !== 'streamly-oauth') return;
      if (typeof data.message === 'string') {
        setOauthNotice(data.message);
      } else if (data.success) {
        setOauthNotice('Account connected.');
      } else {
        setOauthNotice('N/A — OAuth flow ended without a success confirmation.');
      }
      setConnectingProvider(null);
      void fetchConnections();
    }

    window.addEventListener('message', handleOAuthMessage);
    return () => window.removeEventListener('message', handleOAuthMessage);
  }, []);

  function getConnection(provider: ProviderKey): OAuthConnectionStatus | undefined {
    return connections.find((c) => c.provider === provider);
  }

  function openOAuthPopup(provider: ProviderKey) {
    setOauthNotice(null);
    setConnectingProvider(provider);
    const popup = window.open(
      `/api/oauth/${provider}/start`,
      `oauth_${provider}`,
      'popup=yes,width=560,height=720,left=120,top=80',
    );
    if (!popup) {
      setConnectingProvider(null);
      setOauthNotice('N/A — popup was blocked by the browser. Enable popups and try again.');
      return;
    }
    popup.focus();
  }

  async function disconnectProvider(provider: ProviderKey) {
    setDisconnectingProvider(provider);
    try {
      const res = await fetch(`/api/oauth/${provider}/disconnect`, { method: 'POST' });
      if (!res.ok) throw new Error(`Disconnect failed (${res.status})`);
      setOauthNotice(`${provider} disconnected.`);
      await fetchConnections();
    } catch (err) {
      setOauthNotice(
        `N/A — could not disconnect ${provider}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    } finally {
      setDisconnectingProvider(null);
    }
  }

  const trustLine = useMemo(() => {
    if (loadingConnections) return 'Checking linked platforms...';
    const connectedCount = connections.filter((c) => c.connected).length;
    return connectedCount > 0
      ? `${connectedCount} social account${connectedCount === 1 ? '' : 's'} currently connected.`
      : 'No social accounts connected yet.';
  }, [connections, loadingConnections]);

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      {/* Nav */}
      <header className="sticky top-0 z-20 border-b border-slate-800/80 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-4">
          <div className="text-xl font-bold tracking-tight text-indigo-300">streamly.blog</div>
          <nav className="hidden items-center gap-5 text-sm text-slate-300 md:flex">
            <a href="#tutorials" className="hover:text-white">Tutorials</a>
            <a href="#affiliate" className="hover:text-white">Affiliate</a>
            <a href="#pricing" className="hover:text-white">Pricing</a>
            <a href="#blog" className="hover:text-white">Blog</a>
            <a href="#tools" className="hover:text-white">Tools</a>
            <a href="#projects" className="hover:text-white">Projects</a>
          </nav>
          <div className="flex items-center gap-2">
            <a
              href="/social-media-generator"
              className="rounded-lg border border-slate-700 px-3 py-1.5 text-sm font-medium text-slate-200 hover:border-slate-500"
            >
              Sign Up / Login
            </a>
            <a
              href="/social-media-generator"
              className="rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-indigo-500"
            >
              Open App
            </a>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-6 pb-16 pt-10">
        {/* Hero */}
        <section className="rounded-2xl border border-indigo-500/25 bg-gradient-to-br from-slate-900 to-slate-800 px-8 py-10">
          <p className="inline-flex rounded-full border border-indigo-400/40 bg-indigo-500/10 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-indigo-200">
            Public Platform + Internal OAuth Engine
          </p>
          <h1 className="mt-4 text-4xl font-black leading-tight md:text-5xl">
            Create faceless videos fast.
            <br />
            Publish everywhere from one dashboard.
          </h1>
          <p className="mt-4 max-w-3xl text-slate-300">
            streamly.blog handles social integrations for you. Your users never need API keys or manual setup.
            They just sign in, tap connect, approve access, and they are done.
          </p>
          <div className="mt-6 flex flex-wrap gap-3">
            <a
              href="/social-media-generator"
              className="rounded-xl bg-indigo-600 px-5 py-3 text-sm font-bold hover:bg-indigo-500"
            >
              Get Started
            </a>
            <a
              href="#connections"
              className="rounded-xl border border-slate-600 px-5 py-3 text-sm font-semibold hover:border-slate-400"
            >
              Connect Social Accounts
            </a>
          </div>
          <p className="mt-4 text-sm text-slate-400">{trustLine}</p>
        </section>

        {/* OAuth Connection Flow */}
        <section id="connections" className="mt-10">
          <div className="mb-4">
            <h2 className="text-2xl font-bold">One-click account linking</h2>
            <p className="mt-1 text-slate-400">
              Flow: sign up → click connect → popup login → account connected.
            </p>
          </div>

          {oauthNotice && (
            <div className="mb-4 rounded-lg border border-slate-700 bg-slate-900 px-4 py-3 text-sm text-slate-300">
              {oauthNotice}
            </div>
          )}

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {PROVIDERS.map((provider) => {
              const status = getConnection(provider.key);
              const isConnected = Boolean(status?.connected);
              const connectDisabled = connectingProvider === provider.key;
              const disconnectDisabled = disconnectingProvider === provider.key;
              return (
                <div
                  key={provider.key}
                  className="rounded-xl border border-slate-800 bg-slate-900/70 p-4"
                >
                  <div className="mb-2 flex items-center justify-between">
                    <div className="font-semibold">
                      {provider.icon} {status?.displayName ?? provider.key}
                    </div>
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-semibold ${
                        isConnected
                          ? 'bg-emerald-500/20 text-emerald-300'
                          : 'bg-slate-700 text-slate-300'
                      }`}
                    >
                      {isConnected ? 'Connected' : 'Not Connected'}
                    </span>
                  </div>

                  <p className="min-h-10 text-xs text-slate-400">
                    {!status
                      ? 'Checking status...'
                      : status.configured
                        ? status.accountLabel ?? 'Ready to connect.'
                        : status.reason ?? 'N/A — provider not configured.'}
                  </p>

                  <div className="mt-3 flex gap-2">
                    <button
                      onClick={() => openOAuthPopup(provider.key)}
                      disabled={connectDisabled || Boolean(status && !status.configured)}
                      className="flex-1 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold disabled:cursor-not-allowed disabled:bg-indigo-900"
                    >
                      {connectDisabled ? 'Opening…' : `Connect ${status?.displayName ?? provider.key}`}
                    </button>
                    {isConnected && (
                      <button
                        onClick={() => void disconnectProvider(provider.key)}
                        disabled={disconnectDisabled}
                        className="rounded-lg border border-slate-600 px-3 py-2 text-xs font-semibold disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {disconnectDisabled ? '...' : 'Disconnect'}
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        {/* Pricing */}
        <section id="pricing" className="mt-14">
          <div className="text-center">
            <p className="text-xs font-semibold uppercase tracking-wider text-indigo-300">
              Clear pricing
            </p>
            <h2 className="mt-2 text-3xl font-black">Pick the plan that fits your channel</h2>
            <p className="mt-2 text-slate-400">
              Annual billing gives a meaningful discount and can be canceled at any time.
            </p>
          </div>

          <div className="mt-5 flex justify-center">
            <div className="inline-flex rounded-xl border border-slate-700 bg-slate-900 p-1">
              <button
                onClick={() => setBilling('monthly')}
                className={`rounded-lg px-4 py-2 text-sm font-semibold ${
                  billing === 'monthly'
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-300 hover:text-white'
                }`}
              >
                Monthly
              </button>
              <button
                onClick={() => setBilling('yearly')}
                className={`rounded-lg px-4 py-2 text-sm font-semibold ${
                  billing === 'yearly'
                    ? 'bg-indigo-600 text-white'
                    : 'text-slate-300 hover:text-white'
                }`}
              >
                Yearly
              </button>
            </div>
          </div>

          <div className="mt-8 grid gap-5 lg:grid-cols-4">
            {PRICING.map((plan) => {
              const price = billing === 'monthly' ? plan.monthly : plan.yearlyEquivalent;
              return (
                <article
                  key={plan.name}
                  className={`rounded-2xl border p-5 ${
                    plan.name === 'Growth'
                      ? 'border-indigo-500 bg-indigo-500/10'
                      : 'border-slate-800 bg-slate-900/70'
                  }`}
                >
                  <p className="text-sm font-semibold text-indigo-300">{plan.name}</p>
                  <p className="mt-1 text-xs text-slate-400">{plan.subtitle}</p>
                  <div className="mt-4">
                    <span className="text-4xl font-black">${price}</span>
                    <span className="ml-1 text-slate-400">/month</span>
                  </div>

                  <button className="mt-4 w-full rounded-lg bg-indigo-600 px-3 py-2 text-sm font-bold hover:bg-indigo-500">
                    Select Plan
                  </button>

                  <div className="mt-4 space-y-1 text-sm text-slate-300">
                    <p className="font-semibold">Unlimited Series</p>
                    <p>{plan.credits.toLocaleString()} Credits /month</p>
                    <p>Auto-post videos</p>
                  </div>

                  <div className="mt-4 space-y-1 text-xs text-slate-300">
                    {PLAN_FEATURES.map((feature) => (
                      <p key={`${plan.name}-${feature}`} className="flex items-start gap-2">
                        <span className="mt-0.5 text-emerald-400">✓</span>
                        <span>{feature}</span>
                      </p>
                    ))}
                    <p className="flex items-start gap-2">
                      <span className="mt-0.5 text-emerald-400">✓</span>
                      <span>{plan.teamMembers} team members</span>
                    </p>
                    <p className="flex items-start gap-2">
                      <span className="mt-0.5 text-emerald-400">✓</span>
                      <span>{plan.support}</span>
                    </p>
                    {plan.extra.map((extra) => (
                      <p key={`${plan.name}-${extra}`} className="flex items-start gap-2">
                        <span className="mt-0.5 text-emerald-400">✓</span>
                        <span>{extra}</span>
                      </p>
                    ))}
                  </div>

                  <p className="mt-4 text-xs font-semibold uppercase tracking-wide text-amber-300">
                    {plan.highlight}
                  </p>
                </article>
              );
            })}
          </div>
        </section>

        {/* How it works */}
        <section className="mt-14 grid gap-4 md:grid-cols-5">
          {[
            ['1', 'Create account', 'Users register from the public streamly.blog page.'],
            ['2', 'Tap connect', 'They choose a platform like Instagram or YouTube.'],
            ['3', 'Popup login', 'A secure OAuth popup opens for provider authorization.'],
            ['4', 'Approve access', 'The provider returns a token to streamly.blog automatically.'],
            ['5', 'Ready to publish', 'Videos can be posted without any manual API setup.'],
          ].map(([step, title, body]) => (
            <div key={step} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
              <div className="mb-2 inline-flex h-7 w-7 items-center justify-center rounded-full bg-indigo-600 text-sm font-bold">
                {step}
              </div>
              <h3 className="font-semibold">{title}</h3>
              <p className="mt-1 text-xs text-slate-400">{body}</p>
            </div>
          ))}
        </section>

        {/* FAQ */}
        <section className="mt-14 rounded-2xl border border-slate-800 bg-slate-900/70 p-6">
          <h2 className="text-2xl font-bold">Frequently asked questions</h2>
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            <FaqItem
              question="How does OAuth connection work in streamly.blog?"
              answer="streamly.blog stores provider OAuth settings internally. Users only approve access in a popup. No keys or tokens are required from the user."
            />
            <FaqItem
              question="Can users manage their remaining credits?"
              answer="Yes. Credits are tracked monthly and shown in-app after login."
            />
            <FaqItem
              question="Can I upgrade or downgrade plans?"
              answer="Yes. Plan changes are reflected in credits and team limits on the next billing cycle, or immediately based on your billing rules."
            />
            <FaqItem
              question="Can connected platforms be removed?"
              answer="Yes. Every connected account can be disconnected from the same dashboard in one click."
            />
          </div>
        </section>

        {/* Footer */}
        <footer className="mt-16 border-t border-slate-800 pt-8 text-sm text-slate-400">
          <div className="flex flex-col items-start justify-between gap-4 md:flex-row md:items-center">
            <div>
              <p className="font-semibold text-slate-200">streamly.blog</p>
              <p>Public-facing faceless video platform with internal OAuth social integrations.</p>
            </div>
            <div className="flex gap-4">
              <a href="#pricing" className="hover:text-white">Pricing</a>
              <a href="#tools" className="hover:text-white">Tools</a>
              <a href="#blog" className="hover:text-white">Blog</a>
            </div>
          </div>
        </footer>
      </main>
    </div>
  );
}

function FaqItem({ question, answer }: { question: string; answer: string }) {
  return (
    <article className="rounded-lg border border-slate-800 bg-slate-950 p-4">
      <h3 className="font-semibold text-slate-200">{question}</h3>
      <p className="mt-1 text-sm text-slate-400">{answer}</p>
    </article>
  );
}
