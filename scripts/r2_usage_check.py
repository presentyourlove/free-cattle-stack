#!/usr/bin/env python3
"""Daily R2 usage check for bucket free-cattle-files (safeguard 6).

Reads storage usage from the Cloudflare GraphQL analytics API and
classifies it against the free tier (10 GB):
  ok     < 80%  (8 GB)
  warn80 >= 80%
  warn90 >= 90%
  warn95 >= 95%
Writes level and storage_gb to $GITHUB_OUTPUT when that variable is
set. Exit code is 0 unless the check itself failed (missing credentials
or an API error), so a broken check is loud instead of silently green.
Operations counts (Class A / B) are best-effort: printed when the API
returns them, never fatal.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone

ACCOUNT = os.environ.get("CF_ACCOUNT_ID", "").strip()
TOKEN = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
BUCKET = "free-cattle-files"
FREE_BYTES = 10 * 1000**3  # Cloudflare bills storage per GB = 10^9 bytes

CLASS_A = {
    "ListBuckets", "ListObjects", "PutObject", "CopyObject",
    "CompleteMultipartUpload", "CreateMultipartUpload", "UploadPart",
    "UploadPartCopy", "ListMultipartUploads", "ListParts",
}
CLASS_B = {"GetObject", "HeadObject", "HeadBucket", "GetBucketLocation"}

QUERY = """
query($accountTag: String!, $start: Date!, $monthStart: Date!, $end: Date!) {
  viewer {
    accounts(filter: {accountTag: $accountTag}) {
      storage: r2StorageAdaptiveGroups(limit: 100, filter: {date_geq: $start, date_leq: $end}) {
        max { payloadSize metadataSize }
        dimensions { date bucketName }
      }
      ops: r2OperationsAdaptiveGroups(limit: 500, filter: {date_geq: $monthStart, date_leq: $end}) {
        sum { requests }
        dimensions { actionType }
      }
    }
  }
}
"""


def graphql(variables):
    body = json.dumps({"query": QUERY, "variables": variables}).encode()
    req = urllib.request.Request("https://api.cloudflare.com/client/v4/graphql", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        out = json.loads(resp.read())
    if out.get("errors"):
        raise RuntimeError(f"GraphQL errors: {out['errors']}")
    return out["data"]["viewer"]["accounts"][0]


def main():
    if not TOKEN or not ACCOUNT:
        print("CLOUDFLARE_API_TOKEN or CF_ACCOUNT_ID not set; cannot check R2 usage.", file=sys.stderr)
        return 1
    today = datetime.now(timezone.utc).date()
    variables = {
        "accountTag": ACCOUNT,
        "start": str(today - timedelta(days=2)),
        "monthStart": str(today.replace(day=1)),
        "end": str(today),
    }
    try:
        acct = graphql(variables)
    except Exception as e:
        print(f"R2 usage check failed: {e}", file=sys.stderr)
        return 1

    # Storage: latest date's max payload+metadata size for our bucket.
    by_date = {}
    for row in acct.get("storage") or []:
        dims = row.get("dimensions") or {}
        if dims.get("bucketName") != BUCKET:
            continue
        m = row.get("max") or {}
        by_date[dims.get("date")] = (m.get("payloadSize") or 0) + (m.get("metadataSize") or 0)
    if not by_date:
        print(f"No storage samples for bucket {BUCKET} yet; treating usage as 0.")
        storage_bytes = 0
    else:
        storage_bytes = by_date[max(by_date)]
    storage_gb = storage_bytes / 1000**3
    pct = storage_bytes / FREE_BYTES * 100
    if pct >= 95:
        level = "warn95"
    elif pct >= 90:
        level = "warn90"
    elif pct >= 80:
        level = "warn80"
    else:
        level = "ok"
    print(f"R2 storage: {storage_gb:.3f} GB of 10 GB free ({pct:.1f}%) -> {level}")

    # Operations this month, best-effort only.
    try:
        a = b = 0
        for row in acct.get("ops") or []:
            action = (row.get("dimensions") or {}).get("actionType")
            reqs = (row.get("sum") or {}).get("requests") or 0
            if action in CLASS_A:
                a += reqs
            elif action in CLASS_B:
                b += reqs
        print(f"R2 ops this month (approx): Class A {a} of 1,000,000 free; Class B {b} of 10,000,000 free")
    except Exception as e:
        print(f"ops breakdown unavailable: {e}")

    out_path = os.environ.get("GITHUB_OUTPUT")
    if out_path:
        with open(out_path, "a") as f:
            f.write(f"level={level}\n")
            f.write(f"storage_gb={storage_gb:.3f}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
