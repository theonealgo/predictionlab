# Frontend Contract

The production homepage is `/` and is rendered from `templates/homepage_preview.html`.
Despite its filename, it is not an experimental-only page.

## Protected UI

The following files define the approved site-wide research design:

- `templates/homepage_preview.html`
- `templates/base.html`
- `templates/espn_predictions_template.html`
- `templates/underdogs_layout.html`
- `templates/partials/site_directory_footer.html`
- `static/css/research-theme.css`

Do not replace, broadly restyle, or revert these files without an explicit user request.
Do not change the homepage to a different template.

## Required Behavior

- The homepage and routed pages use the shared `.navbar` header.
- All routed pages using the main layouts load `research-theme.css`.
- The shared footer uses the text wordmark `predictionlabs.io`; do not restore a footer logo image.
- Homepage glossary labels use black backgrounds with neon-green text.
- The fixed homepage side index must not overlap main content.
- Responsive changes must be checked at desktop, tablet, and mobile widths.

## Verification

Run:

```bash
python3 -m pytest tests/test_frontend_theme_contract.py -q
```

For frontend changes, also inspect `/`, `/nba-picks`, `/all-sports-results`, and `/faq`
in a browser before considering the work complete.
