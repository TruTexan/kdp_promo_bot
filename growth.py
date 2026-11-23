#!/usr/bin/env python3
"""
Growth engine — the "completely free" daily marketing loop, automated.

  1. AI writes the posts          -> ChatGPT or Claude (llm.py), templates if no key
  2. Images/videos                -> local quote cards (Pillow) + Canva Bulk Create CSV
  3. Post daily on TikTok, Instagram Reels, Facebook, Pinterest (+ Reddit drafts)
                                  -> Buffer queue (webhook via Zapier/Make) or CSV
  4. Repurpose 1 piece -> 10 posts (one per platform/format)
  + HubSpot free CRM: sync reader/ARC/newsletter contacts, blog + email drafts
  + TikTok Creative Center: weekly trend check feeds hashtags/sounds (trends.json)

Commands:
  python growth.py plan30                 # 30-day daily plan, all platforms, all drafts
  python growth.py repurpose "text"       # one idea -> 10 platform-native posts
  python growth.py images                 # quote cards + out/canva_bulk.csv
  python growth.py blog                   # HubSpot/website blog drafts
  python growth.py hubspot [--live]       # leads.csv -> HubSpot contacts (dry-run default)
  python growth.py publish [--live]       # send today's items to Buffer webhook
  python growth.py daily [--live]         # what cron/GitHub Actions runs every morning
  python growth.py reschedule 2026-12-01  # move the plan so day 1 is that date
  python growth.py status                 # show the plan's first/last day
  python growth.py add-platform x         # write posts for one more platform into the current plan
  python growth.py drop-platform pinterest

Everything passes promo_bot.compliance() first; blocked items are never queued.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from promo_bot import OUT, HERE, compliance, load_book, store_link  # noqa: E402  (also sets UTF-8)
import llm

# ---------------------------------------------------------------------------
# Platforms: the 10 repurposing targets. Auto = Buffer can schedule it.
# ---------------------------------------------------------------------------
PLATFORMS = {
    "tiktok":    {"auto": True,  "limit": 2200, "fmt": "TikTok PHOTO post caption (it goes under an image card, not a video): hook as the first line, 2–4 short lines in a BookTok voice, CTA 'link in bio', then 3–5 hashtags including #BookTok"},
    "reels":     {"auto": True,  "limit": 2200, "fmt": "Instagram Reel script + caption, hook first line, 3–5 hashtags"},
    "facebook":  {"auto": True,  "limit": 1500, "fmt": "conversational Facebook post ending in a question"},
    "pinterest": {"auto": True,  "limit": 500,  "fmt": "Pin title (≤100 chars) + keyword-rich description (≤500 chars)"},
    "x":         {"auto": True,  "limit": 280,  "fmt": "single post ≤280 chars"},
    "bluesky":   {"auto": True,  "limit": 300,  "fmt": "single post ≤300 chars"},
    "mastodon":  {"auto": True,  "limit": 500,  "fmt": "single post ≤500 chars, CamelCase hashtags"},
    "reddit":    {"auto": False, "limit": 3000, "fmt": "value-first Reddit post (title + body), no hard sell, link only if the community allows"},
    "email":     {"auto": False, "limit": 3000, "fmt": "newsletter section with Subject: line"},
    "blog":      {"auto": False, "limit": 6000, "fmt": "300-word blog post with H2s for author site/HubSpot"},
}
DAILY = ["tiktok", "reels", "facebook", "pinterest"]  # default; override with growth.daily_platforms in book.json
EXTRA = ["x", "bluesky", "mastodon"]

# 10 content pillars rotated over 30 days
PILLARS = [
    ("hook",      "The premise in one gripping line"),
    ("excerpt",   "A short, spoiler-free excerpt"),
    ("character", "Meet a character"),
    ("trope",     "'If you liked X, try this' / tropes in the book"),
    ("bts",       "Behind the scenes of writing it"),
    ("aesthetic", "Mood board / setting aesthetic"),
    ("question",  "Engagement question for readers of the genre"),
    ("reaction",  "Reader reaction / honest review highlight"),
    ("offer",     "Where to get it (KU, deal or free day if live)"),
    ("author",    "Author life / why I wrote this"),
]


def trends(book: dict) -> dict:
    """TikTok Creative Center has no public API — paste weekly findings into trends.json."""
    p = HERE / "trends.json"
    t = json.loads(p.read_text()) if p.exists() else {}
    t.setdefault("hashtags", [])
    t.setdefault("sounds", [])
    t.setdefault("formats", [])
    t["all_tags"] = list(dict.fromkeys(t["hashtags"] + book.get("hashtags", [])))
    return t


def seed_for(book: dict, pillar: str, i: int) -> str:
    pick = lambda key, alt: (book.get(key) or [alt])[i % len(book.get(key) or [alt])]
    return {
        "hook": book["hook"],
        "excerpt": pick("excerpts", book["hook"]),
        "character": pick("characters", f"The protagonist of {book['title']}"),
        "trope": f"For fans of {', '.join(book.get('comparable_titles', [])[:2])}: "
                 f"{', '.join(book.get('themes', []))}.",
        "bts": pick("behind_the_scenes", f"How {book['title']} started as a single image."),
        "aesthetic": f"The world of {book['title']}: {', '.join(book.get('themes', []))}.",
        "question": f"What's the best {book.get('genre', 'book')} you read this year?",
        "reaction": pick("reader_quotes", "Early readers are finishing it in one sitting."),
        "offer": f"{book['title']} is on Kindle"
                 + (" and free with Kindle Unlimited" if book.get("kdp_select") else "") + ".",
        "author": f"Why I wrote {book['title']}.",
    }[pillar]


# ---------------------------------------------------------------------------
# Step 4: repurpose one piece into 10
# ---------------------------------------------------------------------------
def template_post(book: dict, platform: str, seed: str, tags: list[str]) -> str:
    link, t4 = store_link(book), " ".join(tags[:4])
    T = book["title"]
    return {
        "tiktok": f"{seed}\n\n{T} by {book['author']}. {book.get('tagline', '')}\nLink in bio.\n\n{t4}",
        "reels": f"{seed}\n\n{T} by {book['author']}. Link in bio.\n\n{t4}",
        "facebook": f"{seed}\n\nThat's the heart of {T}. {link}\n\n{seed_for(book, 'question', 0)}",
        "pinterest": f"TITLE: {T} — {book.get('genre', '')} for {book['audience'][:40]}\n"
                     f"DESCRIPTION: {seed} {book.get('blurb', '')[:250]} {link}",
        "x": f"{seed[:180]} {link} {' '.join(tags[:2])}",
        "bluesky": f"{seed[:200]} {link}",
        "mastodon": f"{seed}\n\n{T} by {book['author']}: {link}\n\n"
                    + " ".join(h if h.startswith("#") else "#" + h for h in tags[:3]),
        "reddit": f"TITLE: {seed_for(book, 'question', 0)}\nBODY: I've been thinking about this while "
                  f"writing {book.get('genre', 'fiction')}. {seed}\n(Only add your link where the "
                  f"community's self-promo rules allow it.)",
        "email": f"Subject: {seed[:60]}\n\n{seed}\n\n{book.get('blurb', '')}\n\nRead it: {link}\n— {book['author']}",
        "blog": f"# {seed[:70]}\n\n## The idea\n{seed}\n\n## About the book\n{book.get('blurb', '')}\n\n"
                f"## Get it\n[{T} on Amazon]({link})",
    }[platform]


LONG = {"reddit", "email", "blog"}


def _ai_batch(book: dict, seed: str, platforms: list[str], tr: dict) -> dict:
    """One AI call for a group of platforms; retries once if the JSON comes back broken."""
    if not platforms:
        return {}
    prompt = ("Repurpose this single idea into platform-native posts. Return ONLY a JSON object "
              f"whose keys are {platforms} and values are the post text.\n"
              + "\n".join(f"- {p}: {PLATFORMS[p]['fmt']} (max {PLATFORMS[p]['limit']} chars)" for p in platforms)
              + f"\nIdea: {seed}\nBook: {json.dumps({k: book.get(k) for k in ('title','author','genre','hook','tagline','blurb','audience','setting','tone','characters','spoiler_guard')})}"
              + f"\nLink: {store_link(book)}\nTrending hashtags: {tr['all_tags'][:8]}"
              + (f"\nTrending sounds/formats (TikTok/Reels): {tr['sounds'][:3] + tr['formats'][:3]}" if tr['sounds'] or tr['formats'] else ""))
    for attempt in (1, 2):
        ai = llm.write(prompt, max_tokens=8000)
        if not ai:
            return {}
        try:
            data = json.loads(ai[ai.find("{"): ai.rfind("}") + 1])
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
        print(f"  [repurpose] AI reply wasn't valid JSON (attempt {attempt}/2)")
    return {}


def repurpose(book: dict, seed: str, platforms: list[str] | None = None) -> dict[str, dict]:
    platforms = platforms or list(PLATFORMS)
    tr = trends(book)
    tags = tr["all_tags"]
    parsed = _ai_batch(book, seed, [p for p in platforms if p not in LONG], tr)
    parsed.update(_ai_batch(book, seed, [p for p in platforms if p in LONG], tr))
    out = {}
    for p in platforms:
        text = str(parsed.get(p) or template_post(book, p, seed, tags))[: PLATFORMS[p]["limit"]]
        blocked, flags = compliance(text)
        out[p] = {"text": text, "source": "ai" if parsed.get(p) else "template",
                  "auto": PLATFORMS[p]["auto"], "status": "blocked" if blocked else "ready",
                  "blocked_reasons": blocked, "review_flags": flags}
    return out


# ---------------------------------------------------------------------------
# 30-day daily plan
# ---------------------------------------------------------------------------
def plan30(book: dict) -> list[dict]:
    g = book.get("growth", {})
    start = dt.date.fromisoformat(g.get("start_date") or dt.date.today().isoformat())
    days = int(g.get("days", 30))
    plats = [p for p in g.get("daily_platforms", DAILY) if p in PLATFORMS] \
        + [p for p in EXTRA if p in g.get("extra_platforms", EXTRA)]
    reddit_day = int(g.get("reddit_weekday", 2))  # 0=Mon; one value-first post a week max
    queue = []
    (OUT / "daily").mkdir(parents=True, exist_ok=True)
    for n in range(days):
        day = start + dt.timedelta(days=n)
        key, label = PILLARS[n % len(PILLARS)]
        seed = seed_for(book, key, n // len(PILLARS))
        targets = plats + (["reddit"] if day.weekday() == reddit_day else []) \
            + (["email"] if day.weekday() == 3 else []) + (["blog"] if day.weekday() == 0 else [])
        posts = repurpose(book, seed, targets)
        md = [f"# Day {n+1} — {day} — {label}\n", f"**Seed:** {seed}\n"]
        if day.weekday() == 0:
            md.append("**Weekly task:** open TikTok Creative Center → Trends → hashtags/songs "
                      "(filter Books/Entertainment); paste winners into trends.json.\n")
        for p, item in posts.items():
            mode = "auto (Buffer)" if item["auto"] else "manual"
            md.append(f"## {p} — {mode} — {item['status']}\n```\n{item['text']}\n```")
            for f in item["review_flags"]:
                md.append(f"> review: {f}")
            queue.append({"date": day.isoformat(), "time": g.get("post_times", {}).get(p, "18:00"),
                          "platform": p, "pillar": key, "image": f"out/images/{key}_{p}.{'jpg' if p == 'tiktok' else 'png'}"
                          if p in ("pinterest", "reels", "tiktok", "facebook") else "", **item})
        (OUT / "daily" / f"day{n+1:02d}.md").write_text("\n\n".join(md) + "\n")
    (OUT / "queue.json").write_text(json.dumps(queue, indent=2, ensure_ascii=False))
    with open(OUT / "buffer_queue.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "time", "platform", "text", "image", "status"])
        for q in queue:
            if q["auto"]:
                w.writerow([q["date"], q["time"], q["platform"], q["text"], q["image"], q["status"]])
    ready = sum(q["status"] == "ready" for q in queue)
    print(f"plan30: {days} days, {len(queue)} posts ({ready} ready) -> out/daily/, "
          f"out/queue.json, out/buffer_queue.csv")
    return queue


# ---------------------------------------------------------------------------
# Images: local cards (free, no account) + Canva Bulk Create CSV
# ---------------------------------------------------------------------------
SIZES = {"tiktok": (1080, 1920), "reels": (1080, 1350), "pinterest": (1000, 1500),
         "facebook": (1080, 1080)}


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    for name in (["DejaVuSerif-Bold.ttf", "DejaVuSans-Bold.ttf"] if bold
                 else ["DejaVuSerif.ttf", "DejaVuSans.ttf"]):
        for base in ("/usr/share/fonts/truetype/dejavu/", "C:/Windows/Fonts/", "/Library/Fonts/", ""):
            try:
                return ImageFont.truetype(base + name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def images(book: dict) -> None:
    rows = []
    for i, (key, label) in enumerate(PILLARS):
        rows.append({"pillar": key, "headline": seed_for(book, key, 0)[:140],
                     "subtext": f"{book['title']} · {book['author']}",
                     "cta": "Read free with Kindle Unlimited" if book.get("kdp_select") else "Available on Kindle",
                     "label": label})
    with open(OUT / "canva_bulk.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("images: Pillow not installed (pip install pillow) — wrote out/canva_bulk.csv only")
        return
    pal = book.get("brand", {})
    bg, fg, accent = pal.get("bg", "#14161c"), pal.get("fg", "#f2efe8"), pal.get("accent", "#c9a35b")
    cover = HERE / pal.get("cover_image", "cover.jpg")
    (OUT / "images").mkdir(parents=True, exist_ok=True)
    count = 0
    for r in rows:
        for p, (W, H) in SIZES.items():
            img = Image.new("RGB", (W, H), bg)
            dr = ImageDraw.Draw(img)
            y = int(H * 0.12)
            if cover.exists():
                c = Image.open(cover).convert("RGB")
                c.thumbnail((int(W * 0.45), int(H * 0.38)))
                img.paste(c, ((W - c.width) // 2, y))
                y += c.height + int(H * 0.05)
            fs = int(W * 0.065)
            f, fb = _font(fs, True), _font(int(fs * 0.55))
            maxw = W * 0.84

            def wrap(text, font):
                lines, cur = [], ""
                for word in text.split():
                    t = (cur + " " + word).strip()
                    if dr.textlength(t, font=font) <= maxw:
                        cur = t
                    else:
                        lines.append(cur)
                        cur = word
                return lines + [cur] if cur else lines

            head = wrap(r["headline"], f)
            block = len(head) * fs * 1.3 + fs * 1.4 + 2 * fs * 0.9
            if not cover.exists():
                y = int((H - block) / 2)
            for line in head:
                dr.text(((W - dr.textlength(line, font=f)) / 2, y), line, font=f, fill=fg)
                y += int(fs * 1.3)
            y += int(fs * 0.6)
            dr.line([(W * 0.35, y), (W * 0.65, y)], fill=accent, width=4)
            y += int(fs * 0.8)
            for line in (r["subtext"], r["cta"]):
                for sub in wrap(line, fb):
                    dr.text(((W - dr.textlength(sub, font=fb)) / 2, y), sub, font=fb,
                            fill=accent if line == r["cta"] else fg)
                    y += int(fs * 0.9)
            if p == "tiktok":  # TikTok photo posts take JPEG/WebP, not PNG
                img.save(OUT / "images" / f"{r['pillar']}_{p}.jpg", quality=92)
            else:
                img.save(OUT / "images" / f"{r['pillar']}_{p}.png")
            count += 1
    print(f"images: {count} cards -> out/images/ ; Canva Bulk Create data -> out/canva_bulk.csv"
          + ("" if cover.exists() else " (add cover.jpg next to book.json to include your cover)"))


# ---------------------------------------------------------------------------
# HubSpot free CRM + blog drafts
# ---------------------------------------------------------------------------
def blog(book: dict) -> None:
    (OUT / "blog").mkdir(parents=True, exist_ok=True)
    topics = ["bts", "trope", "character", "author"]
    for k in topics:
        seed = seed_for(book, k, 0)
        text = llm.write(f"Write a 400–600 word blog post (markdown, H2s, ends with a soft CTA to {store_link(book)}) "
                         f"for the author's website about: {seed}. Tone: {book.get('tone','')}. Spoiler rule: {book.get('spoiler_guard','')}. Book: {book['title']} by {book['author']}, "
                         f"{book.get('genre')}. Blurb: {book.get('blurb')}", 1500) \
            or template_post(book, "blog", seed, [])
        blocked, _ = compliance(text)
        (OUT / "blog" / f"{k}.md").write_text(("<!-- BLOCKED: review copy -->\n" if blocked else "") + text)
    print(f"blog: {len(topics)} drafts -> out/blog/ (paste into HubSpot's free blog or your site)")


def hubspot(book: dict, live: bool) -> None:
    """Contacts must have opted in (newsletter signup, ARC form). leads.csv: email,firstname,lastname,source"""
    p = HERE / "leads.csv"
    if not p.exists():
        p.write_text("email,firstname,lastname,source\n")
        print("hubspot: created empty leads.csv — add opted-in readers and rerun")
        return
    rows = [r for r in csv.DictReader(p.open(encoding="utf-8-sig")) if r.get("email")]
    token = os.environ.get(book.get("hubspot", {}).get("token_env", "HUBSPOT_TOKEN"), "")
    for r in rows:
        props = {"email": r["email"], "firstname": r.get("firstname", ""), "lastname": r.get("lastname", ""),
                 "lifecyclestage": "subscriber"}
        if not live or not token:
            print(f"[dry-run] HubSpot contact {r['email']} ({r.get('source', '')})")
            continue
        req = urllib.request.Request("https://api.hubapi.com/crm/v3/objects/contacts",
                                     data=json.dumps({"properties": props}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {token}"})
        try:
            urllib.request.urlopen(req, timeout=20)
            print(f"hubspot: added {r['email']}")
        except urllib.error.HTTPError as ex:
            print(f"hubspot: {r['email']} -> {ex.code} ({'already exists' if ex.code == 409 else ex.reason})")
    if live and not token:
        print("hubspot: set HUBSPOT_TOKEN (private app token, crm.objects.contacts.write scope)")


# ---------------------------------------------------------------------------
# Publish to Buffer (via Zapier/Make webhook) — Buffer then posts to
# TikTok, Instagram, Facebook, Pinterest, X, Bluesky, Mastodon.
# ---------------------------------------------------------------------------
def _section(text: str, label: str) -> str | None:
    """Text after a 'LABEL:' line (e.g. CAPTION:, Description:), up to the next ALLCAPS label."""
    m = re.search(rf"(?ims)^\W*{label}\W*:[ \t]*(.*?)(?=^\W*[A-Z][A-Z /()-]{{2,}}:|\Z)", text)
    return m.group(1).strip() if m and m.group(1).strip() else None


def payload_for(book: dict, x: dict) -> dict:
    """What Zapier receives: ready-to-map fields for Buffer's 'Add to Buffer' action."""
    text, plat = x["text"], x["platform"]
    caption, title, script = text, "", ""
    if plat in ("reels", "tiktok"):
        caption = _section(text, "CAPTION") or text
        script = text.split("CAPTION")[0].strip() if "CAPTION" in text else ""
    elif plat == "pinterest":
        title = _section(text, "TITLE") or ""
        desc = _section(text, "DESCRIPTION")
        if not title:
            lines = [l for l in text.splitlines() if l.strip()]
            title, desc = (lines[0] if lines else book["title"]), "\n".join(lines[1:])
        title = re.sub(r"(?i)^\W*title\W*:\s*", "", title).split("\n")[0][:100]
        caption = (desc or text)[:500]
    base = book.get("image_base_url", "").rstrip("/")
    img = x.get("image", "")
    return {"platform": plat, "date": x["date"], "caption": caption, "title": title,
            "link": store_link(book), "image_url": f"{base}/{Path(img).name}" if base and img else "",
            "reel_script": script}


def _hook_url(book: dict, plat: str) -> str:
    names = book.get("buffer", {}).get("webhooks", {})
    return os.environ.get(names.get(plat, f"BUFFER_WEBHOOK_{plat.upper()}"), "") \
        or os.environ.get("BUFFER_WEBHOOK_URL", "")


def publish(book: dict, live: bool, today: dt.date) -> None:
    p = OUT / "queue.json"
    if not p.exists():
        sys.exit("No queue — run `plan30` first.")
    q = json.loads(p.read_text())
    due = [x for x in q if x["date"] == today.isoformat() and x["status"] == "ready"]
    for x in due:
        if not x["auto"]:
            print(f"[manual] {x['platform']}: copy today's draft from out/daily/ and post it yourself")
            continue
        body, url = payload_for(book, x), _hook_url(book, x["platform"])
        if x["platform"] in ("reels", "pinterest", "tiktok") and not body["image_url"]:
            print(f"  warning: {x['platform']} needs an image — set image_base_url in book.json")
        if not live or not url:
            why = "" if live else "[dry-run] "
            print(f"{why}{x['platform']:9} -> {body['caption'][:70]!r}"
                  + (f"\n          image: {body['image_url']}" if body["image_url"] else "")
                  + ("  (no webhook set for this platform)" if live and not url else ""))
            continue
        req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=20)
            x["status"] = "queued"
            print(f"sent to Zapier -> Buffer: {x['platform']}")
        except Exception as ex:
            x["status"], x["error"] = "error", str(ex)
            print(f"error {x['platform']}: {ex}")
    if live:
        p.write_text(json.dumps(q, indent=2, ensure_ascii=False))
    if not due:
        print(f"publish: nothing scheduled for {today}. Run `python growth.py status` to see the plan's dates.")


def reschedule(start: dt.date) -> None:
    """Shift the whole 30-day plan so day 1 lands on `start`."""
    p = OUT / "queue.json"
    q = json.loads(p.read_text())
    old = sorted({x["date"] for x in q})
    remap = {d: (start + dt.timedelta(days=i)).isoformat() for i, d in enumerate(old)}
    for x in q:
        x["date"] = remap[x["date"]]
        if x["status"] in ("queued", "error"):
            x["status"] = "ready"
    p.write_text(json.dumps(q, indent=2, ensure_ascii=False))
    for i, f in enumerate(sorted((OUT / "daily").glob("day*.md"))):
        t = f.read_text()
        f.write_text(re.sub(r"^# Day (\d+) — \d{4}-\d{2}-\d{2}",
                            lambda m: f"# Day {m.group(1)} — {remap.get(old[int(m.group(1)) - 1], '')}", t, count=1))
    print(f"reschedule: day 1 = {start}, day {len(old)} = {start + dt.timedelta(days=len(old) - 1)}")


def add_platform(book: dict, plat: str) -> None:
    """Write posts for one more platform into every day of the current plan."""
    p = OUT / "queue.json"
    q = json.loads(p.read_text())
    dates = sorted({x["date"] for x in q})
    q = [x for x in q if x["platform"] != plat]
    g = book.get("growth", {})
    for n, day in enumerate(dates):
        pillar = next(x["pillar"] for x in q if x["date"] == day)
        seed = seed_for(book, pillar, n // len(PILLARS))
        item = repurpose(book, seed, [plat])[plat]
        q.append({"date": day, "time": g.get("post_times", {}).get(plat, "18:00"), "platform": plat,
                  "pillar": pillar, "image": f"out/images/{pillar}_{plat}.{'jpg' if plat == 'tiktok' else 'png'}"
                  if plat in SIZES else "", **item})
        f = OUT / "daily" / f"day{n+1:02d}.md"
        if f.exists():
            t = re.sub(rf"\n## {plat} — .*?```\n.*?```\n*", "\n", f.read_text(), flags=re.S)
            mode = "auto (Buffer)" if item["auto"] else "manual"
            f.write_text(t.rstrip() + f"\n\n## {plat} — {mode} — {item['status']}\n```\n{item['text']}\n```\n")
        print(f"  day {n+1:2d} {day}: {plat} {item['source']}")
    q.sort(key=lambda x: (x["date"], x["platform"]))
    p.write_text(json.dumps(q, indent=2, ensure_ascii=False))
    _write_buffer_csv(q)


def drop_platform(plat: str) -> None:
    p = OUT / "queue.json"
    q = [x for x in json.loads(p.read_text()) if x["platform"] != plat]
    p.write_text(json.dumps(q, indent=2, ensure_ascii=False))
    for f in (OUT / "daily").glob("day*.md"):
        f.write_text(re.sub(rf"## {plat} — .*?```\n.*?```\n*", "", f.read_text(), flags=re.S))
    _write_buffer_csv(q)
    print(f"drop-platform: removed {plat}; {len(q)} posts left")


def _write_buffer_csv(q: list) -> None:
    with open(OUT / "buffer_queue.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "time", "platform", "text", "image", "status"])
        for x in q:
            if x["auto"]:
                w.writerow([x["date"], x["time"], x["platform"], x["text"], x["image"], x["status"]])


def status() -> None:
    q = json.loads((OUT / "queue.json").read_text())
    dates = sorted({x["date"] for x in q})
    left = [d for d in dates if d >= dt.date.today().isoformat()]
    print(f"plan runs {dates[0]} to {dates[-1]}; {len(left)} days left")
    if not left:
        print("plan finished: run `python growth.py plan30` for a new month")


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["plan30", "repurpose", "images", "blog", "hubspot", "publish",
                                    "daily", "reschedule", "status", "add-platform", "drop-platform"])
    ap.add_argument("text", nargs="?")
    ap.add_argument("--book", default=str(HERE / "book.json"))
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--today")
    a = ap.parse_args()
    book = load_book(Path(a.book))
    OUT.mkdir(exist_ok=True)
    today = dt.date.fromisoformat(a.today) if a.today else dt.date.today()
    if a.cmd == "plan30":
        plan30(book)
    elif a.cmd == "repurpose":
        res = repurpose(book, a.text or book["hook"])
        (OUT / "repurposed.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
        for p, item in res.items():
            print(f"--- {p} [{item['status']}]\n{item['text']}\n")
    elif a.cmd == "images":
        images(book)
    elif a.cmd == "blog":
        blog(book)
    elif a.cmd == "hubspot":
        hubspot(book, a.live)
    elif a.cmd == "publish":
        publish(book, a.live, today)
    elif a.cmd == "reschedule":
        if not a.text:
            sys.exit("Usage: python growth.py reschedule YYYY-MM-DD")
        reschedule(dt.date.fromisoformat(a.text))
    elif a.cmd == "add-platform":
        add_platform(book, a.text)
    elif a.cmd == "drop-platform":
        drop_platform(a.text)
    elif a.cmd == "status":
        status()
    elif a.cmd == "daily":
        if not (OUT / "queue.json").exists():
            plan30(book)
            images(book)
        publish(book, a.live, today)
        hubspot(book, a.live)


if __name__ == "__main__":
    main()
