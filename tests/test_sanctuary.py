# SPDX-License-Identifier: MIT
"""Unit tests for the multi-node Memnet Compute Sanctuary dogfood orchestrator."""

from pathlib import Path

from ultrawhale.sanctuary import (
    SANCTUARY_CLUSTER,
    collect_all_compute_dogfood,
    parse_offer_compute_summary,
    parse_sanctuary_receipt,
)


def test_sanctuary_cluster_inventory():
    """Cluster should include all 4 hardware nodes totaling 34 cores and 106,193 MB RAM."""
    node_ids = [n.node_id for n in SANCTUARY_CLUSTER]
    assert node_ids == [
        "m1-max-64gb",
        "dev-main-m3",
        "galaxy-tab-s9-ultra",
        "blu-g75",
    ]
    assert sum(n.cores for n in SANCTUARY_CLUSTER) == 34
    assert sum(n.ram_mb for n in SANCTUARY_CLUSTER) == 106_193


def test_parse_sanctuary_receipt():
    """Parser should extract RTT, 0o0 savings, and lease grant fields from request-sanctuary output."""
    sample = """
========================================================================
  MEMNET/1.0 LIVE WIRE + KOMPRESS-SUPER-ULTRA (0o0) SANCTUARY RECEIPT
========================================================================
  • Target Sanctuary : 192.168.11.24:8788 (RTT: 70.123 ms)
  • SOS Payload (0o0): 124B raw -> 99B compressed (20.2% saved) | Wire packet: 171B
  • Reply MNET Frame : seq=0 | 2 Soul-Verified capsules in 263B (<=1450B MTU)
  • Capsule #1       : 33B 0o0 -> 136B raw | OFFER|node=blu-g75|ep=g75://192.168.11.24:8788|ram_mb=2048|cores=6
  • Capsule #2       : 66B 0o0 -> 131B raw | GRANT|lease=lease-d058b740|provider=blu-g75|friend=ultrawhale-blu-worker|ep=g75://192.168.11.24:8788|ram_mb=1024|cores=2|cap=mt6768
"""
    receipt = parse_sanctuary_receipt(sample)
    assert receipt["target"] == "192.168.11.24:8788"
    assert receipt["rtt_ms"] == 70.123
    assert receipt["sos_saved_pct"] == 20.2
    assert receipt["verified_capsules"] == 2
    assert receipt["lease_id"] == "lease-d058b740"
    assert receipt["provider"] == "blu-g75"
    assert receipt["friend"] == "ultrawhale-blu-worker"
    assert receipt["granted_ram_mb"] == 1024
    assert receipt["granted_cores"] == 2
    assert receipt["capability"] == "mt6768"


def test_parse_offer_compute_summary():
    """Parser should extract SpherePop #ValidWitness hashes from offer-compute output."""
    sample = (
        "     ↳ SpherePop [depth=3 | #ValidWitness=196862752d2cfb73 | 0o0-SSU 166B->73B]: "
        "((( <( ? offer:dev-main-m3 ? stock:24576mb:8c:h100 ~ kompress:0o0-ssu "
        "! soul_sig:verified ! vigilance:clean @ dev-main-m3 > advertise:m3://169.254.212.115:8788 )> )))\n"
        "     ↳ SpherePop [#Commit=#ValidWitness=819073fb7050b8f3]: "
        "((( <( ? admit:field-lora-sensor ? match:blu-g75 ~ slice:1024mb:2c:mt6768 ~ kompress:0o0-ssu "
        "! soul_sig:verified ! ct000:admissible @ blu-g75 @ 8bit-wraith > grant:lease-b7015f5f )> )))\n"
    )
    leases = parse_offer_compute_summary(sample)
    assert len(leases) == 2
    assert leases[0]["lease_id"] == "lease-b7015f5f"
    assert leases[0]["provider_node_id"] == "blu-g75"
    assert leases[0]["granted_ram_mb"] == 1024
    assert leases[0]["granted_cores"] == 2
    assert leases[0]["spherepop_valid_witness"] == "819073fb7050b8f3"
    assert leases[1]["provider_node_id"] == "dev-main-m3"
    assert leases[1]["spherepop_valid_witness"] == "196862752d2cfb73"


def test_collect_all_compute_dogfood_without_bin(tmp_path: Path):
    """Graceful fallback when memnet binary path does not exist and LLM is skipped."""
    bundle = collect_all_compute_dogfood(
        memnet_bin=tmp_path / "nonexistent-memnet",
        include_llm=False,
    )
    assert bundle["summary"]["total_nodes"] == 4
    assert bundle["summary"]["total_cores"] == 34
    assert len(bundle["node_rows"]) == 4
    assert all(r["spherepop_invariant"] == "#Commit = #ValidWitness" for r in bundle["node_rows"])
