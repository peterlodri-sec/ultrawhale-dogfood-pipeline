# /// script
# requires-python = ">=3.11"
# dependencies = ["huggingface-hub>=0.23"]
# ///
"""multidog.py — multi-dimensional dogfeeding into PeetPedro/ultrawhale-dogfood.

Each dimension rides as feeds/<dim>/<ts>-<seq>.jsonl and the run updates
feeds/MANIFEST-multidog.json. State (row counts per source) lives in
.multidog-state.json next to this file: only NEW rows are fed — the loop is
resumable, non-destructive, honest.

Dimensions today:
  music     — music.vaked.dev/.dogfeed-music.jsonl
  laps      — training-pipeline data/backyard108-laps.jsonl
  atlas     — lissajoverse data/atlas.jsonl (+ seeds.json meta row)
  vault     — 8b-is raw_research digest: notes from Today, one row each
  telemetry — a heartbeat row per run

Usage (from the ultrawhale-dogfood-pipeline dir):
  HF_TOKEN=… uv run multidog.py --once
  HF_TOKEN=… uv run multidog.py          # the loop, every FEED_SECS
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from huggingface_hub import CommitOperationAdd, HfApi

HF_REPO = "PeetPedro/ultrawhale-dogfood"
HERE = Path(__file__).resolve().parent
STATE = HERE / ".multidog-state.json"
FEED_SECS = float(os.environ.get("FEED_SECS", "600"))

ROOT = "/Users/lodripeter/workspace/peterlodri-sec"
SRC = {
    "music": Path(ROOT) / "music.vaked.dev" / ".dogfeed-music.jsonl",
    "laps": Path("/Users/lodripeter/workspace/training-pipeline") / "data" / "backyard108-laps.jsonl",
    "atlas": Path(ROOT) / "lissajoverse" / "data" / "atlas.jsonl",
    "vault": Path(ROOT) / "8b-is" / "raw_research",
}


def rows_of(dim, path):
    if dim == "vault":
        return [p for p in path.glob("*.md")]
    if not path.exists():
        return []
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def save_state(s):
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2))


def now():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def main() -> None:
    once = "--once" in sys.argv
    api = HfApi(token=os.environ.get("HF_TOKEN") or None)
    print(f"multidog -> {HF_REPO}")

    while True:
        state = load_state()
        ts = now()
        ops = []
        meta = {}

        # vault digest
        vault_files = sorted(rows_of("vault", SRC["vault"]))
        today = datetime.now().date().isoformat()
        vault_rows = []
        for p in vault_files:
            mtime = datetime.fromtimestamp(p.stat().st_mtime).date().isoformat()
            if mtime >= today:
                head = ""
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("# "):
                        head = line[2:]
                        break
                vault_rows.append({"file": p.name, "head": head})
        if vault_rows:
            ops.append(CommitOperationAdd(f"feeds/vault/{ts}.jsonl",
                                          json.dumps(vault_rows, ensure_ascii=False).encode()))
            meta["vault"] = f"{len(vault_rows)} notes"

        for dim, path in SRC.items():
            if dim == "vault":
                continue
            rows = rows_of(dim, path)
            prev = state.get(dim, 0)
            new = rows[prev:]
            if new:
                ops.append(CommitOperationAdd(f"feeds/{dim}/{ts}.jsonl",
                          ("\n".join(new) + "\n").encode()))
                state[dim] = len(rows)
                meta[dim] = f"{len(new)} new ({len(rows)} total)"
            else:
                meta[dim] = "nothing new"

        # telemetry heartbeat
        hb = {"ts": ts, "dims": meta}
        ops.append(CommitOperationAdd(f"feeds/telemetry/{ts}.jsonl",
                          (json.dumps(hb) + "\n").encode()))
        meta["telemetry"] = "1 row"

        # manifest
        ops.append(CommitOperationAdd("feeds/MANIFEST-multidog.json",
                          (json.dumps({"ts": ts, "feeds": meta},
                                                       ensure_ascii=False, indent=2) + "\n").encode()))

        try:
            api.create_commit(HF_REPO, operations=ops, commit_message=f"multidog {ts}", repo_type="dataset")
            save_state(state)
            print(f"  {ts}: {json.dumps(meta)}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! upload failed: {e}")

        if once:
            break
        time.sleep(FEED_SECS)


if __name__ == "__main__":
    main()