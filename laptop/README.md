# Laptop helper

A small program that runs quietly on your laptop (Windows or Mac). It does three jobs:

1. **Uploads your movies.** Drop a movie file into your "To upload" folder. Once it has finished
   copying (it waits until the file stops growing for a minute), the helper moves it to Google
   Drive → `TikTok automation v2/To edit`. The cutter on GitHub takes it from there. The file
   is removed from the laptop once it's safely on Drive.
2. **Fetches wish-list films.** Titles you add to the wish list in the iPhone app are looked up
   on archive.org. Only **public-domain** films are downloaded (old films whose copyright has
   run out, like *Nosferatu (1922)*). Anything else shows **"not available"** in the app — if
   you own a copy, put the file in your "To upload" folder instead. No torrents, ever.
3. **Tells the app it's online**, every 5 minutes, plus what it's uploading.

You only set it up once.

---

## 1. Install Python

- **Windows:** go to <https://www.python.org/downloads/>, download, run it, and **tick
  "Add python.exe to PATH"** on the first screen before clicking Install.
- **Mac:** same website, download the macOS installer and run it.

## 2. Install rclone and connect Google Drive

rclone is the free tool that copies files to Google Drive.

- **Windows:** download from <https://rclone.org/downloads/> (Windows, Intel/AMD 64 bit), unzip it,
  and put `rclone.exe` somewhere permanent such as `C:\rclone\rclone.exe`.
- **Mac:** open Terminal and run `curl https://rclone.org/install.sh | sudo bash`.

Then connect it to Google Drive. Open a Command Prompt (Windows) or Terminal (Mac) and type
`rclone config` (on Windows: `C:\rclone\rclone.exe config`), then answer:

| Question | Answer |
|---|---|
| `n/s/q>` | `n` (new remote) |
| `name>` | `gdrive` |
| `Storage>` | `drive` (Google Drive) |
| `client_id>` / `client_secret>` | press Enter (or paste your own, if you made one) |
| `scope>` | `1` (full access) |
| everything else | press Enter |
| "Use web browser to automatically authenticate?" | `y` — a browser opens; log in and allow |
| "Configure this as a Shared Drive?" | `n` |
| last question | `y` then `q` |

Check it works: `rclone lsd gdrive:` should list your Drive folders, including
`TikTok automation v2`.

## 3. Make a GitHub token for the helper

1. On GitHub: your picture → **Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → Generate new token**.
2. Name: `laptop helper`. Expiration: 1 year (set a reminder to renew it).
3. **Repository access → Only select repositories →** `movie-splitter-v2`.
4. **Permissions → Repository permissions → Contents → Read and write.** Nothing else.
5. Generate, then copy the token (starts with `github_pat_`).

## 4. Fill in the settings

Copy `config.example.json` to `config.json` (the installers below do this for you and open it).
Change:

- `watch_folder` — the folder you'll drop movies into, e.g. `C:/Users/kimmeng/Movies/To upload`
  (Mac: `/Users/kimmeng/Movies/To upload`). Use forward slashes `/`. It's created if missing.
- `download_folder` — where wish-list films are saved while downloading.
- `rclone_path` — `rclone` on Mac; on Windows the full path, e.g. `C:/rclone/rclone.exe`.
- `rclone_remote` — `gdrive` (the name from step 2).
- `drive_folder` — `TikTok automation v2`.
- `github_owner` — your GitHub user name. `github_repo` — `movie-splitter-v2`.
- `github_token` — the token from step 3.

Keep `config.json` private: it contains your token.

## 5. Make it start automatically

- **Windows:** right-click `install_windows.ps1` → **Run with PowerShell**. (If Windows refuses,
  open PowerShell in this folder and run
  `powershell -ExecutionPolicy Bypass -File install_windows.ps1`.)
  It runs hidden at every login. To remove: same command with `-Uninstall` at the end.
- **Mac:** open Terminal, type `bash ` (with a space), drag `install_mac.sh` into the window,
  press Enter. To remove: `bash install_mac.sh --uninstall`.
  Tip: macOS protects Desktop, Documents and Downloads. Use a folder in **Movies** (or allow
  Python in System Settings → Privacy & Security → Files and Folders).

## 6. Check it's working

- In the iPhone app the laptop shows **online** within a few minutes.
- Drop a small video into the watch folder. After about a minute it disappears from the folder
  and appears in Drive → `To edit`.
- Problems? Open `agent.log` in this folder; the last lines say what went wrong.
- To test by hand: `python agent.py --once` (Mac: `python3 agent.py --once`) does one check and stops.

## Questions

- **Does the laptop need to stay on?** Only while it's uploading or downloading. The cutting
  and posting happen on GitHub.
- **Can I add subtitles?** Yes: put a `.srt` file with exactly the same name as the movie in the
  folder too (e.g. `Nosferatu (1922).mp4` and `Nosferatu (1922).srt`).
- **A wish-list film says "failed".** It's tried again after an hour; "not available" is
  checked again once a day.
