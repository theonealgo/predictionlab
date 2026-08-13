# Agent Instructions

Read and follow every rule in this file before attempting any fix or change.

## Project context

This folder (`predictionlabfix_work`) is a **local copy** of predictionlab.io.

**This is a live business with paying customers (~129 subscriptions).** Outages cost real money. Treat every change as if a mistake could take the site down for every customer.

The goal of work in this folder is to:

1. Fix issues **locally**
2. **Run and verify locally** before anything is considered done
3. Hand off for the owner to deploy — agents do **not** ship to production

Do not treat this as a sandbox for unrelated changes.

## Rules

### 0. Never touch production / the live site — critical

**Agents never work on the live site.**

- Do **not** push to git, trigger deploys, restart Render, or change anything that affects `predictionlab.io`.
- Do **not** run `git push` or equivalent — **not even if the user says “push” in a panic**, unless the user also explicitly confirms they understand it will redeploy production and still wants that specific action.
- Default assumption: **local only.** Edit here (or in approved isolation folders), run locally, prove it works, stop.
- A redeploy of “just HTML” still restarts the only Render worker and can take the whole site down. There is no safe casual push.

If production is already down and the user asks for help diagnosing, **advise** (restart steps, what to check). Do not push fixes unless they clearly re-confirm a production deploy after local verification.

### 1. Stay in this project folder — critical

**Only work inside this workspace folder** (`predictionlabfix_work`), except when Rule 8 applies.

- Do **not** read, modify, create, or delete files in any other folder on this computer.
- Do **not** run commands that write to paths outside this project.
- **Exception:** Cursor's own configuration folders (for example `~/.cursor/`) may be used when the task requires it.
- **Exception (Rule 8):** Sport-isolation workspaces under `~/Documents/Personal/<sport>/` (for example `~/Documents/Personal/soccer/`) when the user asks to fix or rework that sport outside this repo.

If files outside this project are touched outside those exceptions, the entire set of working folders may need to be deleted. Treat the default boundary as hard.

### 2. Fix only what was asked

Work only on the specific problem the user asked you to fix. Do not make unrelated changes, drive-by refactors, or "while I'm here" improvements unless the user requests them.

### 3. Local work only — verify locally before calling anything done

Work locally only.

1. Make the change in this folder or an approved isolation folder.
2. **Run the app locally** and smoke-test the affected pages (and shared paths if risk is non-zero).
3. Report what you verified. Do not claim “fixed” without a local check when the change could affect serving pages.
4. **Never push.** The owner deploys after they are satisfied.

### 4. Ask before risky changes

If a fix may affect functionality on other pages, features, or shared code paths, ask clarifying questions before implementing. Do not guess when the impact could break behavior elsewhere.

### 5. Know your limits

Do not attempt a fix if you cannot implement it to the level of quality and correctness the codebase requires. Say so clearly, explain what is blocking you, and ask for guidance or a narrower scope instead of shipping a partial or unreliable fix.

### 6. Review this file first

Before starting any task, re-read this file and confirm your plan complies with all rules above.

### 7. Always report where changes were made

After any task that creates, modifies, or deletes files, **always tell the user which folder(s) were changed**. Be explicit — for example:

- `predictionlabfix_work/` (project root)
- `predictionlabfix_work/templates/`
- `predictionlabfix_work/.cursor/rules/`
- `~/Documents/Personal/soccer/` (sport isolation workspace)

If multiple folders were touched, list each one. If no files were changed, say that clearly.

### 8. Sport model / picks / results work — isolate first

**From now on, changes that rework how a sport's models pick, grade, or show results must NOT be done first inside `predictionlabfix_work`.**

Required workflow:

1. **Copy** the related sport files into a **separate folder** outside this repo, named for the sport — e.g. `~/Documents/Personal/soccer/`, `~/Documents/Personal/wnba/`.
2. **Fix and test** entirely in that outside folder (league-by-league when needed). Build standalone picks/results UIs there — never wire them into the live app during testing.
3. **Do not merge** any sport back into `predictionlabfix_work` / live until the owner explicitly says to. Plan is to ship **all** approved sport fixes together, not one at a time.
4. After a sport fix is live, wait ~**one week** of real results before judging the new parameters.

Do **not** invent a new pick/results pipeline for a sport inside this directory while it is still broken. Prefer not to touch live sport model files at all during isolation work.

Current isolation workspaces:

- Soccer: `~/Documents/Personal/soccer/`
- WNBA: `~/Documents/Personal/wnba/`
- SEO / growth features duplicate: `~/Documents/Personal/predictionlab_newfeatures/` (full site copy; merge only after owner approval)

### 9. Never put internal / sandbox notes on user-facing HTML — critical

**Public and paid users must only see product UI.** This applies to the offline hub (`:5081` / `_sandbox_hub_run`) and to live PredictionLab alike.

**NEVER** put any of the following in user-facing HTML (pages, banners, sticky notes, lead paragraphs, home blurbs, card intros, **View Details panels**, data-attribute labels visible in “View Source” that name vendors):

- Personal notes or agent scratchpad text
- Isolation / debug / sandbox banners or yellow sticky chrome
- Internal implementation details (training-set sizes, data-pipeline names, IP, model-training commentary)
- “Sandbox only” / “not live” / “isolation — …” tech copy aimed at developers
- **Probability / data-source IP:** “Prob source”, “Elo + market blend”, “Odds-implied”, “TheOddsAPI”, “The Odds API”, “market lean”, ESPN training counts, books-count-as-vendor labels like `TheOddsAPI (4)`

**FORBIDDEN examples (do not ship):**  
- `UFC isolation — Elo trained on 310 ESPN finals; market lean from The Odds API…`  
- `Prob source: Elo + market blend` / `Books: TheOddsAPI (4)`

Book **odds numbers** (e.g. DraftKings-style face ML like −150) may stay when shown like the live product. Vendor / blend / training-pipeline labels must not.

Internal docs (`README.md`, code comments, server logs, API debug JSON that is not rendered) may describe isolation — **HTML that customers or hub visitors see must not.**

### 10. Blog: keep Soro; never revive Google Trends auto-posts

- The `/blog` **Soro** “Trending in Sports” embed (`#soro-blog` / `app.trysoro.com`) is a **paid product**. **Do not remove or “clean” it** when fixing blog content.
- **Google Trends → blog auto-posts are forbidden** (no `Google Trends Betting Angle` articles). Keep `_BLOG_AUTO_TRENDS_ENABLED = False`. See `data/BLOG_OWNER_NOTES.md`.
