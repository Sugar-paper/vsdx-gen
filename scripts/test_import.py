#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Import a VSDX through draw.io and validate the exported XML.

The generator remains standard-library-only.  Playwright and Firefox are
checked only when this optional integration tool is executed.
"""

import argparse
from dataclasses import dataclass
import math
from pathlib import Path
import sys
import time
import unicodedata
import urllib.request
from xml.etree import ElementTree as ET


DEFAULT_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 120.0


class ImportToolError(RuntimeError):
    """A user-facing import failure with a stable process exit code."""

    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExportSummary:
    node_count: int
    edge_count: int


class _ArgumentExit(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ImportToolError("参数错误: %s" % message, code=2)

    def exit(self, status=0, message=None):
        if message:
            self._print_message(message, sys.stderr)
        raise _ArgumentExit(status)


def _display(value, limit=300):
    parts = []
    for character in str(value):
        category = unicodedata.category(character)
        if category in ("Cc", "Cs"):
            codepoint = ord(character)
            if codepoint <= 0xFF:
                parts.append("\\x%02x" % codepoint)
            elif codepoint <= 0xFFFF:
                parts.append("\\u%04x" % codepoint)
            else:
                parts.append("\\U%08x" % codepoint)
        else:
            parts.append(character)
    text = "".join(parts)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _direct_child(element, name):
    for child in element:
        if child.tag == name:
            return child
    return None


def _find_model(root):
    if root.tag == "mxGraphModel":
        return root
    if root.tag == "mxfile":
        diagrams = [child for child in root if child.tag == "diagram"]
        if len(diagrams) > 1:
            raise ImportToolError(
                "暂不支持多页 draw.io XML；请导出单页文档", code=1
            )
        if len(diagrams) == 1:
            models = [
                child for child in diagrams[0] if child.tag == "mxGraphModel"
            ]
        else:
            models = []
        if len(models) == 1:
            return models[0]
    raise ImportToolError(
        "导出的 XML 不是 draw.io 文档: 需要根级 mxGraphModel 或 "
        "mxfile/diagram/mxGraphModel",
        code=1,
    )


def _expected_count(value, name):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ImportToolError("%s 必须是非负整数" % name, code=2)


def validate_exported_xml(xml, expect_nodes=None, expect_edges=None):
    """Parse exported XML and enforce node, edge, and binding contracts."""
    _expected_count(expect_nodes, "expect_nodes")
    _expected_count(expect_edges, "expect_edges")
    if not isinstance(xml, (str, bytes)) or not xml:
        raise ImportToolError("导出 XML 为空", code=1)
    try:
        root = ET.fromstring(xml)
    except (ET.ParseError, TypeError, ValueError) as error:
        raise ImportToolError(
            "导出 XML 无法解析: %s" % _display(error), code=1
        ) from None

    model = _find_model(root)
    graph_root = _direct_child(model, "root")
    if graph_root is None:
        raise ImportToolError("导出 XML 缺少 mxGraphModel/root", code=1)

    nodes = []
    edges = []

    def visit(element, wrapper_id=None):
        next_wrapper_id = wrapper_id
        if element.tag in ("UserObject", "object"):
            next_wrapper_id = element.get("id") or wrapper_id
        if element.tag == "mxCell":
            cell_id = element.get("id") or wrapper_id
            if element.get("vertex") == "1":
                nodes.append((element, cell_id))
            elif element.get("edge") == "1":
                edges.append((element, cell_id))
        for child in element:
            visit(child, next_wrapper_id)

    visit(graph_root)

    if not nodes:
        raise ImportToolError("导出 XML 没有节点", code=1)
    node_ids = {}
    for node, node_id in nodes:
        if not node_id:
            raise ImportToolError("节点缺少 id", code=1)
        if node_id in node_ids:
            raise ImportToolError("节点 id 重复: %s" % _display(node_id), code=1)
        node_ids[node_id] = node

    for edge, raw_edge_id in edges:
        edge_id = _display(raw_edge_id or "<missing>")
        source = edge.get("source")
        target = edge.get("target")
        if not source:
            raise ImportToolError("边 %s 的 source 未绑定" % edge_id, code=1)
        if not target:
            raise ImportToolError("边 %s 的 target 未绑定" % edge_id, code=1)
        if source not in node_ids:
            raise ImportToolError(
                "边 %s 的 source 引用不存在: %s" % (edge_id, _display(source)),
                code=1,
            )
        if target not in node_ids:
            raise ImportToolError(
                "边 %s 的 target 引用不存在: %s" % (edge_id, _display(target)),
                code=1,
            )

    summary = ExportSummary(len(nodes), len(edges))
    if expect_nodes is not None and summary.node_count != expect_nodes:
        raise ImportToolError(
            "节点数不匹配: 期望 %d，实际 %d"
            % (expect_nodes, summary.node_count),
            code=1,
        )
    if expect_edges is not None and summary.edge_count != expect_edges:
        raise ImportToolError(
            "边数不匹配: 期望 %d，实际 %d"
            % (expect_edges, summary.edge_count),
            code=1,
        )
    return summary


def check_server(url=DEFAULT_URL, timeout=3.0):
    """Return whether the draw.io webapp responds at ``url``."""
    try:
        with urllib.request.urlopen(
            url.rstrip("/") + "/index.html", timeout=timeout
        ) as response:
            return 200 <= response.status < 400
    except (OSError, ValueError, TimeoutError):
        return False


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except (ImportError, OSError) as error:
        raise ImportToolError(
            "缺少或无法加载 Playwright: %s" % _display(error), code=2
        ) from None
    return sync_playwright


def _launch_firefox(firefox, timeout_ms):
    retry_delays = (0.5, 1.0)
    for attempt in range(3):
        try:
            return firefox.launch(headless=True, timeout=timeout_ms)
        except Exception as error:
            if "EBUSY" not in str(error) or attempt >= len(retry_delays):
                raise
            time.sleep(retry_delays[attempt])
    raise AssertionError("unreachable")


def open_file_menu(page, timeout_ms):
    """draw.io menus open on mousedown rather than a normal click."""
    element = page.locator(".geMenubar a.geItem", has_text="File")
    box = element.bounding_box(timeout=timeout_ms)
    if not box:
        raise ImportToolError("找不到 draw.io File 菜单", code=1)
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(300)
    page.mouse.up()
    page.wait_for_timeout(500)


def click_menu_item(page, text, timeout_ms):
    page.locator("td.mxPopupMenuItem", has_text=text).first.click(
        timeout=timeout_ms
    )
    page.wait_for_timeout(800)


def _dialogs(page):
    values = page.evaluate(
        """() => [...document.querySelectorAll('.geDialog')]
            .filter(e => {
                const style = getComputedStyle(e);
                return style.display !== 'none' && style.visibility !== 'hidden';
            })
            .map(e => e.innerText).filter(t => t)"""
    )
    return tuple(str(value).strip() for value in (values or ()) if str(value).strip())


def _dismiss_dialogs(page):
    """Close export dialogs before capturing the final diagram screenshot."""
    try:
        for _ in range(3):
            dialogs = page.locator(".geDialog:visible")
            if dialogs.count() == 0:
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
        if page.locator(".geDialog:visible").count() == 0:
            return
    except Exception as error:
        raise ImportToolError(
            "无法关闭导出对话框: %s" % _display(error), code=1
        ) from None
    raise ImportToolError("无法关闭导出对话框", code=1)


def _install_editor_probe(page, timeout_ms):
    """Capture draw.io's supported EditorUi instance for viewport checks."""
    try:
        page.wait_for_function(
            "() => window.Draw && typeof window.Draw.loadPlugin === 'function'",
            timeout=timeout_ms,
        )
        page.evaluate(
            """() => {
                if (!window.__vsdxImportUi) {
                    window.Draw.loadPlugin((ui) => {
                        window.__vsdxImportUi = ui;
                    });
                }
            }"""
        )
        page.wait_for_function(
            """() => window.__vsdxImportUi &&
                window.__vsdxImportUi.editor &&
                window.__vsdxImportUi.editor.graph""",
            timeout=timeout_ms,
        )
    except Exception as error:
        raise ImportToolError(
            "无法访问 draw.io 编辑器 API: %s" % _display(error), code=1
        ) from None


def _fit_diagram_to_view(page):
    """Fit graph content into the scroll viewport and prove it is framed."""
    try:
        result = page.evaluate(
            """async ({tolerance}) => {
                const ui = window.__vsdxImportUi;
                const graph = ui && ui.editor && ui.editor.graph;
                if (!graph || typeof ui.initialFitDiagram !== 'function' ||
                    typeof ui.fitDiagramToWindow !== 'function' ||
                    typeof mxRectangle !== 'function') {
                    throw new Error('required draw.io fit API is unavailable');
                }
                const view = graph.view;
                const container = graph.container;
                graph.clearSelection();
                view.validate();
                ui.initialFitDiagram(1);
                ui.fitDiagramToWindow(
                    1, new mxRectangle(10, 10, 10, 10), false
                );
                view.validate();
                await new Promise((resolve) => requestAnimationFrame(() =>
                    requestAnimationFrame(resolve)));
                view.validate();

                const bounds = graph.getGraphBounds();
                const viewport = {
                    left: container.scrollLeft,
                    top: container.scrollTop,
                    right: container.scrollLeft + container.clientWidth,
                    bottom: container.scrollTop + container.clientHeight
                };
                const framed = {
                    left: bounds.x,
                    top: bounds.y,
                    right: bounds.x + bounds.width,
                    bottom: bounds.y + bounds.height
                };
                const intersectionWidth = Math.max(
                    0,
                    Math.min(framed.right, viewport.right) -
                        Math.max(framed.left, viewport.left)
                );
                const intersectionHeight = Math.max(
                    0,
                    Math.min(framed.bottom, viewport.bottom) -
                        Math.max(framed.top, viewport.top)
                );
                const coverage = bounds.width > 0 && bounds.height > 0
                    ? intersectionWidth * intersectionHeight /
                        (bounds.width * bounds.height)
                    : 0;
                const overflow = {
                    left: Math.max(0, viewport.left - framed.left),
                    top: Math.max(0, viewport.top - framed.top),
                    right: Math.max(0, framed.right - viewport.right),
                    bottom: Math.max(0, framed.bottom - viewport.bottom)
                };
                const finite = [
                    view.scale, bounds.x, bounds.y, bounds.width, bounds.height,
                    container.clientWidth, container.clientHeight,
                    container.scrollLeft, container.scrollTop
                ].every(Number.isFinite);
                const fullyFramed = finite && view.scale > 0 &&
                    container.clientWidth > 0 && container.clientHeight > 0 &&
                    bounds.width > 0 && bounds.height > 0 &&
                    Object.values(overflow).every((value) => value <= tolerance) &&
                    coverage >= 0.99;
                return {
                    scale: view.scale,
                    bounds: framed,
                    viewport,
                    overflow,
                    coverage,
                    fullyFramed
                };
            }""",
            {"tolerance": 4.0},
        )
    except Exception as error:
        raise ImportToolError(
            "无法将图形适配到截图视口: %s" % _display(error), code=1
        ) from None
    if not isinstance(result, dict) or not result.get("fullyFramed"):
        raise ImportToolError(
            "图形未完整进入截图视口: %s" % _display(result), code=1
        )
    return result


def _paths_alias(first, second):
    if first.resolve() == second.resolve():
        return True
    if first.exists() and second.exists():
        try:
            return first.samefile(second)
        except OSError:
            return False
    return False


def import_vsdx(
    vsdx_path,
    output_path,
    url=DEFAULT_URL,
    timeout=DEFAULT_TIMEOUT,
    screenshot=None,
    expect_nodes=None,
    expect_edges=None,
):
    """Import one file and return an :class:`ExportSummary` on success."""
    try:
        input_path = Path(vsdx_path)
        output = Path(output_path)
    except (TypeError, ValueError, OSError) as error:
        raise ImportToolError(
            "无法解析输入或输出路径: %s" % _display(error), code=2
        ) from None
    if not input_path.is_file():
        raise ImportToolError("输入文件不存在: %s" % _display(input_path), code=2)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ImportToolError("timeout 必须是正数", code=2)
    try:
        if output.is_dir():
            raise ImportToolError(
                "输出 XML 路径不能是目录: %s" % _display(output), code=2
            )
        screenshot_path = (
            Path(screenshot) if screenshot is not None else output.with_suffix(".png")
        )
        if screenshot_path.is_dir():
            raise ImportToolError(
                "截图路径不能是目录: %s" % _display(screenshot_path), code=2
            )
        if screenshot_path.suffix.casefold() not in {".png", ".jpg", ".jpeg"}:
            raise ImportToolError(
                "截图扩展名必须是 .png、.jpg 或 .jpeg: %s"
                % _display(screenshot_path),
                code=2,
            )
        aliases = (
            (input_path, output, "输入 VSDX 和输出 XML"),
            (input_path, screenshot_path, "输入 VSDX 和截图"),
            (output, screenshot_path, "输出 XML 和截图"),
        )
        for first, second, description in aliases:
            if _paths_alias(first, second):
                raise ImportToolError(
                    "%s 不能是同一路径或文件别名" % description, code=2
                )
    except ImportToolError:
        raise
    except (TypeError, ValueError, OSError) as error:
        raise ImportToolError(
            "无法解析输入或输出路径: %s" % _display(error), code=2
        ) from None
    if not check_server(url, timeout=min(timeout, 3.0)):
        raise ImportToolError(
            "draw.io 服务不可用: %s" % _display(url), code=1
        )
    playwright_factory = _load_playwright()

    timeout_ms = max(1, int(timeout * 1000))
    browser = None
    try:
        with playwright_factory() as playwright:
            executable = Path(playwright.firefox.executable_path)
            if not executable.is_file():
                raise ImportToolError(
                    "Playwright Firefox 未安装: %s" % _display(executable),
                    code=2,
                )
            browser = _launch_firefox(playwright.firefox, timeout_ms)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
            page.goto(url, timeout=timeout_ms, wait_until="load")
            page.wait_for_function(
                "() => document.querySelectorAll('.geMenubar a.geItem').length >= 5",
                timeout=timeout_ms,
            )
            _install_editor_probe(page, timeout_ms)
            print("[OK] editor loaded")

            page.evaluate(
                """() => {
                    window.__clip = '';
                    try {
                        Object.defineProperty(navigator, 'clipboard', {
                            value: {
                                writeText: t => { window.__clip = t; return Promise.resolve(); },
                                readText: () => Promise.resolve(window.__clip)
                            }, configurable: true
                        });
                    } catch (e) {}
                }"""
            )

            open_file_menu(page, timeout_ms)
            click_menu_item(page, "Open from", timeout_ms)
            click_menu_item(page, "Device", timeout_ms)
            page.wait_for_timeout(min(1000, timeout_ms))
            page.set_input_files(
                "input[type=file]", str(input_path), timeout=timeout_ms
            )
            page.wait_for_timeout(min(10000, timeout_ms))

            dialogs = _dialogs(page)
            if dialogs:
                raise ImportToolError(
                    "导入出现对话框: %s" % " | ".join(_display(value) for value in dialogs),
                    code=1,
                )

            open_file_menu(page, timeout_ms)
            click_menu_item(page, "Export as", timeout_ms)
            click_menu_item(page, "XML", timeout_ms)
            page.wait_for_timeout(min(2000, timeout_ms))
            page.locator(".geDialog button.gePrimaryBtn").click(timeout=timeout_ms)
            page.wait_for_timeout(min(2500, timeout_ms))
            page.get_by_role("button", name="Copy").click(timeout=timeout_ms)
            page.wait_for_timeout(min(1500, timeout_ms))
            xml = page.evaluate("window.__clip")
            if not xml:
                raise ImportToolError("导出剪贴板为空", code=1)

            summary = validate_exported_xml(
                xml, expect_nodes=expect_nodes, expect_edges=expect_edges
            )
            try:
                output.write_text(xml, encoding="utf-8")
            except OSError as error:
                raise ImportToolError(
                    "无法写入导出 XML: %s" % _display(error), code=2
                ) from None
            _dismiss_dialogs(page)
            framing = _fit_diagram_to_view(page)
            try:
                screenshot_path.parent.mkdir(parents=True, exist_ok=True)
                page.screenshot(
                    path=str(screenshot_path),
                    full_page=True,
                    timeout=timeout_ms,
                )
            except OSError as error:
                raise ImportToolError(
                    "无法写入截图: %s" % _display(error), code=2
                ) from None
            print(
                "[OK] XML captured: nodes=%d edges=%d framing=%.3f output=%s"
                % (
                    summary.node_count,
                    summary.edge_count,
                    float(framing.get("coverage", 0.0)),
                    output,
                )
            )
            return summary
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _nonnegative_integer(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("必须是非负整数") from None
    if result < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return result


def _positive_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("必须是正数") from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("必须是正数")
    return result


def _build_parser():
    parser = _ArgumentParser(description="通过 draw.io 导入并验证 VSDX")
    parser.add_argument("vsdx", help="输入 .vsdx 文件")
    parser.add_argument("output", help="导出的 .drawio XML 文件")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--timeout", type=_positive_float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--screenshot", help="截图路径；默认与输出 XML 同目录")
    parser.add_argument("--expect-nodes", type=_nonnegative_integer)
    parser.add_argument("--expect-edges", type=_nonnegative_integer)
    return parser


def main(argv=None):
    """Return 0 on success, 1 on runtime/import failure, or 2 on input errors."""
    try:
        arguments = _build_parser().parse_args(argv)
    except _ArgumentExit as error:
        return error.status
    except ImportToolError as error:
        print("错误: %s" % _display(error), file=sys.stderr)
        return error.code

    try:
        import_vsdx(
            arguments.vsdx,
            arguments.output,
            url=arguments.url,
            timeout=arguments.timeout,
            screenshot=arguments.screenshot,
            expect_nodes=arguments.expect_nodes,
            expect_edges=arguments.expect_edges,
        )
    except ImportToolError as error:
        print("错误: %s" % _display(error), file=sys.stderr)
        return error.code
    except TimeoutError as error:
        print("错误: %s" % _display(error), file=sys.stderr)
        return 1
    except OSError as error:
        print("错误: %s" % _display(error), file=sys.stderr)
        return 2
    except Exception as error:
        print("错误: 导入验证失败: %s" % _display(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
