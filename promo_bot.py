#!/usr/bin/env python3
"""
KDP Promo Bot — plans, writes, compliance-checks and (optionally) posts the
promotion campaign for a book published on Kindle Direct Publishing.

Built around the levers on KDP's "Promote Your Book" help page:
  Author Central, Amazon Ads, Free Promotions, Kindle Countdown Deals,
  eBook Pre-order, Buying & Gifting eBooks, Lending for Kindle,
  Kindle Instant Previews, Sample Chapters, KDP Select / Kindle Unlimited,
  A+ Content, Expanded Distribution, "Nominate Your eBook" (beta),
  self-promotion (email/website) and vetted third-party services.

Zero required dependencies (Python 3.9+). AI copy is optional; otherwise
built-in templates are used. Set OPENAI_API_KEY (ChatGPT) or ANTHROPIC_API_KEY
(Claude); see llm.py. Daily multi-platform automation lives in growth.py. Every piece of copy passes a compliance guard
before it reaches the outbox.

Commands:
  python promo_bot.py all                 # plan + content + ads + checklist
  python promo_bot.py plan                # campaign calendar (csv, ics, md)
  python promo_bot.py content             # posts/emails -> out/outbox.json
  python promo_bot.py ads                 # Amazon Ads keyword + campaign sheet
  python promo_bot.py checklist           # one-time KDP / Author Central setup
  python promo_bot.py check "some text"   # run the compliance guard on text
  python promo_bot.py post [--live]       # send due outbox items (dry-run default)
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
from dataclasses import dataclass, asdict, field
from pathlib import Path

# Windows defaults to a legacy text encoding; posts contain emoji and dashes, so
# force UTF-8 for every file this bot reads/writes and for console/log output.
import pathlib as _pl

_rt, _wt = _pl.Path.read_text, _pl.Path.write_text
_pl.Path.read_text = lambda self, encoding="utf-8", errors=None: _rt(self, encoding=encoding, errors=errors)
_pl.Path.write_text = lambda self, data, encoding="utf-8", errors=None, newline=None: \
    _wt(self, data, encoding=encoding, errors=errors, newline=newline)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"

# ---------------------------------------------------------------------------
# KDP program rules used by the planner. The help page names the programs but
# does not list their limits; these are KDP's published rules as last known —
# confirm each one in your KDP dashboard before scheduling.
# ---------------------------------------------------------------------------
RULES = {
    "select_period_days": 90,          # KDP Select enrollment term
    "free_days_max": 5,                # free-promo days per 90-day term
    "countdown_days_max": 7,           # Countdown Deal length
    "price_lock_before_countdown": 30, # list price unchanged N days before
    "countdown_min_list_price": 2.99,
    "countdown_marketplaces": ["Amazon.com", "Amazon.co.uk"],
    "preorder_max_days": 365,          # page: up to one year before release
}


# ---------------------------------------------------------------------------
# Compliance guard — the help page warns that tactics which manipulate Kindle
# programs can terminate your account and forfeit royalties, and to avoid any
# service that "guarantees" ROI.  The guard blocks or flags that kind of copy.
# ---------------------------------------------------------------------------
BLOCK = [
    (r"\b(5|five)[- ]?star\b.*\b(review|rating)s?\b.*\b(free|gift|exchange|reward|pay)",
     "Offering something in exchange for a positive review"),
    (r"\b(free|gift|reward|pay|paid|\$)\b.*\bfor (a |an |your )?(5|five)[- ]?star",
     "Offering something in exchange for a positive review"),
    (r"\breview (swap|exchange|trade)s?\b", "Review swaps"),
    (r"\b(buy|purchase|download)\b.*\band (then )?return\b", "Buy-and-return schemes"),
    (r"\b(borrow|open|flip|page) (through|to the end)\b.*\b(KU|kindle unlimited)",
     "Asking readers to page through KU books to inflate page reads"),
    (r"\bguarantee[sd]? (sales|rank|ranking|ROI|bestseller|#1)", "Guaranteed results claims"),
    (r"\b(click|bot|review) farm", "Click/review farms"),
    (r"\bupvote my review|downvote\b", "Review vote manipulation"),
    (r"\bmultiple accounts\b", "Using multiple accounts"),
]
FLAG = [
    (r"\b(ARC|advance (reader|review) cop(y|ies))\b",
     "ARCs: ask for an HONEST review only; never require or reward a rating."),
    (r"\bbestsell(er|ing)\b",
     "Only claim 'bestseller' if true and you can say which list/category/date."),
    (r"\bfree\b", "If 'free', make sure a Free Promotion is actually live that day."),
    (r"\b(99¢|\$0\.99|countdown)\b", "Confirm the Countdown Deal is scheduled for that date."),
]


def compliance(text: str) -> tuple[list[str], list[str]]:
    blocked = sorted({why for pat, why in BLOCK if re.search(pat, text, re.I)})
    flags = sorted({why for pat, why in FLAG if re.search(pat, text, re.I)})
    return blocked, flags


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
@dataclass
class Event:
    date: str
    phase: str
    lever: str            # the KDP program / channel this uses
    action: str
    channels: list[str] = field(default_factory=list)
    kind: str = "task"    # task | post | email


def load_book(path: Path) -> dict:
    book = json.loads(path.read_text())
    book.setdefault("hashtags", [])
    return book


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def store_link(book: dict) -> str:
    asin = book.get("asin_ebook") or book.get("asin_paperback")
    return f"https://www.amazon.com/dp/{asin}" if asin else "[AMAZON LINK]"


# ---------------------------------------------------------------------------
# 1) Campaign planner
# ---------------------------------------------------------------------------
def build_plan(book: dict) -> tuple[list[Event], list[str]]:
    warn: list[str] = []
    rel = d(book["release_date"])
    ev: list[Event] = []
    add = lambda day, *a, **k: ev.append(Event(day.isoformat(), *a, **k))
    social = ["social"]

    # --- Pre-launch -------------------------------------------------------
    pre = min(int(book.get("preorder_days", 30)), RULES["preorder_max_days"])
    start = rel - dt.timedelta(days=pre if book.get("use_preorder") else 21)
    add(start, "Pre-launch", "Author Central",
        "Claim book in Author Central; add bio, photo, editorial reviews.")
    add(start, "Pre-launch", "A+ Content",
        "Submit A+ Content modules (comp-title banner, character/setting images).")
    if book.get("use_preorder") and "ebook" in book.get("formats", []):
        add(start, "Pre-launch", "Kindle eBook Pre-order",
            "Pre-order goes live — announce cover + pre-order link.", social, "post")
        add(start, "Pre-launch", "Self-promotion (email)",
            "Newsletter: cover reveal + pre-order link.", ["email"], "email")
    add(start + dt.timedelta(days=3), "Pre-launch", "Sample Chapters",
        "Share chapter 1 excerpt on site/newsletter; link to pre-order.", social, "post")
    add(start + dt.timedelta(days=7), "Pre-launch", "Kindle Instant Preview",
        "Post the Instant Preview link so people can read the opening free.", social, "post")
    add(rel - dt.timedelta(days=14), "Pre-launch", "Self-promotion",
        "Recruit honest ARC readers (no rating requirement).", social, "post")
    add(rel - dt.timedelta(days=7), "Pre-launch", "Nominate Your eBook (beta)",
        "Check KDP for 'Nominate your eBook for a promotion' eligibility; submit.")
    add(rel - dt.timedelta(days=3), "Pre-launch", "Self-promotion",
        "Countdown teaser: 3 days to launch — hook line + quote.", social, "post")

    # --- Launch -----------------------------------------------------------
    add(rel, "Launch", "Self-promotion (email)",
        "Launch-day newsletter with buy + KU link.", ["email"], "email")
    add(rel, "Launch", "Self-promotion",
        "Launch-day announcement.", social, "post")
    add(rel, "Launch", "Amazon Advertising",
        f"Start Sponsored Products: Auto + Manual keyword campaigns "
        f"(${book.get('ads_daily_budget_usd', 10)}/day total).")
    add(rel + dt.timedelta(days=2), "Launch", "Buying & Gifting eBooks",
        "Social giveaway: gift 5 eBooks to winners (Gift this eBook).", social, "post")
    add(rel + dt.timedelta(days=4), "Launch", "Lending for Kindle",
        "Remind buyers they can lend the eBook to a friend.", social, "post")
    add(rel + dt.timedelta(days=7), "Launch", "Amazon Advertising",
        "Week-1 ads review: harvest converting search terms, negate wasters.")

    # --- KDP Select promo (one per 90-day term: Free OR Countdown) --------
    if book.get("kdp_select"):
        add(rel, "Launch", "KDP Select / Kindle Unlimited",
            "Enrolled in KDP Select — promote 'Read free with Kindle Unlimited'.", social, "post")
        choice = book.get("select_promo", "free")
        if choice == "free":
            n = int(book.get("free_days", 3))
            if n > RULES["free_days_max"]:
                warn.append(f"free_days {n} > {RULES['free_days_max']} allowed per term; capped.")
                n = RULES["free_days_max"]
            fs = rel + dt.timedelta(days=21)
            add(fs - dt.timedelta(days=2), "Growth", "Free Promotions",
                "Schedule free days in KDP Bookshelf; book promo sites (vetted).")
            for i in range(n):
                add(fs + dt.timedelta(days=i), "Growth", "Free Promotions",
                    f"FREE day {i+1}/{n} — post + email.", social + ["email"], "post")
            add(fs + dt.timedelta(days=n), "Growth", "Amazon Advertising",
                "Free run over — raise ad bids to ride also-boughts.")
        elif choice == "countdown":
            lp = float(book.get("list_price_usd", 0))
            if lp < RULES["countdown_min_list_price"]:
                warn.append(f"List price ${lp} below Countdown minimum "
                            f"${RULES['countdown_min_list_price']}.")
            cs = rel + dt.timedelta(days=max(30, RULES["price_lock_before_countdown"]))
            n = min(int(book.get("countdown_days", 7)), RULES["countdown_days_max"])
            add(cs - dt.timedelta(days=3), "Growth", "Kindle Countdown Deals",
                f"Schedule Countdown Deal (${book.get('countdown_price_usd', 0.99)}) "
                f"— runs only on {', '.join(RULES['countdown_marketplaces'])}.")
            add(cs, "Growth", "Kindle Countdown Deals",
                "Countdown Deal starts — post + email.", social + ["email"], "post")
            add(cs + dt.timedelta(days=n - 1), "Growth", "Kindle Countdown Deals",
                "Last day of the deal.", social, "post")
        add(rel + dt.timedelta(days=RULES["select_period_days"] - 7), "Growth",
            "KDP Select / Kindle Unlimited",
            "Select term ends in 7 days: decide re-enroll (auto-renew) vs wide.")

    # --- Ongoing growth ---------------------------------------------------
    for wk in range(2, 9):
        add(rel + dt.timedelta(weeks=wk), "Growth", "Self-promotion",
            "Weekly content post (theme / excerpt / behind-the-scenes).", social, "post")
    add(rel + dt.timedelta(days=30), "Growth", "Amazon Advertising",
        "Month-1 ads review: add category/product-targeting campaign on comps.")
    add(rel + dt.timedelta(days=30), "Growth", "Self-promotion (email)",
        "Newsletter: reader reactions + ask for honest reviews.", ["email"], "email")
    if "paperback" in book.get("formats", []):
        add(rel + dt.timedelta(days=14), "Growth", "Expanded Distribution",
            "Enable Expanded Distribution for paperback (libraries/bookstores).")
    add(rel + dt.timedelta(days=45), "Growth", "Merchandising (ACX / contests)",
        "Evaluate audiobook via ACX and Amazon literary contests.")

    ev.sort(key=lambda e: (e.date, e.phase))
    return ev, warn


def write_plan(book: dict, ev: list[Event], warn: list[str]) -> None:
    OUT.mkdir(exist_ok=True)
    with open(OUT / "calendar.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "phase", "kdp_lever", "action", "channels", "kind"])
        for e in ev:
            w.writerow([e.date, e.phase, e.lever, e.action, ";".join(e.channels), e.kind])

    ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//kdp-promo-bot//EN"]
    stamp = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).strftime("%Y%m%dT%H%M%SZ")
    for i, e in enumerate(ev):
        day = e.date.replace("-", "")
        nxt = (d(e.date) + dt.timedelta(days=1)).isoformat().replace("-", "")
        ics += ["BEGIN:VEVENT", f"UID:kdp-{i}-{day}@promo-bot", f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{day}", f"DTEND;VALUE=DATE:{nxt}",
                f"SUMMARY:[{e.lever}] {e.action[:60]}",
                "DESCRIPTION:" + e.action.replace(",", "\\,"), "END:VEVENT"]
    ics.append("END:VCALENDAR")
    (OUT / "calendar.ics").write_text("\r\n".join(ics))

    md = [f"# Promo plan — {book['title']} ({book['author']})",
          f"Release: **{book['release_date']}**\n"]
    if warn:
        md += ["## Warnings"] + [f"- {w}" for w in warn] + [""]
    cur = None
    for e in ev:
        if e.phase != cur:
            cur = e.phase
            md.append(f"\n## {cur}\n\n| Date | KDP lever | Action |\n|---|---|---|")
        md.append(f"| {e.date} | {e.lever} | {e.action} |")
    (OUT / "plan.md").write_text("\n".join(md) + "\n")
    print(f"plan: {len(ev)} events -> out/plan.md, calendar.csv, calendar.ics")
    for w in warn:
        print("  WARNING:", w)


# ---------------------------------------------------------------------------
# 2) Content writer
# ---------------------------------------------------------------------------
TEMPLATES = {
    "Kindle eBook Pre-order": "Cover reveal! {title} by {author} is up for pre-order. {hook} "
                              "Reserve your copy: {link} {tags}",
    "Sample Chapters": "Chapter one of {title} is here. {hook} Read the opening, then "
                       "pre-order: {link} {tags}",
    "Kindle Instant Preview": "Read the first pages of {title} free — no download needed: "
                              "{preview} {tags}",
    "Free Promotions": "{title} is FREE on Kindle today only. {hook} Grab it: {link} {tags}",
    "Kindle Countdown Deals": "Deal: {title} is {deal} for a limited time — the price goes up "
                              "as the clock runs down. {link} {tags}",
    "Buying & Gifting eBooks": "Giveaway! I'm gifting 5 Kindle copies of {title}. Reply with "
                               "your favorite {genre} read to enter. Winners picked Friday. {tags}",
    "Lending for Kindle": "Loved {title}? Kindle lets you lend your copy to a friend — pass "
                          "it on. {link}",
    "KDP Select / Kindle Unlimited": "{title} is in Kindle Unlimited — read it free with your "
                                     "subscription. {link} {tags}",
    "Self-promotion": "{hook} {title} by {author}. {link} {tags}",
}
EMAIL = ("Subject: {{subject}}\n\nHi friend,\n\n{{body}}\n\n{blurb}\n\nGet it here: {link}\n\n"
         "If you read it, an honest review on Amazon helps more than you'd think.\n\n— {author}")


def fill(t: str, book: dict) -> str:
    return t.format(title=book["title"], author=book["author"], hook=book["hook"],
                    genre=book.get("genre", "book"), blurb=book.get("blurb", ""),
                    link=store_link(book),
                    preview=book.get("instant_preview_url") or "[INSTANT PREVIEW LINK]",
                    deal=f"${book.get('countdown_price_usd', 0.99)}",
                    tags=" ".join(book.get("hashtags", [])[:4])).strip()


def claude_write(book: dict, e: Event) -> str | None:
    """AI copy via ChatGPT or Claude (see llm.py); None -> templates."""
    import llm
    kind = "short social post (under 280 characters)" if e.kind == "post" \
        else "short newsletter email with a Subject: line"
    return llm.write(f"Write a {kind} promoting a book.\nBook: {json.dumps(book)}\n"
                     f"Purpose ({e.lever}): {e.action}\nLink: {store_link(book)}")


def build_content(book: dict, ev: list[Event]) -> list[dict]:
    outbox = []
    for e in ev:
        if e.kind not in ("post", "email"):
            continue
        text = claude_write(book, e)
        source = "ai" if text else "template"
        if not text:
            if e.kind == "email":
                text = fill(EMAIL, book).replace("{subject}", f"{book['title']}: {e.action[:50]}") \
                    .replace("{body}", e.action)
            else:
                text = fill(TEMPLATES.get(e.lever, TEMPLATES["Self-promotion"]), book)
        blocked, flags = compliance(text)
        outbox.append({"date": e.date, "lever": e.lever, "kind": e.kind,
                       "channels": e.channels, "text": text, "source": source,
                       "status": "blocked" if blocked else "ready",
                       "blocked_reasons": blocked, "review_flags": flags})
    OUT.mkdir(exist_ok=True)
    (OUT / "outbox.json").write_text(json.dumps(outbox, indent=2, ensure_ascii=False))
    ready = sum(o["status"] == "ready" for o in outbox)
    print(f"content: {len(outbox)} items ({ready} ready, {len(outbox)-ready} blocked) "
          f"-> out/outbox.json")
    return outbox


# ---------------------------------------------------------------------------
# 3) Amazon Ads builder
# ---------------------------------------------------------------------------
def build_ads(book: dict) -> None:
    g = book.get("genre", "").lower()
    seeds = {g, f"{g} books", f"{g} kindle", f"{g} kindle unlimited", f"new {g} 2026",
             f"best {g} novels"}
    seeds |= {f"{t} {g}" for t in book.get("themes", [])}
    seeds |= {f"books like {c.lower()}" for c in book.get("comparable_titles", [])}
    seeds |= {c.lower() for c in book.get("comparable_titles", [])}
    rows = []
    budget = float(book.get("ads_daily_budget_usd", 10))
    for kw in sorted(s for s in seeds if s.strip()):
        rows.append(["SP-Manual-Keywords", kw, "broad", 0.45])
        rows.append(["SP-Manual-Keywords", kw, "exact", 0.60])
    rows.insert(0, ["SP-Auto", "(automatic targeting)", "auto", 0.40])
    for c in book.get("comparable_titles", []):
        rows.append(["SP-Product-Targeting", f"ASIN of '{c}' (look up)", "product", 0.50])
    OUT.mkdir(exist_ok=True)
    with open(OUT / "ads_keywords.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["campaign", "target", "match_type", "starting_bid_usd"])
        w.writerows(rows)
    split = {"SP-Auto": 0.4, "SP-Manual-Keywords": 0.4, "SP-Product-Targeting": 0.2}
    (OUT / "ads_plan.md").write_text(
        "# Amazon Ads starter structure\n\n" +
        "\n".join(f"- **{k}**: ${budget*v:.2f}/day" for k, v in split.items()) +
        "\n\nWeekly: move converting search terms from Auto into Manual exact; add "
        "non-converting terms (high clicks, no sales) as negatives. Target ACoS below "
        "your royalty rate (≈70% at $2.99–$9.99 eBook pricing; less for paperback).\n")
    print(f"ads: {len(rows)} targets -> out/ads_keywords.csv, out/ads_plan.md")


# ---------------------------------------------------------------------------
# 4) One-time setup checklist
# ---------------------------------------------------------------------------
CHECKLIST = [
    ("Author Central", "Claim the book, add bio, photo, editorial reviews, link series."),
    ("A+ Content", "Publish image modules: comp banner, character/setting, quote cards."),
    ("Kindle Instant Preview", "Copy the 'Share' preview link into book.json → instant_preview_url."),
    ("Sample Chapters", "Make sure 'Look Inside' opens on a strong first page (no long front matter)."),
    ("KDP Select", "Decide Select (exclusive eBook, KU page reads, Free/Countdown promos) vs wide."),
    ("Kindle eBook Pre-order", "Set pre-order (up to 1 year ahead); upload final file before the deadline."),
    ("Lending for Kindle", "Confirm lending is enabled (default for 35% royalty; check for 70%)."),
    ("Buying & Gifting", "Use 'Give as a gift' on the Kindle page for giveaways / newsletter rewards."),
    ("Amazon Advertising", "Link KDP to the Ads console; set payment; launch campaigns at release."),
    ("Expanded Distribution", "Paperback: enable for bookstores/libraries if pricing allows."),
    ("Nominate Your eBook (beta)", "Check Marketing tab for nomination eligibility."),
    ("Marketplaces", "Some programs aren't in every marketplace — check each before promising readers."),
    ("Third-party services", "Vet promo sites; skip anyone 'guaranteeing' ROI or rank; you own their tactics."),
    ("Self-promotion", "Website with buy links + newsletter signup; add both to book.json."),
]


def write_checklist(book: dict) -> None:
    missing = [k for k in ("asin_ebook", "instant_preview_url", "author_website",
                           "newsletter_signup_url") if not book.get(k)]
    lines = [f"# KDP setup checklist — {book['title']}\n"]
    lines += [f"- [ ] **{k}** — {v}" for k, v in CHECKLIST]
    if missing:
        lines += ["\n## Fill in book.json", *[f"- `{m}` is empty" for m in missing]]
    OUT.mkdir(exist_ok=True)
    (OUT / "checklist.md").write_text("\n".join(lines) + "\n")
    print("checklist -> out/checklist.md" + (f" ({len(missing)} book.json gaps)" if missing else ""))


# ---------------------------------------------------------------------------
# 5) Poster (dry-run unless --live)
# ---------------------------------------------------------------------------
def _post_json(url: str, payload: dict, headers: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = r.read()
        return json.loads(body) if body else {}


def send(book: dict, item: dict) -> list[str]:
    cfg, sent = book.get("social", {}), []
    text = item["text"]
    m = cfg.get("mastodon", {})
    if m.get("enabled") and os.environ.get(m.get("token_env", "")):
        _post_json(f"{m['instance'].rstrip('/')}/api/v1/statuses", {"status": text[:500]},
                   {"Authorization": f"Bearer {os.environ[m['token_env']]}"})
        sent.append("mastodon")
    b = cfg.get("bluesky", {})
    if b.get("enabled") and os.environ.get(b.get("app_password_env", "")):
        s = _post_json("https://bsky.social/xrpc/com.atproto.server.createSession",
                       {"identifier": b["handle"], "password": os.environ[b["app_password_env"]]}, {})
        _post_json("https://bsky.social/xrpc/com.atproto.repo.createRecord",
                   {"repo": s["did"], "collection": "app.bsky.feed.post",
                    "record": {"$type": "app.bsky.feed.post", "text": text[:300],
                               "createdAt": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"}},
                   {"Authorization": f"Bearer {s['accessJwt']}"})
        sent.append("bluesky")
    h = cfg.get("webhook", {})   # e.g. Zapier/Make → X, Facebook, Instagram, email tool
    if h.get("enabled") and os.environ.get(h.get("url_env", "")):
        _post_json(os.environ[h["url_env"]], item, {})
        sent.append("webhook")
    return sent


def run_post(book: dict, live: bool, today: dt.date) -> None:
    p = OUT / "outbox.json"
    if not p.exists():
        sys.exit("No outbox — run `content` first.")
    box = json.loads(p.read_text())
    due = [o for o in box if o["status"] == "ready" and d(o["date"]) <= today]
    if not due:
        print("Nothing due.")
    for o in due:
        if not live:
            print(f"[dry-run] {o['date']} {o['lever']}: {o['text'][:90]}...")
            continue
        try:
            ch = send(book, o)
            o["status"] = "sent" if ch else "ready"
            o["sent_to"] = ch
            print(f"sent {o['date']} -> {ch or 'no channel enabled'}")
        except Exception as ex:  # keep going on one failure
            o["status"] = "error"
            o["error"] = str(ex)
            print(f"error {o['date']}: {ex}")
    if live:
        p.write_text(json.dumps(box, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("cmd", choices=["all", "plan", "content", "ads", "checklist", "check", "post"])
    ap.add_argument("text", nargs="?", help="text for `check`")
    ap.add_argument("--book", default=str(HERE / "book.json"))
    ap.add_argument("--live", action="store_true", help="actually post (post command)")
    ap.add_argument("--today", help="override today's date YYYY-MM-DD (post command)")
    a = ap.parse_args()

    if a.cmd == "check":
        blocked, flags = compliance(a.text or sys.stdin.read())
        print("BLOCKED:" if blocked else "OK", *blocked, sep="\n  ")
        for f in flags:
            print("  review:", f)
        sys.exit(1 if blocked else 0)

    book = load_book(Path(a.book))
    if a.cmd in ("plan", "content", "all"):
        ev, warn = build_plan(book)
        if a.cmd in ("plan", "all"):
            write_plan(book, ev, warn)
        if a.cmd in ("content", "all"):
            build_content(book, ev)
    if a.cmd in ("ads", "all"):
        build_ads(book)
    if a.cmd in ("checklist", "all"):
        write_checklist(book)
    if a.cmd == "post":
        run_post(book, a.live, d(a.today) if a.today else dt.date.today())


if __name__ == "__main__":
    main()
