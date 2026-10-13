"""Failed resumes must preserve frozen experiment evidence."""
import copy
import tempfile
import unittest
from pathlib import Path
import zipfile

from scripts.run_cg_acceleration_study import freeze_plan, digest, ROOT


class StudyResumeTests(unittest.TestCase):
    def test_changed_budget_rejected_before_any_evidence_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary)
            structure={"cases":[{"pnr_sizes":{1:2}}],"source_hashes":{}}
            plan={"budget":60,"source_hashes":{}}
            freeze_plan(folder,structure,plan,[])
            before={p.name:p.read_bytes() for p in folder.iterdir()}
            changed=copy.deepcopy(plan);changed["budget"]=20
            with self.assertRaisesRegex(AssertionError,"Resume plan.json mismatch"):
                freeze_plan(folder,structure,changed,[])
            self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})
            freeze_plan(folder,structure,plan,[])
            self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})

    def test_corrupt_source_snapshot_is_rejected_without_replacing_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder=Path(temporary);source=ROOT/"src/cg_acceleration/singleton.py"
            hashes={str(source.resolve()):digest(source)}
            structure={"cases":[],"source_hashes":hashes};plan={"source_hashes":hashes}
            freeze_plan(folder,structure,plan,[source])
            with zipfile.ZipFile(folder/"source-snapshot.zip","w") as archive:
                archive.writestr(str(source.relative_to(ROOT)).replace("\\","/"),b"corrupted")
            before={p.name:p.read_bytes() for p in folder.iterdir()}
            with self.assertRaises(AssertionError):freeze_plan(folder,structure,plan,[source])
            self.assertEqual(before,{p.name:p.read_bytes() for p in folder.iterdir()})


if __name__ == "__main__":unittest.main()
