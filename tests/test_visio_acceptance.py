import json
from pathlib import Path
import subprocess
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
HARNESS = SKILL_ROOT / "tests" / "fixtures" / "run_visio_acceptance_harness.ps1"
POWERSHELL = "powershell.exe"


class VisioAcceptanceScriptTests(unittest.TestCase):
    """Exercise the public PowerShell gate through STA PowerShell processes."""

    def run_harness(self, scenario):
        completed = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(HARNESS),
                "-Scenario",
                scenario,
            ],
            cwd=str(SKILL_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertTrue(lines, completed.stderr)
        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as error:
            self.fail(
                "harness did not emit one JSON result: %s\nstdout=%r\nstderr=%r"
                % (error, completed.stdout, completed.stderr)
            )
        return completed, payload

    def test_invalid_input_is_environment_failure(self):
        completed, payload = self.run_harness("invalid-input")
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertEqual(2, payload["exit_code"])
        self.assertFalse(payload["application_quit"])

    def test_missing_com_is_environment_failure(self):
        completed, payload = self.run_harness("missing-com")
        self.assertEqual(2, completed.returncode, completed.stderr)
        self.assertEqual(2, payload["exit_code"])
        self.assertFalse(payload["application_quit"])

    def test_success_closes_quits_cleans_and_preserves_source(self):
        completed, payload = self.run_harness("success")
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual(0, payload["exit_code"])
        self.assertEqual([39], payload["per_page_shape_counts"])
        self.assertTrue(payload["document_closed"])
        self.assertTrue(payload["application_quit"])
        self.assertTrue(payload["temp_cleaned"])
        self.assertTrue(payload["source_unchanged"])
        self.assertFalse(payload["fixture_temp_exists_after"])
        self.assertEqual(2, payload["alert_response"])
        self.assertFalse(payload["visible_after"])
        self.assertIsNone(payload["temp_directory"])
        self.assertEqual(payload["open_path"], payload["source_path_used"])

    def test_open_failure_is_compatibility_failure_and_quits_application(self):
        completed, payload = self.run_harness("open-failure")
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual(1, payload["exit_code"])
        self.assertFalse(payload["document_closed"])
        self.assertTrue(payload["application_quit"])
        self.assertTrue(payload["temp_cleaned"])
        self.assertTrue(payload["source_unchanged"])

    def test_count_failure_still_closes_quits_cleans_and_preserves_source(self):
        completed, payload = self.run_harness("count-failure")
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual(1, payload["exit_code"])
        self.assertEqual([38], payload["per_page_shape_counts"])
        self.assertTrue(payload["document_closed"])
        self.assertTrue(payload["application_quit"])
        self.assertTrue(payload["temp_cleaned"])
        self.assertTrue(payload["source_unchanged"])

class VisioAcceptancePublicContractTests(unittest.TestCase):
    def test_direct_script_rejects_missing_input_with_json_exit_2(self):
        script = SKILL_ROOT / "scripts" / "run_visio_acceptance.ps1"
        missing = Path(tempfile.gettempdir()) / "vsdx-gen-no-such-file.vsdx"
        completed = subprocess.run(
            [
                POWERSHELL,
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-VsdxPath",
                str(missing),
                "-ExpectedShapes",
                "39",
            ],
            cwd=str(SKILL_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        self.assertEqual(2, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout.strip())
        self.assertEqual(2, payload["exit_code"])
        self.assertEqual("input", payload["status"])
