import hashlib
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "docs/provenance/benchmark-v2-spec-2026-10-07.json"
DEV_MANIFEST = ROOT / "data/generator_v3_2/benchmark_v2_2026-10-07/manifest.json"


class BenchmarkV2SpecificationTests(unittest.TestCase):
    def test_spec_is_factorized_and_has_no_duplicate_design_points(self):
        spec = json.loads(SPEC.read_text(encoding="utf-8"))
        rows = spec["specs"]
        self.assertEqual(spec["status"], "specification_only")
        self.assertEqual(len(rows), 72)
        self.assertEqual(len({row["spec_id"] for row in rows}), 72)
        self.assertEqual(len(spec["transitions"]), 8)
        self.assertEqual(len(spec["resource_loads"]), 3)
        self.assertEqual(len(spec["pnr_profiles"]), 3)
        self.assertEqual({row["resource_load_target"] for row in rows}, {0.60, 0.85, 0.97})
        self.assertEqual({row["pnr_profile"] for row in rows}, set(spec["pnr_profiles"]))
        self.assertTrue(all(max(map(int, spec["pnr_profiles"][row["pnr_profile"]]["group_size_weights"])) <= 10 for row in rows))
        self.assertEqual(spec["planned_formal_case_count"], 360)

    def test_development_corpus_matches_its_frozen_manifest(self):
        manifest = json.loads(DEV_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(manifest["status"], "generated_development")
        self.assertEqual(manifest["spec_count"], 72)
        self.assertEqual(manifest["seed_count"], 1)
        self.assertEqual(manifest["case_count"], 72)
        self.assertEqual(manifest["generation_failures"], [])

        for case in manifest["cases"]:
            with self.subTest(case=case["case_id"]):
                path = ROOT / case["relative_path"]
                self.assertTrue(path.is_file())
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(digest, case["sha256"])
                payload = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(payload["caseId"], case["case_id"])
                self.assertEqual(payload["targetSeatDemand"], case["target_seat_demand"])
                self.assertEqual(payload["travelerCount"], case["traveler_count"])
                self.assertEqual(
                    payload["targetSeatDemand"],
                    payload["travelerCount"] + payload["protectedSeatCount"],
                )
                self.assertEqual(payload["fixedSeatCount"], case["fixed_seats"])


if __name__ == "__main__":
    unittest.main()
