# Fork notes — Willy Be Smart

This is a fork of [brightbeanxyz/brightbean-studio](https://github.com/brightbeanxyz/brightbean-studio).

It exists for **one** reason, and every divergence below serves it. Keep it that
way: the smaller the delta, the cheaper each upstream merge is.

Base: `d85fce192e687d20e8fd7e9449a40ad7952ec7c3`.

## Why the fork exists

Upstream's TikTok provider only calls the **Direct Post** endpoint
`/v2/post/publish/video/init/`, which requires TikTok's Content Posting API
audit. This deployment cannot get that audit: TikTok's Content Sharing
Guidelines exclude, under Intended Use, *"A utility tool to help upload contents
to the account(s) you or your team manages"* — which is exactly what a
self-hosted, single-brand install is. Upstream hit the same wall (see their
PR #94: the audit was rejected).

Without the audit, Direct Post fails with
`unaudited_client_can_only_post_to_private_accounts`. That error is about the
**account** being private, not the post (upstream PR #144 documents this), so the
only workaround it leaves is making the TikTok profile private — not an option
for a brand account with an audience.

So this fork adds the route TikTok does allow: **Upload/Inbox**
(`/v2/post/publish/inbox/video/init/`, scope `video.upload`, no audit, public
account). The video lands in the creator's TikTok drafts and a person finishes
and publishes it inside the app.

Upstream's own spec asked for this
(`development_specs/feature-spec-social-media-management-v2.md`: *"Direct publish
or 'inbox' mode (user publishes from TikTok app)"*), so it is an unfinished
feature rather than a new one. An issue asking them to implement it upstream is
drafted; if they take it, this fork can go away.

## What changed

| File | Change |
|---|---|
| `providers/tiktok.py` | `POST_MODE_DIRECT` / `POST_MODE_INBOX`; `_publish_inbox()`; `_init_video_publish(..., inbox=True)`; FILE_UPLOAD transfer factored into `_file_upload_source_info()` + `_transfer_video_file()`; duration guard split out of `_check_creator_constraints()` into `_check_video_duration()` so inbox keeps it; `inbox_publish_state()` for reconciliation |
| `apps/composer/models.py` | new `PlatformPost.Status.AWAITING_CREATOR`, its transitions, colour, and PROTECTED_STATUSES |
| `apps/composer/migrations/0021_…` | choices-only `AlterField` for the above |
| `apps/composer/status.py` | `awaiting_creator` in the workflow order (non-terminal) |
| `apps/composer/views.py` | TikTok panel gated on `tiktok_post_mode_*`; Direct-only fields dropped server-side in inbox mode; `_tiktok_default_post_mode()` |
| `apps/publisher/engine.py` | an inbox result parks the row in `awaiting_creator` instead of `published`; `_tiktok_post_mode()` workspace default; `reconcile_awaiting_creator()` |
| `apps/publisher/tasks.py` / `apps.py` | recurring `reconcile_awaiting_creator` task, every 10 minutes |
| `apps/settings_manager/defaults.py` | `publishing.tiktok_post_mode` — **fork default `INBOX`** (upstream's would be `DIRECT_POST`) |
| `apps/calendar/views.py`, `templates/calendar/…`, `templates/organizations/…`, `apps/approvals/templatetags/approval_extras.py` | show the new state: badge reads **"In TikTok drafts"**, and the row appears in the Sent tab |
| `templates/composer/compose.html` | "How to post" selector, and the Direct-Post-only settings hidden in drafts mode |
| `tests/providers/test_tiktok.py` | 11 tests for the new route and the reconciliation reader |

## Two things that are easy to get wrong

1. **An inbox upload is not a publication.** Nothing is on TikTok until a person
   opens the draft. Never let that row read "Published", and never retry it
   through `publish_post` — the bytes are already on TikTok's side, so a retry
   duplicates the video and burns one of the **5 unfinished uploads per user per
   24 hours** TikTok allows.
2. **The caption does not travel.** The inbox endpoint takes `source_info` only —
   no `post_info` — so caption, visibility and comment/duet/stitch are entered by
   the creator in the TikTok app. The composer says so; don't "fix" it by sending
   `post_info` to that endpoint, TikTok refuses it.

## Keeping up with upstream

```sh
git fetch upstream
git rebase upstream/main        # or merge, if the branch is shared
```

`providers/tiktok.py` and `templates/composer/compose.html` are the two files
upstream touches most (#67, #94, #103, #106, #110, #144 all land there), so
expect conflicts there and nowhere else. After any merge, run at least:

```sh
pytest tests/providers/test_tiktok.py
ruff check . && ruff format --check .
```

Design reviewed by Codex before implementation (it caught the
`published`-vs-`awaiting_creator` lie, the duration guard hidden inside the
privacy check, and the retry that would burn pending upload slots).
