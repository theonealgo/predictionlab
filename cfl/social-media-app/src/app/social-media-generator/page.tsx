'use client';

import { useState, useEffect, useCallback } from 'react';
import type {
  SocialJob,
  PostHistory,
  RunJobResult,
  CreateJobInput,
  PlanSnapshot,
} from '@/types';

// ─── Platform config ───────────────────────────────────────────────────────────
const PLATFORMS = [
  { id: 'twitter', label: 'Twitter / X', icon: '🐦', color: 'bg-sky-500' },
  { id: 'instagram', label: 'Instagram', icon: '📸', color: 'bg-pink-500' },
  { id: 'facebook', label: 'Facebook', icon: '📘', color: 'bg-blue-600' },
];

const STATUS_STYLES: Record<string, string> = {
  success: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
  partial: 'bg-amber-500/20 text-amber-400 border border-amber-500/30',
  pending: 'bg-sky-500/20 text-sky-400 border border-sky-500/30',
  error: 'bg-red-500/20 text-red-400 border border-red-500/30',
};

const EMPTY_FORM: CreateJobInput = {
  name: '',
  url: '',
  screenshot_selector: '',
  text_selector: '',
  template_name: 'Cinematic Default',
  platforms: [],
  schedule_time: '09:00',
};

// ─── Main component ────────────────────────────────────────────────────────────
export default function SocialMediaGeneratorPage() {
  const [jobs, setJobs] = useState<SocialJob[]>([]);
  const [posts, setPosts] = useState<PostHistory[]>([]);
  const [planSnapshot, setPlanSnapshot] = useState<PlanSnapshot | null>(null);
  const [loadingPlan, setLoadingPlan] = useState(true);
  const [loadingJobs, setLoadingJobs] = useState(true);

  // Modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [historyJobId, setHistoryJobId] = useState<string | null>(null);

  // Form state
  const [form, setForm] = useState<CreateJobInput>(EMPTY_FORM);
  const [formError, setFormError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  // Per-job run state
  const [runningJobId, setRunningJobId] = useState<string | null>(null);
  const [runResults, setRunResults] = useState<Record<string, RunJobResult>>({});
  const [deletingJobId, setDeletingJobId] = useState<string | null>(null);

  // ─── Data fetching ───────────────────────────────────────────────────────────
  const fetchJobs = useCallback(async () => {
    try {
      const res = await fetch('/api/jobs');
      if (res.ok) setJobs(await res.json() as SocialJob[]);
    } catch {
      // ignore
    } finally {
      setLoadingJobs(false);
    }
  }, []);

  const fetchPosts = useCallback(async (jobId?: string) => {
    const url = jobId ? `/api/posts?jobId=${jobId}` : '/api/posts';
    try {
      const res = await fetch(url);
      if (res.ok) setPosts(await res.json() as PostHistory[]);
    } catch {
      // ignore
    }
  }, []);

  const fetchPlan = useCallback(async () => {
    try {
      const res = await fetch('/api/account/plan');
      if (res.ok) setPlanSnapshot(await res.json() as PlanSnapshot);
    } catch {
      // ignore
    } finally {
      setLoadingPlan(false);
    }
  }, []);

  useEffect(() => { void fetchJobs(); }, [fetchJobs]);
  useEffect(() => { void fetchPlan(); }, [fetchPlan]);

  // ─── Handlers ───────────────────────────────────────────────────────────────
  async function handleCreateJob(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (form.platforms.length === 0) {
      setFormError('Select at least one platform.');
      return;
    }
    setCreating(true);
    try {
      const res = await fetch('/api/jobs', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(form),
      });
      const data = await res.json() as { error?: string };
      if (!res.ok) throw new Error(data.error ?? 'Failed to create job');
      setShowCreateModal(false);
      setForm(EMPTY_FORM);
      await fetchJobs();
      await fetchPlan();
    } catch (err) {
      setFormError((err as Error).message);
    } finally {
      setCreating(false);
    }
  }

  async function handleRunJob(jobId: string) {
    setRunningJobId(jobId);
    // Clear previous result for this job
    setRunResults((prev) => {
      const next = { ...prev };
      delete next[jobId];
      return next;
    });
    try {
      const res = await fetch(`/api/jobs/${jobId}/run`, { method: 'POST' });
      const data = await res.json() as RunJobResult;
      setRunResults((prev) => ({ ...prev, [jobId]: data }));
      await fetchJobs(); // refresh last_run / last_status
      await fetchPlan(); // refresh credits usage
    } catch (err) {
      setRunResults((prev) => ({
        ...prev,
        [jobId]: { success: false, error: (err as Error).message },
      }));
    } finally {
      setRunningJobId(null);
    }
  }

  async function handleDeleteJob(jobId: string) {
    if (!confirm('Delete this job and all its post history?')) return;
    setDeletingJobId(jobId);
    try {
      await fetch(`/api/jobs/${jobId}`, { method: 'DELETE' });
      setRunResults((prev) => {
        const next = { ...prev };
        delete next[jobId];
        return next;
      });
      await fetchJobs();
    } finally {
      setDeletingJobId(null);
    }
  }

  async function openHistory(jobId?: string) {
    setHistoryJobId(jobId ?? null);
    await fetchPosts(jobId);
    setShowHistoryModal(true);
  }

  function togglePlatform(id: string) {
    setForm((f) => ({
      ...f,
      platforms: f.platforms.includes(id)
        ? f.platforms.filter((p) => p !== id)
        : [...f.platforms, id],
    }));
  }

  function closeCreateModal() {
    setShowCreateModal(false);
    setForm(EMPTY_FORM);
    setFormError(null);
  }

  // ─── Derived ─────────────────────────────────────────────────────────────────
  const lastRunJob = jobs.reduce<SocialJob | null>(
    (best, j) =>
      j.last_run && (!best?.last_run || j.last_run > best.last_run) ? j : best,
    null,
  );
  const planFeatureLabels = [
    'Auto-Post Videos',
    'Voiceovers',
    'AI generated content',
    'Script & Hook Generation',
    'Background music',
    'AI effects, zooms, transitions',
    'Cinematic Captions',
    'No watermark',
    'Download Videos',
    'Unlimited Exports',
    'Unlimited Custom Templates',
  ];

  // ─── Render helpers ──────────────────────────────────────────────────────────
  function StatusBadge({ status }: { status: string | null }) {
    if (!status) return null;
    return (
      <span className={`px-2 py-0.5 rounded text-xs font-semibold ${STATUS_STYLES[status] ?? 'bg-slate-500/20 text-slate-400'}`}>
        {status.toUpperCase()}
      </span>
    );
  }

  // ─── JSX ─────────────────────────────────────────────────────────────────────
  const flaskUrl = process.env.NEXT_PUBLIC_FLASK_URL ?? 'http://localhost:5000';

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-800 text-white">

      {/* ── Top nav ──────────────────────────────────────────────────────────── */}
      <header className="sticky top-0 z-20 border-b border-slate-700/50 bg-slate-900/80 backdrop-blur">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-4">
            <a
              href={flaskUrl}
              className="text-slate-400 hover:text-white transition text-sm"
            >
              ← underdogs.bet
            </a>
            <span className="text-slate-700">|</span>
            <h1 className="text-xl font-bold tracking-tight">🤖 Social Media Generator</h1>
          </div>
          <div className="flex gap-3">
            <button
              onClick={() => void openHistory()}
              className="px-4 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm font-medium transition"
            >
              📋 Post History
            </button>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-sm font-bold transition"
            >
              + Create Job
            </button>
          </div>
        </div>
      </header>

      <main className="max-w-7xl mx-auto px-6 py-8">
        {/* ── Plan / entitlement card ───────────────────────────────────────── */}        
        <section className="mb-8 bg-slate-800/60 rounded-2xl border border-indigo-500/30 p-5">
          <div className="flex flex-col lg:flex-row lg:items-start lg:justify-between gap-4">
            <div>
              <p className="text-xs uppercase tracking-wider text-indigo-300/80 mb-1">
                Active Package
              </p>
              <h2 className="text-2xl font-bold">
                {planSnapshot?.planName ?? 'Unlimited Series'}
              </h2>
              <p className="text-slate-300 mt-1">5,000 Credits /month</p>
              <p className="text-slate-500 text-sm mt-1">Auto-Post Videos</p>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 min-w-[260px]">
              <div className="rounded-xl bg-slate-900/70 border border-slate-700 p-3">
                <p className="text-xs text-slate-500">Monthly Credits</p>
                <p className="text-xl font-bold">{planSnapshot?.monthlyCredits ?? '5,000'}</p>
              </div>
              <div className="rounded-xl bg-slate-900/70 border border-slate-700 p-3">
                <p className="text-xs text-slate-500">Used This Month</p>
                <p className="text-xl font-bold">{planSnapshot?.creditsUsedThisMonth ?? 0}</p>
              </div>
              <div className="rounded-xl bg-slate-900/70 border border-slate-700 p-3">
                <p className="text-xs text-slate-500">Credits Remaining</p>
                <p className="text-xl font-bold">{planSnapshot?.creditsRemaining ?? 'N/A'}</p>
              </div>
            </div>
          </div>

          <div className="mt-4 grid sm:grid-cols-2 lg:grid-cols-3 gap-2 text-sm">
            {planFeatureLabels.map((feature) => (
              <div key={feature} className="flex items-center gap-2 text-slate-200">
                <span className="text-emerald-400">✓</span>
                <span>{feature}</span>
              </div>
            ))}
          </div>

          <div className="mt-4 text-xs text-slate-500">
            {loadingPlan
              ? 'Loading plan status...'
              : `Billing cycle: ${planSnapshot?.monthKey ?? 'N/A'} • Active templates: ${planSnapshot?.activeTemplateCount ?? 0}`}
          </div>
        </section>

        {/* ── Stats ────────────────────────────────────────────────────────────── */}
        <div className="grid grid-cols-3 gap-4 mb-8">
          {[
            { icon: '⚙️', value: jobs.length, label: 'Total Jobs' },
            { icon: '✅', value: jobs.filter((j) => j.is_active).length, label: 'Active Jobs' },
            {
              icon: '🕐',
              value: lastRunJob
                ? new Date(lastRunJob.last_run!).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
                : '—',
              label: 'Last Run',
            },
          ].map((s) => (
            <div key={s.label} className="bg-slate-800/60 rounded-xl p-4 border border-slate-700/50">
              <div className="text-2xl mb-1">{s.icon}</div>
              <div className="text-2xl font-bold">{s.value}</div>
              <div className="text-slate-400 text-sm">{s.label}</div>
            </div>
          ))}
        </div>

        {/* ── Jobs section header ───────────────────────────────────────────────── */}
        <div className="mb-5 flex items-center justify-between">
          <h2 className="text-lg font-semibold text-slate-200">Automation Jobs</h2>
          <span className="text-slate-500 text-sm">{jobs.length} job{jobs.length !== 1 ? 's' : ''}</span>
        </div>

        {/* ── Jobs grid ────────────────────────────────────────────────────────── */}
        {loadingJobs ? (
          <p className="text-center py-20 text-slate-400">Loading…</p>
        ) : jobs.length === 0 ? (
          <div className="text-center py-20 bg-slate-800/40 rounded-2xl border-2 border-dashed border-slate-700">
            <div className="text-5xl mb-4">🤖</div>
            <p className="text-slate-300 text-lg font-semibold mb-2">No jobs yet</p>
            <p className="text-slate-500 text-sm mb-6">
              Create a job to start automating your daily social posts
            </p>
            <button
              onClick={() => setShowCreateModal(true)}
              className="px-6 py-3 bg-indigo-600 hover:bg-indigo-500 rounded-xl font-semibold transition"
            >
              + Create Your First Job
            </button>
          </div>
        ) : (
          <div className="grid gap-6 md:grid-cols-2 xl:grid-cols-3">
            {jobs.map((job) => {
              const result = runResults[job.id];
              const isRunning = runningJobId === job.id;

              return (
                <div
                  key={job.id}
                  className="bg-slate-800/60 rounded-2xl border border-slate-700/50 overflow-hidden hover:border-indigo-500/40 transition-colors"
                >
                  {/* Card body */}
                  <div className="p-5">
                    {/* Title + delete */}
                    <div className="flex items-start justify-between mb-2">
                      <div className="min-w-0">
                        <h3 className="font-bold text-lg truncate">{job.name}</h3>
                        <a
                          href={job.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-indigo-400 text-xs hover:underline block truncate max-w-[220px]"
                        >
                          {job.url}
                        </a>
                      </div>
                      <button
                        onClick={() => void handleDeleteJob(job.id)}
                        disabled={deletingJobId === job.id}
                        className="text-slate-500 hover:text-red-400 transition p-1 ml-2 shrink-0"
                        title="Delete job"
                      >
                        🗑
                      </button>
                    </div>

                    {/* Platform badges */}
                    <div className="flex flex-wrap gap-1.5 mb-3">
                      {job.platforms.map((p) => {
                        const info = PLATFORMS.find((pl) => pl.id === p);
                        return (
                          <span
                            key={p}
                            className={`px-2 py-0.5 rounded text-xs font-semibold text-white ${info?.color ?? 'bg-slate-600'}`}
                          >
                            {info?.icon} {info?.label ?? p}
                          </span>
                        );
                      })}
                    </div>

                    {/* Schedule */}
                    <p className="text-sm text-slate-400 mb-2">
                      🕐 Daily at{' '}
                      <span className="text-white font-mono">{job.schedule_time}</span>
                    </p>
                    <p className="text-sm text-slate-400 mb-2">
                      🎬 Template:{' '}
                      <span className="text-white">{job.template_name || 'Cinematic Default'}</span>
                    </p>

                    {/* Last run */}
                    <div className="text-xs text-slate-500 mb-4 flex items-center gap-2 flex-wrap">
                      {job.last_run ? (
                        <>
                          Last run: {new Date(job.last_run).toLocaleString()}
                          <StatusBadge status={job.last_status} />
                        </>
                      ) : (
                        'Never run'
                      )}
                    </div>

                    {/* Selectors */}
                    <div className="bg-slate-900/60 rounded-lg p-3 mb-4 text-xs font-mono space-y-1.5">
                      <div className="flex gap-2 items-start">
                        <span className="text-slate-500 shrink-0">📷</span>
                        <span
                          className="text-slate-300 truncate"
                          title={job.screenshot_selector}
                        >
                          {job.screenshot_selector}
                        </span>
                      </div>
                      <div className="flex gap-2 items-start">
                        <span className="text-slate-500 shrink-0">📝</span>
                        <span
                          className="text-slate-300 truncate"
                          title={job.text_selector}
                        >
                          {job.text_selector}
                        </span>
                      </div>
                    </div>

                    {/* Action buttons */}
                    <div className="flex gap-2">
                      <button
                        onClick={() => void handleRunJob(job.id)}
                        disabled={isRunning}
                        className="flex-1 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-800 disabled:cursor-not-allowed rounded-lg text-sm font-semibold transition flex items-center justify-center gap-2"
                      >
                        {isRunning ? (
                          <>
                            <span className="inline-block animate-spin">⟳</span>
                            Running…
                          </>
                        ) : (
                          '▶ Run Now'
                        )}
                      </button>
                      <button
                        onClick={() => void openHistory(job.id)}
                        className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-sm transition"
                        title="View post history for this job"
                      >
                        📋
                      </button>
                    </div>
                  </div>

                  {/* ── Run result panel ───────────────────────────────────────── */}
                  {result && (
                    <div
                      className={`border-t p-4 text-sm space-y-3 ${
                        result.success
                          ? 'bg-emerald-900/20 border-emerald-700/30'
                          : 'bg-red-900/20 border-red-700/30'
                      }`}
                    >
                      {result.error ? (
                        <p className="text-red-400 font-medium">❌ {result.error}</p>
                      ) : (
                        <>
                          <p className="text-emerald-400 font-semibold">✅ Job completed</p>

                          {result.hook && (
                            <div>
                              <p className="text-slate-400 text-xs mb-1">Hook</p>
                              <p className="text-white leading-snug">{result.hook}</p>
                            </div>
                          )}

                          {result.script && (
                            <div>
                              <p className="text-slate-400 text-xs mb-1">Script</p>
                              <p className="text-white leading-snug">{result.script}</p>
                            </div>
                          )}

                          {result.caption && (
                            <div>
                              <p className="text-slate-400 text-xs mb-1">Caption</p>
                              <p className="text-white leading-snug">{result.caption}</p>
                            </div>
                          )}

                          {result.hashtags && (
                            <div>
                              <p className="text-slate-400 text-xs mb-1">Hashtags</p>
                              <p className="text-indigo-400 font-mono text-xs leading-snug">
                                {result.hashtags}
                              </p>
                            </div>
                          )}

                          {result.screenshotPath && (
                            <img
                              src={result.screenshotPath}
                              alt="Page screenshot"
                              className="rounded-lg border border-slate-600 max-h-40 w-full object-contain bg-slate-900"
                            />
                          )}

                          {result.videoPath && (
                            <div className="space-y-2">
                              <p className="text-slate-400 text-xs">Generated Video</p>
                              <video
                                controls
                                src={result.videoPath}
                                className="rounded-lg border border-slate-600 w-full bg-slate-900 max-h-56"
                              >
                                {result.captionsPath && (
                                  <track
                                    default
                                    kind="captions"
                                    src={result.captionsPath}
                                    label="Cinematic captions"
                                  />
                                )}
                              </video>
                              {result.exportPath && (
                                <a
                                  href={result.exportPath}
                                  download
                                  className="inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold transition"
                                >
                                  ⬇ Download Video
                                </a>
                              )}
                            </div>
                          )}

                          {result.voiceoverPath && (
                            <div>
                              <p className="text-slate-400 text-xs mb-1">Voiceover Track</p>
                              <audio controls src={result.voiceoverPath} className="w-full" />
                            </div>
                          )}

                          {typeof result.creditsUsed === 'number' && (
                            <p className="text-xs text-slate-400">
                              Credits used: {result.creditsUsed} · Remaining: {result.creditsRemaining ?? 'N/A'}
                            </p>
                          )}

                          {result.videoNotes && result.videoNotes.length > 0 && (
                            <div className="space-y-1">
                              <p className="text-slate-400 text-xs">Video Notes</p>
                              {result.videoNotes.map((note, idx) => (
                                <p key={`${job.id}_note_${idx}`} className="text-xs text-slate-400">
                                  • {note}
                                </p>
                              ))}
                            </div>
                          )}

                          {result.platformResults && (
                            <div className="space-y-1">
                              {result.platformResults.map((r) => (
                                <div key={r.platform} className="flex items-start gap-2 text-xs">
                                  <span>{r.success ? '✅' : '⚠️'}</span>
                                  <span className="capitalize font-medium shrink-0">
                                    {r.platform}
                                  </span>
                                  <span className="text-slate-400">{r.message}</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </main>

      {/* ── Create Job Modal ───────────────────────────────────────────────────── */}
      {showCreateModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 rounded-2xl border border-slate-700 w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl">
            {/* Header */}
            <div className="p-6 border-b border-slate-700 flex items-center justify-between sticky top-0 bg-slate-900 rounded-t-2xl">
              <h2 className="text-xl font-bold">Create Automation Job</h2>
              <button
                onClick={closeCreateModal}
                className="text-slate-400 hover:text-white text-2xl leading-none"
              >
                ×
              </button>
            </div>

            <form onSubmit={(e) => void handleCreateJob(e)} className="p-6 space-y-5">
              {/* Name */}
              <label className="block">
                <span className="text-sm font-medium text-slate-300 block mb-1">Job Name *</span>
                <input
                  type="text"
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  placeholder="e.g. NHL Daily Predictions Post"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </label>

              {/* URL */}
              <label className="block">
                <span className="text-sm font-medium text-slate-300 block mb-1">Website URL *</span>
                <input
                  type="url"
                  required
                  value={form.url}
                  onChange={(e) => setForm({ ...form, url: e.target.value })}
                  placeholder="http://localhost:5000/sport/NHL/predictions"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </label>

              {/* Screenshot selector */}
              <label className="block">
                <span className="text-sm font-medium text-slate-300 block mb-1">
                  Screenshot CSS Selector *{' '}
                  <span className="font-normal text-slate-500">— element to capture</span>
                </span>
                <input
                  type="text"
                  required
                  value={form.screenshot_selector}
                  onChange={(e) => setForm({ ...form, screenshot_selector: e.target.value })}
                  placeholder=".predictions-table"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono text-sm"
                />
              </label>

              {/* Text selector */}
              <label className="block">
                <span className="text-sm font-medium text-slate-300 block mb-1">
                  Text CSS Selector *{' '}
                  <span className="font-normal text-slate-500">— element to extract text from</span>
                </span>
                <input
                  type="text"
                  required
                  value={form.text_selector}
                  onChange={(e) => setForm({ ...form, text_selector: e.target.value })}
                  placeholder=".results-container"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500 font-mono text-sm"
                />
              </label>

              {/* Template name */}              
              <label className="block">
                <span className="text-sm font-medium text-slate-300 block mb-1">
                  Video Template Name *{' '}
                  <span className="font-normal text-slate-500">— unlimited custom templates</span>
                </span>
                <input
                  type="text"
                  required
                  value={form.template_name}
                  onChange={(e) => setForm({ ...form, template_name: e.target.value })}
                  placeholder="Cinematic Default"
                  className="w-full px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-white placeholder-slate-500 focus:outline-none focus:border-indigo-500"
                />
              </label>

              {/* Platforms */}
              <div>
                <span className="text-sm font-medium text-slate-300 block mb-2">Platforms *</span>
                <div className="flex flex-wrap gap-3">
                  {PLATFORMS.map((p) => {
                    const selected = form.platforms.includes(p.id);
                    return (
                      <button
                        type="button"
                        key={p.id}
                        onClick={() => togglePlatform(p.id)}
                        className={`flex items-center gap-2 px-3 py-2 rounded-lg border-2 text-sm font-medium transition ${
                          selected
                            ? `${p.color} border-transparent text-white`
                            : 'border-slate-600 text-slate-400 hover:border-slate-500'
                        }`}
                      >
                        {p.icon} {p.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Schedule */}
              <label className="block">
                <span className="text-sm font-medium text-slate-300 block mb-1">
                  Daily Schedule Time *{' '}
                  <span className="font-normal text-slate-500">— when to auto-post</span>
                </span>
                <input
                  type="time"
                  required
                  value={form.schedule_time}
                  onChange={(e) => setForm({ ...form, schedule_time: e.target.value })}
                  className="px-3 py-2 bg-slate-800 border border-slate-600 rounded-lg text-white focus:outline-none focus:border-indigo-500"
                />
              </label>

              {/* Error */}
              {formError && (
                <div className="text-red-400 text-sm bg-red-900/20 border border-red-700/30 rounded-lg p-3">
                  {formError}
                </div>
              )}

              {/* Buttons */}
              <div className="flex gap-3 pt-1">
                <button
                  type="button"
                  onClick={closeCreateModal}
                  className="flex-1 py-2.5 bg-slate-700 hover:bg-slate-600 rounded-xl font-medium transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={creating}
                  className="flex-1 py-2.5 bg-indigo-600 hover:bg-indigo-500 disabled:bg-indigo-800 rounded-xl font-semibold transition"
                >
                  {creating ? 'Creating…' : 'Create Job'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* ── Post History Modal ─────────────────────────────────────────────────── */}
      {showHistoryModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-900 rounded-2xl border border-slate-700 w-full max-w-4xl max-h-[85vh] flex flex-col shadow-2xl">
            {/* Header */}
            <div className="p-6 border-b border-slate-700 flex items-center justify-between shrink-0">
              <div>
                <h2 className="text-xl font-bold">📋 Post History</h2>
                {historyJobId && (
                  <p className="text-slate-400 text-sm mt-0.5">
                    {jobs.find((j) => j.id === historyJobId)?.name ?? historyJobId}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-3">
                {historyJobId && (
                  <button
                    onClick={() => { setHistoryJobId(null); void fetchPosts(); }}
                    className="text-xs text-indigo-400 hover:text-white transition"
                  >
                    Show all jobs
                  </button>
                )}
                <button
                  onClick={() => setShowHistoryModal(false)}
                  className="text-slate-400 hover:text-white text-2xl leading-none"
                >
                  ×
                </button>
              </div>
            </div>

            {/* Body */}
            <div className="overflow-y-auto flex-1 p-6">
              {posts.length === 0 ? (
                <div className="text-center py-16 text-slate-400">
                  <div className="text-4xl mb-3">📭</div>
                  <p>No posts yet</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {posts.map((post) => (
                    <div
                      key={post.id}
                      className="bg-slate-800/60 rounded-xl border border-slate-700/50 p-4"
                    >
                      {/* Post header */}
                      <div className="flex items-start justify-between mb-3">
                        <div>
                          <span className="font-semibold text-slate-200">{post.job_name}</span>
                          <span className="text-slate-500 text-xs ml-3">
                            {new Date(post.created_at).toLocaleString()}
                          </span>
                        </div>
                        <StatusBadge status={post.status} />
                      </div>

                      {/* Caption */}
                      {post.caption && (
                        <p className="text-sm text-white mb-1">{post.caption}</p>
                      )}

                      {/* Hashtags */}
                      {post.hashtags && (
                        <p className="text-xs text-indigo-400 font-mono mb-3">{post.hashtags}</p>
                      )}

                      <div className="text-xs text-slate-400 mb-3 space-y-1">
                        <p>🎬 Template: {post.template_name ?? 'Cinematic Default'}</p>
                        <p>💳 Credits used: {post.credits_used ?? 0}</p>
                        <p>🧼 Watermark: {post.watermark_applied ? 'Applied' : 'No watermark'}</p>
                      </div>

                      <div className="flex gap-4 items-start">
                        {/* Screenshot thumbnail */}
                        {post.screenshot_path && (
                          <a href={post.screenshot_path} target="_blank" rel="noreferrer">
                            <img
                              src={post.screenshot_path}
                              alt="Screenshot"
                              className="w-36 rounded-lg border border-slate-600 object-cover shrink-0 hover:opacity-90 transition"
                            />
                          </a>
                        )}

                        {/* Video export preview */}
                        {post.video_path && (
                          <div className="w-56 shrink-0 space-y-2">
                            <video
                              controls
                              src={post.video_path}
                              className="w-full rounded-lg border border-slate-600 bg-slate-900"
                            >
                              {post.captions_path && (
                                <track
                                  default
                                  kind="captions"
                                  src={post.captions_path}
                                  label="Cinematic captions"
                                />
                              )}
                            </video>
                            {post.export_path && (
                              <a
                                href={post.export_path}
                                download
                                className="inline-flex items-center gap-1 px-2 py-1 text-xs rounded bg-indigo-600 hover:bg-indigo-500 transition"
                              >
                                ⬇ Download Video
                              </a>
                            )}
                            {post.voiceover_path && (
                              <audio controls src={post.voiceover_path} className="w-full" />
                            )}
                          </div>
                        )}

                        {/* Platform results */}
                        {post.platform_results && (
                          <div className="flex-1 space-y-1.5">
                            {post.platform_results.map((r) => (
                              <div key={r.platform} className="flex items-start gap-2 text-xs">
                                <span className="shrink-0">{r.success ? '✅' : '⚠️'}</span>
                                <span className="capitalize font-medium text-slate-300 shrink-0">
                                  {r.platform}
                                </span>
                                <span className="text-slate-500">{r.message}</span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>

                      {post.video_notes && post.video_notes.length > 0 && (
                        <div className="mt-3 space-y-1">
                          <p className="text-xs text-slate-400">Video Notes</p>
                          {post.video_notes.map((note, idx) => (
                            <p key={`${post.id}_vnote_${idx}`} className="text-xs text-slate-500">
                              • {note}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
