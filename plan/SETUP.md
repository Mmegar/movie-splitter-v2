# Movie Splitter v2: setup guide

You need about 45–60 minutes in total. Do the steps in this order. Steps 1–5 get the system
running; step 6 (TikTok auto-posting) can wait.

---

## 1. Create the v2 repo on GitHub (2 min)

1. github.com → **+** → **New repository**.
2. Name: `movie-splitter-v2` · **Public** (free unlimited minutes) · leave it **empty**: no README.
3. Tell Claude "repo created". Claude pushes v2 into it.

## 2. Google Drive folder ✅ done by Claude

Created in your My Drive:

| Item | ID (you'll paste these later) |
|---|---|
| **TikTok automation v2** (main folder) → `GDRIVE_ROOT_FOLDER_ID` | `1IQK7I_n3ug3tC263Q-D_5MNHQPvfnzaT` |
| **To edit** → watcher `TO_EDIT_FOLDER_ID` | `1vea1cWIJEMd2ekkatYE54fwMeqGlv1em` |
| **Ready to post** | `10LRgv2f_yIX9eWW-imIkWICJPSpGHpSc` |
| **notification name.txt** contains | `kimmeng-v2-bn8m2wdq82uj6r` |

Your only job here: **subscribe to `kimmeng-v2-bn8m2wdq82uj6r` in the ntfy app** on your iPhone.

## 3. Laptop (15–20 min)

Full step-by-step instructions are in `laptop/README.md`. In short:

1. Install **Python 3** (python.org; on Windows tick "Add to PATH") and **rclone** (rclone.org/downloads).
2. Run `rclone config` → new remote named **gdrive** → type **drive** → sign in with your Google account.
   The `token` that appears there also gives you the Drive secrets for step 4.
3. Create a GitHub token: github.com → Settings → Developer settings → **Fine-grained tokens** → only
   the `movie-splitter-v2` repo → **Contents: Read and write**. You'll make a second token for the app in step 7.
4. Download the repo, then run the installer:
   - Windows: right-click `laptop/install_windows.ps1` → **Run with PowerShell**
   - Mac: in Terminal, `bash laptop/install_mac.sh`

   It creates `laptop/config.json` and opens it. Fill in your watch folder (for example `Movies/To upload`),
   the GitHub owner and repo, and the token.
5. After that, it starts by itself every time you log in. Drop movies you own into the watch folder, and
   add public-domain titles to the wish list from the app.

## 4. GitHub secrets (5 min)

Repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Where to find it |
|---|---|
| `GDRIVE_CLIENT_ID` / `GDRIVE_CLIENT_SECRET` | only if you made your own Google client in `rclone config`; if you just pressed Enter (rclone's default), **skip these two** |
| `GDRIVE_REFRESH_TOKEN` | the `refresh_token` value inside rclone's token (step 3.2) |
| ~~`GDRIVE_ROOT_FOLDER_ID`~~ | **not needed**: already built into the workflow |

The same values as your current splitter work too, if you still have them.

## 5. Drive watcher (5 min)

1. script.google.com → **New project** → paste `drive-watcher/Code.gs` from the v2 repo.
2. Project Settings → **Script properties**:
   - `GITHUB_TOKEN`: a fine-grained token with **Actions: Read and write** on `movie-splitter-v2`
   - `TO_EDIT_FOLDER_ID`: the ID from step 2.4
3. Choose **setUp** in the function menu → **Run** → allow access.

## 6. TikTok auto-posting (optional, 20–30 min)

1. Create a free account at **composio.dev** and copy your **API key**.
2. Composio has no ready-made TikTok login, so you create your own TikTok developer app:
   - Go to developers.tiktok.com, create an app, and add **Login Kit** and **Content Posting API**.
   - Enter its client key and secret in Composio's TikTok setup, then connect your TikTok account.
3. Add GitHub secrets `COMPOSIO_API_KEY` and `COMPOSIO_USER_ID` (the user ID you connected with in Composio).
4. **Important limit:** until TikTok audits your app, posts can only be **private** ("Only me"). Your
   TikTok account must also be set to private for that. You can make each post public by hand in TikTok.
   Applying for TikTok's audit removes the limit.
5. In the app: Settings → Posting → turn on, choose times. Test first: GitHub → Actions → **Post to
   TikTok** → Run workflow with **dry run** ticked.

## 7. iPhone app (5 min)

1. Repo → **Settings → Pages** → Source: **Deploy from a branch** → branch **main**, folder **/docs** → Save.
   After about a minute you get a link like `https://<you>.github.io/movie-splitter-v2/`.
2. Open that link in **Safari** → Share → **Add to Home Screen**.
3. Make a token for the app: fine-grained token, only `movie-splitter-v2`, **Contents: Read and write** +
   **Actions: Read and write**, expiry 1 year.
4. In the app: **Settings → Connection** → owner (your GitHub username), repo `movie-splitter-v2`, token,
   and your ntfy name from step 2.3 → **Test connection** → turn **Demo mode** off → **Save and connect**.

---

### Day to day
- **Add movies:** drop files you own into the laptop folder, or add public-domain titles to the wish list in the app.
- **Watch progress:** the **Now** tab, plus ntfy notifications.
- **Post:** automatic (step 6), or copy each part's caption in **Movies** and post by hand.
- **Change anything:** the **Settings** tab. It applies to the next run.
