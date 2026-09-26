import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "data/generator_v3_2/d4_smooth_multi_aircraft_2026-09-25"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_smooth_corpus_manifest_and_files_are_consistent():
    manifest = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["corpus_id"] == "d4_smooth_multi_aircraft_2026-09-25"
    assert manifest["generator_version"] == "generator-v3.2"
    assert len(manifest["cases"]) == 16
    assert {item["layout"] for item in manifest["seatmaps"]} == {
        "2-2", "2-3", "2-4-2", "3-3", "3-3-3", "3-4-3"
    }

    for item in manifest["seatmaps"]:
        assert sha256(ROOT / item["relative_path"]) == item["sha256"]

    by_direction = {}
    for case in manifest["cases"]:
        path = ROOT / case["relative_path"]
        assert path.is_file()
        assert sha256(path) == case["sha256"]
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["generatorVersion"] == "generator-v3.2"
        assert payload["caseId"] == case["case_id"]
        assert payload["oldLayout"] == case["old_layout"]
        assert payload["newLayout"] == case["new_layout"]
        assert payload["targetSeatDemand"] == payload["passengerCount"]
        assert payload["passengerCount"] <= payload["passengerCapacity"]
        assert payload["groups"]
        by_direction.setdefault(case["direction"], []).append(case)

    assert set(by_direction) == {
        "rj22_to_nb23", "nb23_to_rj22", "nb33_to_wb242", "wb343_to_wb333"
    }
    for cases in by_direction.values():
        cases.sort(key=lambda item: item["difficulty_interpolation"])
        assert [case["difficulty_level"] for case in cases] == ["d00", "d33", "d66", "d100"]
        assert [case["difficulty_interpolation"] for case in cases] == sorted(
            case["difficulty_interpolation"] for case in cases
        )
        # The generator's resource demand remains fixed while the generated
        # instance becomes more constrained as difficulty increases.
        assert [case["passenger_count"] for case in cases] == sorted(
            (case["passenger_count"] for case in cases), reverse=True
        )
