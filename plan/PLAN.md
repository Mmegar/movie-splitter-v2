# Movie Splitter v2 — Goal, Requirements, Plan, Checklist

## Goal
Run the whole movie-to-TikTok workflow from an iPhone, with the laptop only doing the jobs that need
a computer at home:

1. **Get movies in**: drop owned files into a laptop folder, or list public-domain films in a wish list.
2. **Cut them** (GitHub Actions, free): Best stretch + Highlights, subtitles, flips, line, cockroach.
3. **Post them** to TikTok automatically on a schedule (Composio), or keep them for manual posting.
4. **See and control everything** from a home-screen iPhone app: progress, notifications, movie list,
   queue/wish list, posting schedule, and every setting.

## Requirements

### Accounts / services
| Need | Cost | Why |
|---|---|---|
| GitHub (new public repo `movie-splitter-v2`) | free | runs the cutting + posting, stores status |
| Google Drive (separate folder "TikTok automation v2") | free (15 GB) | movies in, parts out |
| ntfy app (iPhone) | free | phone notifications |
| Composio account + TikTok connected | free tier | automatic TikTok posting (optional) |
| rclone + Python 3.10+ on the laptop | free | laptop agent uploads to Drive |

### Out of scope
- Radarr / torrent / Usenet downloading of copyrighted movies (piracy).
- App Store publishing (the app is a free home-screen web app).

### Constraints
- TikTok's posting API only allows **private (self-only)** posts until an app passes TikTok's audit;
  Composio's TikTok connection is subject to the same rule. Default: `posting.enabled = false`.
- Google Drive 15 GB: originals deleted permanently, smaller parts (CRF 23), optional auto-clean.
- Public repo: status (movie names) is publicly readable; tokens/keys stay in GitHub Secrets and on
  the phone only.

## Plan (who does what)

| Track | Owner | Output |
|---|---|---|
| Contract (data formats) | Lead | `plan/CONTRACT.md`, `config/*`, `plan/sample-*.json` |
| Pipeline v2 | Agent A | settings.json support, status writer, storage savers, `post.yml` + Composio poster, `laptop/agent.py` + installers |
| iPhone app | Agent B | `docs/` home-screen web app (PWA) — Dashboard, Movies, Alerts, Queue & wish list, Settings |
| Integration + setup guide | Lead | end-to-end check, `plan/SETUP.md`, push |

## Checklist

### Pipeline
- [x] process.py reads `config/settings.json` (env still overrides)
- [x] status branch: `state.json` updated at every stage, part, finish, failure, post
- [x] originals deleted permanently (setting), CRF setting, auto-clean old parts (setting)
- [x] `post.yml`: hourly; posts next unposted part at scheduled times via Composio; records result
- [x] laptop agent: watch folder → upload to Drive "To edit" (waits until file finished copying)
- [x] laptop agent: wish list → archive.org public-domain search → download → upload; status in laptop.json
- [x] laptop agent: heartbeat; runs at login (Windows Task Scheduler / macOS launchd installers)

### App
- [x] Dashboard: live run card (movie, mode, stage, part x/y, progress), laptop online, next post
- [x] Movies: list with status; detail with parts per mode, captions (copy), posted state
- [x] Alerts: notification feed from ntfy
- [x] Queue: To edit queue + wish list editor (add/remove titles, see status)
- [x] Settings: all pipeline settings + posting schedule, saved to GitHub
- [x] Actions: start run, stop run
- [x] Works offline with last data; installable to home screen; iOS look and feel
- [x] Demo mode with sample data (prototype without any setup)

### Integration
- [x] Contract check both sides
- [x] Tests pass (pipeline unit tests, app loads sample data)
- [x] Setup guide for laptop + iPhone + Composio (`plan/SETUP.md`)

### Still to do (needs you)
- [ ] Create the `movie-splitter-v2` repo, then Claude pushes v2 into it
- [ ] Follow `plan/SETUP.md` steps 2–7
- [ ] First real run with one short movie; check the app shows it live
- [ ] (Later) TikTok developer app audit for public auto-posts
