# Movie Splitter v2: setup guide (laptop + GitHub, no Google Drive)

How it works:
1. **Laptop:** you drop a movie into the "To upload" folder.
2. **Laptop → GitHub:** the laptop sends it to your repo as a *hidden draft* (only you can see drafts),
   in pieces if it's over 2 GB.
3. **GitHub** cuts it (Best stretch, Highlights, subtitles, flips, line, cockroach) and sends every
   part back as another hidden draft.
4. **GitHub → laptop:** the laptop downloads the parts into `Ready to post/<movie>/<mode>/` with
   their captions, then deletes the drafts. The original goes to `Done/`.
5. **Optional:** the laptop posts parts to TikTok at your chosen times (Composio).
6. **iPhone app** shows all of it live and lets you change settings and the wish list.

Already done: ✅ the repo `movie-splitter-v2` · ✅ Python on your laptop.
**Not needed:** Google Drive and rclone. You can ignore the "TikTok automation v2" Drive folder.

---

## 1. Laptop helper (10 min)

Follow **`laptop/README.md`**: download the repo ZIP, make a GitHub token, run
`install_windows.ps1`, and fill in `config.json`. For `ntfy_topic` you can reuse
`kimmeng-v2-bn8m2wdq82uj6r` (the one you subscribed to), or leave it empty to get a new one.

## 2. One GitHub secret (1 min)

github.com/Mmegar/movie-splitter-v2 → **Settings → Secrets and variables → Actions → New repository secret**

- Name: `NTFY_TOPIC`
- Secret: the same ntfy name as in the laptop's `config.json` (the installer shows it, and it's in `github-secrets.txt`)

Old secrets like `GDRIVE_REFRESH_TOKEN` aren't used any more. You can delete them.

## 3. iPhone app (5 min)

1. Repo → **Settings → Pages** → Source: **Deploy from a branch** → **main** / **/docs** → Save.
   About a minute later the link appears: `https://mmegar.github.io/movie-splitter-v2/`
2. Open it in **Safari** → Share → **Add to Home Screen**.
3. Make a token for the app: a fine-grained token for `movie-splitter-v2` only, with
   **Contents: Read and write** + **Actions: Read and write**. (You can reuse the laptop's token.)
4. In the app: **Settings → Connection** → owner `Mmegar`, repo `movie-splitter-v2`, token, ntfy name
   → **Test connection** → turn **Demo mode** off → **Save and connect**.

## 4. First test

Drop a **short** video (a few minutes) into the laptop's "To upload" folder. Within a few minutes you
should see: phone notifications ("Sending…", "Started", "Part 1 ready"…), the run live in the app's
**Now** tab, and the parts appearing in `Ready to post` on the laptop.

## 5. TikTok auto-posting (optional, later)

See `plan/COMPOSIO-NOTES.md`. In short:
1. Create a Composio account and your own TikTok developer app, then connect TikTok in Composio.
2. Run `py -m pip install composio` on the laptop.
3. Put `composio_api_key` and `composio_user_id` in the laptop's `config.json`.
4. Switch posting on in the app.

Until TikTok audits your developer app, posts can only be **private**.
