# everfold-autopost

Publishes Everfold's approved posts to Instagram (**@everfold.online**) on schedule, using Instagram's official
Graph API. No browser automation and no unofficial APIs.

## How it works
1. `schedule.yaml` lists each post: date and time (Pacific), type, media, caption, hashtags and alt text.
2. Each post has `approved: false` until Mukul approves that week. Only `approved: true` posts are ever published.
3. GitHub Pages serves `media/` publicly so that Meta can fetch the files.
4. The `publish` GitHub Action runs every 30 minutes. It publishes whatever is approved and due, then records it in
   `state/posted.json` so nothing is ever posted twice.
5. Every post is published with Instagram's **AI info** label (`is_ai_generated=true`), because all of the imagery is AI-generated.

## Safety rails
- A post more than 3 hours late is marked `missed`, not posted, so an outage can't produce a burst of catch-up posts.
- A failed post is not retried automatically, which avoids double posting. The run fails and GitHub emails you.
- At most one post per run.
- **Emergency stop:** add an empty file called `PAUSED` to the repo root. Delete it to resume.
- `python publish.py --validate` runs on every push and rejects bad captions, missing files, a missing AI flag,
  and links to the checkout backend.

## Secrets and settings (repo → Settings → Secrets and variables → Actions)
- Secret `META_TOKEN`: a Page access token for the Everfold.online Page with `instagram_basic`,
  `instagram_content_publish`, `pages_show_list`, `pages_read_engagement` and `business_management`. Only Mukul handles it.
- Variable `MEDIA_BASE_URL`: the GitHub Pages URL of this repo, e.g. `https://<user>.github.io/everfold-autopost`.
- Variable `IG_USER_ID` (optional): discovered from the token if it isn't set.

## Manual runs
Actions → publish → Run workflow → mode `check` (tests the token), `validate`, or `publish`.

## Not automated
- Stories: the API can't add link stickers.
- Music: Reels go out with their built-in audio. Trending sounds can only be added in the app.
- Collaborator tags, location and product tags.
