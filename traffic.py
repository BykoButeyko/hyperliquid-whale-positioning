"""Daily visit counts for bykobuteyko.com and the dashboard, from Caddy's access log.

The site sends a beacon to whales.bykobuteyko.com/hit (hit.js in the site repo) with the page and
the referrer. Dashboard visits are page loads of "/". A visitor is a masked IP plus user agent, so
counts are approximate. Bots and my own deploy checks (curl, python) are dropped.

  python traffic.py            # last 24 hours, printed
  python traffic.py --days 7   # last 7 days
  python traffic.py --notify   # also push the summary to ntfy (NTFY_TOPIC)
"""
import argparse, glob, gzip, json, os, time
from collections import Counter
from urllib.parse import parse_qs, urlparse

import requests

LOGS = "/var/log/caddy/whales*.log*"
BOTS = ("bot", "crawl", "spider", "preview", "curl", "python", "go-http", "wget", "headless")


def entries(since):
    for path in glob.glob(LOGS):
        if os.path.getmtime(path) < since:
            continue
        opener = gzip.open if path.endswith(".gz") else open
        with opener(path, "rt") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("ts", 0) >= since:
                    yield e


def source(ref):
    host = urlparse(ref).netloc.removeprefix("www.")
    if not host:
        return "direct"
    if host in ("t.co", "x.com", "twitter.com"):
        return "X"
    return host


def summary(days):
    pages, refs, site_v, dash_v = Counter(), Counter(), set(), set()
    for e in entries(time.time() - days * 86400):
        r = e["request"]
        ua = (r.get("headers", {}).get("User-Agent") or [""])[0]
        if not ua or any(b in ua.lower() for b in BOTS) or r.get("method") != "GET":
            continue
        who = (r.get("client_ip"), ua)
        uri = urlparse(r.get("uri", ""))
        if uri.path == "/hit":
            q = parse_qs(uri.query)
            page, ref = q.get("p", ["?"])[0], q.get("r", [""])[0]
            if "bykobuteyko.com" in urlparse(ref).netloc:
                ref = ""  # moving between my own pages is not a new source
            else:
                refs[source(ref)] += 1
            pages[page] += 1
            site_v.add(who)
        elif uri.path == "/" and e.get("status") == 200:
            dash_v.add(who)

    span = "24 hours" if days == 1 else f"{days:g} days"
    out = [f"Last {span}: site {sum(pages.values())} page views from ~{len(site_v)} visitors, "
           f"dashboard ~{len(dash_v)} visitors."]
    if pages:
        out.append("Pages: " + ", ".join(f"{p} {n}" for p, n in pages.most_common(6)))
    if refs:
        out.append("Came from: " + ", ".join(f"{s} {n}" for s, n in refs.most_common(6)))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=1)
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    text = summary(a.days)
    print(text)
    if a.notify:
        requests.post(f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}", data=text.encode(),
                      headers={"Title": "Site traffic"}, timeout=10)


if __name__ == "__main__":
    main()
