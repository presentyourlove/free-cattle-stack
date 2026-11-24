#!/usr/bin/env python3
"""Generate the army status page at docs/index.html.

Probes each service, renders a static HTML page with live results and a
timestamp. Designed to run in GitHub Actions (see status-page.yml) using
the repo secrets as env vars, but also runs locally: probes whose
credentials are missing are reported as "not configured" instead of
failing. Always exits 0 - this page reports status; alerting is the job
of keepalive / r2-usage.
"""
import html
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

WORKER_HEALTH = "https://free-cattle-gateway.jasonwth0928.workers.dev/health"
NORTHFLANK_HEALTH = "https://p01--free-cattle-stack--54fcgkkc2jbh.code.run/health"
SUPABASE_TASKS = "https://nvraifaypheugzstdetg.supabase.co/rest/v1/tasks?select=id&limit=1"
CF_GRAPHQL = "https://api.cloudflare.com/client/v4/graphql"
R2_BUCKET = "free-cattle-files"
R2_FREE_GB = 10.0


def http_get(url, headers=None, timeout=20):
    # Cloudflare's edge returns 403 (error 1010, "bad browser") to
    # urllib's default "Python-urllib/3.x" User-Agent, so the Workers
    # probe would always read as down. Send a plain browser-like UA.
    merged = {"User-Agent": "free-cattle-status/1.0 (+https://presentyourlove.github.io/free-cattle-stack/)"}
    merged.update(headers or {})
    req = urllib.request.Request(url, headers=merged)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", "replace")


def probe_health(url):
    # One retry on transient network errors so a blip does not paint the
    # page red for 6 hours.
    last = None
    for _ in range(2):
        try:
            status, _ = http_get(url)
            return ("ok", f"HTTP {status}") if status == 200 else ("down", f"HTTP {status}")
        except urllib.error.HTTPError as e:
            return ("down", f"HTTP {e.code}")
        except Exception as e:  # noqa: BLE001 - status page must never crash
            last = e
    return ("down", f"{type(last).__name__}")


def probe_supabase():
    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    if not key:
        return ("unknown", "未設定金鑰")
    try:
        status, _ = http_get(SUPABASE_TASKS, headers={"apikey": key})
        return ("ok", f"HTTP {status}") if status == 200 else ("down", f"HTTP {status}")
    except urllib.error.HTTPError as e:
        return ("down", f"HTTP {e.code}")
    except Exception as e:  # noqa: BLE001
        return ("down", f"{type(e).__name__}")


def probe_r2():
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    account = os.environ.get("CF_ACCOUNT_ID", "").strip()
    if not token or not account:
        return ("unknown", "未設定金鑰")
    now = datetime.now(timezone.utc)
    query = """
    query($account: String!, $start: Time!, $end: Time!) {
      viewer { accounts(filter: {accountTag: $account}) {
        r2StorageAdaptiveGroups(limit: 100, filter: {datetime_geq: $start, datetime_lt: $end}) {
          max { payloadSize metadataSize } dimensions { bucketName datetime }
        }
      } }
    }"""
    variables = {
        "account": account,
        "start": now.strftime("%Y-%m-%dT00:00:00Z"),
        "end": now.strftime("%Y-%m-%dT23:59:59Z"),
    }
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(
        CF_GRAPHQL, data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001
        return ("down", f"{type(e).__name__}")
    if payload.get("errors"):
        return ("down", "GraphQL 錯誤")
    groups = (
        payload.get("data", {}).get("viewer", {}).get("accounts", [{}])[0]
        .get("r2StorageAdaptiveGroups", [])
    )
    latest = {}
    for g in groups:
        dims = g.get("dimensions", {})
        if dims.get("bucketName") != R2_BUCKET:
            continue
        day = dims.get("datetime", "")[:10]
        size = (g.get("max", {}).get("payloadSize") or 0) + (g.get("max", {}).get("metadataSize") or 0)
        if day not in latest or size > latest[day]:
            latest[day] = size
    if not latest:
        return ("ok", "0 GB / 10 GB")
    gb = max(latest.values()) / 1e9
    pct = gb / R2_FREE_GB * 100
    level = "ok" if pct < 80 else "down"
    return (level, f"{gb:.3f} GB / 10 GB（{pct:.1f}%）")


ROWS_STATIC = [
    ("Kaggle", "雙 T4 GPU 主力", "ready", "已驗收 · 按需使用"),
    ("Google Colab", "T4 GPU 備用", "ready", "已驗收 · 按需使用"),
    ("Simular Box", "AI 雲端電腦", "ready", "已驗收 · keepalive 每 3 日喚醒"),
    ("GitHub Actions", "自動任務 / 保活", "ready", "keepalive 每 3 日 · R2 檢查每日"),
]

STYLE = (
    "body{font-family:system-ui,sans-serif;max-width:720px;margin:40px auto;"
    "padding:0 16px;line-height:1.6}li{margin:6px 0}"
    ".ok{color:#0a7d2c}.down{color:#c0392b}.unknown{color:#b26a00}.ready{color:#0a7d2c}"
    "small{color:#666}"
)
ICON = {"ok": "✅", "down": "❌", "unknown": "❔", "ready": "✅"}


def main():
    checks = [
        ("Cloudflare Workers", "API 入口", *probe_health(WORKER_HEALTH)),
        ("Northflank", "常駐服務", *probe_health(NORTHFLANK_HEALTH)),
        ("Supabase", "資料庫", *probe_supabase()),
        ("Cloudflare R2", "檔案倉", *probe_r2()),
    ]
    rows = [(name, role, status, detail) for name, role, status, detail in checks]
    rows += ROWS_STATIC
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    items = "\n".join(
        f'  <li class="{s}">{ICON[s]} {html.escape(n)} — {html.escape(r)}'
        f" <small>（{html.escape(d)}）</small></li>"
        for n, r, s, d in rows
    )
    page = f"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>免費牛馬軍團 — 狀態頁</title>
<style>{STYLE}</style>
</head><body>
<h1>免費牛馬軍團 — 狀態頁</h1>
<ul>
{items}
</ul>
<p><small>最後檢查：{now}（由 GitHub Actions 每 6 小時自動更新）</small></p>
</body></html>
"""
    out_dir = os.path.join(os.getcwd(), "docs")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(page)
    summary = ", ".join(f"{n}={s}" for n, _, s, _ in rows)
    print(f"wrote {out_path}: {summary}")


if __name__ == "__main__":
    main()
