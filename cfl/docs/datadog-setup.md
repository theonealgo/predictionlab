# Datadog setup (predictionlab.io on Render)

## 1. Render environment variables

In **Render → predictionlab → Environment**, add:

| Variable | Example | Purpose |
|----------|---------|---------|
| `DD_API_KEY` | *(from Datadog → Organization Settings → API Keys)* | APM + agentless intake |
| `DD_SITE` | `datadoghq.com` | Use `datadoghq.eu` if EU org |
| `DD_SERVICE` | `predictionlab` | Service name in APM |
| `DD_ENV` | `production` | Environment tag |
| `DD_VERSION` | `git-sha` or deploy id | Optional version tag |
| `DD_TRACE_ENABLED` | `true` | Turn on Python tracing |
| `DD_LOGS_INJECTION` | `true` | Correlate logs with traces |

**Start command** (must stay):

```bash
bash render_start.sh
```

`render_start.sh` wraps Gunicorn with `ddtrace-run` when `DD_API_KEY` or `DD_TRACE_ENABLED` is set.

Redeploy after saving env vars.

## 2. Verify traces

1. Datadog → **APM → Traces**
2. Filter: `service:predictionlab env:production`
3. Open a trace for `GET /nba-results` — should show Flask span duration

## 3. Monitors to create

### A. High p95 latency on NBA results

- **Type:** APM Trace Metrics
- **Metric:** `trace.flask.request` (or `trace.web.request` depending on integration)
- **Filter:** `resource_name:get_nba_results` or `resource_name:GET /nba-results` *(match what appears in APM after first traffic)*
- **Alert:** p95 > **45s** for 5 minutes
- **Message:** NBA results page is slow — check Render logs, cache v7, book backfill job

### B. 5xx error rate

- **Type:** APM Trace Metrics / Error Tracking
- **Metric:** error rate on `service:predictionlab`
- **Filter:** `http.status_code:5*` or `@http.status_code:[500 TO 599]`
- **Alert:** > **5%** of requests over 10 minutes (tune threshold)
- **Message:** Production 5xx spike — check Render deploy + worker timeout

### C. Optional — log monitor

If using Datadog log collection from Render:

```
service:predictionlab @level:error "spread/total integration error"
```

## 4. Import monitors (optional)

If you use the Datadog API, adapt `datadog/monitors.example.json` with your notification handles (`@slack-...`).

## 5. Book lines backfill (reduces results-page ESPN load)

One-off on Render shell or locally:

```bash
python3 scripts/backfill_betting_line_totals.py --sport NBA --limit 300 --sleep 0.25
python3 scripts/backfill_betting_line_totals.py --sport NHL --limit 150
```

Schedule weekly via Render Cron Job if needed.
