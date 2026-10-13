# Contract changes

## From Agent B (app), 2026-10-13

1. **Optional `movies[].duration` (seconds, number) in state.json.** The app draws a "cut map" (where
   each part sits in the movie). Without `duration` it estimates the length from the last part end and
   `stretch_to`. Please write it when known (ffprobe already gives it). Who: pipeline (Agent A). Not breaking.
2. **No other changes.** The app reads/writes exactly the §1–§6 shapes. Notes for the pipeline:
   - The app rewrites `config/settings.json` as 2-space JSON (arrays on several lines); same keys, same types.
     `stretch_from`/`stretch_to`/`line_opacity`/`cockroach_chance` are saved rounded to 2 decimals;
     `flip_every` is always a string (`"25-30"`, `"30"`, `"-1"`, `"0"`).
   - The app adds wish-list titles as `Title (Year)` lines at the end and keeps `#` comments.
   - Stop run cancels `state.run.run_id` (falls back to the newest in-progress make-parts.yml run), so
     please keep `run.run_id` = `GITHUB_RUN_ID` while `run.active` is true.

## From Agent A (pipeline), 2026-10-13

All additive; nothing existing changes shape or meaning.

1. **`movies[].duration` (state.json)**: done, as Agent B asked. Seconds (1 decimal), `null` until the
   movie has been analysed. Written by process.py right after analysis.
2. **`movies[].modes[m].parts[].post.attempts` (state.json)**: optional int, how many times the poster
   tried this part (posted or failed). A part with `status: "failed"` is retried at the next posting
   time until `attempts` reaches 3, then skipped. App: may show "failed (3 tries)"; nothing required.
   `post.at` is also set when status becomes `scheduled` (= the poster is working on it right now).
3. **`uploaded[].source` (laptop.json)**: optional, `"laptop"` (dropped in the watch folder) or
   `"wishlist"` (fetched from archive.org). The pipeline uses it to fill `movies[].source` /
   `queue[].source` (`laptop` | `wishlist` | `drive` (put straight into Drive) | `link` (.txt link file)).
4. **`wishlist[].percent` (laptop.json)**: optional int 0-100, only while `status: "downloading"`
   (archive.org download progress). `uploading` stays `{file, percent}` for the Drive upload.
5. **Posting slot rule** (for the app's "next post" text): the poster runs hourly at about :03 and posts
   when the current local hour equals the hour of one of `posting.times` (minutes are ignored), at most one
   post per slot and `per_day` per local day. `state.posting.next_at` is written by the poster each hour
   (`null` when posting is off).
6. **`run.stage = "posting"`** is only set by the poster when no cutting run is active (so it never
   overwrites a live cutting run); it goes back to `idle` afterwards.
7. **`events[].type = "new_file"`** is written by the pipeline when it first sees a file in "To edit"
   (the Drive watcher's ntfy message has no state.json access).
8. **Settings env names**: every settings.json key maps to its upper-case env name; the `posting` section
   maps to `POSTING_<KEY>` (e.g. `POSTING_ENABLED=on`). Not used by the app.

## v2.1 laptop transport (no Google Drive), 2026-10-13

- Movies travel laptop → GitHub as draft releases `incoming-<id>` (chunks < 1.9 GB; body JSON with name/file/srt);
  parts travel back as draft releases `parts-<id>` (body JSON per part). Drafts are deleted once received.
- `movies[].modes[m].parts[].drive_path` keeps its name but now means the path *inside the laptop's output
  folder* (`Ready to post/<movie>/<Mode>/<file>.mp4`). The app shows it as "On laptop" with a Copy path button.
- `laptop.json` adds `ready_folder` and `transfers` [{direction: send|receive, movie, file, percent}];
  `uploading` is kept (first send) for back-compat.
- `run.stage = "uploading"` now means "sending parts back" (app text updated).
- Posting moved from GitHub (post.yml removed) to the laptop helper; it writes the same `post` fields.
- `delete_originals` / `cleanup_parts_after_days` now act on the laptop's folders (bin = Recycle Bin).
- Only GitHub secret: `NTFY_TOPIC`. Encryption of drafts was not added (drafts are already private).
