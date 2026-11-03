"""Collects BFSI stories from RSS feeds (no keys needed) and writes docs/data.json.
Trending = a story that several different sources cover, plus official and community signals."""
import os, re, json, datetime as dt, urllib.parse
from pathlib import Path
import yaml, feedparser

CFG = yaml.safe_load(open(os.environ.get("CONFIG", "config.yaml"), encoding="utf-8"))
NOW = dt.datetime.now(dt.timezone.utc)
AGENT = "Mozilla/5.0 (compatible; BFSIRadar/1.0)"
STOP = set("the a an of to in for on and or is are as at by with from how what why new this that it its be".split())
status = []


def read_feed(name, url, official=False, reddit_pos_bonus=False, limit=40):
    try:
        d = feedparser.parse(url, agent=AGENT)
    except Exception as e:
        status.append(dict(name=name, ok=False, note=str(e)[:80])); return []
    entries = d.entries[:limit]
    if not entries:
        status.append(dict(name=name, ok=False, note="no entries (blocked or wrong link)")); return []
    status.append(dict(name=name, ok=True, note=f"{len(entries)} items"))
    rows = []
    for idx, e in enumerate(entries):
        ts = e.get("published_parsed") or e.get("updated_parsed")
        created = dt.datetime(*ts[:6], tzinfo=dt.timezone.utc) if ts else NOW
        title = re.sub(r"\s+-\s+[^-]{2,40}$", "", e.get("title", "")).strip()   # drop " - Publisher"
        src = name
        if "news.google" in url and e.get("source"):
            src = e["source"].get("title", name)
        rows.append(dict(title=title, url=e.get("link", ""), source=src, created=created,
                         official=official, text=re.sub("<[^>]+>", " ", e.get("summary", ""))[:500],
                         community=(10 * (limit - idx) / limit) if reddit_pos_bonus else 0))
    return rows


def tokens(t):
    return {w for w in re.findall(r"[a-z0-9]+", t.lower()) if w not in STOP and len(w) > 1}


items = []
for f in CFG.get("feeds", []):
    items += read_feed(f["name"], f["url"], official=f.get("official", False))
for q in CFG.get("google_news_searches", []):
    url = "https://news.google.com/rss/search?q=" + urllib.parse.quote_plus(f"{q} when:3d") + "&hl=en-IN&gl=IN&ceid=IN:en"
    items += read_feed(f"Google News: {q}", url, limit=15)
if CFG.get("reddit_rss", {}).get("enabled"):
    for sub in CFG["reddit_rss"]["subreddits"]:
        items += read_feed(f"r/{sub}", f"https://www.reddit.com/r/{sub}/hot/.rss", reddit_pos_bonus=True, limit=25)

# keep recent items only
cutoff = NOW - dt.timedelta(days=CFG["max_age_days"])
items = [i for i in items if i["created"] >= cutoff and i["title"]]

# group near-identical headlines into one story
stories = []
for it in sorted(items, key=lambda x: x["created"], reverse=True):
    tk = tokens(it["title"])
    for s in stories:
        if tk and s["tk"] and len(tk & s["tk"]) / len(tk | s["tk"]) >= 0.45:
            s["items"].append(it); break
    else:
        stories.append(dict(tk=tk, items=[it]))

out_items = []
for s in stories:
    lead = max(s["items"], key=lambda x: (x["official"], x["created"]))
    blob = " ".join(f"{i['title']} {i['text']}" for i in s["items"]).lower()
    hits = {c: sum(k in blob for k in kws) for c, kws in CFG["categories"].items()}
    hits = {c: n for c, n in hits.items() if n}
    if not hits:
        continue
    sources = sorted({i["source"] for i in s["items"]})
    official = any(i["official"] for i in s["items"])
    community = max(i["community"] for i in s["items"])
    priority = sum(w in blob for w in CFG["priority_words"])
    age_h = max((NOW - lead["created"]).total_seconds() / 3600, 0)
    score = (10 * len(sources) + (4 if official else 0) + community + 3 * sum(hits.values()) + 5 * priority) / (1 + age_h / 48)
    out_items.append(dict(title=lead["title"], url=lead["url"], sources=sources[:4], source_count=len(sources),
                          official=official, category=max(hits, key=hits.get), priority=priority,
                          age_hours=round(age_h), score=round(score, 1)))

out_items.sort(key=lambda x: -x["score"])
top = out_items[:CFG["top_count"]]
used = {i["url"] for i in top}
industry = {}
for cat in CFG["categories"]:
    rows = [i for i in out_items if i["category"] == cat and i["url"] not in used][:CFG["per_category"]]
    industry[cat] = rows
    used |= {i["url"] for i in rows}
secondary = sorted([i for i in out_items if i["url"] not in used and i["priority"] > 0],
                   key=lambda x: (-x["priority"], -x["score"]))[:CFG["secondary_count"]]

ist = NOW + dt.timedelta(hours=5, minutes=30)
Path("docs").mkdir(exist_ok=True)
Path("docs/data.json").write_text(json.dumps(dict(
    updated=ist.strftime("%d %b %Y, %I:%M %p IST"), top=top, industry=industry,
    secondary=secondary, feed_status=status), indent=1, ensure_ascii=False), encoding="utf-8")
print(f"Done: {len(out_items)} BFSI stories; {sum(s['ok'] for s in status)}/{len(status)} feeds working")
