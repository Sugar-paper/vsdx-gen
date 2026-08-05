#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run strict draw.io import validation for one or more VSDX files."""

import argparse
import importlib.util
import math
from pathlib import Path
import re
import sys


SCRIPT_DIR = Path(__file__).resolve().parent


def _load_single_import_module():
    module_name = "_vsdx_gen_test_import"
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        module_name, SCRIPT_DIR / "test_import.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("无法加载单文件导入工具")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_single_import = _load_single_import_module()
DEFAULT_TIMEOUT = _single_import.DEFAULT_TIMEOUT
DEFAULT_URL = _single_import.DEFAULT_URL
ImportToolError = _single_import.ImportToolError
import_vsdx = _single_import.import_vsdx


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


def _positive_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("必须是正数") from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("必须是正数")
    return result


def _nonnegative_integer(value):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("必须是非负整数") from None
    if result < 0:
        raise argparse.ArgumentTypeError("必须是非负整数")
    return result


def _safe_stem(path):
    stem = Path(path).stem
    stem = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", stem)
    while ".." in stem:
        stem = stem.replace("..", "_")
    stem = stem.strip(". ")
    return stem or "diagram"


def _output_path(output_dir, input_path, reserved_names):
    base = _safe_stem(input_path)
    index = 1
    while True:
        suffix = "" if index == 1 else "-%d" % index
        result_stem = base + suffix
        name = result_stem + ".drawio"
        screenshot_name = result_stem + ".png"
        candidate_names = (name.casefold(), screenshot_name.casefold())
        if all(value not in reserved_names for value in candidate_names):
            reserved_names.update(candidate_names)
            break
        index += 1
    candidate = output_dir / name
    root = output_dir.resolve()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ImportToolError("输出路径越出目标目录", code=2) from None
    return candidate


def _build_parser():
    parser = _ArgumentParser(description="批量验证 VSDX 的 draw.io 导入结果")
    parser.add_argument("inputs", nargs="+", help="一个或多个 .vsdx 文件")
    parser.add_argument("--output-dir", required=True, help="XML 和截图输出目录")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--timeout", type=_positive_float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--expect-nodes", type=_nonnegative_integer)
    parser.add_argument("--expect-edges", type=_nonnegative_integer)
    return parser


def main(argv=None):
    """Return nonzero when any individual import fails."""
    try:
        arguments = _build_parser().parse_args(argv)
    except _ArgumentExit as error:
        return error.status
    except ImportToolError as error:
        print("错误: %s" % error, file=sys.stderr)
        return error.code

    output_dir = Path(arguments.output_dir)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        print("错误: 无法创建输出目录: %s" % error, file=sys.stderr)
        return 2

    failures = []
    try:
        reserved_names = {path.name.casefold() for path in output_dir.iterdir()}
    except OSError as error:
        print("错误: 无法读取输出目录: %s" % error, file=sys.stderr)
        return 2
    for value in arguments.inputs:
        input_path = Path(value)
        try:
            output_path = _output_path(output_dir, input_path, reserved_names)
            import_vsdx(
                input_path,
                output_path,
                url=arguments.url,
                timeout=arguments.timeout,
                expect_nodes=arguments.expect_nodes,
                expect_edges=arguments.expect_edges,
            )
        except (ImportToolError, OSError, TimeoutError) as error:
            failures.append((input_path, error))
            print("[FAIL] %s: %s" % (input_path, error), file=sys.stderr)
        except Exception as error:
            failures.append((input_path, error))
            print("[FAIL] %s: %s" % (input_path, error), file=sys.stderr)
        else:
            print("[OK] %s -> %s" % (input_path, output_path))

    print(
        "批量导入完成: 成功 %d，失败 %d"
        % (len(arguments.inputs) - len(failures), len(failures))
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
