# /// script
# requires-python = ">=3.11"
# dependencies = ["huggingface-hub>=0.23"]
# ///
"""multidog.py — multi-dimensional dogfeeding into PeetPedro/ultrawhale-dogfood
and PeetPedro/osc9000-traces, powered by the Ultrawhale Ternary NAND Ring Oscillator.

Every heartbeat is a riva breath: the feed daemon breathes on the same
rhythm as riva.vaked.dev's breathing cycle - in, out, row.

Each dimension rides as feeds/<dim>/<ts>.jsonl and the run updates
feeds/MANIFEST-multidog.json. State (row counts + NAND oscillator ring state)
lives in .multidog-state.json next to this file: only NEW rows are fed — the
loop is resumable, non-destructive, honest. When a dimension has no new rows,
the ±3 empty-frame NAND fuse fires so the oscillator carrier wave never starves.

Dimensions:
  music            — music.vaked.dev/.dogfeed-music.jsonl
  laps             — training-pipeline data/backyard108-laps.jsonl
  atlas            — lissajoverse data/atlas.jsonl
  vault            — 8b-is raw_research digest: new/updated notes, one row each
  nand_oscillator  — 5-stage cross-coupled {−1, 0, +1} NAND ring + ±3 empty-frame fuse
  zoneinn          — 8b-is/zoneInn & zoneinn.vaked.dev radio dial + Bluetooth telemetry
  memnet_sanctuary — 4-node MEMNET/1.0 0o0-SSU + SpherePop ((( <( ? ~ ! @ > )> ))) compute sanctuary
                     (m1-max-64gb, dev-main-m3, galaxy-tab-s9-ultra, blu-g75) + M1 Max Q&A synthesis
  babilon          — Babilon quant-dog-v1 Xoshiro128** baby-babble translation pairs
  qri              — qualia-research digest
  nate             — nate-canon essays digest
  telemetry        — a heartbeat row per run

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

from huggingface_hub import CommitOperationAdd, HfApi, get_token

HF_REPO = "PeetPedro/ultrawhale-dogfood"
HF_OSC_REPO = "PeetPedro/osc9000-traces"
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

# ── Ultrawhale Ternary NAND Ring Oscillator ({−1, 0, +1}) ────────────────────
# Exact parity with 8b-is/zoneInn (`src/nand.rs`, `src/tern.rs`) and
# zoneinn.vaked.dev (`window.zoneInnCore`).
TERN_BANDS = 8
DEFAULT_THRESHOLD = 0.08
FNV_OFFSET = 0xCBF29CE484222325
FNV_PRIME = 0x100000001B3
MASK_64 = 0xFFFFFFFFFFFFFFFF
INITIAL_RING = [1, -1, 1, -1, 1]
INITIAL_KLEENE = [1, 0, -1, 0, 1]
NAND_STEPS_PER_CYCLE = 16


def _bin_trit(a: int) -> int:
    return 1 if a > 0 else 0


def nand_gate(a: int, b: int) -> int:
    """Binary-projected NAND gate mapped onto {−1, +1}."""
    return -1 if (_bin_trit(a) == 1 and _bin_trit(b) == 1) else 1


def kleene_nand(a: int, b: int) -> int:
    """Strong-3 Kleene NAND ¬min(a, b) on {−1, 0, +1}."""
    m = min(a, b)
    return 0 if m == 0 else -m


def quantize_band(v: float, threshold: float = DEFAULT_THRESHOLD) -> int:
    if v >= threshold:
        return 1
    if v <= -threshold:
        return -1
    return 0


def fnv1a64_trits(trits: list[int]) -> int:
    h = FNV_OFFSET
    for t in trits:
        b = (t + 1) & 0xFF
        h ^= b
        h = (h * FNV_PRIME) & MASK_64
    return h


def format_glyphs(trits: list[int]) -> str:
    return "".join("+" if t == 1 else ("−" if t == -1 else "·") for t in trits)


def step_nand_ring(
    ring: list[int],
    kleene: list[int],
    step_index: int,
) -> dict:
    n = len(ring)
    next_ring = [0] * n
    next_kleene = [0] * n
    feedback = ring[n - 1]

    for i in range(n):
        prev = feedback if i == 0 else ring[i - 1]
        partner = ring[(i + 2) % n]
        next_ring[i] = nand_gate(prev, prev) if i == 0 else nand_gate(prev, partner)

        k_prev = kleene[n - 1] if i == 0 else kleene[i - 1]
        k_mod = ((step_index + i) % 3) - 1
        next_kleene[i] = kleene_nand(k_prev, k_mod)

    bins = [
        0.55 * next_ring[b % n] + 0.45 * next_kleene[b % n]
        for b in range(TERN_BANDS)
    ]
    trits = [quantize_band(v) for v in bins]
    plus = sum(1 for t in trits if t == 1)
    minus = sum(1 for t in trits if t == -1)
    dominant = 1 if plus > minus else (-1 if minus > plus else 0)
    h = fnv1a64_trits(trits)
    hash_hex = f"0x{h:016x}"

    return {
        "step": step_index,
        "ring": next_ring,
        "kleene": next_kleene,
        "trits": trits,
        "glyphs": format_glyphs(trits),
        "dominant": dominant,
        "hash_hex": hash_hex,
    }


def rows_of(dim: str, path: Path):
    if dim == "vault":
        return sorted(path.glob("*.md")) if path.exists() else []
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def to_jsonl(rows: list[dict]) -> bytes:
    return ("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n").encode("utf-8")


def main() -> None:
    once = "--once" in sys.argv
    token = os.environ.get("HF_TOKEN") or get_token()
    api = HfApi(token=token)
    print(f"multidog -> {HF_REPO} (+ {HF_OSC_REPO})")

    while True:
        state = load_state()
        ts = now()
        ts_iso = datetime.now(timezone.utc).isoformat()
        ops = []
        meta = {}
        empty_dims = []

        # 1. vault digest (stateful tracking of seen file mtimes)
        vault_files = rows_of("vault", SRC["vault"])
        vault_seen = state.get("vault_mtimes", {})
        vault_rows = []
        for p in vault_files:
            mtime = int(p.stat().st_mtime)
            if vault_seen.get(p.name, 0) < mtime:
                head = ""
                for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                    if line.startswith("# "):
                        head = line[2:]
                        break
                vault_rows.append({
                    "ts": ts_iso,
                    "dim": "vault",
                    "file": p.name,
                    "mtime": mtime,
                    "head": head,
                })
                vault_seen[p.name] = mtime
        if vault_rows:
            ops.append(CommitOperationAdd(f"feeds/vault/{ts}.jsonl", to_jsonl(vault_rows)))
            state["vault_mtimes"] = vault_seen
            meta["vault"] = f"{len(vault_rows)} notes ({len(vault_files)} total)"
        else:
            meta["vault"] = "nothing new"
            empty_dims.append("vault")

        # 2. JSONL stream dimensions (music, laps, atlas)
        for dim, path in SRC.items():
            if dim == "vault":
                continue
            rows = rows_of(dim, path)
            prev = state.get(dim, 0)
            new = rows[prev:]
            if new:
                ops.append(
                    CommitOperationAdd(
                        f"feeds/{dim}/{ts}.jsonl",
                        ("\n".join(new) + "\n").encode("utf-8"),
                    )
                )
                state[dim] = len(rows)
                meta[dim] = f"{len(new)} new ({len(rows)} total)"
            else:
                meta[dim] = "nothing new"
                empty_dims.append(dim)

        # 3. Ultrawhale Ternary NAND Ring Oscillator + ±3 empty-frame fuse refeed
        nand_step = int(state.get("nand_step", 0))
        ring = list(state.get("nand_ring", INITIAL_RING))
        kleene = list(state.get("nand_kleene", INITIAL_KLEENE))
        nand_rows = []
        for _ in range(NAND_STEPS_PER_CYCLE):
            st = step_nand_ring(ring, kleene, nand_step)
            ring = st["ring"]
            kleene = st["kleene"]
            short_hash = st["hash_hex"][2:10]
            nand_rows.append({
                "id": f"ultrawhale-nand-osc-{nand_step}-{short_hash}",
                "ts": ts_iso,
                "dim": "nand_oscillator",
                "repo": HF_REPO,
                "source": "8b-is/zoneInn",
                "step": nand_step,
                "ring_trits": ring,
                "kleene_trits": kleene,
                "spectrum_trits": st["trits"],
                "glyphs": st["glyphs"],
                "dominant": st["dominant"],
                "frame_golden": st["hash_hex"],
            })
            nand_step += 1

        # Empty-frame ±3 NAND fuse refeed for any dimension that had no new rows
        for idle_dim in empty_dims:
            st = step_nand_ring(ring, kleene, nand_step)
            ring = st["ring"]
            kleene = st["kleene"]
            short_hash = st["hash_hex"][2:10]
            nand_rows.append({
                "id": f"ultrawhale-nand-fuse-{idle_dim}-{nand_step}-{short_hash}",
                "ts": ts_iso,
                "dim": "nand_oscillator",
                "fuse": "±3_empty_frame_refeed",
                "idle_dim": idle_dim,
                "repo": HF_REPO,
                "source": "8b-is/zoneInn",
                "step": nand_step,
                "ring_trits": ring,
                "kleene_trits": kleene,
                "spectrum_trits": st["trits"],
                "glyphs": st["glyphs"],
                "dominant": st["dominant"],
                "frame_golden": st["hash_hex"],
            })
            nand_step += 1

        ops.append(
            CommitOperationAdd(f"feeds/nand_oscillator/{ts}.jsonl", to_jsonl(nand_rows))
        )
        state["nand_step"] = nand_step
        state["nand_ring"] = ring
        state["nand_kleene"] = kleene
        meta["nand_oscillator"] = (
            f"{len(nand_rows)} steps (step->{nand_step}, fuse={len(empty_dims)}, "
            f"golden={nand_rows[-1]['frame_golden']})"
        )

        # 4. zoneInn Web Radio & Bluetooth Studio dimension
        zoneinn_rows = [
            {
                "ts": ts_iso,
                "dim": "zoneinn",
                "service": "quantTernRadio",
                "repo": "8b-is/zoneInn",
                "web_url": "https://zoneinn.vaked.dev",
                "bluetooth": {
                    "web_bluetooth": True,
                    "audio_sink_routing": True,
                    "media_session_avrcp": True,
                    "featured_speaker": "Soundcore Select 4 GO",
                },
                "stations": [
                    "radio.vaked.dev",
                    "music.vaked.dev",
                    "nand.vaked.dev",
                    "mirror.vaked.dev",
                ],
                "canonical_golden": "0xe0efe6a3385d3dd3",
                "demo_t0_golden": "0x7311bcbd581290fd",
                "latest_nand_golden": nand_rows[-1]["frame_golden"],
            }
        ]
        ops.append(CommitOperationAdd(f"feeds/zoneinn/{ts}.jsonl", to_jsonl(zoneinn_rows)))
        meta["zoneinn"] = "1 studio snapshot (Soundcore Select 4 GO + 4 stations)"

        # 4b. memnet_sanctuary dimension (all 4 compute nodes: m1-max-64gb, dev-main-m3, galaxy-tab-s9-ultra, blu-g75)
        sys.path.insert(0, str(HERE / "src"))
        sys.path.insert(0, str(HERE))
        try:
            from ultrawhale.sanctuary import collect_all_compute_dogfood

            sanc_bundle = collect_all_compute_dogfood(include_llm=True)
            sanc_rows = sanc_bundle["all_rows"]
            sanc_summary = sanc_bundle["summary"]
            ops.append(
                CommitOperationAdd(f"feeds/memnet_sanctuary/{ts}.jsonl", to_jsonl(sanc_rows))
            )
            meta["memnet_sanctuary"] = (
                f"{len(sanc_rows)} rows ({sanc_summary['total_nodes']} nodes, "
                f"{sanc_summary['live_wire_daemons']} live wire daemons, "
                f"{sanc_summary['total_cores']}c/{sanc_summary['total_ram_mb']}MB, "
                f"{sanc_summary['llm_qa_pairs']} M1-Max LLM Q&A)"
            )
        except Exception as e:  # noqa: BLE001
            meta["memnet_sanctuary"] = f"skipped: {e}"

        # 4c. babilon quant-dog-v1 dimension
        try:
            from babilon_quant_dogfood import generate_babilon_dogfood

            bab_tmp = HERE / "dogfeed_parallel" / f"babilon_multidog_{ts}.jsonl"
            bab_tmp.parent.mkdir(parents=True, exist_ok=True)
            bab_rows = generate_babilon_dogfood(num_cycles=16, output_path=str(bab_tmp), eye_seed=True)
            ops.append(CommitOperationAdd(f"feeds/babilon/{ts}.jsonl", to_jsonl(bab_rows)))
            avg_bab_q = sum(r["quality_score"] for r in bab_rows) / len(bab_rows)
            meta["babilon"] = f"{len(bab_rows)} quant-dog-v1 pairs (avg_q={avg_bab_q:.3f})"
        except Exception as e:  # noqa: BLE001
            meta["babilon"] = f"skipped: {e}"

        # 5. qri & nate-canon threads (bundled into the same atomic commit)
        qri_digest = [
            {"ts": ts_iso, "dim": "qri", "source": "qri.org", "keep": "the flame kept, texture over narrative"},
            {"ts": ts_iso, "dim": "qri", "tie": "kuramoto log-polar lattice = enthea's sibling instrument"},
            {"ts": ts_iso, "dim": "qri", "tie": "ITN = the wip-catalog's hidden triage"},
            {"ts": ts_iso, "dim": "qri", "tie": "valence = the lane's shelf (emotionalE, harm to warm)"},
        ]
        ops.append(CommitOperationAdd(f"feeds/qri/{ts}.jsonl", to_jsonl(qri_digest)))
        meta["qri"] = f"{len(qri_digest)} rows"

        nate_canon = [
            {"ts": ts_iso, "dim": "nate", "essay": "rehearsable abstraction", "key": "the quoted term stays in the loop"},
            {"ts": ts_iso, "dim": "nate", "essay": "the residual channel", "key": "differences feed coordinates, residuals feed differences"},
            {"ts": ts_iso, "dim": "nate", "essay": "holocoordinates", "key": "four independent warrants, the winding no chart rows"},
            {"ts": ts_iso, "dim": "nate", "essay": "the roots of optimism", "key": "transformability over probability, hope needs one viable action"},
        ]
        ops.append(CommitOperationAdd(f"feeds/nate/{ts}.jsonl", to_jsonl(nate_canon)))
        meta["nate"] = f"{len(nate_canon)} rows"

        # 6. telemetry heartbeat
        hb = {
            "ts": ts,
            "ts_iso": ts_iso,
            "dims": meta,
            "nand_step": nand_step,
            "latest_golden": nand_rows[-1]["frame_golden"],
        }
        ops.append(
            CommitOperationAdd(
                f"feeds/telemetry/{ts}.jsonl",
                (json.dumps(hb, ensure_ascii=False) + "\n").encode("utf-8"),
            )
        )
        meta["telemetry"] = "1 row"

        # 7. manifest
        ops.append(
            CommitOperationAdd(
                "feeds/MANIFEST-multidog.json",
                (
                    json.dumps({"ts": ts, "feeds": meta}, ensure_ascii=False, indent=2)
                    + "\n"
                ).encode("utf-8"),
            )
        )

        try:
            commit_info = api.create_commit(
                HF_REPO,
                operations=ops,
                commit_message=f"multidog {ts} (ultrawhale NAND oscillator + zoneInn)",
                repo_type="dataset",
            )
            save_state(state)
            print(f"  {ts} [{HF_REPO} @ {getattr(commit_info, 'oid', 'ok')}]: {json.dumps(meta)}")
        except Exception as e:  # noqa: BLE001
            print(f"  ! upload failed ({HF_REPO}): {e}")

        # 8. osc9000-traces refeed: Ultrawhale NAND Oscillator trace snapshot
        osc_trace = {
            "session_id": f"sess-nand-{ts}",
            "ts": ts_iso,
            "generator": "ultrawhale-nand-oscillator",
            "source_repo": "8b-is/zoneInn",
            "web_studio": "https://zoneinn.vaked.dev",
            "nand_step_end": nand_step,
            "ring_trits": ring,
            "kleene_trits": kleene,
            "canonical_golden": "0xe0efe6a3385d3dd3",
            "frames": nand_rows,
        }
        try:
            osc_op = [
                CommitOperationAdd(
                    f"data/sess-nand-{ts}/{ts}.json",
                    (json.dumps(osc_trace, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
            ]
            osc_commit = api.create_commit(
                HF_OSC_REPO,
                operations=osc_op,
                commit_message=f"refeed ultrawhale NAND oscillator {ts}",
                repo_type="dataset",
            )
            print(f"  {ts} [{HF_OSC_REPO} @ {getattr(osc_commit, 'oid', 'ok')}]: {len(nand_rows)} NAND frames")
        except Exception as e:  # noqa: BLE001
            print(f"  ! osc9000-traces failed: {e}")

        # 9. cogito dimension: the summa breathes on the same cycle
        cog_rows = []
        for name in (
            "the-dyad-mapping-essences.md",
            "the-small-testament.md",
            "the-base-emotionale.md",
            "william-leonard-pickard-read-with-care.md",
        ):
            p = SRC["vault"] / name
            if p.exists():
                head = next(
                    (
                        line[2:]
                        for line in p.read_text(encoding="utf-8").splitlines()
                        if line.startswith("# ")
                    ),
                    "",
                )
                cog_rows.append({"ts": ts_iso, "dim": "vault", "file": name, "head": head})
        if cog_rows:
            cop = [CommitOperationAdd(f"cogito/{ts}.jsonl", to_jsonl(cog_rows))]
            try:
                api.create_commit(
                    "PeetPedro/cogito-ergo-summa",
                    operations=cop,
                    commit_message=f"multidog cogito {ts}",
                    repo_type="dataset",
                )
            except Exception as e:  # noqa: BLE001
                print(f"  ! cogito: {e}")

        if once:
            break
        time.sleep(FEED_SECS)


if __name__ == "__main__":
    main()