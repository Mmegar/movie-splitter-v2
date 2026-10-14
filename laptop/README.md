# Laptop helper

A small program that runs quietly on your laptop (Windows or Mac). It's the only link between
your laptop and GitHub; there's no Google Drive in v2.

1. **Sends your movies.** Drop a movie file into your "To upload" folder (plus a `.srt` with the
   same name if you have subtitles). Once it has finished copying (it waits until the file stops
   growing for a minute), the helper sends it to your GitHub repo (big files in pieces under 2 GB),
   then starts the cutter there. You get a phone notification when it starts, at 50%, and when it's sent.
2. **Fetches wish-list films.** Titles you add to the wish list in the iPhone app are looked up on
   archive.org. Only **public-domain** films are downloaded (old films whose copyright has run out,
   like *Nosferatu (1922)*), then sent like step 1. Anything else shows **"not available"**: if you
   own a copy, put the file in your "To upload" folder instead. No torrents, ever.
3. **Brings the parts back.** As GitHub finishes each part, the helper downloads it into
   `<output folder>/Ready to post/<movie>/<mode>/`, with `_parts list.txt` and `captions.txt`
   (ready-to-paste TikTok captions). The copies on GitHub are then deleted. When all parts are back,
   your original movie moves to `<output folder>/Done` (or is kept/deleted: `original_after`).
   If cutting fails, the original goes to `<output folder>/Failed`; move it back into the watch
   folder to try again.
4. **Posts to TikTok (optional).** If you switch posting on in the app, the helper posts the next part
   at your posting times, through Composio (see `plan/COMPOSIO-NOTES.md`). This is the only part
   that needs an extra package: `py -m pip install composio` (once, only if you want it).
5. **Tells the app it's online**, every 5 minutes, plus what it's sending or receiving.

Where do the files go on GitHub? Into *draft* releases of your repo. Drafts are only visible to
people with write access (just you), even though the repo itself is public, and they're deleted as
soon as the laptop has the parts.

You only set it up once. The steps below are for **Windows**; Mac differences are noted.

---

## 1. Install Python

Go to <https://www.python.org/downloads/>, download, run it, and **tick "Add python.exe to PATH"**
on the first screen before clicking Install. (Mac: same website, run the macOS installer.)

## 2. Download the repo

On github.com open `movie-splitter-v2` → green **Code** button → **Download ZIP**. Unzip it somewhere
permanent, e.g. `C:\Users\<you>\movie-splitter-v2` (not in Downloads; Windows may clean that up).
The helper is in the `laptop` folder inside it.

## 3. Make a GitHub token for the helper

1. On GitHub: your picture → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Name: `laptop helper`. Expiration: 1 year (set a reminder to renew it).
3. **Repository access → Only select repositories →** `movie-splitter-v2`.
4. **Permissions → Repository permissions:**
   - **Contents → Read and write** (sending movies, getting parts, status)
   - **Actions → Read and write** (starting the cutter)
5. Generate, then copy the token (starts with `github_pat_`).

## 4. Run the installer (it asks you to fill in the settings)

- **Windows:** in the `laptop` folder, right-click `install_windows.ps1` → **Run with PowerShell**.
  (If Windows refuses: open PowerShell in that folder and run
  `powershell -ExecutionPolicy Bypass -File install_windows.ps1`.)
- **Mac:** open Terminal, type `bash ` (with a space), drag `install_mac.sh` into the window, press Enter.

There's nothing to pip install. The first time, it makes `config.json` and opens it. Change:

- `watch_folder`: the folder you'll drop movies into, e.g. `C:/Users/kimmeng/Videos/To upload`.
  Use forward slashes `/`. It's created if missing.
- `output_folder`: where finished parts arrive, e.g. `C:/Users/kimmeng/Videos/Movie Splitter`.
- `download_folder`: where wish-list films are saved while downloading.
- `github_owner` (`Mmegar`), `github_repo` (`movie-splitter-v2`), `github_token` (step 3).
- `ntfy_topic`: the ntfy name you subscribed to on your iPhone (leave empty to get a new one).

Save, close Notepad, and **run the installer again**. This time it:

1. checks your GitHub token,
2. starts the helper (hidden), and sets it to start whenever you log in,
3. prints NEXT STEPS and opens `github-secrets.txt` with the one secret to add on GitHub.

## 5. Add the GitHub secret (the only one)

github.com → `movie-splitter-v2` → **Settings → Secrets and variables → Actions → New repository
secret**:

| Name | Value |
|---|---|
| `NTFY_TOPIC` | your ntfy name (shown in `github-secrets.txt`) |

That's what lets the cutter on GitHub send notifications to your phone. Keep `config.json` private:
it contains your GitHub token.

## 6. Check it's working

- In the iPhone app the laptop shows **online** within a few minutes.
- Drop a short video into the watch folder. After about a minute you get "Sending to GitHub" and
  then "Sent to GitHub"; the cutter starts; parts appear in `Ready to post` as they're made.
- Problems? Open `agent.log` in the `laptop` folder; the last lines say what went wrong.
- To test by hand: open a Command Prompt in the `laptop` folder and run `py agent.py --once`
  (one round, then stop). `py agent.py --post-now --dry-run` shows which part would be posted next.

## Updates

From version 2.2 on, the helper **updates itself**: every 5 minutes it checks GitHub for newer
versions of its own files, checks they're valid, saves the old ones in `laptop/.backup` and restarts.
Turn it off in the app: Settings → Laptop & internet → "Laptop helper updates itself".
Your `config.json` and `agent_memory.json` are never touched.

## What the app can tell the laptop to do

The app writes requests to `config/commands.json` on GitHub; the helper checks every 30 seconds:
move a movie from your Movies folder (`download_folder`) into To edit, change the order of To edit,
take a movie back out, cut a finished original again (from `Done`/`Failed`), move one to the
Recycle Bin (never deleted for good), and make **custom clips** (it sends the original once more
with the start/end times you picked; the clips arrive in `Ready to post/<movie>/Custom clips`).
**Pause everything** (and "pause when on battery", "upload speed limit") stop it using the internet.

## Questions

- **Does the laptop need to stay on?** While sending a movie, yes. Cutting happens on GitHub even if
  the laptop is off; the parts wait there and download the next time the laptop is on.
- **How big can a movie be?** Any size; big files are sent in pieces under 2 GB. Sending takes as long
  as your internet upload speed needs (a 1.5 GB film at 20 Mbit/s is about 10 minutes).
- **A wish-list film says "failed".** It's tried again after an hour; "not available" is checked again
  once a day.
- **Mac:** macOS protects Desktop, Documents and Downloads. Use folders in **Movies** (or allow Python in
  System Settings → Privacy & Security → Files and Folders).
