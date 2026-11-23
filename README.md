# KDP Promo Bot

Plans, writes, compliance-checks and posts the promotion campaign for a KDP book.

## Quick start
1. Edit `book.json` (title, hook, blurb, release date, ASIN, Instant Preview link, Select choice).
2. `python promo_bot.py all` → writes `out/`:
   - `plan.md`, `calendar.csv`, `calendar.ics` (import into your calendar)
   - `outbox.json` (every post/email, compliance-checked)
   - `ads_keywords.csv`, `ads_plan.md` (Amazon Ads starter campaigns)
   - `checklist.md` (one-time KDP / Author Central setup)
3. Daily: `python promo_bot.py post` (dry run) → `python promo_bot.py post --live`.
   Run it from cron / Task Scheduler once a day.

## Optional
- `ANTHROPIC_API_KEY` + `pip install anthropic` → Claude writes the copy instead of templates.
- Posting: enable Mastodon, Bluesky, or a webhook (Zapier/Make → X, Facebook, Instagram,
  MailerLite, etc.) in `book.json` and set the matching env vars.
- `python promo_bot.py check "text"` → compliance check any copy you write yourself.

## KDP levers covered
Author Central · A+ Content · Amazon Ads · KDP Select/Kindle Unlimited · Free Promotions ·
Kindle Countdown Deals · eBook Pre-order · Buying & Gifting eBooks · Lending for Kindle ·
Kindle Instant Previews · Sample Chapters · Nominate Your eBook (beta) · Expanded Distribution ·
ACX / literary contests · self-promotion (email, website) · vetted third-party services.

Program limits in `RULES` (free days, Countdown length, price lock, marketplaces) aren't
on KDP's help page — confirm them in your KDP dashboard before scheduling.

---

# Growth engine (`growth.py`): the free daily marketing loop

| Free tool | What the bot does with it |
|---|---|
| **ChatGPT / Claude** | Writes every post, email and blog draft. Set `OPENAI_API_KEY` (ChatGPT) or `ANTHROPIC_API_KEY`; `PROMO_LLM=openai\|claude` to choose. Without a key it uses templates. The API is pay-per-use (a few cents per 30-day plan with a small model), unlike free ChatGPT in the browser. |
| **Canva** | `images` writes `out/canva_bulk.csv` → Canva *Apps → Bulk create* (check your plan has it) to batch-brand graphics/videos. Also renders free local cards in `out/images/` (TikTok/Reels 9:16, Pinterest 2:3, Facebook 1:1). Put `cover.jpg` beside `book.json` to include your cover. |
| **Buffer** | Auto-posts TikTok, Instagram, Facebook, Pinterest, X, Bluesky, Mastodon. Bot sends each due post to `BUFFER_WEBHOOK_URL` — a Zapier or Make "catch hook" → Buffer "Add to queue" step. `out/buffer_queue.csv` is the same queue for manual entry. Buffer's free plan covers a limited number of channels. |
| **HubSpot (free CRM)** | `hubspot --live` pushes opted-in readers from `leads.csv` (newsletter, ARC, giveaway entrants) into contacts (`HUBSPOT_TOKEN` = private-app token, contacts-write scope). `blog` writes 4 drafts for HubSpot's blog or your site. |
| **TikTok Creative Center** | No public API, so: every Monday the plan reminds you to check Trends → hashtags/songs and paste them into `trends.json`; the bot folds them into every post. |
| **Reddit** | Drafted once a week, **posted by you** — subreddits ban automated or link-heavy self-promotion. Lead with value and read each community's rules. |
| **Pinterest** | Daily pin title + description + 2:3 image, routed through Buffer. |

**Repurpose 1 → 10:** `python growth.py repurpose "your idea"` → TikTok script, Reel, Facebook, Pinterest, X, Bluesky, Mastodon, Reddit, email, blog.

**30-day plan:** `python growth.py plan30` → `out/daily/day01.md … day30.md` rotating 10 content pillars (hook, excerpt, character, tropes/comps, behind-the-scenes, aesthetic, question, reader reaction, offer, author story). Reels, Facebook and Pinterest daily (set `growth.daily_platforms` in book.json; add "tiktok" back any time). X, Bluesky and Mastodon are off: list them in `growth.extra_platforms` to turn them back on, plus X/Bluesky/Mastodon, a weekly Reddit draft, a weekly email and a weekly blog post.

**Run it automatically:** push this folder to a GitHub repo, add the secrets, and `.github/workflows/daily-promo.yml` runs `growth.py daily --live` every morning for free. Or use `run_daily.sh` with cron on your own machine.

Skipped on purpose: Google Business Profile (local storefronts), Product Hunt (software), Facebook Marketplace/Etsy (physical goods). They don't fit a Kindle book, though Etsy can work if you later sell signed paperbacks.

## API keys (.env)
Keys live in `.env` (loaded automatically by `llm.py`; already in `.gitignore`).
`ANTHROPIC_API_KEY` is set, with `PROMO_LLM=claude`. For GitHub Actions, don't upload `.env` —
add the same value as a repository secret named `ANTHROPIC_API_KEY`.

## Posting setup (current)
Platforms: Instagram (4:5 image + caption; the Reel script is included for filming by hand), Facebook Page, Pinterest.
- `image_base_url` in book.json = public link to `out/images` (GitHub raw link, see the setup guide).
- One Zapier "Catch Hook → Add to Buffer" Zap per platform; hook URLs go in `.env` as
  BUFFER_WEBHOOK_REELS / BUFFER_WEBHOOK_FACEBOOK / BUFFER_WEBHOOK_PINTEREST (and as GitHub secrets).
- Fields sent to Zapier: caption, title (Pinterest), image_url, link, reel_script.
- `python growth.py reschedule YYYY-MM-DD` moves the plan; `python growth.py status` shows its dates.
- Windows daily run without GitHub: `run_daily.bat` in Task Scheduler.
