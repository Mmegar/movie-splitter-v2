# Composio + TikTok: what the poster relies on

Checked 2026-10-13. Network to composio.dev's API is blocked in the build sandbox, so nothing was
called for real; the code was tested against a fake `composio` module that mimics the documented shapes.

## Verified from documentation

| Fact | Source |
|---|---|
| Composio's TikTok toolkit has `TIKTOK_UPLOAD_VIDEO`, `TIKTOK_PUBLISH_VIDEO`, `TIKTOK_FETCH_PUBLISH_STATUS`, `TIKTOK_QUERY_CREATOR_INFO`, `TIKTOK_UPLOAD_VIDEOS`, `TIKTOK_POST_PHOTO`, `TIKTOK_LIST_VIDEOS`, `TIKTOK_QUERY_VIDEOS`, `TIKTOK_GET_USER_STATS` | https://docs.composio.dev/toolkits/tiktok |
| `TIKTOK_UPLOAD_VIDEO` inputs: `file_to_upload` (object, required; MP4/MOV/WEBM), `caption` (≤2200), `publish` (bool: publish after upload using the returned publish_id), `privacy_level` (`PUBLIC_TO_EVERYONE` / `MUTUAL_FOLLOW_FRIENDS` / `FOLLOWER_OF_CREATOR` / `SELF_ONLY`), plus `disable_duet/stitch/comment`, `is_aigc`, `chunk_size_bytes`, `video_cover_timestamp_ms`, brand toggles | https://docs.composio.dev/toolkits/tiktok.md |
| `TIKTOK_PUBLISH_VIDEO` needs a **public URL** (`video_url`) that TikTok pulls from: not usable for private Drive files, so not used | same |
| `TIKTOK_FETCH_PUBLISH_STATUS(publish_id)`; poll with backoff (5 s, 10 s, 20 s...), limit 30 req/min per token | same, and https://composio.dev/toolkits/tiktok/framework/codex.md |
| Output of every tool: `{data, error, successful}` | https://docs.composio.dev/toolkits/tiktok.md |
| Auth is OAuth2 and Composio has **no managed TikTok OAuth app**: you need your own TikTok developer app (client key/secret) in a Composio auth config | https://docs.composio.dev/toolkits/tiktok |
| Python SDK: `Composio(api_key=...)`, `composio.tools.execute(slug, arguments, connected_account_id=None, user_id=None, version=None, dangerously_skip_version_check=None, ...)` | https://docs.composio.dev/reference/sdk-reference/python/tools.md |
| Direct execution needs a toolkit version, or `"latest"` + `dangerously_skip_version_check=True` | https://docs.composio.dev/docs/tools-direct/executing-tools |
| Local file paths for file inputs are uploaded by the SDK when `dangerously_allow_auto_upload_download_files=True` and the path is inside `file_upload_dirs` | same |
| Unaudited TikTok API clients: posts are `SELF_ONLY` only, max 5 users per 24 h, and the posting account **must be set to private**; ~15 posts/day/creator cap for everyone | https://developers.tiktok.com/doc/content-sharing-guidelines |

## Assumed (not verifiable here; check on the first real run)

- The nested shape of `data` (publish_id may be at `data.publish_id` or `data.data.publish_id`; `data` may be a
  JSON string). The code searches the whole result for `publish_id`, `status`, `fail_reason`,
  `publicaly_available_post_id` (TikTok's own spelling) so it copes with either.
- Status values are TikTok's: `PROCESSING_UPLOAD`, `PROCESSING_DOWNLOAD`, `SEND_TO_USER_INBOX`,
  `PUBLISH_COMPLETE`, `FAILED`. If still processing after ~5 minutes, the poster counts it as posted
  (TikTok accepted it) and stores the publish_id in the log/event (not as `url`).
- Older SDK versions without the auto-upload/version keyword arguments: the code retries without them.
- The public video URL needs your @name (`TIKTOK_USERNAME` variable); SELF_ONLY posts have no public id, so `url` stays null.

## Implementation (poster.py `post_video`)

1. `Composio(api_key, dangerously_allow_auto_upload_download_files=True, file_upload_dirs=[folder of the file])`
2. `tools.execute("TIKTOK_UPLOAD_VIDEO", {file_to_upload: <local path>, caption, publish: true, privacy_level}, user_id=COMPOSIO_USER_ID [, connected_account_id])`
3. Poll `TIKTOK_FETCH_PUBLISH_STATUS {publish_id}` at 5/10/20/30... s for up to ~5 min.

No fallback to TikTok's own Content Posting API was needed: Composio has a working upload action.

## One-time setup the owner must do

1. TikTok developer account → create an app with **Content Posting API** (scopes `video.upload`, `video.publish`),
   redirect URL from Composio's auth-config screen. Until TikTok audits the app, posts are private only.
2. Set the TikTok account to **private** (required for unaudited apps).
3. composio.dev → create an auth config for TikTok with your app's client key/secret → connect your TikTok account
   under a user id of your choice (e.g. your email).
4. GitHub secrets: `COMPOSIO_API_KEY`, `COMPOSIO_USER_ID` (optional `COMPOSIO_CONNECTED_ACCOUNT_ID`);
   optional variables `TIKTOK_USERNAME`, `COMPOSIO_TIKTOK_VERSION`.
5. Test: Actions → "Post to TikTok" → Run workflow with "dry run" ticked, then once with "force".
