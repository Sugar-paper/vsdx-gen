#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run reproducible generator and real draw.io acceptance checks."""

import argparse
import base64
from dataclasses import dataclass, replace
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
from typing import Dict, Optional, Tuple
import urllib.parse
from xml.etree import ElementTree as ET
import zlib


PX = 101.6
DEFAULT_DRAWIO_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 120.0
EXAMPLE_NAMES = (
    "login-flow",
    "shapes-showcase",
    "ecommerce-order-distribution",
    "stress-flow",
)

Point = Tuple[float, float]
Box = Tuple[float, float, float, float]


class AcceptanceError(RuntimeError):
    """A failed acceptance assertion or command."""


@dataclass(frozen=True)
class ImportedNode:
    id: str
    vsdx_id: Optional[int]
    label: str
    box: Box
    style: Dict[str, str]

    @property
    def rotation(self):
        return _style_number(self.style, "rotation", 0.0, "node %s" % self.id)


@dataclass(frozen=True)
class ImportedEdge:
    id: str
    vsdx_id: Optional[int]
    label: str
    source: str
    target: str
    points: Tuple[Point, ...]
    source_point: Optional[Point]
    target_point: Optional[Point]
    style: Dict[str, str]


@dataclass(frozen=True)
class ImportedDiagram:
    page_height_px: float
    nodes: Tuple[ImportedNode, ...]
    edges: Tuple[ImportedEdge, ...]


@dataclass(frozen=True)
class CaseResult:
    name: str
    node_count: int
    edge_count: int
    vsdx_path: Path
    drawio_path: Path
    screenshot_path: Path


@dataclass(frozen=True)
class MovementResult:
    source_bounds_delta: Point
    source_endpoint_delta: Point
    target_endpoint_delta: Point
    same_source_terminal: bool
    same_target_terminal: bool


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _direct_child(element, name):
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _finite_number(value, context):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise AcceptanceError("%s is not numeric: %r" % (context, value)) from None
    if not math.isfinite(result):
        raise AcceptanceError("%s must be finite" % context)
    return result


def parse_style(style):
    """Parse a draw.io style without splitting Base64 padding characters."""
    result = {}
    for token in (style or "").split(";"):
        key, separator, value = token.partition("=")
        if separator and key:
            result[key] = value
    return result


def _style_number(style, key, default, context):
    if key not in style:
        return default
    return _finite_number(style[key], "%s style %s" % (context, key))


def _parse_vsdx_id(style, context):
    raw = style.get("vsdxID")
    if raw is None or raw == "":
        return None
    value = _finite_number(raw, "%s vsdxID" % context)
    integer = int(value)
    if value != integer or integer <= 0:
        raise AcceptanceError("%s vsdxID must be a positive integer" % context)
    return integer


def _point(element, context):
    return (
        _finite_number(element.get("x"), "%s x" % context),
        _finite_number(element.get("y"), "%s y" % context),
    )


def _find_model(root):
    if _local_name(root.tag) == "mxGraphModel":
        return root
    if _local_name(root.tag) == "mxfile":
        diagrams = [child for child in root if _local_name(child.tag) == "diagram"]
        models = []
        for diagram in diagrams:
            models.extend(
                child for child in diagram
                if _local_name(child.tag) == "mxGraphModel"
            )
        if len(models) == 1:
            return models[0]
        if len(models) > 1:
            raise AcceptanceError("draw.io export contains multiple graph models")
    raise AcceptanceError("document does not contain one mxGraphModel")


def parse_drawio_xml_text(xml):
    """Parse an uncompressed draw.io export into stable acceptance records."""
    try:
        document = ET.fromstring(xml)
    except (ET.ParseError, TypeError, ValueError) as error:
        raise AcceptanceError("draw.io XML cannot be parsed: %s" % error) from None
    model = _find_model(document)
    graph_root = _direct_child(model, "root")
    if graph_root is None:
        raise AcceptanceError("mxGraphModel is missing root")
    page_height = _finite_number(
        model.get("pageHeight", 11.0 * PX), "mxGraphModel pageHeight"
    )
    if page_height <= 0:
        raise AcceptanceError("mxGraphModel pageHeight must be positive")

    nodes = []
    raw_edges = []
    aliases = {}

    def add_alias(alias, node_id):
        if not alias:
            return
        previous = aliases.get(alias)
        if previous is not None and previous != node_id:
            raise AcceptanceError("duplicate node alias: %s" % alias)
        aliases[alias] = node_id

    def visit(element, wrapper_id=None, wrapper_label=""):
        name = _local_name(element.tag)
        next_wrapper_id = wrapper_id
        next_wrapper_label = wrapper_label
        if name.lower() in ("userobject", "object"):
            next_wrapper_id = element.get("id") or wrapper_id
            next_wrapper_label = (
                element.get("label") or element.get("value") or wrapper_label
            )
        if name == "mxCell":
            cell_id = element.get("id") or wrapper_id
            label = next_wrapper_label or element.get("value", "")
            style = parse_style(element.get("style", ""))
            if element.get("vertex") == "1":
                if not cell_id:
                    raise AcceptanceError("vertex is missing both cell and wrapper IDs")
                geometry = _direct_child(element, "mxGeometry")
                if geometry is None:
                    raise AcceptanceError("node %s is missing mxGeometry" % cell_id)
                x = _finite_number(geometry.get("x"), "node %s x" % cell_id)
                y = _finite_number(geometry.get("y"), "node %s y" % cell_id)
                width = _finite_number(
                    geometry.get("width"), "node %s width" % cell_id
                )
                height = _finite_number(
                    geometry.get("height"), "node %s height" % cell_id
                )
                if width <= 0 or height <= 0:
                    raise AcceptanceError("node %s has non-positive size" % cell_id)
                node = ImportedNode(
                    id=cell_id,
                    vsdx_id=_parse_vsdx_id(style, "node %s" % cell_id),
                    label=label,
                    box=(x, y, x + width, y + height),
                    style=style,
                )
                nodes.append(node)
                add_alias(cell_id, cell_id)
                add_alias(wrapper_id, cell_id)
            elif element.get("edge") == "1":
                if not cell_id:
                    raise AcceptanceError("edge is missing both cell and wrapper IDs")
                source_point = None
                target_point = None
                points = []
                geometry = _direct_child(element, "mxGeometry")
                if geometry is not None:
                    for child in geometry:
                        child_name = _local_name(child.tag)
                        role = child.get("as")
                        if child_name == "mxPoint" and role == "sourcePoint":
                            source_point = _point(child, "edge %s sourcePoint" % cell_id)
                        elif child_name == "mxPoint" and role == "targetPoint":
                            target_point = _point(child, "edge %s targetPoint" % cell_id)
                        elif child_name == "Array" and role == "points":
                            for item in child:
                                if _local_name(item.tag) == "mxPoint":
                                    points.append(_point(item, "edge %s waypoint" % cell_id))
                        elif child_name == "mxPoint" and role in (None, "point"):
                            points.append(_point(child, "edge %s waypoint" % cell_id))
                raw_edges.append(ImportedEdge(
                    id=cell_id,
                    vsdx_id=_parse_vsdx_id(style, "edge %s" % cell_id),
                    label=label,
                    source=element.get("source") or "",
                    target=element.get("target") or "",
                    points=tuple(points),
                    source_point=source_point,
                    target_point=target_point,
                    style=style,
                ))
        for child in element:
            visit(child, next_wrapper_id, next_wrapper_label)

    visit(graph_root)
    if not nodes:
        raise AcceptanceError("draw.io export contains no nodes")

    node_ids = set()
    node_vsdx_ids = set()
    for node in nodes:
        if node.id in node_ids:
            raise AcceptanceError("duplicate node ID: %s" % node.id)
        node_ids.add(node.id)
        if node.vsdx_id is not None:
            if node.vsdx_id in node_vsdx_ids:
                raise AcceptanceError("duplicate node vsdxID: %s" % node.vsdx_id)
            node_vsdx_ids.add(node.vsdx_id)

    edges = []
    edge_ids = set()
    edge_vsdx_ids = set()
    for edge in raw_edges:
        if edge.id in edge_ids:
            raise AcceptanceError("duplicate edge ID: %s" % edge.id)
        edge_ids.add(edge.id)
        if edge.vsdx_id is not None:
            if edge.vsdx_id in edge_vsdx_ids:
                raise AcceptanceError("duplicate edge vsdxID: %s" % edge.vsdx_id)
            edge_vsdx_ids.add(edge.vsdx_id)
        if not edge.source or edge.source not in aliases:
            raise AcceptanceError(
                "edge %s has missing or dangling source %r" % (edge.id, edge.source)
            )
        if not edge.target or edge.target not in aliases:
            raise AcceptanceError(
                "edge %s has missing or dangling target %r" % (edge.id, edge.target)
            )
        edges.append(replace(
            edge, source=aliases[edge.source], target=aliases[edge.target]
        ))
    return ImportedDiagram(page_height, tuple(nodes), tuple(edges))


def load_drawio(path):
    try:
        xml = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise AcceptanceError("cannot read draw.io XML %s: %s" % (path, error)) from None
    return parse_drawio_xml_text(xml)


def expected_sides(source, target, edge):
    """Apply the generator's deterministic automatic and explicit side rules."""
    dx = target["x"] - source["x"]
    dy = target["y"] - source["y"]
    if abs(dy) >= abs(dx):
        default = ("bottom", "top") if dy < 0 else ("top", "bottom")
    else:
        default = ("right", "left") if dx > 0 else ("left", "right")
    return (
        edge.get("fromSide", default[0]),
        edge.get("toSide", default[1]),
    )


def _anchor(node, side):
    anchors = {
        "left": (node["x"] - node["w"] / 2.0, node["y"]),
        "right": (node["x"] + node["w"] / 2.0, node["y"]),
        "top": (node["x"], node["y"] + node["h"] / 2.0),
        "bottom": (node["x"], node["y"] - node["h"] / 2.0),
    }
    try:
        anchor = anchors[side]
    except KeyError:
        raise AcceptanceError("unsupported connector side: %s" % side) from None
    rotation = float(node.get("rotation", 0) or 0)
    if not rotation:
        return anchor
    angle = math.radians(rotation)
    dx, dy = anchor[0] - node["x"], anchor[1] - node["y"]
    return (
        node["x"] + dx * math.cos(angle) - dy * math.sin(angle),
        node["y"] + dx * math.sin(angle) + dy * math.cos(angle),
    )


def inches_to_drawio(point, page_height_inches):
    return (point[0] * PX, (page_height_inches - point[1]) * PX)


def _node_by_id(diagram, node_id):
    matches = [node for node in diagram.nodes if node.id == node_id]
    if len(matches) != 1:
        raise AcceptanceError("expected one imported node %s" % node_id)
    return matches[0]


def terminal_evidence(diagram, edge, source):
    """Return an imported terminal point and the XML evidence that proves it."""
    node = _node_by_id(diagram, edge.source if source else edge.target)
    prefix = "exit" if source else "entry"
    x_key = prefix + "X"
    y_key = prefix + "Y"
    if x_key in edge.style or y_key in edge.style:
        if x_key not in edge.style or y_key not in edge.style:
            raise AcceptanceError("edge %s has an incomplete %s constraint" % (
                edge.id, prefix
            ))
        x1, y1, x2, y2 = node.box
        x = x1 + _style_number(edge.style, x_key, 0.0, "edge %s" % edge.id) * (x2 - x1)
        y = y1 + _style_number(edge.style, y_key, 0.0, "edge %s" % edge.id) * (y2 - y1)
        x += _style_number(edge.style, prefix + "Dx", 0.0, "edge %s" % edge.id)
        y += _style_number(edge.style, prefix + "Dy", 0.0, "edge %s" % edge.id)
        return (x, y), "%s constraint" % prefix

    direct = edge.source_point if source else edge.target_point
    if direct is not None:
        return direct, "%sPoint" % ("source" if source else "target")

    if abs(node.rotation) > 1e-9 and edge.points:
        return (
            edge.points[0] if source else edge.points[-1],
            "route endpoint",
        )
    raise AcceptanceError(
        "edge %s has no %s terminal position evidence" % (
            edge.id, "source" if source else "target"
        )
    )


def _close(first, second, tolerance):
    return abs(first - second) <= tolerance


def _assert_point(actual, expected, tolerance, context):
    if not (_close(actual[0], expected[0], tolerance) and
            _close(actual[1], expected[1], tolerance)):
        raise AcceptanceError(
            "%s differs by more than %.3fpx: expected %r, got %r"
            % (context, tolerance, expected, actual)
        )


def _expected_node_box(node, page_height_inches):
    left = (node["x"] - node["w"] / 2.0) * PX
    top = (page_height_inches - node["y"] - node["h"] / 2.0) * PX
    return (left, top, left + node["w"] * PX, top + node["h"] * PX)


def _indexed_by_vsdx_id(items, kind):
    result = {}
    for item in items:
        if item.vsdx_id is None:
            raise AcceptanceError("imported %s %s is missing vsdxID" % (kind, item.id))
        if item.vsdx_id in result:
            raise AcceptanceError("duplicate %s vsdxID %d" % (kind, item.vsdx_id))
        result[item.vsdx_id] = item
    return result


def assert_case_matches(data, diagram, tolerance=1.0):
    """Assert counts, bindings, node positions, terminals, and route points."""
    nodes = data.get("nodes")
    edges = data.get("edges", [])
    page = data.get("page") or {}
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise AcceptanceError("example nodes and edges must be arrays")
    page_height = _finite_number(page.get("height", 11.0), "page height")
    if len(diagram.nodes) != len(nodes) or len(diagram.edges) != len(edges):
        raise AcceptanceError(
            "imported counts differ: expected %d/%d, got %d/%d"
            % (len(nodes), len(edges), len(diagram.nodes), len(diagram.edges))
        )
    imported_nodes = _indexed_by_vsdx_id(diagram.nodes, "node")
    imported_edges = _indexed_by_vsdx_id(diagram.edges, "edge")
    json_nodes = {node["id"]: node for node in nodes}

    for index, node in enumerate(nodes, start=1):
        imported = imported_nodes.get(index)
        if imported is None:
            raise AcceptanceError("missing imported node vsdxID %d" % index)
        expected_box = _expected_node_box(node, page_height)
        actual_geometry = (
            imported.box[0],
            imported.box[1],
            imported.box[2] - imported.box[0],
            imported.box[3] - imported.box[1],
        )
        expected_geometry = (
            expected_box[0],
            expected_box[1],
            expected_box[2] - expected_box[0],
            expected_box[3] - expected_box[1],
        )
        for coordinate, actual, expected in zip(
                ("left", "top", "width", "height"),
                actual_geometry, expected_geometry):
            if not _close(actual, expected, tolerance):
                raise AcceptanceError(
                    "node %s %s differs by more than %.3fpx: expected %.6g, got %.6g"
                    % (node["id"], coordinate, tolerance, expected, actual)
                )

    for index, edge_data in enumerate(edges, start=len(nodes) + 1):
        imported = imported_edges.get(index)
        if imported is None:
            raise AcceptanceError("missing imported edge vsdxID %d" % index)
        source_data = json_nodes[edge_data["from"]]
        target_data = json_nodes[edge_data["to"]]
        source_imported = imported_nodes[nodes.index(source_data) + 1]
        target_imported = imported_nodes[nodes.index(target_data) + 1]
        if imported.source != source_imported.id:
            raise AcceptanceError(
                "edge %s source binding is %s, expected %s"
                % (edge_data.get("id", index), imported.source, source_imported.id)
            )
        if imported.target != target_imported.id:
            raise AcceptanceError(
                "edge %s target binding is %s, expected %s"
                % (edge_data.get("id", index), imported.target, target_imported.id)
            )

        source_side, target_side = expected_sides(
            source_data, target_data, edge_data
        )
        expected_source = inches_to_drawio(
            _anchor(source_data, source_side), page_height
        )
        expected_target = inches_to_drawio(
            _anchor(target_data, target_side), page_height
        )
        actual_source, source_evidence = terminal_evidence(
            diagram, imported, source=True
        )
        actual_target, target_evidence = terminal_evidence(
            diagram, imported, source=False
        )
        edge_name = edge_data.get("id", str(index))
        _assert_point(
            actual_source, expected_source, tolerance,
            "edge %s source (%s)" % (edge_name, source_evidence),
        )
        _assert_point(
            actual_target, expected_target, tolerance,
            "edge %s target (%s)" % (edge_name, target_evidence),
        )

        route = list(imported.points)
        if source_evidence == "route endpoint":
            route.pop(0)
        if target_evidence == "route endpoint":
            if not route:
                raise AcceptanceError(
                    "edge %s reused one route point for both terminals" % edge_name
                )
            route.pop()
        expected_route = [
            inches_to_drawio(tuple(point), page_height)
            for point in edge_data.get("points", [])
        ]
        if len(route) != len(expected_route):
            raise AcceptanceError(
                "edge %s waypoint count differs: expected %d, got %d"
                % (edge_name, len(expected_route), len(route))
            )
        for route_index, (actual, expected) in enumerate(
                zip(route, expected_route), start=1):
            _assert_point(
                actual, expected, tolerance,
                "edge %s waypoint %d" % (edge_name, route_index),
            )


def decode_stencil(style_value):
    """Decode draw.io's Base64 + raw-DEFLATE + URI encoded stencil XML."""
    style = style_value if isinstance(style_value, dict) else parse_style(style_value)
    shape = style.get("shape", "")
    if not shape.startswith("stencil(") or not shape.endswith(")"):
        raise AcceptanceError("cell does not contain a compressed stencil")
    payload = shape[len("stencil("):-1]
    try:
        compressed = base64.b64decode(payload, validate=True)
        quoted = zlib.decompress(compressed, -zlib.MAX_WBITS).decode("ascii")
        xml = urllib.parse.unquote(quoted, encoding="utf-8", errors="strict")
        return ET.fromstring(xml)
    except (ValueError, UnicodeError, zlib.error, ET.ParseError) as error:
        raise AcceptanceError("stencil cannot be decoded: %s" % error) from None


def _command_points(stencil):
    result = []
    for element in stencil.iter():
        name = _local_name(element.tag)
        if name not in ("move", "line", "arc", "curve", "quad"):
            continue
        if element.get("x") is None or element.get("y") is None:
            continue
        result.append((
            name,
            _finite_number(element.get("x"), "%s x" % name),
            _finite_number(element.get("y"), "%s y" % name),
            element,
        ))
    return result


def assert_showcase_stencils(diagram):
    """Check the real imported cylinder and vertical-arrow stencil geometry."""
    nodes = _indexed_by_vsdx_id(diagram.nodes, "node")
    try:
        cylinder = decode_stencil(nodes[5].style)
        up_arrow = decode_stencil(nodes[15].style)
        down_arrow = decode_stencil(nodes[16].style)
    except KeyError as error:
        raise AcceptanceError("showcase is missing stencil vsdxID %s" % error) from None

    commands = _command_points(cylinder)
    names = [item[0] for item in commands]
    if names != ["move", "arc", "line", "arc", "line"]:
        raise AcceptanceError("cylinder stencil command sequence is %r" % names)
    expected_endpoints = (
        (0.0, 90.0), (100.0, 90.0), (100.0, 10.0),
        (0.0, 10.0), (0.0, 90.0),
    )
    for index, (command, expected) in enumerate(zip(commands, expected_endpoints)):
        _assert_point(command[1:3], expected, 1.0, "cylinder command %d" % index)
    previous = commands[0]
    for command in commands[1:]:
        if command[0] == "arc":
            element = command[3]
            rx = _finite_number(element.get("rx"), "cylinder arc rx")
            ry = _finite_number(element.get("ry"), "cylinder arc ry")
            if rx <= 0 or ry <= 0:
                raise AcceptanceError("cylinder arc radii must be positive")
            if element.get("large-arc-flag") != "0" or element.get("sweep-flag") != "0":
                raise AcceptanceError("cylinder arcs must use the approved small sweep")
            chord = abs(command[1] - previous[1])
            if chord <= 0 or rx <= chord / 2.0:
                raise AcceptanceError("cylinder arc radius cannot span its chord")
            sagitta = ry * (1.0 - math.sqrt(1.0 - (chord / (2.0 * rx)) ** 2))
            if abs(sagitta - 10.0) > 1.5:
                raise AcceptanceError(
                    "cylinder arc bow is %.3f%%, expected about 10%%" % sagitta
                )
        previous = command

    expected_arrows = {
        "upArrow": (
            ("move", 20.0, 100.0), ("line", 20.0, 55.0),
            ("line", 0.0, 55.0), ("line", 50.0, 0.0),
            ("line", 100.0, 55.0), ("line", 80.0, 55.0),
            ("line", 80.0, 100.0), ("line", 20.0, 100.0),
        ),
        "downArrow": (
            ("move", 20.0, 0.0), ("line", 80.0, 0.0),
            ("line", 80.0, 45.0), ("line", 100.0, 45.0),
            ("line", 50.0, 100.0), ("line", 0.0, 45.0),
            ("line", 20.0, 45.0), ("line", 20.0, 0.0),
        ),
    }
    for name, stencil in (("upArrow", up_arrow), ("downArrow", down_arrow)):
        commands = _command_points(stencil)
        expected = expected_arrows[name]
        if len(commands) != len(expected):
            raise AcceptanceError(
                "%s stencil command count is %d, expected %d"
                % (name, len(commands), len(expected))
            )
        for index, (command, expected_command) in enumerate(
                zip(commands, expected)):
            if command[0] != expected_command[0]:
                raise AcceptanceError(
                    "%s stencil command %d is %s, expected %s"
                    % (name, index, command[0], expected_command[0])
                )
            _assert_point(
                command[1:3], expected_command[1:3], 1.0,
                "%s stencil command %d" % (name, index),
            )


def run_command(command, timeout):
    """Run one acceptance subprocess and surface its complete failure output."""
    try:
        result = subprocess.run(
            [str(item) for item in command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AcceptanceError("command could not complete: %s" % error) from None
    if result.returncode != 0:
        output = "\n".join(
            part.strip() for part in (result.stdout, result.stderr) if part.strip()
        )
        raise AcceptanceError(
            "command exited %d: %s\n%s"
            % (result.returncode, " ".join(command), output)
        )
    return result


def _clear_case_artifacts(paths, output_dir):
    """Remove only exact case outputs so stale files cannot satisfy the gate."""
    root = Path(output_dir).resolve()
    for path in paths:
        candidate = Path(path)
        resolved = candidate.resolve()
        if resolved.parent != root:
            raise AcceptanceError(
                "acceptance artifact escapes output directory: %s" % candidate
            )
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise AcceptanceError(
                "cannot remove stale acceptance artifact %s: %s"
                % (candidate, error)
            ) from None


def assert_png_screenshot(path):
    """Reject missing, truncated, or non-PNG acceptance screenshots."""
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise AcceptanceError("cannot read acceptance screenshot %s: %s" % (
            path, error
        )) from None
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise AcceptanceError("acceptance screenshot is not a valid PNG: %s" % path)

    offset = 8
    chunk_index = 0
    saw_header = False
    saw_end = False
    image_data = []
    while offset < len(data):
        if len(data) - offset < 12:
            raise AcceptanceError("acceptance screenshot is truncated: %s" % path)
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_end = offset + 12 + length
        if chunk_end > len(data):
            raise AcceptanceError("acceptance screenshot is truncated: %s" % path)
        kind = data[offset + 4:offset + 8]
        payload = data[offset + 8:offset + 8 + length]
        stored_crc = struct.unpack(">I", data[chunk_end - 4:chunk_end])[0]
        actual_crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        if stored_crc != actual_crc:
            raise AcceptanceError(
                "acceptance screenshot has an invalid PNG CRC: %s" % path
            )
        if chunk_index == 0 and kind != b"IHDR":
            raise AcceptanceError("acceptance screenshot has no leading IHDR: %s" % path)
        if kind == b"IHDR":
            if saw_header or length != 13:
                raise AcceptanceError(
                    "acceptance screenshot has an invalid IHDR: %s" % path
                )
            width, height = struct.unpack(">II", payload[:8])
            if width <= 0 or height <= 0:
                raise AcceptanceError(
                    "acceptance screenshot has invalid dimensions: %s" % path
                )
            saw_header = True
        elif kind == b"IDAT":
            image_data.append(payload)
        elif kind == b"IEND":
            if length != 0 or chunk_end != len(data):
                raise AcceptanceError(
                    "acceptance screenshot has an invalid IEND: %s" % path
                )
            saw_end = True
            offset = chunk_end
            break
        offset = chunk_end
        chunk_index += 1

    if not saw_header or not image_data or not saw_end or offset != len(data):
        raise AcceptanceError(
            "acceptance screenshot is missing required PNG chunks: %s" % path
        )
    try:
        zlib.decompress(b"".join(image_data))
    except zlib.error as error:
        raise AcceptanceError(
            "acceptance screenshot has invalid image data %s: %s" % (path, error)
        ) from None


def _load_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except (ImportError, OSError) as error:
        raise AcceptanceError("Playwright cannot be loaded: %s" % error) from None
    return sync_playwright


def _launch_firefox(firefox, timeout_ms):
    """Retry only Windows' transient spawn EBUSY launch failure."""
    for attempt in range(3):
        try:
            return firefox.launch(headless=True, timeout=timeout_ms)
        except Exception as error:
            if "EBUSY" not in str(error).upper() or attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))
    raise AssertionError("unreachable")


def _open_file_menu(page, timeout_ms):
    item = page.locator(".geMenubar a.geItem", has_text="File")
    box = item.bounding_box(timeout=timeout_ms)
    if not box:
        raise AcceptanceError("draw.io File menu is unavailable")
    page.mouse.move(box["x"] + box["width"] / 2.0, box["y"] + box["height"] / 2.0)
    page.mouse.down()
    page.wait_for_timeout(300)
    page.mouse.up()
    page.wait_for_timeout(500)


def _click_menu_item(page, text, timeout_ms):
    page.locator("td.mxPopupMenuItem", has_text=text).first.click(timeout=timeout_ms)
    page.wait_for_timeout(800)


def _visible_dialogs(page):
    return page.evaluate(
        """() => [...document.querySelectorAll('.geDialog')]
            .filter((item) => {
                const style = getComputedStyle(item);
                return style.display !== 'none' && style.visibility !== 'hidden';
            })
            .map((item) => item.innerText).filter(Boolean)"""
    ) or []


def _fit_live_diagram(page):
    result = page.evaluate(
        """async ({tolerance}) => {
            const ui = window.__vsdxAcceptanceUi;
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
            return {scale: view.scale, overflow, coverage, fullyFramed};
        }""",
        {"tolerance": 4.0},
    )
    if not isinstance(result, dict) or not result.get("fullyFramed"):
        raise AcceptanceError("movement diagram is not framed: %r" % result)
    return result


def run_live_movement(
        vsdx_path, drawio_url, timeout, expect_nodes, expect_edges,
        edge_vsdx_id, screenshot_path):
    """Import the showcase and prove a rendered bound edge follows its source."""
    screenshot_path = Path(screenshot_path)
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    _clear_case_artifacts((screenshot_path,), screenshot_path.parent)
    sync_playwright = _load_playwright()
    timeout_ms = max(1, int(timeout * 1000))
    browser = None
    try:
        with sync_playwright() as playwright:
            executable = Path(playwright.firefox.executable_path)
            if not executable.is_file():
                raise AcceptanceError("Playwright Firefox is not installed: %s" % executable)
            browser = _launch_firefox(playwright.firefox, timeout_ms)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.set_default_timeout(timeout_ms)
            page.set_default_navigation_timeout(timeout_ms)
            page.goto(drawio_url, timeout=timeout_ms, wait_until="load")
            page.wait_for_function(
                "() => window.Draw && typeof window.Draw.loadPlugin === 'function'",
                timeout=timeout_ms,
            )
            page.evaluate(
                """() => {
                    window.__vsdxAcceptanceUi = null;
                    window.Draw.loadPlugin((ui) => {
                        window.__vsdxAcceptanceUi = ui;
                    });
                }"""
            )
            page.wait_for_function(
                """() => window.__vsdxAcceptanceUi &&
                    window.__vsdxAcceptanceUi.editor &&
                    window.__vsdxAcceptanceUi.editor.graph""",
                timeout=timeout_ms,
            )
            _open_file_menu(page, timeout_ms)
            _click_menu_item(page, "Open from", timeout_ms)
            _click_menu_item(page, "Device", timeout_ms)
            page.wait_for_timeout(min(1000, timeout_ms))
            page.set_input_files(
                "input[type=file]", str(vsdx_path), timeout=timeout_ms
            )
            page.wait_for_function(
                """(expected) => {
                    const graph = window.__vsdxAcceptanceUi.editor.graph;
                    const parent = graph.getDefaultParent();
                    return graph.getChildVertices(parent).length === expected.nodes &&
                        graph.getChildEdges(parent).length === expected.edges;
                }""",
                arg={"nodes": expect_nodes, "edges": expect_edges},
                timeout=timeout_ms,
            )
            dialogs = _visible_dialogs(page)
            if dialogs:
                raise AcceptanceError(
                    "draw.io movement import showed a dialog: %s"
                    % " | ".join(str(item) for item in dialogs)
                )
            _fit_live_diagram(page)
            raw = page.evaluate(
                """async ({edgeVsdxId, movePx}) => {
                    const graph = window.__vsdxAcceptanceUi.editor.graph;
                    const model = graph.getModel();
                    const view = graph.view;
                    const cells = Object.values(model.getCells ? model.getCells() : model.cells);
                    const edge = cells.find((cell) =>
                        model.isEdge(cell) &&
                        String((graph.getCellStyle(cell) || {}).vsdxID) === String(edgeVsdxId));
                    if (!edge) throw new Error(`edge vsdxID=${edgeVsdxId} not found`);
                    const source = model.getTerminal(edge, true);
                    const target = model.getTerminal(edge, false);
                    if (!source || !target) throw new Error('edge has an unbound terminal');

                    const snapshot = () => {
                        view.validate();
                        const edgeState = view.getState(edge);
                        const sourceState = view.getState(source);
                        if (!edgeState || !sourceState || !edgeState.absolutePoints ||
                            edgeState.absolutePoints.length < 2) {
                            throw new Error('graph has no rendered edge/source state');
                        }
                        const points = edgeState.absolutePoints;
                        return {
                            source: model.getTerminal(edge, true),
                            target: model.getTerminal(edge, false),
                            sourceBounds: {x: sourceState.x, y: sourceState.y},
                            first: {x: points[0].x, y: points[0].y},
                            last: {x: points[points.length - 1].x, y: points[points.length - 1].y}
                        };
                    };
                    const before = snapshot();
                    const scale = view.scale;
                    if (!(scale > 0)) throw new Error('graph view scale is invalid');
                    graph.setSelectionCells([source, edge]);
                    graph.moveCells([source], movePx / scale, 0, false, null);
                    view.invalidate();
                    view.validate();
                    await new Promise((resolve) => requestAnimationFrame(resolve));
                    const after = snapshot();
                    return {
                        sameSourceTerminal: model.getTerminal(edge, true) === before.source &&
                            before.source === after.source,
                        sameTargetTerminal: model.getTerminal(edge, false) === before.target &&
                            before.target === after.target,
                        sourceBoundsDelta: {
                            x: after.sourceBounds.x - before.sourceBounds.x,
                            y: after.sourceBounds.y - before.sourceBounds.y
                        },
                        sourceEndpointDelta: {
                            x: after.first.x - before.first.x,
                            y: after.first.y - before.first.y
                        },
                        targetEndpointDelta: {
                            x: after.last.x - before.last.x,
                            y: after.last.y - before.last.y
                        }
                    };
                }""",
                {"edgeVsdxId": edge_vsdx_id, "movePx": 40.0},
            )
            page.screenshot(
                path=str(screenshot_path), full_page=True, timeout=timeout_ms
            )
            assert_png_screenshot(screenshot_path)
    except AcceptanceError:
        raise
    except Exception as error:
        raise AcceptanceError("live movement check failed: %s" % error) from None
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    result = MovementResult(
        source_bounds_delta=(
            float(raw["sourceBoundsDelta"]["x"]),
            float(raw["sourceBoundsDelta"]["y"]),
        ),
        source_endpoint_delta=(
            float(raw["sourceEndpointDelta"]["x"]),
            float(raw["sourceEndpointDelta"]["y"]),
        ),
        target_endpoint_delta=(
            float(raw["targetEndpointDelta"]["x"]),
            float(raw["targetEndpointDelta"]["y"]),
        ),
        same_source_terminal=bool(raw["sameSourceTerminal"]),
        same_target_terminal=bool(raw["sameTargetTerminal"]),
    )
    if not result.same_source_terminal or not result.same_target_terminal:
        raise AcceptanceError("moving the source changed an edge terminal binding")
    _assert_point(
        result.source_bounds_delta, (40.0, 0.0), 1.0,
        "moved source bounds delta",
    )
    _assert_point(
        result.source_endpoint_delta, (40.0, 0.0), 1.0,
        "rendered source endpoint delta",
    )
    _assert_point(
        result.target_endpoint_delta, (0.0, 0.0), 1.0,
        "rendered target endpoint delta",
    )
    return result


def _load_example(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AcceptanceError("cannot read example %s: %s" % (path, error)) from None
    if not isinstance(data, dict):
        raise AcceptanceError("example %s is not a JSON object" % path)
    if not isinstance(data.get("nodes"), list):
        raise AcceptanceError("example %s has no nodes array" % path)
    if not isinstance(data.get("edges", []), list):
        raise AcceptanceError("example %s has no edges array" % path)
    return data


def run_case(skill_root, output_dir, name, drawio_url, timeout):
    example = skill_root / "examples" / (name + ".json")
    generator = skill_root / "scripts" / "vsdx_gen.py"
    importer = skill_root / "scripts" / "test_import.py"
    verifier = skill_root / "scripts" / "verify_layout.py"
    data = _load_example(example)
    node_count = len(data["nodes"])
    edge_count = len(data.get("edges", []))
    vsdx_path = output_dir / (name + ".vsdx")
    drawio_path = output_dir / (name + ".drawio")
    screenshot_path = output_dir / (name + ".png")
    _clear_case_artifacts(
        (vsdx_path, drawio_path, screenshot_path), output_dir
    )

    run_command(
        [sys.executable, str(generator), str(example), str(vsdx_path)],
        timeout,
    )
    run_command(
        [
            sys.executable, str(importer), str(vsdx_path), str(drawio_path),
            "--url", drawio_url,
            "--timeout", str(timeout),
            "--screenshot", str(screenshot_path),
            "--expect-nodes", str(node_count),
            "--expect-edges", str(edge_count),
        ],
        timeout + 30.0,
    )
    run_command(
        [
            sys.executable, str(verifier), str(drawio_path),
            "--expect-nodes", str(node_count),
            "--expect-edges", str(edge_count),
        ],
        timeout,
    )
    diagram = load_drawio(drawio_path)
    assert_case_matches(data, diagram, tolerance=1.0)
    if name == "shapes-showcase":
        assert_showcase_stencils(diagram)
    for path in (vsdx_path, drawio_path, screenshot_path):
        if not path.is_file() or path.stat().st_size <= 0:
            raise AcceptanceError("acceptance artifact is missing or empty: %s" % path)
    assert_png_screenshot(screenshot_path)
    return CaseResult(
        name=name,
        node_count=node_count,
        edge_count=edge_count,
        vsdx_path=vsdx_path,
        drawio_path=drawio_path,
        screenshot_path=screenshot_path,
    )


def _unit_only(skill_root, output_dir):
    fixture = skill_root / "tests" / "fixtures" / "valid.drawio"
    diagram = load_drawio(fixture)
    if len(diagram.nodes) != 2 or len(diagram.edges) != 1:
        raise AcceptanceError("unit fixture must contain exactly 2 nodes and 1 edge")
    data = {
        "page": {"width": 8.5, "height": 11.0},
        "nodes": [
            {"id": "A", "x": -0.5, "y": 9.5, "w": 1.0, "h": 1.0},
            {"id": "B", "x": 2.5, "y": 9.5, "w": 1.0, "h": 1.0},
        ],
        "edges": [
            {
                "id": "E", "from": "A", "to": "B",
                "points": [[1.0, 10.5], [1.0, 9.5]],
            },
        ],
    }
    assert_case_matches(data, diagram, tolerance=1.0)
    output_dir.mkdir(parents=True, exist_ok=True)
    print("[PASS] unit-only parser/assertion fixture: nodes=2 edges=1")


def _positive_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("must be a positive number") from None
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return result


def _build_parser():
    parser = argparse.ArgumentParser(description="Run vsdx-gen draw.io acceptance")
    parser.add_argument("--skill-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--drawio-url", default=DEFAULT_DRAWIO_URL)
    parser.add_argument("--timeout", type=_positive_float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--unit-only", action="store_true",
        help="run parser helper checks without draw.io or Playwright",
    )
    return parser


def main(argv=None):
    try:
        arguments = _build_parser().parse_args(argv)
    except SystemExit as error:
        return int(error.code)
    try:
        skill_root = Path(arguments.skill_root).resolve()
        output_dir = Path(arguments.output_dir).resolve()
        if not skill_root.is_dir():
            raise AcceptanceError("skill root is not a directory: %s" % skill_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        if arguments.unit_only:
            _unit_only(skill_root, output_dir)
            return 0

        results = []
        failures = []
        for name in EXAMPLE_NAMES:
            try:
                result = run_case(
                    skill_root, output_dir, name,
                    arguments.drawio_url, arguments.timeout,
                )
                results.append(result)
            except Exception as error:
                failures.append((name, str(error)))

        if not failures:
            showcase = next(item for item in results if item.name == "shapes-showcase")
            try:
                run_live_movement(
                    showcase.vsdx_path,
                    arguments.drawio_url,
                    arguments.timeout,
                    showcase.node_count,
                    showcase.edge_count,
                    showcase.node_count + 1,
                    output_dir / "shapes-showcase-moved.png",
                )
            except Exception as error:
                failures.append(("shapes-showcase movement", str(error)))

        print("\nAcceptance summary:")
        passed_names = {item.name for item in results}
        for name in EXAMPLE_NAMES:
            if name in passed_names:
                result = next(item for item in results if item.name == name)
                print(
                    "  [PASS] %s nodes=%d edges=%d xml=%s screenshot=%s"
                    % (
                        name, result.node_count, result.edge_count,
                        result.drawio_path, result.screenshot_path,
                    )
                )
            else:
                message = next(
                    (text for failed_name, text in failures if failed_name == name),
                    "not completed",
                )
                print("  [FAIL] %s: %s" % (name, message))
        for name, message in failures:
            if name not in EXAMPLE_NAMES:
                print("  [FAIL] %s: %s" % (name, message))
        if not failures:
            print("  [PASS] shapes-showcase movement: binding retained, source moved 40px")
        return 1 if failures else 0
    except (AcceptanceError, OSError, ValueError) as error:
        print("[FAIL] acceptance setup: %s" % error, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
