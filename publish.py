"""Everfold Instagram autoposter.

Publishes approved entries from schedule.yaml to @everfold.online through the official
Instagram Graph API (Instagram API with Facebook Login). Media is served publicly by GitHub Pages
from this repo; Meta fetches it from there.

  python publish.py --validate   check schedule + media, no network calls to Meta
  python publish.py --check      verify the token and print the connected IG account
  python publish.py              publish everything that is approved and due (used by GitHub Actions)

Env: META_TOKEN (required for --check/publish), MEDIA_BASE_URL (GitHub Pages root),
     IG_USER_ID (optional; discovered from the token if absent), DRY_RUN=1 (log, don't call Meta).
"""
import json, os, sys, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import yaml

ROOT = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = os.path.join(ROOT, 'schedule.yaml')
STATE = os.path.join(ROOT, 'state', 'posted.json')
GRAPH = 'https://graph.facebook.com/v25.0'
TZ = ZoneInfo('America/Vancouver')
LATE_LIMIT = timedelta(hours=3)        # a post more than 3 h late is marked "missed", never dumped out in a burst
TYPES = {'reel', 'carousel', 'image', 'story'}
VIDEO, IMAGE = ('.mp4', '.mov'), ('.jpg', '.jpeg')


def load():
    with open(SCHEDULE, encoding='utf-8') as f:
        sched = yaml.safe_load(f) or {}
    with open(STATE, encoding='utf-8') as f:
        state = json.load(f)
    return sched.get('posts', []), state


def save_state(state):
    with open(STATE, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write('\n')


def when(p):
    return datetime.fromisoformat(f"{p['date']}T{p.get('time', '17:00')}").replace(tzinfo=TZ)


def caption(p):
    text = p.get('caption', '').strip()
    tags = p.get('hashtags', '').strip()
    return f'{text}\n\n{tags}' if tags else text


# ------------------------------------------------------------------ validation
def validate(posts):
    errors, seen = [], set()
    for p in posts:
        pid = p.get('id', '?')
        if pid in seen: errors.append(f'{pid}: duplicate id')
        seen.add(pid)
        t = p.get('type')
        if t not in TYPES: errors.append(f'{pid}: type must be one of {sorted(TYPES)}'); continue
        media = p.get('media') or []
        if not media: errors.append(f'{pid}: no media')
        for m in media:
            path = os.path.join(ROOT, m)
            if not os.path.isfile(path): errors.append(f'{pid}: missing file {m}')
            ext = os.path.splitext(m)[1].lower()
            want = VIDEO if t == 'reel' else IMAGE
            if ext not in want: errors.append(f'{pid}: {m} should be {"/".join(want)} for a {t}')
        if t == 'carousel' and not 2 <= len(media) <= 10: errors.append(f'{pid}: carousels take 2-10 items')
        if t in ('reel', 'image', 'story') and len(media) != 1: errors.append(f'{pid}: {t} takes exactly 1 file')
        cap = caption(p)
        if len(cap) > 2200: errors.append(f'{pid}: caption is {len(cap)} chars (max 2200)')
        if cap.count('#') > 30: errors.append(f'{pid}: more than 30 hashtags')
        if len(p.get('alt_text', '')) > 1000: errors.append(f'{pid}: alt text over 1000 chars')
        if p.get('ai_generated') is not True: errors.append(f'{pid}: ai_generated must be true (all imagery is AI)')
        if 'shop.everfold.online' in cap: errors.append(f'{pid}: never link the checkout backend')
        try: when(p)
        except Exception: errors.append(f'{pid}: bad date/time')
    return errors


# ------------------------------------------------------------------ Graph API
class Meta:
    def __init__(self, token, ig_user=None):
        self.token, self.ig = token, ig_user

    def call(self, method, path, **params):
        # token goes in a header, never the URL, so it can't surface in exception text or public Action logs
        try:
            r = requests.request(method, f'{GRAPH}/{path}', data=params if method == 'POST' else None,
                                 params=params if method == 'GET' else None, timeout=60,
                                 headers={'Authorization': f'Bearer {self.token}'})
            body = r.json()
        except Exception as e:
            raise RuntimeError(f'Graph API {path}: {type(e).__name__}') from None
        if 'error' in body:
            raise RuntimeError(f"Graph API {path}: {body['error'].get('message')} (code {body['error'].get('code')})")
        return body

    def discover(self):
        if self.ig: return self.ig
        me = self.call('GET', 'me', fields='id,name,instagram_business_account{id,username}')
        acct = me.get('instagram_business_account')
        if not acct:   # user token: walk the Pages it manages
            pages = self.call('GET', 'me/accounts', fields='name,instagram_business_account{id,username}')['data']
            acct = next((pg['instagram_business_account'] for pg in pages if pg.get('instagram_business_account')), None)
        if not acct: raise RuntimeError('No Instagram professional account is linked to this token.')
        self.ig, self.username = acct['id'], acct.get('username')
        return self.ig

    def container(self, **params):
        return self.call('POST', f'{self.ig}/media', **params)['id']

    def wait_ready(self, cid, timeout=600):
        t0 = time.time()
        while time.time() - t0 < timeout:
            st = self.call('GET', cid, fields='status_code,status')
            if st.get('status_code') == 'FINISHED': return
            if st.get('status_code') in ('ERROR', 'EXPIRED'): raise RuntimeError(f'container {cid}: {st}')
            time.sleep(10)
        raise RuntimeError(f'container {cid} not ready after {timeout}s')

    def publish(self, cid):
        mid = self.call('POST', f'{self.ig}/media_publish', creation_id=cid)['id']
        link = self.call('GET', mid, fields='permalink').get('permalink')
        return mid, link


def post_one(meta, p, base):
    url = lambda m: f"{base.rstrip('/')}/{m}"
    t, media, cap = p['type'], p['media'], caption(p)
    ai = 'true'
    if t == 'reel':
        cid = meta.container(media_type='REELS', video_url=url(media[0]), caption=cap,
                             share_to_feed='true', is_ai_generated=ai)
    elif t == 'image':
        cid = meta.container(image_url=url(media[0]), caption=cap, alt_text=p.get('alt_text', ''), is_ai_generated=ai)
    elif t == 'story':
        cid = meta.container(media_type='STORIES', image_url=url(media[0]), is_ai_generated=ai)
    else:  # carousel: alt text per slide, AI flag on the parent only
        alts = p.get('slide_alt') or []
        kids = []
        for i, m in enumerate(media):
            kid = meta.container(image_url=url(m), is_carousel_item='true',
                                 alt_text=alts[i] if i < len(alts) else p.get('alt_text', ''))
            meta.wait_ready(kid, 120); kids.append(kid)
        cid = meta.container(media_type='CAROUSEL', children=','.join(kids), caption=cap, is_ai_generated=ai)
    meta.wait_ready(cid)
    return meta.publish(cid)


def main():
    posts, state = load()
    errors = validate(posts)
    if '--validate' in sys.argv:
        for e in errors: print('ERROR', e)
        approved = [p['id'] for p in posts if p.get('approved')]
        print(f'{len(posts)} posts, {len(approved)} approved, {len(errors)} errors')
        sys.exit(1 if errors else 0)
    if errors:
        for e in errors: print('ERROR', e)
        sys.exit('schedule.yaml has errors; nothing published')

    if os.path.exists(os.path.join(ROOT, 'PAUSED')):
        print('PAUSED file present; not publishing'); return

    token = os.environ.get('META_TOKEN')
    if not token: sys.exit('META_TOKEN is not set')
    meta = Meta(token, os.environ.get('IG_USER_ID'))
    meta.discover()
    if '--check' in sys.argv:
        print(f'Token OK. Instagram account id {meta.ig} (@{getattr(meta, "username", "?")})'); return

    base = os.environ.get('MEDIA_BASE_URL')
    if not base: sys.exit('MEDIA_BASE_URL is not set')
    now = datetime.now(TZ)
    changed, failed = False, False
    for p in sorted(posts, key=when):
        pid = p['id']
        if pid in state or not p.get('approved'): continue
        due = when(p)
        if now < due: continue
        if now - due > LATE_LIMIT:
            state[pid] = {'status': 'missed', 'due': due.isoformat(), 'noted': now.isoformat()}
            print(f'MISSED {pid} (due {due:%b %d %H:%M}); reschedule it by changing its id/date'); changed = True
            continue
        if os.environ.get('DRY_RUN') == '1':
            print(f'DRY RUN would publish {pid}'); continue
        try:
            mid, link = post_one(meta, p, base)
            state[pid] = {'status': 'posted', 'media_id': mid, 'permalink': link, 'at': datetime.now(TZ).isoformat()}
            print(f'POSTED {pid} -> {link}')
        except Exception as e:
            state[pid] = {'status': 'failed', 'error': str(e).replace(token, '***')[:500], 'at': datetime.now(TZ).isoformat()}
            print(f'FAILED {pid}: {e}'); failed = True
        changed = True
        save_state(state)
        break   # at most one feed post per run
    if changed: save_state(state)
    if failed: sys.exit('a post failed; see state/posted.json (it will not be retried automatically)')


if __name__ == '__main__':
    main()
