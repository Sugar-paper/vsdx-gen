import importlib.util
import base64
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import urllib.parse
import urllib.request
import zlib


SKILL_ROOT = Path(__file__).resolve().parents[1]


def load_script(name, relative_path):
    path = SKILL_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


test_import = load_script("test_import_under_test", "scripts/test_import.py")
batch_import = load_script("batch_import_under_test", "scripts/batch_import.py")
ACCEPTANCE_PATH = SKILL_ROOT / "tests" / "run_drawio_acceptance.py"


def load_acceptance():
    return load_script(
        "drawio_acceptance_under_test", "tests/run_drawio_acceptance.py"
    )


VALID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <mxCell id="A" parent="1" vertex="1"><mxGeometry x="0" y="0" width="10" height="10" as="geometry"/></mxCell>
  <mxCell id="B" parent="1" vertex="1"><mxGeometry x="20" y="0" width="10" height="10" as="geometry"/></mxCell>
  <mxCell id="E" parent="1" edge="1" source="A" target="B"><mxGeometry relative="1" as="geometry"/></mxCell>
</root></mxGraphModel>"""


WRAPPED_ID_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mxGraphModel><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <UserObject id="A" label="Alpha"><mxCell parent="1" vertex="1"><mxGeometry x="0" y="0" width="10" height="10" as="geometry"/></mxCell></UserObject>
  <UserObject id="B" label="Beta"><mxCell parent="1" vertex="1"><mxGeometry x="20" y="0" width="10" height="10" as="geometry"/></mxCell></UserObject>
  <UserObject id="E"><mxCell parent="1" edge="1" source="A" target="B"><mxGeometry relative="1" as="geometry"/></mxCell></UserObject>
</root></mxGraphModel>"""


ACCEPTANCE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mxGraphModel pageHeight="1117.6"><root>
  <mxCell id="0"/><mxCell id="1" parent="0"/>
  <UserObject id="wrapper-a" label="Alpha">
    <mxCell parent="1" vertex="1" style="vsdxID=1;">
      <mxGeometry x="50.8" y="152.4" width="101.6" height="101.6" as="geometry"/>
    </mxCell>
  </UserObject>
  <UserObject id="wrapper-b" label="Beta">
    <mxCell parent="1" vertex="1" style="vsdxID=2;">
      <mxGeometry x="254" y="152.4" width="101.6" height="101.6" as="geometry"/>
    </mxCell>
  </UserObject>
  <UserObject id="wrapper-r" label="Rotated">
    <mxCell parent="1" vertex="1" style="vsdxID=3;rotation=30;">
      <mxGeometry x="50.8" y="355.6" width="101.6" height="101.6" as="geometry"/>
    </mxCell>
  </UserObject>
  <UserObject id="edge-auto" label="Auto">
    <mxCell parent="1" edge="1" source="wrapper-a" target="wrapper-b"
      style="vsdxID=4;exitX=1;exitY=0.5;entryX=0;entryY=0.5;">
      <mxGeometry relative="1" as="geometry"><Array as="points"/></mxGeometry>
    </mxCell>
  </UserObject>
  <UserObject id="edge-routed" label="Routed">
    <mxCell parent="1" edge="1" source="wrapper-r" target="wrapper-b"
      style="vsdxID=5;entryX=0.5;entryY=1;">
      <mxGeometry relative="1" as="geometry"><Array as="points">
        <mxPoint x="76.2" y="362.4059"/>
        <mxPoint x="203.2" y="304.8"/>
      </Array></mxGeometry>
    </mxCell>
  </UserObject>
</root></mxGraphModel>"""


class FakeMouse:
    def move(self, *args, **kwargs):
        pass

    def down(self):
        pass

    def up(self):
        pass


class FakeKeyboard:
    def __init__(self, page):
        self.page = page

    def press(self, key):
        self.page.events.append(("key", key))
        if key == "Escape" and self.page.dismisses_dialogs:
            self.page.visible_dialogs = 0


class FakeLocator:
    def __init__(self, page, selector):
        self.page = page
        self.selector = selector

    @property
    def first(self):
        return self

    def bounding_box(self, timeout=None):
        return {"x": 0, "y": 0, "width": 40, "height": 20}

    def click(self, timeout=None):
        self.page.events.append(("click", self.selector, timeout))

    def count(self):
        if self.selector in (".geDialog", ".geDialog:visible"):
            return self.page.visible_dialogs
        return 1


class FakePage:
    def __init__(self, clipboard=VALID_XML, import_dialogs=(), screenshot_error=None,
                 goto_error=None, dismisses_dialogs=True, fit_result=None):
        self.clipboard = clipboard
        self.import_dialogs = tuple(import_dialogs)
        self.screenshot_error = screenshot_error
        self.goto_error = goto_error
        self.dismisses_dialogs = dismisses_dialogs
        self.fit_result = fit_result or {
            "fullyFramed": True,
            "coverage": 1.0,
            "scale": 1.0,
        }
        self.visible_dialogs = 1
        self.events = []
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard(self)

    def locator(self, selector, **kwargs):
        return FakeLocator(self, selector)

    def get_by_role(self, role, name=None):
        return FakeLocator(self, "%s:%s" % (role, name))

    def goto(self, url, timeout=None, wait_until=None):
        self.events.append(("goto", url, timeout, wait_until))
        if self.goto_error is not None:
            raise self.goto_error

    def wait_for_function(self, expression, timeout=None):
        self.events.append(("wait_for_function", timeout))

    def set_default_timeout(self, timeout):
        self.events.append(("default_timeout", timeout))

    def set_default_navigation_timeout(self, timeout):
        self.events.append(("navigation_timeout", timeout))

    def wait_for_timeout(self, timeout):
        self.events.append(("wait", timeout))

    def evaluate(self, expression, arg=None):
        if expression == "window.__clip":
            return self.clipboard
        if "querySelectorAll('.geDialog')" in expression:
            return list(self.import_dialogs)
        if "Draw.loadPlugin" in expression:
            self.events.append(("editor_probe",))
        if "initialFitDiagram" in expression:
            self.events.append(("fit_diagram", arg))
            return self.fit_result
        return None

    def set_input_files(self, selector, path, timeout=None):
        self.events.append(("input", selector, path, timeout))

    def screenshot(self, path, full_page=False, timeout=None):
        self.events.append(
            ("screenshot", path, full_page, self.visible_dialogs, timeout)
        )
        if self.screenshot_error is not None:
            raise self.screenshot_error


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.closed = False

    def new_page(self, **kwargs):
        return self.page

    def close(self):
        self.closed = True


class FakeFirefox:
    def __init__(self, browser):
        self.browser = browser
        self.executable_path = sys.executable

    def launch(self, **kwargs):
        self.browser.launch_kwargs = kwargs
        return self.browser


class FakePlaywright:
    def __init__(self, browser):
        self.firefox = FakeFirefox(browser)


class FakePlaywrightContext:
    def __init__(self, browser):
        self.playwright = FakePlaywright(browser)

    def __enter__(self):
        return self.playwright

    def __exit__(self, exc_type, exc, traceback):
        return False


class ImportToolContractTests(unittest.TestCase):
    def run_import(self, page, output_path, **kwargs):
        browser = FakeBrowser(page)
        with mock.patch.object(test_import, "check_server", return_value=True), \
                mock.patch.object(
                    test_import,
                    "_load_playwright",
                    return_value=lambda: FakePlaywrightContext(browser),
                ):
            result = test_import.import_vsdx(
                kwargs.pop("input_path"), output_path, **kwargs
            )
        return result, browser

    def test_validate_exported_xml_accepts_valid_model_and_exact_counts(self):
        result = test_import.validate_exported_xml(VALID_XML, expect_nodes=2, expect_edges=1)
        self.assertEqual(result.node_count, 2)
        self.assertEqual(result.edge_count, 1)

    def test_validate_exported_xml_uses_wrapper_ids_when_cells_omit_them(self):
        result = test_import.validate_exported_xml(
            WRAPPED_ID_XML, expect_nodes=2, expect_edges=1
        )

        self.assertEqual(result.node_count, 2)
        self.assertEqual(result.edge_count, 1)

    def test_validate_exported_xml_rejects_invalid_xml_and_unbound_edges(self):
        with self.assertRaises(test_import.ImportToolError) as invalid:
            test_import.validate_exported_xml("not xml")
        self.assertEqual(invalid.exception.code, 1)

        unbound = VALID_XML.replace(' target="B"', "")
        with self.assertRaises(test_import.ImportToolError) as missing:
            test_import.validate_exported_xml(unbound)
        self.assertEqual(missing.exception.code, 1)
        self.assertIn("target", str(missing.exception))

    def test_validate_exported_xml_rejects_exact_count_mismatch(self):
        with self.assertRaises(test_import.ImportToolError) as mismatch:
            test_import.validate_exported_xml(VALID_XML, expect_nodes=3, expect_edges=1)
        self.assertEqual(mismatch.exception.code, 1)
        self.assertIn("节点数", str(mismatch.exception))

    def test_validate_exported_xml_rejects_multiple_pages_instead_of_skipping_one(self):
        model = VALID_XML.split("\n", 1)[1]
        second_page = model.replace(' target="B"', "")
        multi_page = (
            '<mxfile><diagram name="one">%s</diagram>'
            '<diagram name="two">%s</diagram></mxfile>'
        ) % (model, second_page)
        with self.assertRaises(test_import.ImportToolError) as failure:
            test_import.validate_exported_xml(multi_page)
        self.assertEqual(failure.exception.code, 1)
        self.assertIn("多页", str(failure.exception))

        compressed_second_page = (
            '<mxfile><diagram name="one">%s</diagram>'
            '<diagram name="two">compressed-page-data</diagram></mxfile>'
        ) % model
        with self.assertRaises(test_import.ImportToolError) as compressed_failure:
            test_import.validate_exported_xml(compressed_second_page)
        self.assertEqual(compressed_failure.exception.code, 1)
        self.assertIn("多页", str(compressed_failure.exception))

    def test_main_missing_input_is_an_argument_error(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            output = Path(temp_dir) / "result.drawio"
            code = test_import.main([str(Path(temp_dir) / "missing.vsdx"), str(output)])
        self.assertEqual(code, 2)

    def test_main_maps_runtime_and_validation_failures_to_one(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            output_path = Path(temp_dir) / "result.drawio"
            input_path.write_bytes(b"vsdx")
            for failure, expected_code in (
                (test_import.ImportToolError("service unavailable", code=1), 1),
                (test_import.ImportToolError("empty clipboard", code=1), 1),
                (TimeoutError("timed out"), 1),
                (OSError("cannot write output"), 2),
            ):
                with self.subTest(failure=type(failure).__name__):
                    with mock.patch.object(test_import, "import_vsdx", side_effect=failure):
                        self.assertEqual(
                            test_import.main([str(input_path), str(output_path)]),
                            expected_code,
                        )

    def test_service_and_playwright_gates_fail_before_browser_launch(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            output_path = Path(temp_dir) / "result.drawio"
            input_path.write_bytes(b"vsdx")

            with mock.patch.object(test_import, "check_server", return_value=False), \
                    mock.patch.object(test_import, "_load_playwright") as factory:
                with self.assertRaises(test_import.ImportToolError) as unavailable:
                    test_import.import_vsdx(input_path, output_path)
            self.assertEqual(unavailable.exception.code, 1)
            factory.assert_not_called()

            with mock.patch.object(test_import, "check_server", return_value=True), \
                    mock.patch.object(
                        test_import,
                        "_load_playwright",
                        side_effect=test_import.ImportToolError(
                            "missing Playwright", code=2
                        ),
                    ):
                with self.assertRaises(test_import.ImportToolError) as dependency:
                    test_import.import_vsdx(input_path, output_path)
            self.assertEqual(dependency.exception.code, 2)
            self.assertIn("Playwright", str(dependency.exception))

            browser = FakeBrowser(FakePage())
            context = FakePlaywrightContext(browser)
            context.playwright.firefox.executable_path = Path(temp_dir) / "missing-firefox.exe"
            with mock.patch.object(test_import, "check_server", return_value=True), \
                    mock.patch.object(
                        test_import,
                        "_load_playwright",
                        return_value=lambda: context,
                    ):
                with self.assertRaises(test_import.ImportToolError) as firefox:
                    test_import.import_vsdx(input_path, output_path)
            self.assertEqual(firefox.exception.code, 2)
            self.assertIn("Firefox", str(firefox.exception))
            self.assertFalse(hasattr(browser, "launch_kwargs"))

    def test_import_vsdx_retries_transient_ebusy_until_launch_succeeds(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            output_path = Path(temp_dir) / "result.drawio"
            input_path.write_bytes(b"vsdx")
            browser = FakeBrowser(FakePage())
            context = FakePlaywrightContext(browser)
            firefox = mock.Mock()
            firefox.executable_path = sys.executable
            firefox.launch.side_effect = [
                RuntimeError("BrowserType.launch: spawn EBUSY"),
                RuntimeError("BrowserType.launch: spawn EBUSY"),
                browser,
            ]
            context.playwright.firefox = firefox

            with mock.patch.object(test_import, "check_server", return_value=True), \
                    mock.patch.object(
                        test_import,
                        "_load_playwright",
                        return_value=lambda: context,
                    ), \
                    mock.patch("time.sleep") as sleep:
                result = test_import.import_vsdx(input_path, output_path)

        self.assertEqual((result.node_count, result.edge_count), (2, 1))
        self.assertEqual(firefox.launch.call_count, 3)
        self.assertEqual(
            [call.args for call in sleep.call_args_list],
            [(0.5,), (1.0,)],
        )
        self.assertTrue(browser.closed)

    def test_launch_firefox_does_not_retry_non_ebusy_errors(self):
        firefox = mock.Mock()
        firefox.launch.side_effect = RuntimeError(
            "BrowserType.launch: executable doesn't exist"
        )

        with mock.patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "executable"):
                test_import._launch_firefox(firefox, timeout_ms=7500)

        firefox.launch.assert_called_once_with(headless=True, timeout=7500)
        sleep.assert_not_called()

    def test_launch_firefox_raises_third_ebusy_after_two_backoffs(self):
        firefox = mock.Mock()
        firefox.launch.side_effect = RuntimeError(
            "BrowserType.launch: spawn EBUSY"
        )

        with mock.patch("time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "EBUSY"):
                test_import._launch_firefox(firefox, timeout_ms=7500)

        self.assertEqual(firefox.launch.call_count, 3)
        self.assertEqual(
            [call.args for call in sleep.call_args_list],
            [(0.5,), (1.0,)],
        )

    def test_page_startup_error_classifier_matches_only_the_exact_signature(self):
        signature = (
            "Browser.new_page: Cannot read properties of undefined "
            "(reading '_page')"
        )

        self.assertTrue(
            test_import._is_page_startup_error(RuntimeError(signature))
        )
        for different in (
            "BrowserContext.new_page: Cannot read properties of undefined "
            "(reading '_page')",
            signature + " after target crash",
            "Browser.new_page: Target page, context or browser has been closed",
        ):
            with self.subTest(message=different):
                self.assertFalse(
                    test_import._is_page_startup_error(RuntimeError(different))
                )

    def test_open_browser_page_retries_exact_failure_once_normally(self):
        signature = (
            "Browser.new_page: Cannot read properties of undefined "
            "(reading '_page')"
        )
        first_browser = mock.Mock()
        first_browser.new_page.side_effect = RuntimeError(signature)
        second_browser = mock.Mock()
        page = object()
        second_browser.new_page.return_value = page
        firefox = mock.Mock()
        firefox.launch.side_effect = [first_browser, second_browser]

        with mock.patch.object(test_import.sys, "platform", "linux"), \
                mock.patch.object(test_import.time, "sleep") as sleep:
            result = test_import._open_browser_page(firefox, timeout_ms=7500)

        self.assertEqual(result, (second_browser, page))
        self.assertEqual(
            firefox.launch.call_args_list,
            [
                mock.call(headless=True, timeout=7500),
                mock.call(headless=True, timeout=7500),
            ],
        )
        first_browser.new_page.assert_called_once_with(
            viewport={"width": 1280, "height": 900}
        )
        second_browser.new_page.assert_called_once_with(
            viewport={"width": 1280, "height": 900}
        )
        first_browser.close.assert_called_once_with()
        second_browser.close.assert_not_called()
        sleep.assert_called_once_with(0.25)

    def test_open_browser_page_uses_windows_only_sandbox_fallback(self):
        signature = (
            "Browser.new_page: Cannot read properties of undefined "
            "(reading '_page')"
        )
        browsers = [mock.Mock(), mock.Mock(), mock.Mock()]
        browsers[0].new_page.side_effect = RuntimeError(signature)
        browsers[1].new_page.side_effect = RuntimeError(signature)
        page = object()
        browsers[2].new_page.return_value = page
        firefox = mock.Mock()
        firefox.launch.side_effect = browsers
        stderr = io.StringIO()

        with mock.patch.object(test_import.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"KEEP_ME": "yes"}, clear=True), \
                mock.patch.object(test_import.time, "sleep") as sleep, \
                mock.patch.object(test_import.sys, "stderr", stderr):
            result = test_import._open_browser_page(firefox, timeout_ms=9000)
            self.assertNotIn("MOZ_DISABLE_CONTENT_SANDBOX", os.environ)

        self.assertEqual(result, (browsers[2], page))
        self.assertEqual(
            firefox.launch.call_args_list[:2],
            [
                mock.call(headless=True, timeout=9000),
                mock.call(headless=True, timeout=9000),
            ],
        )
        fallback_options = firefox.launch.call_args_list[2].kwargs
        self.assertEqual(fallback_options["headless"], True)
        self.assertEqual(fallback_options["timeout"], 9000)
        self.assertEqual(
            fallback_options["env"],
            {
                "KEEP_ME": "yes",
                "MOZ_DISABLE_CONTENT_SANDBOX": "1",
            },
        )
        browsers[0].close.assert_called_once_with()
        browsers[1].close.assert_called_once_with()
        browsers[2].close.assert_not_called()
        self.assertEqual(
            [call.args for call in sleep.call_args_list],
            [(0.25,), (0.5,)],
        )
        self.assertIn("MOZ_DISABLE_CONTENT_SANDBOX=1", stderr.getvalue())

    def test_open_browser_page_does_not_retry_unrelated_page_errors(self):
        browser = mock.Mock()
        browser.new_page.side_effect = RuntimeError(
            "Browser.new_page: Target page, context or browser has been closed"
        )
        firefox = mock.Mock()
        firefox.launch.return_value = browser

        with mock.patch.object(test_import.sys, "platform", "win32"), \
                mock.patch.object(test_import.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "Target page"):
                test_import._open_browser_page(firefox, timeout_ms=5000)

        firefox.launch.assert_called_once_with(headless=True, timeout=5000)
        browser.close.assert_called_once_with()
        sleep.assert_not_called()

    def test_open_browser_page_stops_after_two_normal_attempts_off_windows(self):
        signature = (
            "Browser.new_page: Cannot read properties of undefined "
            "(reading '_page')"
        )
        browsers = [mock.Mock(), mock.Mock()]
        for browser in browsers:
            browser.new_page.side_effect = RuntimeError(signature)
        firefox = mock.Mock()
        firefox.launch.side_effect = browsers

        with mock.patch.object(test_import.sys, "platform", "linux"), \
                mock.patch.object(test_import.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "_page"):
                test_import._open_browser_page(firefox, timeout_ms=5000)

        self.assertEqual(firefox.launch.call_count, 2)
        for browser in browsers:
            browser.close.assert_called_once_with()
        sleep.assert_called_once_with(0.25)

    def test_documented_import_cli_retries_page_startup_in_a_subprocess(self):
        fake_playwright = r'''import os
import sys
from pathlib import Path

_launch_count = 0
_log_path = Path(os.environ["FAKE_PLAYWRIGHT_LOG"])
_xml = """<mxGraphModel><root>
<mxCell id="0"/><mxCell id="1" parent="0"/>
<mxCell id="A" parent="1" vertex="1"/>
<mxCell id="B" parent="1" vertex="1"/>
<mxCell id="E" parent="1" edge="1" source="A" target="B"/>
</root></mxGraphModel>"""

def _record(value):
    with _log_path.open("a", encoding="utf-8") as stream:
        stream.write(value + "\n")

class _Mouse:
    def move(self, *args): pass
    def down(self): pass
    def up(self): pass

class _Keyboard:
    def press(self, key): pass

class _Locator:
    @property
    def first(self): return self
    def bounding_box(self, timeout=None):
        return {"x": 0, "y": 0, "width": 40, "height": 20}
    def click(self, timeout=None): pass
    def count(self): return 0

class _Page:
    mouse = _Mouse()
    keyboard = _Keyboard()
    def locator(self, selector, **kwargs): return _Locator()
    def get_by_role(self, role, name=None): return _Locator()
    def set_default_timeout(self, timeout): pass
    def set_default_navigation_timeout(self, timeout): pass
    def goto(self, url, timeout=None, wait_until=None): pass
    def wait_for_function(self, expression, timeout=None): pass
    def wait_for_timeout(self, timeout): pass
    def set_input_files(self, selector, path, timeout=None): pass
    def evaluate(self, expression, arg=None):
        if expression == "window.__clip": return _xml
        if "querySelectorAll('.geDialog')" in expression: return []
        if "initialFitDiagram" in expression:
            return {"fullyFramed": True, "coverage": 1.0, "scale": 1.0}
        return None
    def screenshot(self, path, full_page=False, timeout=None):
        Path(path).write_bytes(b"fake-png")

class _Browser:
    def __init__(self, number): self.number = number
    def new_page(self, **kwargs):
        if self.number == 1:
            raise RuntimeError(
                "Browser.new_page: Cannot read properties of undefined "
                "(reading '_page')"
            )
        return _Page()
    def close(self): _record("close:%d" % self.number)

class _Firefox:
    executable_path = sys.executable
    def launch(self, **kwargs):
        global _launch_count
        _launch_count += 1
        _record("launch:%s" % kwargs.get("env", {}).get(
            "MOZ_DISABLE_CONTENT_SANDBOX", ""
        ))
        return _Browser(_launch_count)

class _Playwright:
    firefox = _Firefox()

class _Context:
    def __enter__(self): return _Playwright()
    def __exit__(self, exc_type, exc, traceback): return False

def sync_playwright(): return _Context()
'''
        fake_preflight = """import urllib.request
class _Response:
    status = 200
    def __enter__(self): return self
    def __exit__(self, exc_type, exc, traceback): return False
urllib.request.urlopen = lambda *args, **kwargs: _Response()
"""
        with tempfile.TemporaryDirectory(
            prefix="import-cli-", dir=SKILL_ROOT / "tests"
        ) as temp_dir:
            root = Path(temp_dir)
            fake_root = root / "fake-runtime"
            package = fake_root / "playwright"
            package.mkdir(parents=True)
            (package / "__init__.py").write_text("", encoding="utf-8")
            (package / "sync_api.py").write_text(
                fake_playwright, encoding="utf-8"
            )
            (fake_root / "sitecustomize.py").write_text(
                fake_preflight, encoding="utf-8"
            )
            input_path = root / "input.vsdx"
            output_path = root / "result.drawio"
            log_path = root / "playwright.log"
            input_path.write_bytes(b"vsdx")
            environment = os.environ.copy()
            environment.pop("MOZ_DISABLE_CONTENT_SANDBOX", None)
            environment["PYTHONPATH"] = str(fake_root)
            environment["FAKE_PLAYWRIGHT_LOG"] = str(log_path)

            completed = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(SKILL_ROOT / "scripts" / "test_import.py"),
                    str(input_path),
                    str(output_path),
                    "--url", "http://127.0.0.1:1",
                    "--expect-nodes", "2",
                    "--expect-edges", "1",
                    "--timeout", "5",
                ],
                cwd=SKILL_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=20,
            )

            self.assertEqual(
                completed.returncode,
                0,
                "stdout=%r stderr=%r" % (completed.stdout, completed.stderr),
            )
            self.assertTrue(output_path.is_file())
            self.assertTrue(output_path.with_suffix(".png").is_file())
            self.assertEqual(
                log_path.read_text(encoding="utf-8").splitlines(),
                ["launch:", "close:1", "launch:", "close:2"],
            )

    def test_rejects_input_output_and_screenshot_path_aliases_before_runtime(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.vsdx"
            output_path = root / "result.drawio"
            input_path.write_bytes(b"vsdx")
            output_path.write_text("existing", encoding="utf-8")
            hardlink = root / "result-hardlink.png"
            os.link(output_path, hardlink)
            cases = (
                (input_path, output_path, output_path, "same output"),
                (input_path, output_path, input_path, "same input"),
                (input_path, output_path, root / "RESULT.DRAWIO", "case alias"),
                (input_path, output_path, hardlink, "hardlink alias"),
            )
            for source, output, screenshot, name in cases:
                with self.subTest(case=name):
                    with mock.patch.object(test_import, "check_server") as server, \
                            mock.patch.object(test_import, "_load_playwright") as loader:
                        with self.assertRaises(test_import.ImportToolError) as failure:
                            test_import.import_vsdx(
                                source, output, screenshot=screenshot
                            )
                    self.assertEqual(failure.exception.code, 2)
                    server.assert_not_called()
                    loader.assert_not_called()

    def test_rejects_invalid_output_and_screenshot_paths_before_runtime(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.vsdx"
            input_path.write_bytes(b"vsdx")
            output_directory = root / "output-directory"
            screenshot_directory = root / "screenshot-directory"
            output_directory.mkdir()
            screenshot_directory.mkdir()
            cases = (
                (output_directory, None, "output directory"),
                (root / "result.drawio", screenshot_directory, "screenshot directory"),
                (root / "result.drawio", root / "result.gif", "unsupported screenshot"),
            )
            for output, screenshot, name in cases:
                with self.subTest(case=name):
                    with mock.patch.object(test_import, "check_server") as server, \
                            mock.patch.object(test_import, "_load_playwright") as loader:
                        with self.assertRaises(test_import.ImportToolError) as failure:
                            test_import.import_vsdx(
                                input_path, output, screenshot=screenshot
                            )
                    self.assertEqual(failure.exception.code, 2)
                    server.assert_not_called()
                    loader.assert_not_called()

    def test_error_dialog_empty_clipboard_and_invalid_xml_close_browser(self):
        cases = (
            (FakePage(import_dialogs=("Import failed" * 100,)), "对话框"),
            (FakePage(clipboard=""), "剪贴板"),
            (FakePage(clipboard="not xml"), "XML"),
        )
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            input_path.write_bytes(b"vsdx")
            for index, (page, fragment) in enumerate(cases):
                with self.subTest(case=fragment):
                    output_path = Path(temp_dir) / ("result-%d.drawio" % index)
                    browser = FakeBrowser(page)
                    with mock.patch.object(test_import, "check_server", return_value=True), \
                            mock.patch.object(
                                test_import,
                                "_load_playwright",
                                return_value=lambda: FakePlaywrightContext(browser),
                            ):
                        with self.assertRaises(test_import.ImportToolError) as failure:
                            test_import.import_vsdx(input_path, output_path)
                    self.assertIn(fragment, str(failure.exception))
                    self.assertTrue(browser.closed)

    def test_timeout_output_write_and_screenshot_failures_close_browser(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            input_path.write_bytes(b"vsdx")
            cases = (
                (FakePage(goto_error=TimeoutError("late")), Path(temp_dir) / "timeout.drawio", TimeoutError, None),
                (FakePage(), Path(temp_dir) / "missing" / "write.drawio", test_import.ImportToolError, 2),
                (FakePage(screenshot_error=OSError("capture")), Path(temp_dir) / "shot.drawio", test_import.ImportToolError, 2),
            )
            for page, output_path, error_type, expected_code in cases:
                with self.subTest(case=output_path.name):
                    browser = FakeBrowser(page)
                    with mock.patch.object(test_import, "check_server", return_value=True), \
                            mock.patch.object(
                                test_import,
                                "_load_playwright",
                                return_value=lambda: FakePlaywrightContext(browser),
                            ):
                        with self.assertRaises(error_type) as failure:
                            test_import.import_vsdx(
                                input_path, output_path, timeout=7.5
                            )
                    if expected_code is not None:
                        self.assertEqual(failure.exception.code, expected_code)
                    self.assertTrue(browser.closed)
                    goto_events = [event for event in page.events if event[0] == "goto"]
                    self.assertEqual(goto_events[0][2], 7500)
                    if page.goto_error is None:
                        self.assertEqual(browser.launch_kwargs["timeout"], 7500)
                        input_event = next(
                            event for event in page.events if event[0] == "input"
                        )
                        self.assertEqual(input_event[3], 7500)

    def test_success_closes_export_dialog_before_screenshot_and_browser_afterward(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            output_path = Path(temp_dir) / "result.drawio"
            screenshot = Path(temp_dir) / "custom.png"
            input_path.write_bytes(b"vsdx")
            page = FakePage()
            result, browser = self.run_import(
                page,
                output_path,
                input_path=input_path,
                screenshot=screenshot,
                expect_nodes=2,
                expect_edges=1,
            )

        self.assertEqual((result.node_count, result.edge_count), (2, 1))
        self.assertTrue(browser.closed)
        screenshot_event = next(event for event in page.events if event[0] == "screenshot")
        self.assertEqual(screenshot_event[1], str(screenshot))
        self.assertEqual(screenshot_event[3], 0)
        self.assertEqual(screenshot_event[4], 120000)
        self.assertLess(
            next(i for i, event in enumerate(page.events) if event[:2] == ("key", "Escape")),
            next(i for i, event in enumerate(page.events) if event[0] == "screenshot"),
        )
        self.assertLess(
            next(i for i, event in enumerate(page.events) if event[0] == "fit_diagram"),
            next(i for i, event in enumerate(page.events) if event[0] == "screenshot"),
        )

    def test_unframed_diagram_is_a_failure_and_prevents_screenshot(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            output_path = Path(temp_dir) / "result.drawio"
            input_path.write_bytes(b"vsdx")
            page = FakePage(fit_result={
                "fullyFramed": False,
                "coverage": 0.0,
                "scale": 0.35,
            })
            browser = FakeBrowser(page)
            with mock.patch.object(test_import, "check_server", return_value=True), \
                    mock.patch.object(
                        test_import,
                        "_load_playwright",
                        return_value=lambda: FakePlaywrightContext(browser),
                    ):
                with self.assertRaises(test_import.ImportToolError) as failure:
                    test_import.import_vsdx(input_path, output_path)

        self.assertIn("视口", str(failure.exception))
        self.assertTrue(browser.closed)
        self.assertFalse(any(event[0] == "screenshot" for event in page.events))

    def test_stubborn_export_dialog_is_a_failure_and_prevents_screenshot(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            input_path = Path(temp_dir) / "input.vsdx"
            output_path = Path(temp_dir) / "result.drawio"
            input_path.write_bytes(b"vsdx")
            page = FakePage(dismisses_dialogs=False)
            browser = FakeBrowser(page)
            with mock.patch.object(test_import, "check_server", return_value=True), \
                    mock.patch.object(
                        test_import,
                        "_load_playwright",
                        return_value=lambda: FakePlaywrightContext(browser),
                    ):
                with self.assertRaises(test_import.ImportToolError) as failure:
                    test_import.import_vsdx(input_path, output_path)

        self.assertIn("对话框", str(failure.exception))
        self.assertTrue(browser.closed)
        self.assertFalse(any(event[0] == "screenshot" for event in page.events))

    def test_batch_derives_outputs_under_requested_directory_and_reports_any_failure(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "outputs"
            first = root / "a..b.vsdx"
            second = root / "..second.vsdx"
            first.write_bytes(b"1")
            second.write_bytes(b"2")
            calls = []

            def fake_import(input_path, output_path, **kwargs):
                calls.append((Path(input_path), Path(output_path)))
                if Path(input_path) == second:
                    raise test_import.ImportToolError("bad import", code=1)

            with mock.patch.object(batch_import, "import_vsdx", side_effect=fake_import):
                code = batch_import.main(
                    [str(first), str(second), "--output-dir", str(output_dir)]
                )

        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 2)
        for _, output_path in calls:
            self.assertTrue(output_path.parent == output_dir)
            self.assertNotIn("..", output_path.name)

    def test_batch_avoids_case_insensitive_output_name_collisions(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            root = Path(temp_dir)
            first_dir = root / "first"
            second_dir = root / "second"
            third_dir = root / "third"
            first_dir.mkdir()
            second_dir.mkdir()
            third_dir.mkdir()
            first = first_dir / "a.vsdx"
            second = second_dir / "a-2.vsdx"
            third = third_dir / "A.vsdx"
            first.write_bytes(b"1")
            second.write_bytes(b"2")
            third.write_bytes(b"3")
            output_dir = root / "outputs"
            output_dir.mkdir()
            (output_dir / "a.png").write_bytes(b"existing screenshot")
            outputs = []

            def fake_import(input_path, output_path, **kwargs):
                outputs.append(Path(output_path))

            with mock.patch.object(batch_import, "import_vsdx", side_effect=fake_import):
                code = batch_import.main(
                    [
                        str(first), str(second), str(third),
                        "--output-dir", str(output_dir),
                    ]
                )

        self.assertEqual(code, 0)
        self.assertEqual(len(outputs), 3)
        names = [output.name.casefold() for output in outputs]
        self.assertEqual(len(set(names)), len(names))
        self.assertNotIn("a.drawio", names)

    def test_batch_output_directory_file_error_returns_two(self):
        with tempfile.TemporaryDirectory(prefix="import-tools-", dir=SKILL_ROOT / "tests") as temp_dir:
            root = Path(temp_dir)
            input_path = root / "input.vsdx"
            output_file = root / "output-file"
            input_path.write_bytes(b"vsdx")
            output_file.write_text("not a directory", encoding="utf-8")
            code = batch_import.main(
                [str(input_path), "--output-dir", str(output_file)]
            )
        self.assertEqual(code, 2)


class DrawioAcceptanceHelperTests(unittest.TestCase):
    @staticmethod
    def case_data():
        return {
            "page": {"width": 8.5, "height": 11},
            "nodes": [
                {"id": "A", "x": 1, "y": 9, "w": 1, "h": 1},
                {"id": "B", "x": 3, "y": 9, "w": 1, "h": 1},
                {
                    "id": "R", "x": 1, "y": 7, "w": 1, "h": 1,
                    "rotation": 30,
                },
            ],
            "edges": [
                {"id": "auto", "from": "A", "to": "B"},
                {
                    "id": "routed", "from": "R", "to": "B",
                    "fromSide": "top", "toSide": "bottom",
                    "points": [[2, 8]],
                },
            ],
        }

    def acceptance(self):
        self.assertTrue(
            ACCEPTANCE_PATH.is_file(),
            "acceptance runner must exist before its helpers can be tested",
        )
        return load_acceptance()

    def test_expected_anchor_rotates_visual_side_about_node_center(self):
        acceptance = self.acceptance()
        node = {
            "id": "R", "x": 10.0, "y": 20.0,
            "w": 4.0, "h": 2.0, "rotation": 90,
        }
        expected = {
            "left": (10.0, 18.0),
            "right": (10.0, 22.0),
            "top": (9.0, 20.0),
            "bottom": (11.0, 20.0),
        }

        for side, wanted in expected.items():
            with self.subTest(side=side):
                actual = acceptance._anchor(node, side)
                self.assertAlmostEqual(actual[0], wanted[0], places=9)
                self.assertAlmostEqual(actual[1], wanted[1], places=9)

    def test_parser_uses_userobject_ids_and_vsdx_ids(self):
        acceptance = self.acceptance()
        diagram = acceptance.parse_drawio_xml_text(ACCEPTANCE_XML)

        self.assertEqual(diagram.page_width_px, 8.5 * acceptance.PX)
        self.assertEqual(diagram.page_height_px, 11.0 * acceptance.PX)
        self.assertEqual([node.id for node in diagram.nodes], [
            "wrapper-a", "wrapper-b", "wrapper-r",
        ])
        self.assertEqual([node.vsdx_id for node in diagram.nodes], [1, 2, 3])
        self.assertEqual([edge.vsdx_id for edge in diagram.edges], [4, 5])
        self.assertEqual(
            (diagram.edges[0].source, diagram.edges[0].target),
            ("wrapper-a", "wrapper-b"),
        )

    def test_parser_validates_explicit_page_width_and_height(self):
        acceptance = self.acceptance()
        explicit = ACCEPTANCE_XML.replace(
            '<mxGraphModel pageHeight="1117.6">',
            '<mxGraphModel pageWidth="1422.4" pageHeight="2336.8">',
        )
        diagram = acceptance.parse_drawio_xml_text(explicit)
        self.assertEqual(diagram.page_width_px, 1422.4)
        self.assertEqual(diagram.page_height_px, 2336.8)

        for attribute in ('pageWidth="0"', 'pageHeight="-1"'):
            with self.subTest(attribute=attribute):
                invalid = explicit.replace(
                    'pageWidth="1422.4"' if attribute.startswith("pageWidth")
                    else 'pageHeight="2336.8"',
                    attribute,
                )
                with self.assertRaises(acceptance.AcceptanceError):
                    acceptance.parse_drawio_xml_text(invalid)

    def test_page_contract_is_strict_by_default_and_tiled_only_when_explicit(self):
        acceptance = self.acceptance()
        diagram = acceptance.parse_drawio_xml_text(ACCEPTANCE_XML)
        data = self.case_data()
        acceptance.assert_page_contract(data, diagram, tolerance=1.0)

        larger = dict(data)
        larger["page"] = {"width": 14.0, "height": 23.0}
        with self.assertRaises(acceptance.AcceptanceError) as strict:
            acceptance.assert_page_contract(larger, diagram, tolerance=1.0)
        self.assertIn("page size", str(strict.exception))

        acceptance.assert_page_contract(
            larger, diagram, tolerance=1.0, allow_tiled_paper=True
        )

    def test_page_contract_rejects_rotated_nodes_and_edge_terminals_outside_source(self):
        acceptance = self.acceptance()
        rotated_outside = ACCEPTANCE_XML.replace(
            'x="50.8" y="355.6" width="101.6" height="101.6"',
            'x="-50" y="0" width="101.6" height="101.6"',
        )
        with self.assertRaises(acceptance.AcceptanceError) as node_failure:
            acceptance.assert_page_contract(
                self.case_data(),
                acceptance.parse_drawio_xml_text(rotated_outside),
                tolerance=1.0,
            )
        self.assertIn("node", str(node_failure.exception))
        self.assertIn("page bounds", str(node_failure.exception))

        terminal_outside = ACCEPTANCE_XML.replace(
            "exitX=1;", "exitX=1;exitDx=-2000;", 1
        )
        with self.assertRaises(acceptance.AcceptanceError) as edge_failure:
            acceptance.assert_page_contract(
                self.case_data(),
                acceptance.parse_drawio_xml_text(terminal_outside),
                tolerance=1.0,
            )
        self.assertIn("edge", str(edge_failure.exception))
        self.assertIn("page bounds", str(edge_failure.exception))

    def test_contract_checks_auto_and_explicit_sides_with_independent_target(self):
        acceptance = self.acceptance()
        diagram = acceptance.parse_drawio_xml_text(ACCEPTANCE_XML)

        acceptance.assert_case_matches(self.case_data(), diagram, tolerance=1.0)
        self.assertEqual(
            acceptance.expected_sides(
                self.case_data()["nodes"][0],
                self.case_data()["nodes"][1],
                self.case_data()["edges"][0],
            ),
            ("right", "left"),
        )
        self.assertEqual(diagram.edges[1].points, (
            (76.2, 362.4059),
            (203.2, 304.8),
        ))
        target, evidence = acceptance.terminal_evidence(
            diagram, diagram.edges[1], source=False
        )
        self.assertEqual(evidence, "entry constraint")
        self.assertEqual(target, (304.8, 254.0))

    def test_contract_rejects_dangling_binding_and_endpoint_drift(self):
        acceptance = self.acceptance()
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.parse_drawio_xml_text(
                ACCEPTANCE_XML.replace('target="wrapper-b"', 'target="missing"', 1)
            )

        drifted = acceptance.parse_drawio_xml_text(
            ACCEPTANCE_XML.replace("exitX=1;", "exitX=0.9;", 1)
        )
        with self.assertRaises(acceptance.AcceptanceError) as failure:
            acceptance.assert_case_matches(
                self.case_data(), drifted, tolerance=1.0
            )
        self.assertIn("source", str(failure.exception))

    def test_node_geometry_allows_independent_drawio_rounding(self):
        acceptance = self.acceptance()
        data = {
            "page": {"width": 8.5, "height": 11},
            "nodes": [
                {"id": "D", "x": 5, "y": 4, "w": 1.5, "h": 0.75},
            ],
            "edges": [],
        }
        xml = """<mxGraphModel pageHeight="1117.6"><root>
          <mxCell id="0"/><mxCell id="1" parent="0"/>
          <mxCell id="D" parent="1" vertex="1" style="vsdxID=1;">
            <mxGeometry x="431" y="673" width="152" height="76" as="geometry"/>
          </mxCell>
        </root></mxGraphModel>"""

        acceptance.assert_case_matches(
            data, acceptance.parse_drawio_xml_text(xml), tolerance=1.0
        )

    def test_raw_deflate_stencils_check_cylinder_and_arrow_directions(self):
        acceptance = self.acceptance()

        def stencil_style(xml):
            quoted = urllib.parse.quote(xml, safe="~()*!.'").encode("ascii")
            compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
            compressed = compressor.compress(quoted) + compressor.flush()
            payload = base64.b64encode(compressed).decode("ascii")
            return "vsdxID={};shape=stencil({});".format(
                xml.split("data-id=\"")[1].split("\"", 1)[0], payload
            )

        cylinder = (
            '<shape data-id="5"><foreground><path>'
            '<move x="0" y="90"/>'
            '<arc rx="143.39" ry="159.32" x="100" y="90" '
            'large-arc-flag="0" sweep-flag="0"/>'
            '<line x="100" y="10"/>'
            '<arc rx="141.75" ry="157.5" x="0" y="10" '
            'large-arc-flag="0" sweep-flag="0"/>'
            '<line x="0" y="90"/>'
            '</path></foreground></shape>'
        )
        up = (
            '<shape data-id="15"><foreground><path>'
            '<move x="20" y="100"/><line x="20" y="55"/>'
            '<line x="0" y="55"/><line x="50" y="0"/>'
            '<line x="100" y="55"/><line x="80" y="55"/>'
            '<line x="80" y="100"/><line x="20" y="100"/>'
            '</path></foreground></shape>'
        )
        down = (
            '<shape data-id="16"><foreground><path>'
            '<move x="20" y="0"/><line x="80" y="0"/>'
            '<line x="80" y="45"/><line x="100" y="45"/>'
            '<line x="50" y="100"/><line x="0" y="45"/>'
            '<line x="20" y="45"/><line x="20" y="0"/>'
            '</path></foreground></shape>'
        )
        xml = (
            '<mxGraphModel pageHeight="1117.6"><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="c" parent="1" vertex="1" style="{}">'
            '<mxGeometry x="0" y="0" width="100" height="100" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="u" parent="1" vertex="1" style="{}">'
            '<mxGeometry x="110" y="0" width="100" height="100" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="d" parent="1" vertex="1" style="{}">'
            '<mxGeometry x="220" y="0" width="100" height="100" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel>'
        ).format(
            stencil_style(cylinder), stencil_style(up), stencil_style(down)
        )
        diagram = acceptance.parse_drawio_xml_text(xml)

        decoded = acceptance.decode_stencil(diagram.nodes[0].style)
        self.assertEqual(decoded.tag, "shape")
        acceptance.assert_showcase_stencils(diagram)

        ambiguous_up = (
            '<shape data-id="15"><foreground><path>'
            '<move x="50" y="0"/><line x="50" y="100"/>'
            '</path></foreground></shape>'
        )
        ambiguous_down = ambiguous_up.replace('data-id="15"', 'data-id="16"')
        ambiguous_xml = (
            '<mxGraphModel pageHeight="1117.6"><root>'
            '<mxCell id="0"/><mxCell id="1" parent="0"/>'
            '<mxCell id="c" parent="1" vertex="1" style="{}">'
            '<mxGeometry x="0" y="0" width="100" height="100" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="u" parent="1" vertex="1" style="{}">'
            '<mxGeometry x="110" y="0" width="100" height="100" as="geometry"/>'
            '</mxCell>'
            '<mxCell id="d" parent="1" vertex="1" style="{}">'
            '<mxGeometry x="220" y="0" width="100" height="100" as="geometry"/>'
            '</mxCell>'
            '</root></mxGraphModel>'
        ).format(
            stencil_style(cylinder),
            stencil_style(ambiguous_up),
            stencil_style(ambiguous_down),
        )
        with self.assertRaises(acceptance.AcceptanceError):
            acceptance.assert_showcase_stencils(
                acceptance.parse_drawio_xml_text(ambiguous_xml)
            )

    def test_parser_accepts_direct_geometry_route_points(self):
        acceptance = self.acceptance()
        xml = """<mxGraphModel pageHeight="1117.6"><root>
          <mxCell id="0"/><mxCell id="1" parent="0"/>
          <mxCell id="A" parent="1" vertex="1" style="vsdxID=1;">
            <mxGeometry x="0" y="0" width="100" height="100" as="geometry"/>
          </mxCell>
          <mxCell id="B" parent="1" vertex="1" style="vsdxID=2;">
            <mxGeometry x="200" y="0" width="100" height="100" as="geometry"/>
          </mxCell>
          <mxCell id="E" parent="1" edge="1" source="A" target="B" style="vsdxID=3;">
            <mxGeometry relative="1" as="geometry">
              <mxPoint x="100" y="25"/>
              <mxPoint x="150" y="75" as="point"/>
            </mxGeometry>
          </mxCell>
        </root></mxGraphModel>"""

        diagram = acceptance.parse_drawio_xml_text(xml)

        self.assertEqual(diagram.edges[0].points, ((100.0, 25.0), (150.0, 75.0)))

    def test_unit_only_avoids_service_and_playwright(self):
        acceptance = self.acceptance()
        with tempfile.TemporaryDirectory(
            prefix="drawio-unit-only-", dir=SKILL_ROOT / "tests"
        ) as temp_dir:
            with mock.patch.object(
                        acceptance,
                        "assert_case_matches",
                        wraps=acceptance.assert_case_matches,
                    ) as contract, \
                    mock.patch.object(acceptance, "_load_playwright") as loader, \
                    mock.patch.object(urllib.request, "urlopen") as urlopen:
                code = acceptance.main([
                    "--unit-only",
                    "--skill-root", str(SKILL_ROOT),
                    "--output-dir", temp_dir,
                ])
        self.assertEqual(code, 0)
        contract.assert_called_once()
        loader.assert_not_called()
        urlopen.assert_not_called()

    def test_runner_inherits_external_cwd_and_removes_stale_case_artifacts(self):
        acceptance = self.acceptance()
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(acceptance.subprocess, "run", return_value=completed) as run:
            acceptance.run_command(["tool", "argument"], timeout=1.0)
        self.assertIsNone(run.call_args.kwargs.get("cwd"))

        with tempfile.TemporaryDirectory(
            prefix="drawio-stale-", dir=SKILL_ROOT / "tests"
        ) as temp_dir:
            output_dir = Path(temp_dir)
            stale = [
                output_dir / "login-flow.vsdx",
                output_dir / "login-flow.drawio",
                output_dir / "login-flow.png",
            ]
            for path in stale:
                path.write_bytes(b"stale")
            with mock.patch.object(acceptance, "run_command"):
                with self.assertRaises(acceptance.AcceptanceError):
                    acceptance.run_case(
                        SKILL_ROOT, output_dir, "login-flow",
                        "http://127.0.0.1:1", 1.0,
                    )
            self.assertFalse(any(path.exists() for path in stale))

    def test_run_case_passes_source_page_dimensions_to_strict_layout_contract(self):
        acceptance = self.acceptance()
        commands = []

        def fake_run(command, timeout):
            commands.append([str(item) for item in command])
            target = Path(command[3])
            if str(command[1]).endswith("vsdx_gen.py"):
                target.write_bytes(b"vsdx")
            elif str(command[1]).endswith("test_import.py"):
                target.write_text("<mxGraphModel/>", encoding="utf-8")
                screenshot_index = command.index("--screenshot") + 1
                Path(command[screenshot_index]).write_bytes(b"png")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory(
            prefix="drawio-page-command-", dir=SKILL_ROOT / "tests"
        ) as temp_dir, \
                mock.patch.object(acceptance, "run_command", side_effect=fake_run), \
                mock.patch.object(acceptance, "load_drawio", return_value=object()), \
                mock.patch.object(acceptance, "assert_case_matches") as contract, \
                mock.patch.object(acceptance, "assert_png_screenshot"):
            acceptance.run_case(
                SKILL_ROOT, Path(temp_dir), "login-flow",
                "http://127.0.0.1:8080", 10.0,
            )

        verifier = next(
            command for command in commands
            if command[1].endswith("verify_layout.py")
        )
        self.assertEqual(
            verifier[-5:],
            [
                "--expect-page-width-in", "8.5",
                "--expect-page-height-in", "11.0",
                "--allow-tiled-paper",
            ],
        )
        contract.assert_called_once_with(
            mock.ANY, mock.ANY, tolerance=1.0, allow_tiled_paper=True
        )

    def test_png_contract_rejects_header_only_and_bad_crc(self):
        acceptance = self.acceptance()

        def chunk(kind, payload):
            body = kind + payload
            return (
                struct.pack(">I", len(payload)) + body +
                struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        signature = b"\x89PNG\r\n\x1a\n"
        ihdr = chunk(
            b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
        )
        idat = chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\xff"))
        iend = chunk(b"IEND", b"")
        with tempfile.TemporaryDirectory(
            prefix="drawio-png-", dir=SKILL_ROOT / "tests"
        ) as temp_dir:
            root = Path(temp_dir)
            valid = root / "valid.png"
            header_only = root / "header-only.png"
            bad_crc = root / "bad-crc.png"
            valid.write_bytes(signature + ihdr + idat + iend)
            header_only.write_bytes((signature + ihdr)[:24])
            damaged = bytearray(signature + ihdr + idat + iend)
            damaged[-1] ^= 0x01
            bad_crc.write_bytes(damaged)

            acceptance.assert_png_screenshot(valid)
            for path in (header_only, bad_crc):
                with self.subTest(path=path.name):
                    with self.assertRaises(acceptance.AcceptanceError):
                        acceptance.assert_png_screenshot(path)

    def test_live_browser_launch_retries_only_transient_ebusy(self):
        acceptance = self.acceptance()
        browser = object()
        firefox = mock.Mock()
        firefox.launch.side_effect = [
            RuntimeError("BrowserType.launch: spawn EBUSY"),
            browser,
        ]
        with mock.patch.object(acceptance.time, "sleep") as sleep:
            result = acceptance._launch_firefox(firefox, timeout_ms=120000)
        self.assertIs(result, browser)
        self.assertEqual(firefox.launch.call_count, 2)
        sleep.assert_called_once_with(0.5)

        firefox = mock.Mock()
        firefox.launch.side_effect = RuntimeError("executable missing")
        with mock.patch.object(acceptance.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "executable missing"):
                acceptance._launch_firefox(firefox, timeout_ms=120000)
        self.assertEqual(firefox.launch.call_count, 1)
        sleep.assert_not_called()

        firefox = mock.Mock()
        firefox.launch.side_effect = RuntimeError("spawn EBUSY")
        with mock.patch.object(acceptance.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "EBUSY"):
                acceptance._launch_firefox(firefox, timeout_ms=120000)
        self.assertEqual(firefox.launch.call_count, 3)
        self.assertEqual(
            [call.args for call in sleep.call_args_list],
            [(0.5,), (1.0,)],
        )

    def test_live_movement_browser_uses_the_same_windows_page_fallback(self):
        acceptance = self.acceptance()
        browsers = [mock.Mock(), mock.Mock(), mock.Mock()]
        for browser in browsers[:2]:
            browser.new_page.side_effect = RuntimeError(
                acceptance.PAGE_STARTUP_ERROR
            )
        page = object()
        browsers[2].new_page.return_value = page
        firefox = mock.Mock()
        firefox.launch.side_effect = browsers

        with mock.patch.object(acceptance.sys, "platform", "win32"), \
                mock.patch.dict(acceptance.os.environ, {"KEEP": "1"}, clear=True), \
                mock.patch.object(acceptance.time, "sleep") as sleep:
            result = acceptance._open_browser_page(firefox, timeout_ms=120000)

        self.assertEqual(result, (browsers[2], page))
        self.assertEqual(
            firefox.launch.call_args_list[:2],
            [
                mock.call(headless=True, timeout=120000),
                mock.call(headless=True, timeout=120000),
            ],
        )
        self.assertEqual(
            firefox.launch.call_args_list[2].kwargs["env"],
            {"KEEP": "1", "MOZ_DISABLE_CONTENT_SANDBOX": "1"},
        )
        for browser in browsers[:2]:
            browser.close.assert_called_once_with()
        self.assertEqual(
            [call.args for call in sleep.call_args_list],
            [(0.25,), (0.5,)],
        )

    def test_ecommerce_order_distribution_example_contract(self):
        acceptance = self.acceptance()
        expected_example_names = (
            "login-flow",
            "shapes-showcase",
            "ecommerce-order-distribution",
            "stress-flow",
        )
        self.assertEqual(acceptance.EXAMPLE_NAMES, expected_example_names)
        self.assertEqual(
            {path.name for path in (SKILL_ROOT / "examples").glob("*.json")},
            {
                "login-flow.json",
                "shapes-showcase.json",
                "ecommerce-order-distribution.json",
                "stress-flow.json",
            },
        )

        data = json.loads(
            (
                SKILL_ROOT
                / "examples"
                / "ecommerce-order-distribution.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(
            data["page"],
            {
                "name": "Page-1",
                "title": "电商平台订单智能分发流程",
                "width": 14,
                "height": 23,
            },
        )
        expected_nodes = {
            "A": ("多渠道订单接入", "rect"),
            "B": ("订单校验与标准化", "rect"),
            "C": ("库存与仓网匹配", "rect"),
            "D": ("地址与运力匹配", "rect"),
            "E": ("订单路由引擎", "rect"),
            "F1": ("区域中心仓候选", "rect"),
            "F2": ("前置仓或门店候选", "rect"),
            "F3": ("供应商直发候选", "rect"),
            "G": ("成本、时效与容量评分", "rect"),
            "H": ("首选方案满足履约承诺？", "diamond"),
            "I": ("拆单并锁定兜底资源", "rect"),
            "J": ("锁定首选库存与运力", "rect"),
            "K": ("生成并下发分发计划", "rect"),
            "L1": ("中心仓履约", "rect"),
            "L2": ("门店或前置仓履约", "rect"),
            "L3": ("供应商直发", "rect"),
            "M": ("回传订单、库存与物流状态", "rect"),
        }
        nodes = {node["id"]: node for node in data["nodes"]}
        self.assertEqual(
            {
                node_id: (node["text"], node["type"])
                for node_id, node in nodes.items()
            },
            expected_nodes,
        )
        self.assertEqual(
            (nodes["H"]["fill"], nodes["H"]["stroke"]),
            ("#FFF2CC", "#D6B656"),
        )
        self.assertEqual(
            (nodes["I"]["fill"], nodes["I"]["stroke"]),
            ("#F8CECC", "#B85450"),
        )
        self.assertEqual(
            (nodes["J"]["fill"], nodes["J"]["stroke"]),
            ("#D5E8D4", "#82B366"),
        )

        expected_edges = [
            ("e1", "A", "B", None, None, None, None),
            ("e2", "B", "C", "商品与数量", "left", "top", None),
            ("e3", "B", "D", "地址与时效", "right", "top", None),
            ("e4", "C", "E", "库存候选", None, None, None),
            ("e5", "D", "E", "运力约束", None, None, None),
            ("e6", "E", "F1", None, None, None, None),
            ("e7", "E", "F2", None, None, None, None),
            ("e8", "E", "F3", None, None, None, None),
            ("e9", "F1", "G", None, None, None, None),
            ("e10", "F2", "G", None, None, None, None),
            ("e11", "F3", "G", None, None, None, None),
            ("e12", "G", "H", None, None, None, None),
            ("e13", "H", "I", "否", "left", "top", "#B85450"),
            ("e14", "H", "J", "是", "right", "top", "#82B366"),
            ("e15", "I", "K", "兜底方案", None, None, None),
            ("e16", "J", "K", "首选方案", None, None, None),
            ("e17", "K", "L1", None, None, None, None),
            ("e18", "K", "L2", None, None, None, None),
            ("e19", "K", "L3", None, None, None, None),
            ("e20", "L1", "M", None, None, None, None),
            ("e21", "L2", "M", None, None, None, None),
            ("e22", "L3", "M", None, None, None, None),
        ]
        self.assertEqual(
            [
                (
                    edge["id"],
                    edge["from"],
                    edge["to"],
                    edge.get("label"),
                    edge.get("fromSide"),
                    edge.get("toSide"),
                    edge.get("lineColor"),
                )
                for edge in data["edges"]
            ],
            expected_edges,
        )

    def test_shapes_showcase_keeps_known_routes_clear(self):
        data = json.loads(
            (SKILL_ROOT / "examples" / "shapes-showcase.json").read_text(
                encoding="utf-8"
            )
        )
        edges = {edge["id"]: edge for edge in data["edges"]}

        self.assertEqual(
            edges["e3"]["points"],
            [[5.2, 5.45], [5.2, 4.2], [5.4, 4.2]],
        )
        self.assertEqual(edges["e4"]["points"], [[7.0, 4.2], [1.95, 4.2]])

    def test_stress_row_wrap_approaches_the_target_top_from_above(self):
        data = json.loads(
            (SKILL_ROOT / "examples" / "stress-flow.json").read_text(
                encoding="utf-8"
            )
        )
        target = next(node for node in data["nodes"] if node["id"] == "n41")
        edge = next(
            edge for edge in data["edges"]
            if edge["from"] == "n40" and edge["to"] == "n41"
        )

        self.assertEqual(edge["toSide"], "top")
        self.assertGreater(
            edge["points"][-1][1], target["y"] + target["h"] / 2.0
        )


if __name__ == "__main__":
    unittest.main()
