# Liba social publish

Procedure for the Hermes `social-media` worker. Liba OS already holds the approved post.

## Poll

1. `social.poll_due` — OS claims one due `social_publish_queue` row, sets post `publishing`, returns caption, platforms, formats, signed asset URLs.
   - Scheduled posts: `scheduled_for` in the past/now after approval.
   - Immediate posts: OS sets `scheduled_for` to now (or past) and enqueues the same way. No separate Meta path.
2. If `has_work` is false, heartbeat and wait.

## Publish

3. `os.start_run({ trigger })` — use `due.trigger` when OS sends `immediate` / `manual`; otherwise `schedule`.
4. Post with Meta Graph (page token). Never call Meta from Liba OS.
   - Facebook page feed: photo URL or feed message.
   - Facebook story: unpublished photo → photo_stories (skip if it fails; still keep feed).
   - Instagram feed: `/media` + `/media_publish`.
   - Instagram story: `media_type=STORIES`.
5. Do not reply to comments.
6. `social.complete` with `meta_ids`, or `social.fail` with the error.

## Learn (display only)

7. Periodically `social.list_published`.
8. Pull insights. `social.save_analytics`.
9. Pull comments. `social.inbox_upsert`. Never send a reply payload to Graph.

## Secrets (Hermes profile `.env`, not git)

- `LIBA_OS_BASE_URL`
- `LIBA_OS_API_KEY` (social-media agent key from Liba OS)
- `META_PAGE_ID`
- `META_PAGE_ACCESS_TOKEN`
- `META_IG_USER_ID`
- optional `SOCIAL_DRY_RUN=1`
