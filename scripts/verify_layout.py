#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate the structure and geometry of an uncompressed draw.io document."""

import argparse
from dataclasses import dataclass
from html.parser import HTMLParser
import math
from pathlib import Path
import sys
import unicodedata
from typing import Optional, Tuple
from xml.etree import ElementTree as ET


PX = 101.6
EPSILON_PX = 0.5
PAGE_DIMENSION_TOLERANCE_PX = 1.0
# VSDX defaults to an 11-inch page. Imported XML without pageHeight uses the
# same explicit fallback so Y-up summaries remain deterministic.
DEFAULT_PAGE_WIDTH_PX = 8.5 * PX
DEFAULT_PAGE_HEIGHT_PX = 11.0 * PX

Point = Tuple[float, float]
Box = Tuple[float, float, float, float]


class LayoutInputError(ValueError):
    """The file or XML structure cannot be analyzed as a draw.io document."""


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    box: Box
    rotation: float = 0.0


@dataclass(frozen=True)
class Edge:
    id: str
    label: str
    source: Optional[str]
    target: Optional[str]
    points: Tuple[Point, ...]
    offset: Point
    label_position: Point = (0.0, 0.0)
    label_relative: bool = False


@dataclass(frozen=True)
class NodeSummary:
    id: str
    label: str
    center_inches: Point
    size_inches: Point


@dataclass(frozen=True)
class LayoutReport:
    nodes: Tuple[Node, ...]
    edges: Tuple[Edge, ...]
    page_width_px: float
    page_height_px: float
    page_width_defaulted: bool
    page_height_defaulted: bool
    expected_page_width_px: Optional[float]
    expected_page_height_px: Optional[float]
    bounds_width_px: float
    bounds_height_px: float
    tiled_paper: bool
    tiled_size_mismatch: bool
    problems: Tuple[str, ...]
    summary: Tuple[NodeSummary, ...]

    @property
    def node_count(self):
        return len(self.nodes)

    @property
    def edge_count(self):
        return len(self.edges)


class _LabelTextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class _ArgumentExit(Exception):
    def __init__(self, status):
        super().__init__(status)
        self.status = status


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise LayoutInputError("参数错误: %s" % message)

    def exit(self, status=0, message=None):
        if message:
            self._print_message(message, sys.stderr)
        raise _ArgumentExit(status)


@dataclass(frozen=True)
class _RawEdge:
    id: str
    label: str
    source: Optional[str]
    target: Optional[str]
    source_point: Optional[Point]
    route_points: Tuple[Point, ...]
    target_point: Optional[Point]
    offset: Point
    source_constraint: Optional[Tuple[float, float, float, float]]
    target_constraint: Optional[Tuple[float, float, float, float]]
    label_position: Point
    label_relative: bool


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _display(value, limit=100):
    text = str(value)
    parts = []
    for character in text:
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
    result = "".join(parts)
    if len(result) > limit:
        return result[: limit - 3] + "..."
    return result


def _plain_label(value):
    if not value:
        return ""
    parser = _LabelTextParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return _display(value).strip()
    return "".join(parser.parts).strip()


def _required_number(element, attribute, context):
    raw = element.get(attribute)
    if raw is None or not raw.strip():
        raise LayoutInputError("%s 缺少数值属性 %s" % (context, attribute))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise LayoutInputError(
            "%s 的 %s 不是有效数字: %s"
            % (context, attribute, _display(raw))
        ) from None
    if not math.isfinite(value):
        raise LayoutInputError("%s 的 %s 必须是有限数字" % (context, attribute))
    return value


def _optional_number(element, attribute, default, context):
    raw = element.get(attribute)
    if raw is None:
        return default
    return _required_number(element, attribute, context)


def _parse_style(raw_style):
    values = {}
    for token in (raw_style or "").split(";"):
        key, separator, value = token.partition("=")
        if separator and key:
            values[key] = value
    return values


def _style_number(style, key, context):
    raw = style[key]
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise LayoutInputError(
            "%s 的 style.%s 不是有效数字: %s"
            % (context, key, _display(raw))
        ) from None
    if not math.isfinite(value):
        raise LayoutInputError(
            "%s 的 style.%s 必须是有限数字" % (context, key)
        )
    return value


def _terminal_constraint(style, prefix, edge_id):
    keys = tuple(prefix + suffix for suffix in ("X", "Y", "Dx", "Dy"))
    if not any(key in style for key in keys):
        return None
    if keys[0] not in style or keys[1] not in style:
        raise LayoutInputError(
            "边 %s 的 %s 约束不完整: 必须同时提供 %s 和 %s"
            % (_display(edge_id), prefix, keys[0], keys[1])
        )
    context = "边 %s" % _display(edge_id)
    x = _style_number(style, keys[0], context)
    y = _style_number(style, keys[1], context)
    dx = _style_number(style, keys[2], context) if keys[2] in style else 0.0
    dy = _style_number(style, keys[3], context) if keys[3] in style else 0.0
    return (x, y, dx, dy)


def _direct_child(element, name):
    for child in element:
        if _local_name(child.tag) == name:
            return child
    return None


def _find_model(document_root):
    if document_root.tag == "mxGraphModel":
        return document_root
    if document_root.tag == "mxfile":
        for diagram in document_root:
            if diagram.tag != "diagram":
                continue
            for child in diagram:
                if child.tag == "mxGraphModel":
                    return child
    raise LayoutInputError(
        "不是有效的 draw.io XML: 需要根级 mxGraphModel 或 "
        "mxfile/diagram/mxGraphModel"
    )


def _parse_page_dimension(model, attribute, default):
    raw = model.get(attribute)
    if raw is None or not raw.strip():
        return default, True
    value = _required_number(model, attribute, "mxGraphModel")
    if value <= 0:
        raise LayoutInputError("mxGraphModel.%s 必须大于 0" % attribute)
    return value, False


def _parse_page_dimensions(model):
    width, width_defaulted = _parse_page_dimension(
        model, "pageWidth", DEFAULT_PAGE_WIDTH_PX
    )
    height, height_defaulted = _parse_page_dimension(
        model, "pageHeight", DEFAULT_PAGE_HEIGHT_PX
    )
    return width, height, width_defaulted, height_defaulted


def _parse_point(element, context, missing_default=None):
    if missing_default is None:
        x = _required_number(element, "x", context)
        y = _required_number(element, "y", context)
    else:
        x = _optional_number(element, "x", missing_default, context)
        y = _optional_number(element, "y", missing_default, context)
    return (x, y)


def _cell_label(cell, wrapper_label):
    raw = wrapper_label if wrapper_label is not None else cell.get("value", "")
    return _plain_label(raw)


def _parse_node(cell, wrapper_label, wrapper_id=None):
    node_id = cell.get("id") or wrapper_id
    if node_id is None or not node_id.strip():
        raise LayoutInputError("vertex mxCell 缺少非空 id")
    geometry = _direct_child(cell, "mxGeometry")
    if geometry is None:
        raise LayoutInputError("节点 %s 缺少 mxGeometry" % _display(node_id))
    context = "节点 %s 的 mxGeometry" % _display(node_id)
    x = _required_number(geometry, "x", context)
    y = _required_number(geometry, "y", context)
    width = _required_number(geometry, "width", context)
    height = _required_number(geometry, "height", context)
    if width <= 0 or height <= 0:
        raise LayoutInputError("节点 %s 的 width/height 必须大于 0" % _display(node_id))
    style = _parse_style(cell.get("style"))
    rotation = (
        _style_number(style, "rotation", "节点 %s" % _display(node_id))
        if "rotation" in style
        else 0.0
    )
    return Node(
        id=node_id,
        label=_cell_label(cell, wrapper_label),
        box=(x, y, x + width, y + height),
        rotation=rotation,
    )


def _parse_edge(cell, wrapper_label, wrapper_id=None):
    edge_id = cell.get("id") or wrapper_id
    if edge_id is None or not edge_id.strip():
        raise LayoutInputError("edge mxCell 缺少非空 id")
    geometry = _direct_child(cell, "mxGeometry")
    source_point = None
    target_point = None
    offset = (0.0, 0.0)
    label_position = (0.0, 0.0)
    label_relative = False
    route_points = []
    if geometry is not None:
        context = "边 %s 的 mxGeometry" % _display(edge_id)
        label_position = (
            _optional_number(geometry, "x", 0.0, context),
            _optional_number(geometry, "y", 0.0, context),
        )
        label_relative = geometry.get("relative") in ("1", "true")
        for child in geometry:
            child_name = _local_name(child.tag)
            role = child.get("as")
            if child_name == "mxPoint":
                context = "边 %s 的 mxPoint" % _display(edge_id)
                if role == "sourcePoint":
                    source_point = _parse_point(child, context)
                elif role == "targetPoint":
                    target_point = _parse_point(child, context)
                elif role == "offset":
                    offset = _parse_point(child, context, missing_default=0.0)
                elif role in (None, "point"):
                    route_points.append(_parse_point(child, context))
            elif child_name == "Array" and role == "points":
                for point in child:
                    if _local_name(point.tag) != "mxPoint":
                        continue
                    route_points.append(
                        _parse_point(
                            point,
                            "边 %s 的路由点" % _display(edge_id),
                        )
                    )
    style = _parse_style(cell.get("style"))
    return _RawEdge(
        id=edge_id,
        label=_cell_label(cell, wrapper_label),
        source=cell.get("source"),
        target=cell.get("target"),
        source_point=source_point,
        route_points=tuple(route_points),
        target_point=target_point,
        offset=offset,
        source_constraint=_terminal_constraint(style, "exit", edge_id),
        target_constraint=_terminal_constraint(style, "entry", edge_id),
        label_position=label_position,
        label_relative=label_relative,
    )


def _collect_cells(graph_root):
    nodes = []
    raw_edges = []

    def visit(element, wrapper_label=None, wrapper_id=None):
        name = _local_name(element.tag)
        next_wrapper = wrapper_label
        next_wrapper_id = wrapper_id
        if name.lower() in ("userobject", "object"):
            next_wrapper = element.get("label")
            if next_wrapper is None:
                next_wrapper = element.get("value")
            next_wrapper_id = element.get("id") or wrapper_id
        if name == "mxCell":
            if element.get("vertex") == "1":
                nodes.append(_parse_node(element, wrapper_label, wrapper_id))
            elif element.get("edge") == "1":
                raw_edges.append(_parse_edge(element, wrapper_label, wrapper_id))
        for child in element:
            visit(child, next_wrapper, next_wrapper_id)

    visit(graph_root)
    node_ids = set()
    for node in nodes:
        if node.id in node_ids:
            raise LayoutInputError("节点 id 重复: %s" % _display(node.id))
        node_ids.add(node.id)
    return tuple(nodes), tuple(raw_edges)


def _center(node):
    x1, y1, x2, y2 = node.box
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def _boundary_point(node, toward):
    center_x, center_y = _center(node)
    dx = toward[0] - center_x
    dy = toward[1] - center_y
    if dx == 0.0 and dy == 0.0:
        return (center_x, node.box[1])
    half_width = (node.box[2] - node.box[0]) / 2.0
    half_height = (node.box[3] - node.box[1]) / 2.0
    scale_x = math.inf if dx == 0.0 else half_width / abs(dx)
    scale_y = math.inf if dy == 0.0 else half_height / abs(dy)
    scale = min(scale_x, scale_y)
    return (center_x + dx * scale, center_y + dy * scale)


def _constraint_point(node, constraint):
    x, y, dx, dy = constraint
    left, top, right, bottom = node.box
    point = (
        left + x * (right - left) + dx,
        top + y * (bottom - top) + dy,
    )
    if abs(node.rotation) <= 1e-9:
        return point
    center_x, center_y = _center(node)
    delta_x = point[0] - center_x
    delta_y = point[1] - center_y
    radians = math.radians(node.rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (
        center_x + delta_x * cosine - delta_y * sine,
        center_y + delta_x * sine + delta_y * cosine,
    )


def _node_polygon(node):
    center_x, center_y = _center(node)
    half_width = (node.box[2] - node.box[0]) / 2.0
    half_height = (node.box[3] - node.box[1]) / 2.0
    radians = math.radians(node.rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    points = []
    for local_x, local_y in (
        (-half_width, -half_height),
        (half_width, -half_height),
        (half_width, half_height),
        (-half_width, half_height),
    ):
        points.append(
            (
                center_x + local_x * cosine - local_y * sine,
                center_y + local_x * sine + local_y * cosine,
            )
        )
    return tuple(points)


def _polygon_axes(polygon):
    axes = []
    for index, first in enumerate(polygon):
        second = polygon[(index + 1) % len(polygon)]
        edge_x = second[0] - first[0]
        edge_y = second[1] - first[1]
        length = math.hypot(edge_x, edge_y)
        axes.append((-edge_y / length, edge_x / length))
    return tuple(axes)


def _nodes_overlap(first_node, second_node):
    first = _node_polygon(first_node)
    second = _node_polygon(second_node)
    for axis in _polygon_axes(first) + _polygon_axes(second):
        first_projection = tuple(
            point[0] * axis[0] + point[1] * axis[1] for point in first
        )
        second_projection = tuple(
            point[0] * axis[0] + point[1] * axis[1] for point in second
        )
        overlap = min(max(first_projection), max(second_projection)) - max(
            min(first_projection), min(second_projection)
        )
        if overlap <= EPSILON_PX + 1e-9:
            return False
    return True


def _point_in_open_node(point, node):
    local_point = _point_in_node_coordinates(point, node)
    return _point_in_open_rect(local_point, _local_node_rect(node))


def _point_in_node_coordinates(point, node):
    center_x, center_y = _center(node)
    delta_x = point[0] - center_x
    delta_y = point[1] - center_y
    radians = math.radians(node.rotation)
    cosine = math.cos(radians)
    sine = math.sin(radians)
    return (
        delta_x * cosine + delta_y * sine,
        -delta_x * sine + delta_y * cosine,
    )


def _local_node_rect(node):
    half_width = (node.box[2] - node.box[0]) / 2.0
    half_height = (node.box[3] - node.box[1]) / 2.0
    return (
        -half_width + EPSILON_PX,
        -half_height + EPSILON_PX,
        half_width - EPSILON_PX,
        half_height - EPSILON_PX,
    )


def _segment_intersects_node(first, second, node):
    return _segment_intersects_open_rect(
        _point_in_node_coordinates(first, node),
        _point_in_node_coordinates(second, node),
        _local_node_rect(node),
    )


def _resolve_edge(raw_edge, node_by_id):
    source_node = node_by_id.get(raw_edge.source)
    target_node = node_by_id.get(raw_edge.target)
    route = list(raw_edge.route_points)

    source_point = None
    if raw_edge.source_constraint is not None and source_node is not None:
        source_point = _constraint_point(source_node, raw_edge.source_constraint)
    elif raw_edge.source_point is not None:
        source_point = raw_edge.source_point
    elif source_node is not None and abs(source_node.rotation) > 1e-9 and route:
        source_point = route.pop(0)
    if source_point is None and source_node is not None:
        if route:
            toward = route[0]
        elif target_node is not None:
            toward = _center(target_node)
        else:
            toward = _center(source_node)
        source_point = _boundary_point(source_node, toward)

    target_point = None
    if raw_edge.target_constraint is not None and target_node is not None:
        target_point = _constraint_point(target_node, raw_edge.target_constraint)
    elif raw_edge.target_point is not None:
        target_point = raw_edge.target_point
    elif target_node is not None and abs(target_node.rotation) > 1e-9 and route:
        target_point = route.pop()
    if target_point is None and target_node is not None:
        if route:
            toward = route[-1]
        elif source_node is not None:
            toward = _center(source_node)
        else:
            toward = _center(target_node)
        target_point = _boundary_point(target_node, toward)

    points = []
    if source_point is not None:
        points.append(source_point)
    points.extend(route)
    if target_point is not None:
        points.append(target_point)
    return Edge(
        id=raw_edge.id,
        label=raw_edge.label,
        source=raw_edge.source,
        target=raw_edge.target,
        points=tuple(points),
        offset=raw_edge.offset,
        label_position=raw_edge.label_position,
        label_relative=raw_edge.label_relative,
    )


def _drawio_round(value):
    return math.floor(value + 0.5)


def _edge_label_center(edge_value):
    first = edge_value.points[0]
    last = edge_value.points[-1]
    if not edge_value.label_relative:
        return (
            (first[0] + last[0]) / 2.0 + edge_value.offset[0],
            (first[1] + last[1]) / 2.0 + edge_value.offset[1],
        )

    segments = tuple(
        math.hypot(second[0] - first[0], second[1] - first[1])
        for first, second in zip(edge_value.points, edge_value.points[1:])
    )
    total_length = sum(segments)
    relative_x, relative_y = edge_value.label_position
    distance = _drawio_round((relative_x / 2.0 + 0.5) * total_length)
    segment_index = 0
    previous_length = 0.0
    while (
        distance >= _drawio_round(previous_length + segments[segment_index])
        and segment_index < len(segments) - 1
    ):
        previous_length += segments[segment_index]
        segment_index += 1

    segment = segments[segment_index]
    factor = 0.0 if segment == 0.0 else (distance - previous_length) / segment
    first = edge_value.points[segment_index]
    second = edge_value.points[segment_index + 1]
    delta_x = second[0] - first[0]
    delta_y = second[1] - first[1]
    normal_x = 0.0 if segment == 0.0 else delta_y / segment
    normal_y = 0.0 if segment == 0.0 else delta_x / segment
    return (
        first[0] + delta_x * factor + normal_x * relative_y + edge_value.offset[0],
        first[1] + delta_y * factor - normal_y * relative_y + edge_value.offset[1],
    )


def _segment_intersects_open_rect(first, second, rect):
    left, top, right, bottom = rect
    if right <= left or bottom <= top:
        return False

    def open_interval(start, end, low, high):
        delta = end - start
        if delta == 0.0:
            if low < start < high:
                return (-math.inf, math.inf)
            return None
        first_t = (low - start) / delta
        second_t = (high - start) / delta
        return (min(first_t, second_t), max(first_t, second_t))

    x_interval = open_interval(first[0], second[0], left, right)
    y_interval = open_interval(first[1], second[1], top, bottom)
    if x_interval is None or y_interval is None:
        return False
    lower = max(0.0, x_interval[0], y_interval[0])
    upper = min(1.0, x_interval[1], y_interval[1])
    return lower < upper


def _point_in_open_rect(point, rect):
    return rect[0] < point[0] < rect[2] and rect[1] < point[1] < rect[3]


def _node_name(node):
    return _display(node.label or node.id)


def _point_inside_page(point, page_width_px, page_height_px):
    return (
        -EPSILON_PX <= point[0] <= page_width_px + EPSILON_PX
        and -EPSILON_PX <= point[1] <= page_height_px + EPSILON_PX
    )


def _layout_problems(
        nodes, edges, expect_nodes, expect_edges,
        page_width_px, page_height_px):
    problems = []
    seen = set()

    def add(problem):
        if problem not in seen:
            seen.add(problem)
            problems.append(problem)

    if not nodes:
        add("解析到零节点")
    if expect_nodes is not None and len(nodes) != expect_nodes:
        add("期望节点数 %d，实际 %d" % (expect_nodes, len(nodes)))
    if expect_edges is not None and len(edges) != expect_edges:
        add("期望边数 %d，实际 %d" % (expect_edges, len(edges)))

    for node in nodes:
        if any(
                not _point_inside_page(point, page_width_px, page_height_px)
                for point in _node_polygon(node)):
            add("节点 %s 超出页面边界" % _node_name(node))

    node_by_id = {node.id: node for node in nodes}
    for edge_value in edges:
        edge_name = _display(edge_value.id)
        if edge_value.source is None or not edge_value.source.strip():
            add("边 %s 的 source 未绑定" % edge_name)
        elif edge_value.source not in node_by_id:
            add("边 %s 的 source 引用不存在: %s" % (edge_name, _display(edge_value.source)))
        if edge_value.target is None or not edge_value.target.strip():
            add("边 %s 的 target 未绑定" % edge_name)
        elif edge_value.target not in node_by_id:
            add("边 %s 的 target 引用不存在: %s" % (edge_name, _display(edge_value.target)))
        if len(edge_value.points) < 2:
            add("边 %s 缺少可验证的几何路径" % edge_name)
        elif any(
                not _point_inside_page(point, page_width_px, page_height_px)
                for point in edge_value.points):
            add("边 %s 的路径点超出页面边界" % edge_name)
        if edge_value.label and len(edge_value.points) >= 2:
            label_center = _edge_label_center(edge_value)
            if not _point_inside_page(
                    label_center, page_width_px, page_height_px):
                add(
                    '标签 "%s" 中心超出页面边界'
                    % _display(edge_value.label)
                )

    for index, first_node in enumerate(nodes):
        for second_node in nodes[index + 1 :]:
            if _nodes_overlap(first_node, second_node):
                add(
                    "节点重叠: %s <-> %s"
                    % (_node_name(first_node), _node_name(second_node))
                )

    for edge_value in edges:
        if len(edge_value.points) < 2:
            continue
        last_segment = len(edge_value.points) - 2
        for node in nodes:
            intersections = []
            for segment_index in range(len(edge_value.points) - 1):
                if _segment_intersects_node(
                    edge_value.points[segment_index],
                    edge_value.points[segment_index + 1],
                    node,
                ):
                    intersections.append(segment_index)
            if not intersections:
                continue
            edge_name = _display(edge_value.id)
            node_name = _node_name(node)
            if node.id == edge_value.source:
                if any(segment_index > 0 for segment_index in intersections):
                    add("边 %s 重新进入 source 节点 %s" % (edge_name, node_name))
                if 0 in intersections:
                    add("边 %s 首段穿入 source 节点 %s" % (edge_name, node_name))
            elif node.id == edge_value.target:
                if any(segment_index < last_segment for segment_index in intersections):
                    add("边 %s 提前进入 target 节点 %s" % (edge_name, node_name))
                if last_segment in intersections:
                    add("边 %s 末段穿入 target 节点 %s" % (edge_name, node_name))
            else:
                add("边 %s 穿过节点 %s" % (edge_name, node_name))

        if edge_value.label:
            label_center = _edge_label_center(edge_value)
            for node in nodes:
                if _point_in_open_node(label_center, node):
                    add(
                        '标签 "%s" 落在节点 %s 内'
                        % (_display(edge_value.label), _node_name(node))
                    )
    return tuple(problems)


def _coordinate_summary(nodes, page_height_px):
    def inches(value):
        return round(value / PX, 12)

    summaries = []
    for node in nodes:
        x1, y1, x2, y2 = node.box
        summaries.append(
            NodeSummary(
                id=node.id,
                label=node.label,
                center_inches=(
                    inches((x1 + x2) / 2.0),
                    inches(page_height_px - (y1 + y2) / 2.0),
                ),
                size_inches=(inches(x2 - x1), inches(y2 - y1)),
            )
        )
    return tuple(summaries)


def _validate_expected_count(value, name):
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LayoutInputError("%s 必须是非负整数" % name)


def _validate_expected_dimensions(width_inches, height_inches):
    if (width_inches is None) != (height_inches is None):
        raise LayoutInputError(
            "expected_page_width_in 和 expected_page_height_in 必须同时提供"
        )
    if width_inches is None:
        return None, None
    values = []
    for value, name in (
            (width_inches, "expected_page_width_in"),
            (height_inches, "expected_page_height_in")):
        if isinstance(value, bool):
            raise LayoutInputError("%s 必须是正数" % name)
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise LayoutInputError("%s 必须是正数" % name) from None
        if not math.isfinite(number) or number <= 0:
            raise LayoutInputError("%s 必须是正数" % name)
        values.append(number * PX)
    return tuple(values)


def analyze_layout(
        path, expect_nodes=None, expect_edges=None,
        expected_page_width_in=None, expected_page_height_in=None,
        allow_tiled_paper=False):
    """Return parsed counts, layout problems, and a Y-up coordinate summary."""
    _validate_expected_count(expect_nodes, "expect_nodes")
    _validate_expected_count(expect_edges, "expect_edges")
    expected_page_width_px, expected_page_height_px = (
        _validate_expected_dimensions(
            expected_page_width_in, expected_page_height_in
        )
    )
    if allow_tiled_paper and expected_page_width_px is None:
        raise LayoutInputError(
            "allow_tiled_paper 需要同时提供期望页面宽度和高度"
        )
    input_path = Path(path)
    try:
        tree = ET.parse(input_path)
    except ET.ParseError as exc:
        raise LayoutInputError(
            "XML 解析失败 (%s): %s" % (_display(input_path), _display(exc))
        ) from None
    except (OSError, UnicodeError) as exc:
        raise LayoutInputError(
            "无法读取文件 %s: %s" % (_display(input_path), _display(exc))
        ) from None

    model = _find_model(tree.getroot())
    graph_root = _direct_child(model, "root")
    if graph_root is None:
        raise LayoutInputError("不是有效的 draw.io XML: mxGraphModel 缺少 root")
    (
        page_width_px,
        page_height_px,
        page_width_defaulted,
        page_height_defaulted,
    ) = _parse_page_dimensions(model)
    nodes, raw_edges = _collect_cells(graph_root)
    node_by_id = {node.id: node for node in nodes}
    edges = tuple(_resolve_edge(edge_value, node_by_id) for edge_value in raw_edges)
    size_mismatch = False
    if expected_page_width_px is not None:
        size_mismatch = (
            abs(page_width_px - expected_page_width_px)
            > PAGE_DIMENSION_TOLERANCE_PX
            or abs(page_height_px - expected_page_height_px)
            > PAGE_DIMENSION_TOLERANCE_PX
        )
    bounds_width_px = (
        expected_page_width_px if allow_tiled_paper else page_width_px
    )
    bounds_height_px = (
        expected_page_height_px if allow_tiled_paper else page_height_px
    )
    problems = list(_layout_problems(
        nodes,
        edges,
        expect_nodes,
        expect_edges,
        bounds_width_px,
        bounds_height_px,
    ))
    if size_mismatch and not allow_tiled_paper:
        problems.insert(
            0,
            "页面尺寸不匹配: 期望 %.1f x %.1fpx (%.2f x %.2fin)，"
            "实际 %.1f x %.1fpx (%.2f x %.2fin)"
            % (
                expected_page_width_px,
                expected_page_height_px,
                expected_page_width_px / PX,
                expected_page_height_px / PX,
                page_width_px,
                page_height_px,
                page_width_px / PX,
                page_height_px / PX,
            ),
        )
    summary_height_px = (
        expected_page_height_px
        if expected_page_height_px is not None
        else page_height_px
    )
    return LayoutReport(
        nodes=nodes,
        edges=edges,
        page_width_px=page_width_px,
        page_height_px=page_height_px,
        page_width_defaulted=page_width_defaulted,
        page_height_defaulted=page_height_defaulted,
        expected_page_width_px=expected_page_width_px,
        expected_page_height_px=expected_page_height_px,
        bounds_width_px=bounds_width_px,
        bounds_height_px=bounds_height_px,
        tiled_paper=bool(allow_tiled_paper),
        tiled_size_mismatch=bool(allow_tiled_paper and size_mismatch),
        problems=tuple(problems),
        summary=_coordinate_summary(nodes, summary_height_px),
    )


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
    parser = _ArgumentParser(description="验证 draw.io XML 的结构和布局")
    parser.add_argument("file", help="draw.io 导出的未压缩 XML 文件")
    parser.add_argument("--expect-nodes", type=_nonnegative_integer)
    parser.add_argument("--expect-edges", type=_nonnegative_integer)
    parser.add_argument("--expect-page-width-in", type=_positive_float)
    parser.add_argument("--expect-page-height-in", type=_positive_float)
    parser.add_argument("--allow-tiled-paper", action="store_true")
    return parser


def _print_report(report):
    print("节点数: %d" % report.node_count)
    print("边数: %d" % report.edge_count)
    if report.page_width_defaulted:
        print(
            "mxGraphModel.pageWidth 缺失，采用默认值 %.1fpx (%.2fin)"
            % (report.page_width_px, report.page_width_px / PX)
        )
    else:
        print(
            "页面宽度: %.1fpx (%.2fin)"
            % (report.page_width_px, report.page_width_px / PX)
        )
    if report.page_height_defaulted:
        print(
            "mxGraphModel.pageHeight 缺失，采用默认值 %.1fpx (%.2fin)"
            % (report.page_height_px, report.page_height_px / PX)
        )

    if report.expected_page_width_px is not None:
        print(
            "期望页面: %.1f x %.1fpx (%.2f x %.2fin)"
            % (
                report.expected_page_width_px,
                report.expected_page_height_px,
                report.expected_page_width_px / PX,
                report.expected_page_height_px / PX,
            )
        )
    if report.tiled_paper:
        status = "允许纸张尺寸不匹配" if report.tiled_size_mismatch else "纸张尺寸一致"
        print(
            "平铺纸张模式: %s；边界使用 %.1f x %.1fpx"
            % (status, report.bounds_width_px, report.bounds_height_px)
        )
    else:
        print(
            "页面高度: %.1fpx (%.2fin)"
            % (report.page_height_px, report.page_height_px / PX)
        )

    if report.problems:
        print("发现问题 %d 处:" % len(report.problems))
        for problem in report.problems:
            print("  - %s" % problem)
    else:
        print("布局检查全部通过: 无节点重叠 / 无边穿节点 / 无标签遮挡")

    print()
    print("布局结构（英寸，Y向上）:")
    for summary in report.summary:
        print(
            "  %-28s 中心(%5.2f, %5.2f) 尺寸(%.2f x %.2f)"
            % (
                _display(summary.label or summary.id, limit=26),
                summary.center_inches[0],
                summary.center_inches[1],
                summary.size_inches[0],
                summary.size_inches[1],
            )
        )


def main(argv=None):
    """Return 0 on success, 1 on layout issues, or 2 on input errors."""
    try:
        arguments = _build_parser().parse_args(argv)
    except _ArgumentExit as exc:
        return exc.status
    except LayoutInputError as exc:
        print("错误: %s" % _display(exc), file=sys.stderr)
        return 2

    try:
        report = analyze_layout(
            arguments.file,
            expect_nodes=arguments.expect_nodes,
            expect_edges=arguments.expect_edges,
            expected_page_width_in=arguments.expect_page_width_in,
            expected_page_height_in=arguments.expect_page_height_in,
            allow_tiled_paper=arguments.allow_tiled_paper,
        )
    except LayoutInputError as exc:
        print("错误: %s" % _display(exc), file=sys.stderr)
        return 2
    _print_report(report)
    return 1 if report.problems else 0


if __name__ == "__main__":
    sys.exit(main())
