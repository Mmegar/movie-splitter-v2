# Movie Splitter v2: shared contract

Both halves of v2 (the **pipeline** and the **iPhone app**) agree on these files. If either side
needs a change, write it in `plan/CONTRACT-CHANGES.md` (what, why, who needs to update) and keep
going; the lead merges changes at the end.

## Big picture

```
 Laptop (Windows/Mac)                Google Drive ("TikTok automation v2")       GitHub (repo: movie-splitter-v2)
 ─────────────────────               ──────────────────────────────────────       ──────────────────────────────────
 laptop/agent.py                     To edit/            ◄── uploads ──           .github/workflows/make-parts.yml
  • watches a local folder ─────────► (movies wait here)                           (cuts parts, writes status)
  • reads config/wishlist.txt         Ready to post/<movie>/<mode>/  ◄── parts ──  .github/workflows/post.yml
    → public-domain films only          (finished parts)                           (posts to TikTok via Composio)
      (archive.org)                   Failed/                                      branch "status":
  • writes status/laptop.json                                                       state.json   (pipeline)
                                                                                    laptop.json  (laptop agent)
 iPhone home-screen app (docs/, served by GitHub Pages)
  • reads  status branch: state.json + laptop.json      (GitHub API)
  • reads  notifications from ntfy                      (ntfy JSON poll)
  • writes config/settings.json + config/wishlist.txt   (GitHub API commit on the default branch)
  • starts / stops runs                                 (GitHub Actions API)
```

Radarr / torrent / Usenet downloading is **out of scope** (piracy). Movie sources are: files the
owner drops in the laptop folder, and public-domain films from archive.org via the wish list.

## 1. `config/settings.json` (default branch) — written by the app, read by the pipeline

See the file for the full default. Keys map 1:1 to the v1 env settings (lower-case), plus:

| key | type | meaning |
|---|---|---|
| `modes` | string[] | order matters; any of `stretch`, `highlights`, `all` |
| `flip_every` | string | `"25-30"`, `"30"`, `"-1"` (every cut), `"0"` (always flipped) |
| `video_quality` | int | x264 CRF, 18 (best/big) … 28 (small). Default 23 |
| `delete_originals` | `"permanent"` \| `"bin"` | how finished originals are removed from Drive |
| `cleanup_parts_after_days` | int | delete finished parts older than N days; 0 = never |
| `posting.enabled` | bool | master switch for automatic TikTok posting |
| `posting.privacy` | `"self_only"` \| `"public"` | TikTok privacy (unaudited apps can only post `self_only`) |
| `posting.modes` | string[] | which modes' parts get posted |
| `posting.per_day`, `posting.times` | int, "HH:MM"[] | schedule, in `posting.timezone` |

Environment variables still override settings.json (back-compat with v1 names, e.g. `MODES`).

## 2. `config/wishlist.txt` (default branch) — written by the app, read by the laptop agent

One title per line, optional `(year)`. Lines starting with `#` are comments. The agent never edits
this file; it reports progress in `laptop.json`.

## 3. `status` branch → `state.json` — written ONLY by the pipeline workflows

Shape: see `plan/sample-state.json`. Rules:
- `version` = 1. Times are ISO-8601 with offset.
- `run.stage` ∈ `idle | downloading | analysing | subtitles | planning | rendering | uploading | posting | done | failed`.
- `movies[].status` ∈ `queued | processing | done | failed`.
- `movies[].modes[<mode>].parts[].post.status` ∈ `none | scheduled | posted | failed`.
- `events` = newest first, max 200, mirrors every phone notification (`type` ∈ `new_file | started |
  planned | part_ready | finished | failed | posted | post_failed | paused | info`).
- Written at: run start, each stage change, each part ready, finish/fail, each post.
- Keep the last 50 movies.

## 4. `status` branch → `laptop.json` — written ONLY by the laptop agent

Shape: see `plan/sample-laptop.json`. Heartbeat every 5 minutes (`updated`); the app shows the
laptop as offline if `updated` is older than 15 minutes.
`wishlist[].status` ∈ `searching | found | downloading | uploaded | not_available | failed`.

## 5. Notifications

ntfy topic name lives in the private Drive file `notification name.txt` (pipeline) and in the
app's local settings (entered once by the user). The app reads history from
`https://ntfy.sh/<topic>/json?poll=1&since=7d`.

## 6. App ↔ GitHub

The app uses a fine-grained GitHub token (repo: contents read/write, actions read/write) kept only
in the phone's local storage. Endpoints:
- read: `GET /repos/{owner}/{repo}/contents/{state.json|laptop.json}?ref=status`
- edit: `PUT /repos/{owner}/{repo}/contents/config/settings.json` (and wishlist.txt) with `sha`
- runs: `GET /repos/{owner}/{repo}/actions/runs?per_page=10`,
  `POST /repos/{owner}/{repo}/actions/workflows/make-parts.yml/dispatches {"ref":"main"}`,
  `POST /repos/{owner}/{repo}/actions/runs/{id}/cancel`
