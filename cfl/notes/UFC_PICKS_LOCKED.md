# UFC picks locked — premerge (:5052)

**Signed off:** Sep 3, 2026  
**Canonical UI:** `http://127.0.0.1:5081/ufc/` (staging hub)  
**Premerge route:** `http://127.0.0.1:5052/ufc-picks`

## Rule

UFC **picks** are locked on `predictionlabfix_work2`. Copy from `:5081`; do not rebuild cards/chart/share on `:5052`.

Unlock: owner says `UNLOCK UFC PICKS`.

## Results (not locked)

`/ufc-results` uses the same `:5081` passthrough (`which="results"`). Cache key `UFC_daily_results_html_v3` stores **post-overlay** HTML so results stay merged when `:5081` is briefly down.

First cold load still needs `:5081` up or vendored `_sandbox_hub_run/hub/` fallback.
