#!/usr/bin/env python3
"""Vendor the pinned agency-agents specialist library into AUREN.

The sync is deterministic: the source ref is explicit, paths are restricted to
known division directories, files are UTF-8, and a registry with SHA-256 hashes
is produced. Existing local files are replaced only under the agency/agents
output root.
"""
from __future__ import annotations
import argparse, hashlib, json, re, sys, urllib.request
from pathlib import Path

SOURCE = "msitarzewski/agency-agents"
DEFAULT_REF = "ad9264e309bd5e5422c04784372d7841b1e5d604"
DIVISIONS = ["academic","design","engineering","finance","game-development","gis","healthcare","marketing","paid-media","product","project-management","research","sales","security","spatial-computing","specialized","support","testing"]
REQUIRED = ["Identity & Memory", "Core Mission", "Critical Rules", "Technical Deliverables", "Workflow Process", "Communication Style", "Success Metrics"]


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept":"application/vnd.github+json", "User-Agent":"AUREN-agency-sync/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def frontmatter(text: str):
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        return {}, text
    data = {}
    for line in text[4:end].splitlines():
        if ":" not in line: continue
        key, value = line.split(":", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]: value = value[1:-1]
        data[key.strip()] = value
    return data, text[end+4:].lstrip("\n")


def headings(body: str):
    return [re.sub(r"^[^A-Za-z0-9]+", "", h).strip().lower() for h in re.findall(r"^##\s+(.+)$", body, re.M)]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ref", default=DEFAULT_REF, help="Pinned git ref; use a commit SHA for deterministic sync")
    p.add_argument("--output", default="agency/agents")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()
    tree = json.loads(fetch(f"https://api.github.com/repos/{SOURCE}/git/trees/{a.ref}?recursive=1").decode())
    output = Path(a.output)
    records, failures = [], []
    for item in tree.get("tree", []):
        path = item.get("path", "")
        if not path.endswith(".md") or path.count("/") != 1 or path.split("/", 1)[0] not in DIVISIONS:
            continue
        try:
            raw = fetch(f"https://raw.githubusercontent.com/{SOURCE}/{a.ref}/{path}")
            text = raw.decode("utf-8")
            fm, body = frontmatter(text)
            hs = headings(body)
            missing = [h for h in REQUIRED if not any(h.lower() in x for x in hs)]
            if missing:
                failures.append({"path":path,"missing":missing}); continue
            out = output / path
            if not a.dry_run:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(raw)
            records.append({"source":path,"division":path.split("/",1)[0],"name":fm.get("name",path[:-3]),"description":fm.get("description",""),"file":str(out).replace("\\","/"),"sha256":hashlib.sha256(raw).hexdigest()})
        except Exception as exc:
            failures.append({"path":path,"error":str(exc)})
    manifest = {"schema_version":"1.0","source":SOURCE,"ref":a.ref,"agent_count":len(records),"agents":records,"failures":failures}
    if not a.dry_run:
        Path("agency").mkdir(exist_ok=True)
        Path("agency/registry.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps({"source":SOURCE,"ref":a.ref,"agents":len(records),"failures":len(failures),"dry_run":a.dry_run}, indent=2))
    return 1 if failures else 0

if __name__ == "__main__": sys.exit(main())
