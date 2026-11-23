# Growth page

A web page showing how the Redline Gaming servers are doing week by week:
https://bonannotjb-prog.github.io/RLG-Discord-leaderboard/

The workflow `.github/workflows/growth.yml` runs every Monday. It pulls from BattleMetrics, WarCon and Steam (read-only),
rebuilds every figure, saves the results in `docs/data/` and publishes the `docs/` folder as the page.

## What is where

- `growth/pipeline/refresh.py` runs everything; the other files in that folder are the pulls and the calculations.
- `growth/static/` holds inputs that do not update themselves: Discord message counts from a one-time export
  (`discord_days.json`), the one-off game mode comparison (`lens/`), the 60 comparison servers (`ids30.json`) and
  hand-added chart markers (`events_manual.json`).
- `docs/` is the page: `index.html`, `app.js`, `dash.js`, `d3.min.js` and the data files in `docs/data/`.
- `docs/data/history.json` is the week-by-week history. Each run adds the newest full week and keeps the earlier ones.

## Secrets (Settings, Secrets and variables, Actions)

- `BATTLEMETRICS_TOKEN`: the same token the seederboard uses.
- `WARCON_API_KEY`: a read-only WarCon key.
- `GROWTH_ADMINS_JSON`: the admin team as `{"admins":[{"player":"<BattleMetrics player number>","role":"Senior","start":"2026-09-27","end":null}]}`.
  It is a secret so the list of who is on the team, and when, stays out of the public files.

## What is public

Everything in this repository and on the page. That includes the 25 players who helped most seeds (names and BattleMetrics
player numbers) and the admin team's seeding by role. The raw join and leave records are pulled fresh on each run and are not saved.

## Changing things

- Add a chart marker: add `{"date": "YYYY-MM-DD", "event": "..."}` to `extra` in `growth/static/events_manual.json`.
- Change the admin team: update the `GROWTH_ADMINS_JSON` secret.
- Rebuild now: Actions, "Growth page weekly update", Run workflow, tick force.
- The written conclusions on the page do not rewrite themselves; they are in `docs/index.html`.
