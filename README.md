# RLG Discord posts

Every post is a picture drawn in Redline Gaming's colours, with a line of text
under it for links and live times.

## Files

| File | What it is |
|---|---|
| `rlgimage.py` | Draws all five pictures |
| `rlgpost.py` | Uploads pictures to Discord |
| `assets/` | The RLG banner and icon, and the fonts. Upload the whole folder |
| `leaderboard.py` | Kills leaderboard, one picture per server |
| `status.py` | Live server status. Also runs `seed.py` and `recap.py` |
| `seed.py` | Seeding calls (text, pings the role) |
| `recap.py` | A picture for each finished match |
| `seedboard.py` | Seederboard picture, from BattleMetrics |
| `awards.py` | Weekly awards picture |
| `recap_backfill.py` | One-time run that posts past match recaps |
| `serverlist.py` | Reads rank, join queue, seeding odds and the gold price from WARDOGS Server List |
| `gold.py` | Gold bar price picture, replaced in place once a day |
| `.github/workflows/*.yml` | The schedules for each job |

Changing the look: swap `assets/banner.jpg` or `assets/icon.png` for a file of
the same name. To see a picture without posting it, a job run with `DRY_RUN=1`
saves the picture to disk instead.

Things a picture cannot do: names in it cannot be copied or searched, and emoji
in player names are left out because the fonts cannot draw them.

The sections below describe each job. Where they mention a "card" or a
"message", read "picture": the settings and setup steps are unchanged.

Keeps one message in a Discord channel showing the Warcon leaderboard for an RLG
server. A GitHub Actions job rewrites that same message every 15 minutes.

## Setup (about 10 minutes)

1. **Make the Discord webhook.** In Discord: channel settings, Integrations,
   Webhooks, New Webhook. Name it (for example "RLG Leaderboard"), pick the
   channel, and copy the webhook URL. Treat the URL like a password: anyone who
   has it can post in that channel.

2. **Make a GitHub repository.** New repository, set to **Public**, tick
   "Add a README file". Public matters: scheduled jobs are free on public
   repositories, and this job would run past the free monthly minutes on a
   private one. Nothing secret is stored in the files.

3. **Add the two files.** In the repository use Add file, Create new file:
   - name it `leaderboard.py` and paste in the contents of `leaderboard.py`
   - name it `.github/workflows/leaderboard.yml` (typing the slashes makes the
     folders) and paste in the contents of `leaderboard.yml`

4. **Add the webhook as a secret.** Settings, Secrets and variables, Actions,
   New repository secret. Name: `DISCORD_WEBHOOK_URL`. Value: the webhook URL.

5. **Allow the job to save its message id.** Settings, Actions, General,
   Workflow permissions: choose "Read and write permissions" and save.

6. **Run it once by hand.** Actions tab, "Discord leaderboard", Run workflow.
   The message appears in the channel, and a `state/message_id.txt` file
   appears in the repository. From then on the job edits that message.

## Changing what it shows

Edit the `env:` block in `.github/workflows/leaderboard.yml`:

| Setting | Options |
|---|---|
| `WARCON_SERVER_IDS` | One or more server ids, separated by commas. The id is the long code in the Warcon page address. Each server gets its own panel in the same message. |
| `LB_RANGE` | `current` (this season), `7d`, `30d`, `90d`, `all` |
| `LB_SORT` | `kills`, `kd`, `perHour`, `playtime`, `seeded`, `matches`, `wins`, `winRate`, `cash` |
| `LB_MIN_MINUTES` | Minimum minutes played to appear |
| `LB_TOP` | Rows shown per server, up to 25 |

To change how often it runs, edit the `cron` line. GitHub's minimum is every
5 minutes, and scheduled runs often start a few minutes late.

## Good to know

- **Deleting the message:** the next run posts a fresh one and remembers it.
- **Moving channels:** make a new webhook, update the secret, delete
  `state/message_id.txt`, and run the job once.
- **The 60-day pause:** GitHub pauses scheduled jobs in a repository with no
  activity for 60 days. Each job here writes a small `state/heartbeat-*.txt`
  file that changes once a week and commits it, so the repository always has a
  recent commit. If a job is ever paused anyway, press "Enable workflow" on the
  Actions tab.
- **Failed runs:** GitHub emails the repository owner. Open the run on the
  Actions tab to see the reason. The webhook URL is never printed in the log.
- **Player names cannot ping anyone:** mentions are switched off for this message.

## Live server status message (second job)

`status.py` and `.github/workflows/status.yml` keep a second message in the
channel showing each server's players online, map, mode, weather and faction
scores. It checks every 20 seconds and edits the message only when something
changed (and at least every 2 minutes so the "Checked" time stays fresh).

Extra setup on top of the steps above:

1. Add `status.py` to the repository, and add the workflow as
   `.github/workflows/status.yml`.
2. Add a second repository secret named `DISCORD_STATUS_WEBHOOK_URL`. Use a
   webhook on the same channel. It can be the same webhook URL as the
   leaderboard one, or a second webhook if you want a different name or avatar.
3. Actions tab, "Discord server status", Run workflow.

How it gets close to live: GitHub cannot schedule a job more often than every
5 minutes, so one job stays running for about 5.5 hours and does the 20 second
updates itself. The 15 minute schedule only exists to line up the next job,
which takes over when the running one ends. Expect one short pause (usually
under a minute or two) at each handover, about four times a day.

On the Actions tab you will normally see one status run "In progress" and one
"Queued". That is the design working, not a fault.

To change the speed, edit `STATUS_INTERVAL` in `status.yml`. 10 seconds is the
floor; Warcon only refreshes its public data every 5 seconds or so.

To stop it: Actions tab, "Discord server status", the "..." menu, Disable
workflow, then cancel the run in progress.

## Seeding alerts (part of the status job)

`seed.py` is used by `status.py`. It pings a role in the seeding channel when a
real seeding attempt is under way.

- **Start:** the role is pinged once a quiet server has 3 players who have
  stayed for 3 minutes. One person popping in and out never pings anyone.
- **One server at a time:** while one server has a seeding call running, no
  call starts for the other. If both are ready together, the busier one is called.
- **While seeding:** the same message is edited with the live count, how many
  more are needed, and who is on. Edits never ping.
- **Final push:** when the count first reaches 15, one short extra message
  pings the role ("6 more and it is live"). It is deleted when the attempt ends.
- **Finish:** past 20 the message says the server is live. If it empties first,
  it says how close it got. Either way it thanks every helper by name with the
  minutes they put in, and names the longest-serving one Seed MVP.
- **Cooldown:** no new call for the same server for 90 minutes.

Player names are written as plain text. Nobody is @mentioned except the role.

Setup:

1. **Role.** In Discord: Server Settings, Roles, Create Role (for example
   "Seeders"). Turn on "Allow anyone to @mention this role". Give the role to
   whoever wants the pings.
2. **Channel.** Create the text channel for the alerts.
3. **Webhook.** In that channel: Integrations, Webhooks, New Webhook. Copy the URL.
4. **Role id.** User Settings, Advanced, turn on Developer Mode. Then Server
   Settings, Roles, right-click the role, Copy Role ID.
5. **GitHub.** Add `seed.py` to the repository next to `status.py`. Replace
   `status.py` and `.github/workflows/status.yml` with the new versions. In
   `status.yml`, put the role id in place of `PASTE_ROLE_ID_HERE`.
6. **Secret.** Add a repository secret named `DISCORD_SEED_WEBHOOK_URL` with the
   new webhook URL.
7. **Restart the job.** Actions tab, cancel the status run in progress, then
   Run workflow, so it picks up the new files.

Settings in `status.yml`:

| Setting | What it does |
|---|---|
| `SEED_MIN`, `SEED_MAX` | The player band that counts as seeding (1 to 20) |
| `SEED_START_PLAYERS`, `SEED_START_MINUTES` | Players needed, and how long they must stay, before the first ping (3 and 3) |
| `SEED_PUSH_AT` | Player count for the final-push ping (15). `0` turns it off |
| `SEED_COOLDOWN_MINUTES` | Shortest gap between pings for the same server (90) |
| `SEED_ALERT_ON_DROP` | `0` pings only when a server fills from empty. `1` also pings when a busy server falls back to 20 or fewer |

Each server is tracked on its own, so both servers can each send a ping.

## Seederboard (its own job, from BattleMetrics)

`seedboard.py` and `.github/workflows/seedboard.yml` keep one Discord message
with two boards: seeding time over the last 30 days, and all time.

The numbers come from BattleMetrics join and leave records, so the first run
fills in the whole history back to `SEEDBOARD_START` (September 1, 2026).

How time is counted:

- A server is seeding in any minute it has 1 to 20 players on.
- Every player on during that minute is credited one minute.
- Both RLG servers are added together.
- A session BattleMetrics shows as longer than 4 hours counts as 40 minutes.
  This is the same rule the RLG growth report uses for sessions that were never
  closed properly.
- Days are UTC days, so "last 30 days" rolls over at 5:00 PM Phoenix time.

Setup:

1. **BattleMetrics token.** On battlemetrics.com, signed in to the account with
   access to the RLG servers: open the Developers page, create a new personal
   access token for this job, and give it read access only. Copy it.
2. **Secrets.** In the repository add two secrets:
   - `BATTLEMETRICS_TOKEN`: the token from step 1
   - `DISCORD_SEEDBOARD_WEBHOOK_URL`: a webhook on the channel for the board
3. **Files.** Add `seedboard.py` and `.github/workflows/seedboard.yml`.
4. **Run it.** Actions tab, "Discord seederboard", Run workflow. The first run
   reads the whole history and can take several minutes. After that it runs
   twice an hour and only recounts the last three days.

Settings in `seedboard.yml`: `SEED_MIN` and `SEED_MAX` (the band),
`SEEDBOARD_TOP` (rows shown), `SEEDBOARD_START` (first day counted) and
`BM_SERVER_IDS`. Changing any of these makes the next run recount from scratch.

What is stored: `state/seed_time.json` holds each player's BattleMetrics player
id, in-game name and seeding seconds per day. The repository is public, so
that file is public too. The token itself is never stored or printed.

## Match recaps (part of the status job)

`recap.py` is used by `status.py`. Each time a match ends it posts one new
message in the recaps channel: the winning faction, final scores, how long the
match ran, how many played, the top five players by kills, and Warcon's awards
for that match.

- Matches with fewer than 10 players get no recap, so empty-server matches do
  not fill the channel. Change `RECAP_MIN_PLAYERS` in `status.yml` to adjust.
- On its first run it only notes the newest finished match. Recaps start with
  the next match that ends.
- A recap appears within about a minute of the match ending.

Setup:

1. Create a webhook on the recaps channel and add it as a repository secret
   named `DISCORD_RECAP_WEBHOOK_URL`.
2. Add `recap.py` to the repository and replace `status.py` and
   `.github/workflows/status.yml` with the new versions.
3. Cancel the status run in progress and press Run workflow.

## Weekly awards (its own job)

`awards.py` and `.github/workflows/awards.yml` post one new message every
Monday at about 8 AM Phoenix time (10 AM Eastern) naming the top three players
of the last 7 days in each category: most kills, best K/D, most wins, most time
played and top seeders.

- Kills, K/D, wins and time come from Warcon across both servers, and need at
  least 120 minutes played that week (`AWARDS_MIN_MINUTES`).
- Top seeders come from the seederboard's saved totals, so that award needs the
  seederboard job to be running.
- The job records the week it posted, so a week is never posted twice.

Setup:

1. Create a webhook on the awards channel and add it as a repository secret
   named `DISCORD_AWARDS_WEBHOOK_URL`.
2. Add `awards.py` and `.github/workflows/awards.yml`.
3. To see one now: Actions tab, "Discord weekly awards", Run workflow. That
   counts as this week's post. To post again in the same week, tick "Post even
   if this week's awards were already posted" on the Run workflow form.

## One-time recap backfill

`recap_backfill.py` and `.github/workflows/recap_backfill.yml` post a recap for
every past match Warcon still has, oldest first. It only runs when you press
Run workflow; it has no schedule.

1. Add `recap_backfill.py` (top level) and `recap_backfill.yml`
   (`.github/workflows/`). Use the latest `recap.py` too.
2. Actions tab, "Discord recap backfill (one time)", Run workflow.
   - Tick "Only count" first if you want to see how many it would post.
3. Leave it running. It posts about 25 a minute, the fastest Discord allows.

It skips matches with fewer than 10 players and any match the live recap job
already posted. When it finishes it records that in
`state/recaps_backfilled.txt` and refuses to run again unless you tick the
force box, which would post everything a second time.

## WARDOGS Server List

`serverlist.py` reads server-level and game-wide figures from
wardogserverlist.com. WarCon and BattleMetrics stay the source for anything
about players.

**Setup:** add the key as a repository secret named `WARDOGSERVERLIST_API_KEY`
(Settings, Secrets and variables, Actions). The key can only read. Until the
secret exists, every job runs as it did before and simply leaves these figures out.

| Where | What it adds |
|---|---|
| Live status pictures | The server's rank among community servers, and how many are waiting to join when it is full |
| Seeding call | A "good hour for it" line when seeds started around that hour usually reach 20 (`SEED_SHOW_ODDS_FROM` in `status.yml`) |
| Weekly numbers | Two more pictures: where each server ranked last week, and the best hours to start a seed. A season card for each server when a season ends |
| Gold price (`gold.py`) | One picture kept up to date. Also needs a `DISCORD_GOLD_WEBHOOK_URL` secret for the channel |
| Growth page | The "Where we rank" page, from `growth/pipeline/serverlist_data.py` |

`SEED_HOLD_BELOW_PCT` in `status.yml` can hold a seeding call at hours when seeds
rarely work. It is `0` (never hold) unless you change it.

**Credit:** the figures are free to reuse under CC BY 4.0 with a visible credit
and a link, so every picture, message and page that shows one carries
"Server data: WARDOGS Server List (CC BY 4.0)". Keep that line.
