"""Results summary share image (Last Night + Last 7 ML/Spread/Total)."""
from results_share_image import (
    payload_from_results_html,
    render_results_summary_share_image,
)


SAMPLE = """
<div class="daily-tally">
  <h2>Last Night's MLB Results — 2026-09-16 (15 games)</h2>
  <div class="daily-tally-card highlight">
    <div class="daily-model">🏆 Sharp Consensus</div>
    <div class="daily-acc">53.3%</div>
    <div class="daily-rec">8-7</div>
  </div>
  <div class="daily-tally-card">
    <div class="daily-model">📈 Spread</div>
    <div class="daily-acc">26.7%</div>
    <div class="daily-rec">4-11</div>
  </div>
  <div class="daily-tally-card">
    <div class="daily-model">🎲 Over/Under</div>
    <div class="daily-acc">66.7%</div>
    <div class="daily-rec">10-5</div>
  </div>
</div>
<div class="daily-tally">
  <h2>Last 7 Days MLB Results — 2026-09-10 to 2026-09-16 (90 games)</h2>
  <div class="daily-tally-card highlight">
    <div class="daily-model">🏆 Sharp Consensus</div>
    <div class="daily-acc">53.3%</div>
    <div class="daily-rec">48-42</div>
  </div>
  <div class="daily-tally-card">
    <div class="daily-model">📈 Spread</div>
    <div class="daily-acc">36.7%</div>
    <div class="daily-rec">33-57</div>
  </div>
  <div class="daily-tally-card">
    <div class="daily-model">🎲 Over/Under</div>
    <div class="daily-acc">64.0%</div>
    <div class="daily-rec">55-31-4</div>
  </div>
</div>
<div class="share-strip"></div>
"""


def test_payload_from_team_results_html():
    p = payload_from_results_html(SAMPLE, "MLB")
    assert p and p["type"] == "results-summary"
    assert p["last_night"]["ml"]["record"] == "8-7"
    assert p["last_night"]["spread"]["acc"] == "26.7%"
    assert p["last_7"]["total"]["record"] == "55-31-4"
    assert p["last_7"]["games"] == 90


def test_render_results_summary_jpeg():
    p = payload_from_results_html(SAMPLE, "MLB")
    data, mime = render_results_summary_share_image(p, "jpg")
    assert mime == "image/jpeg"
    assert data and data[:2] == b"\xff\xd8"
    assert len(data) > 10_000
