import cron from 'node-cron';
import { getDb } from './db';

let schedulerInitialized = false;
const scheduledTasks = new Map<string, cron.ScheduledTask>();

/** Call once on server start — schedules all active jobs. */
export function initializeScheduler(): void {
  if (schedulerInitialized) return;
  schedulerInitialized = true;

  const db = getDb();
  const jobs = db
    .prepare('SELECT * FROM social_jobs WHERE is_active = 1')
    .all() as Array<{
    id: string;
    name: string;
    schedule_time: string;
  }>;

  for (const job of jobs) {
    scheduleJob(job);
  }

  console.log(`[Scheduler] Initialized — scheduled ${jobs.length} job(s).`);
}

/** Schedule a single job using its HH:MM time. */
export function scheduleJob(job: { id: string; name: string; schedule_time: string }): void {
  unscheduleJob(job.id); // remove existing if any

  const parts = job.schedule_time.split(':').map(Number);
  const hours = parts[0];
  const minutes = parts[1];

  if (isNaN(hours) || isNaN(minutes)) {
    console.warn(`[Scheduler] Invalid schedule_time for "${job.name}": ${job.schedule_time}`);
    return;
  }

  const expression = `${minutes} ${hours} * * *`; // daily at HH:MM

  const task = cron.schedule(expression, async () => {
    console.log(`[Scheduler] Running "${job.name}" at ${new Date().toISOString()}`);
    try {
      const baseUrl =
        process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
      const res = await fetch(`${baseUrl}/api/jobs/${job.id}/run`, {
        method: 'POST',
      });
      if (!res.ok) {
        const body = await res.text();
        console.error(`[Scheduler] "${job.name}" failed: ${body}`);
      }
    } catch (err) {
      console.error(`[Scheduler] Error running "${job.name}":`, err);
    }
  });

  scheduledTasks.set(job.id, task);
  console.log(`[Scheduler] Scheduled "${job.name}" daily at ${job.schedule_time} (${expression})`);
}

/** Remove a job's cron task (e.g. when it is deleted). */
export function unscheduleJob(jobId: string): void {
  const existing = scheduledTasks.get(jobId);
  if (existing) {
    existing.stop();
    scheduledTasks.delete(jobId);
  }
}
