#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vsdx_gen.py - Pure-stdlib single-page VSDX package generator.

Turns a JSON diagram description into an OPC .vsdx package (ZIP of XML parts)
intended for the draw.io VSDX importer. The generated package still needs
draw.io integration and, separately, Microsoft Visio verification.

Only the Python standard library is used - fully offline capable.

JSON contract (units: inches, Y-up, PinX/PinY = shape center):
{
  "page": {"name": "Page-1", "title": "登录流程", "width": 8.5, "height": 11},
  "nodes": [
    {"id": "A", "text": "用户登录", "type": "rect",
     "x": 3.0, "y": 9.0, "w": 1.5, "h": 0.75,
     "fill": "#FFFFFF", "stroke": "#000000",
     "strokeWidth": 0.01,          # inches
     "dashed": false,
     "opacity": 100,               # 0-100
     "gradient": "#DAE8FC",        # optional gradient end color
     "rotation": 0,                # degrees, counter-clockwise
     "fontFamily": "Microsoft YaHei",  # Arial|MS Gothic|Microsoft YaHei|SimSun|SimHei|KaiTi
     "fontSize": 12,               # pt
     "fontColor": "#000000",
     "bold": false, "italic": false, "underline": false,
     "align": "center",            # left|center|right
     "valign": "middle"}           # top|middle|bottom
  ],
  "edges": [
    {"id": "e1", "from": "A", "to": "B", "label": "成功",
     "fromSide": "bottom", "toSide": "top",   # optional; auto if omitted
     "lineColor": "#000000", "strokeWidth": 0.01, "dashed": false,
     "startArrow": "none", "endArrow": "block",  # none|open|block|classic|oval|diamond|blockThin|dash
     "points": [[3.5, 6.0], [3.5, 5.0]]}        # optional waypoints (inches)
  ]
}

Shape types: rect, diamond, ellipse, process (rounded rect), cylinder,
document, note, triangle, pentagon, hexagon, parallelogram, trapezoid,
arrow (right), leftArrow, upArrow, downArrow, star.
Custom geometry escape hatch (the 15 row types the importer supports):
  "geometry": [
    ["MoveTo",        {"x": 0, "y": 0}],
    ["LineTo",        {"x": 1, "y": 0}],
    ["ArcTo",         {"x": 1, "y": 0.5, "a": 0.5}],
    ["Ellipse",       {"x": 0.5, "y": 0.5, "a": 1, "b": 0.5, "c": 0.5, "d": 1}],
    ["EllipticalArcTo",{"x": 1, "y": 0.5, "a": 0.5, "b": 0.5, "c": 0.5, "d": 1}],
    ["InfiniteLine",  {"x": 0, "y": 0, "a": 1, "b": 1}],
    ["NURBSTo",       {"x": 1, "y": 0, "a": 0.5, "b": 0.5, "c": 0.5, "d": 0.5, "e": 1}],
    ["PolylineTo",    {"x": 1, "y": 0.5, "a": "1 0 1 1"}],
    ["RelCubBezTo",   {"x": 1, "y": 0.5, "a": 0, "b": 0.5, "c": 1, "d": 0.5}],
    ["RelQuadBezTo",  {"x": 0.5, "y": 1, "a": 0.5, "b": 0.5}],
    ["RelLineTo",     {"x": 1, "y": 0}],
    ["RelMoveTo",     {"x": 1, "y": 1}],
    ["RelEllipticalArcTo", {"x": 0.5, "y": 1, "a": 0.5, "b": 0.5, "c": 0.5, "d": 1}],
    ["SplineStart",   {"x": 0, "y": 1, "a": 1, "b": 0, "c": 0, "d": 0}],
    ["SplineKnot",    {"x": 1, "y": 1, "a": 0}]
  ]

Usage:
    python "<skill-dir>\\scripts\\vsdx_gen.py" "<input.json>" "<output.vsdx>"
"""

import copy
import errno
import json
import math
import os
import re
import secrets
import sys
from types import MappingProxyType
import zipfile
from xml.etree import ElementTree as ET

NS_VISIO = 'http://schemas.microsoft.com/office/visio/2012/main'
NS_R = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
NS_REL = 'http://schemas.openxmlformats.org/package/2006/relationships'
NS_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'
NS_CP = 'http://schemas.openxmlformats.org/package/2006/metadata/core-properties'
NS_DC = 'http://purl.org/dc/elements/1.1/'
NS_DCTERMS = 'http://purl.org/dc/terms/'
NS_XSI = 'http://www.w3.org/2001/XMLSchema-instance'
NS_EP = 'http://schemas.openxmlformats.org/officeDocument/2006/extended-properties'

# Only prefix namespaces that must coexist in one part. The main namespace of
# each part is left to ET's auto nsN prefix and rewritten to the default
# namespace in _serialize (ET can only hold ONE ''-registration; registering
# '' for several URIs silently drops the earlier ones).
ET.register_namespace('r', NS_R)
ET.register_namespace('cp', NS_CP)
ET.register_namespace('dc', NS_DC)
ET.register_namespace('dcterms', NS_DCTERMS)
ET.register_namespace('xsi', NS_XSI)

V = lambda tag: '{%s}%s' % (NS_VISIO, tag)
REL = lambda tag: '{%s}%s' % (NS_REL, tag)
CT = lambda tag: '{%s}%s' % (NS_CT, tag)
RID_ATTR = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'

# Default Visio color palette (indexes 0-55); custom colors appended after
DEFAULT_PALETTE = [
    '#000000', '#FFFFFF', '#FF0000', '#00FF00', '#0000FF', '#FFFF00',
    '#FF00FF', '#00FFFF', '#800000', '#008000', '#000080', '#808000',
    '#800080', '#008080', '#C0C0C0', '#808080', '#9999FF', '#993366',
    '#FFFFCC', '#CCFFFF', '#660066', '#FF8080', '#0066CC', '#CCCCFF',
    '#000080', '#FF00FF', '#FFFF00', '#00FFFF', '#800080', '#800000',
    '#008080', '#0000FF', '#00CCFF', '#CCFFFF', '#CCFFCC', '#FFFF99',
    '#99CCFF', '#FF99CC', '#CC99FF', '#FFCC99', '#3366FF', '#33CCCC',
    '#99CC00', '#FFCC00', '#FF9900', '#FF6600', '#666699', '#969696',
    '#003366', '#339966', '#003300', '#333300', '#993300', '#993366',
    '#333399', '#333333',
]

FONTS = ['Arial', 'MS Gothic', 'Microsoft YaHei', 'SimSun', 'SimHei', 'KaiTi']

# VSDX arrow ids -> draw.io arrow names (importer maps back the same way)
ARROWS = {
    'none': 0, 'open': 1, 'blockThin': 2, 'block': 4, 'classic': 5,
    'openAsync': 9, 'oval': 10, 'diamond': 11, 'blockThin2': 15,
    'classic2': 17, 'oval2': 20, 'diamond2': 22, 'dash': 23,
}
PUBLIC_ARROW_VALUES = frozenset((
    'none', 'open', 'block', 'classic', 'oval', 'diamond', 'blockThin', 'dash',
))

_TOP_LEVEL_FIELDS = frozenset(('page', 'nodes', 'edges'))
_PAGE_FIELDS = frozenset(('name', 'title', 'width', 'height'))
_NODE_FIELDS = frozenset((
    'id', 'x', 'y', 'w', 'h', 'text', 'type', 'fill', 'stroke',
    'strokeWidth', 'dashed', 'opacity', 'gradient', 'rotation', 'fontFamily',
    'fontSize', 'fontColor', 'bold', 'italic', 'underline', 'align', 'valign',
    'geometry',
))
_EDGE_FIELDS = frozenset((
    'id', 'from', 'to', 'label', 'fromSide', 'toSide', 'lineColor',
    'strokeWidth', 'dashed', 'startArrow', 'endArrow', 'fontFamily',
    'fontSize', 'labelColor', 'points', 'routing',
))


def _el(tag, **attrs):
    return ET.Element(tag, {k: str(v) for k, v in attrs.items()})


def _cell(name, value):
    if isinstance(value, float):
        value = round(value, 4)  # avoid float artifacts in generated XML
    return _el(V('Cell'), N=name, V=value)


def _cell_formula(name, value, formula):
    if isinstance(value, float):
        value = round(value, 4)
    return _el(V('Cell'), N=name, V=value, F=formula)


_HEX_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')


class InputValidationError(ValueError):
    pass


class PackageValidationError(RuntimeError):
    """Raised when a generated temporary package fails validation."""

    def __init__(self, errors):
        self.errors = tuple(str(error) for error in errors)
        super().__init__('generated package validation failed:\n  - '
                         + '\n  - '.join(self.errors))


def _is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _is_xml_1_0_text(value):
    return all(
        codepoint in (0x09, 0x0A, 0x0D)
        or 0x20 <= codepoint <= 0xD7FF
        or 0xE000 <= codepoint <= 0xFFFD
        or 0x10000 <= codepoint <= 0x10FFFF
        for codepoint in map(ord, value)
    )


def _safe_diagnostic(value):
    """Return an ASCII-only representation safe for validation messages."""
    rendered = ascii(value)
    if isinstance(value, str):
        return rendered[1:-1]
    return rendered


def _unknown_field_errors(value, allowed, prefix):
    return [
        '%s 未知字段: %s' % (prefix, _safe_diagnostic(key))
        for key in sorted((key for key in value if key not in allowed), key=repr)
    ]


def _print_safe(message):
    """Print without letting the configured stdout encoding mask an error."""
    text = str(message)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', errors='backslashreplace').decode('ascii'))


_POLYLINE_NUMBER_RE = re.compile(
    r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z'
)


def _normalize_polyline_a(value, prefix):
    """Validate and canonicalize the whitespace-separated PolylineTo.A text."""
    if not isinstance(value, str):
        return None, '%s A 必须是包含有限数字标记的字符串' % prefix
    if not _is_xml_1_0_text(value):
        return None, '%s A 包含 XML 1.0 不允许的字符' % prefix
    tokens = value.split()
    if not tokens:
        return None, '%s A 必须包含至少一个有限数字标记' % prefix
    for token in tokens:
        if not _POLYLINE_NUMBER_RE.fullmatch(token):
            return None, '%s A 包含无效数字标记' % prefix
        try:
            parsed = float(token)
        except (OverflowError, TypeError, ValueError):
            return None, '%s A 包含无效数字标记' % prefix
        if not math.isfinite(parsed):
            return None, '%s A 必须只包含有限数字标记' % prefix
    return ' '.join(tokens), None


def _normalize_geometry(raw_geometry, node_width, node_height, prefix):
    """Validate custom geometry and return an independent canonical JSON form."""
    errors = []
    if not isinstance(raw_geometry, list) or not raw_geometry:
        return None, ['%s 必须是非空数组' % prefix]

    normalized = []
    for index, entry in enumerate(raw_geometry):
        row_prefix = '%s[%d]' % (prefix, index)
        if not isinstance(entry, list) or len(entry) != 2:
            errors.append('%s 必须是恰好包含 [rowType, paramsObject] 的数组' % row_prefix)
            continue

        row_type, raw_params = entry
        if not isinstance(row_type, str) or row_type not in _ROW_CELLS:
            errors.append('%s 行类型未知或大小写不正确' % row_prefix)
            continue
        if index == 0 and row_type != 'MoveTo':
            errors.append('%s 第一行必须是 MoveTo' % row_prefix)
        if not isinstance(raw_params, dict):
            errors.append('%s 参数必须是 JSON 对象' % row_prefix)
            continue

        # Collect source names first. This makes x/X a duplicate instead of
        # silently selecting whichever spelling happened to be iterated last.
        source_keys = {}
        for key in raw_params:
            if not isinstance(key, str):
                errors.append('%s 参数键必须是字符串' % row_prefix)
                continue
            canonical_key = key.upper()
            if canonical_key in source_keys:
                errors.append(
                    '%s 参数键重复（忽略大小写）: %s/%s'
                    % (row_prefix,
                       _safe_diagnostic(source_keys[canonical_key]),
                       _safe_diagnostic(key))
                )
            else:
                source_keys[canonical_key] = key

        required_cells = _ROW_CELLS[row_type]
        for key in source_keys:
            if key not in required_cells:
                errors.append('%s 包含未知参数单元格: %s'
                              % (row_prefix, _safe_diagnostic(key)))
        for cell in required_cells:
            if cell not in source_keys:
                errors.append('%s 缺少必需参数单元格: %s' % (row_prefix, cell))

        canonical_params = {}
        for cell in required_cells:
            source_key = source_keys.get(cell)
            if source_key is None:
                continue
            value = raw_params[source_key]
            if row_type == 'PolylineTo' and cell == 'A':
                canonical_value, error = _normalize_polyline_a(value, row_prefix)
                if error:
                    errors.append(error)
                else:
                    canonical_params[cell] = canonical_value
                continue
            if not _is_finite_number(value):
                errors.append(
                    '%s.%s 必须是不能为布尔值的有限 JSON 数字'
                    % (row_prefix, cell)
                )
                continue
            if cell in ('X', 'Y'):
                if row_type.startswith('Rel'):
                    lower, upper = 0.0, 1.0
                else:
                    lower = 0.0
                    upper = node_width if cell == 'X' else node_height
                if _is_finite_number(upper) and not lower <= value <= upper:
                    errors.append(
                        '%s.%s 必须位于 %.12g..%.12g 范围内'
                        % (row_prefix, cell, lower, upper)
                    )
                    continue
            canonical_params[cell] = value
        normalized.append([row_type, canonical_params])

    return normalized, errors


def _validate_and_normalize(data):
    """Return a normalized deep copy and all input-contract errors."""
    errors = []
    if not isinstance(data, dict):
        return {}, ['root 顶层必须是对象']

    normalized = copy.deepcopy(data)
    for key in sorted((k for k in data if k not in _TOP_LEVEL_FIELDS), key=repr):
        errors.append('未知顶层字段: %s' % _safe_diagnostic(key))

    raw_page = data.get('page')
    if raw_page is None:
        raw_page = {}
    elif not isinstance(raw_page, dict):
        errors.append('page 必须是对象或 null')
        raw_page = {}
    errors.extend(_unknown_field_errors(raw_page, _PAGE_FIELDS, 'page'))
    page = {'name': 'Page-1', 'width': 8.5, 'height': 11}
    page.update(copy.deepcopy(raw_page))
    if 'name' in raw_page:
        name = raw_page['name']
        if not isinstance(name, str) or not name.strip():
            errors.append('page.name 必须是非空字符串')
        elif not _is_xml_1_0_text(name):
            errors.append('page.name 包含 XML 1.0 不允许的字符')
        else:
            page['name'] = name.strip()
    if 'title' in raw_page:
        if not isinstance(raw_page['title'], str):
            errors.append('page.title 必须是字符串')
        elif not _is_xml_1_0_text(raw_page['title']):
            errors.append('page.title 包含 XML 1.0 不允许的字符')
    for key in ('width', 'height'):
        if key in raw_page and (
                not _is_finite_number(raw_page[key]) or raw_page[key] <= 0):
            errors.append('page.%s 必须是正的有限数字' % key)
    normalized['page'] = page

    raw_nodes = data.get('nodes')
    nodes = raw_nodes if isinstance(raw_nodes, list) else []
    if not isinstance(raw_nodes, list):
        errors.append('nodes 必须是非空数组')
    elif not raw_nodes:
        errors.append('nodes 必须是非空数组')

    ids = {}
    for i, node in enumerate(nodes):
        prefix = 'nodes[%d]' % i
        if not isinstance(node, dict):
            errors.append('%s 必须是对象' % prefix)
            continue
        node_id = node.get('id')
        display_id = _safe_diagnostic(node_id) if isinstance(node_id, str) else '?'
        errors.extend(_unknown_field_errors(
            node, _NODE_FIELDS, '%s(%s)' % (prefix, display_id)
        ))
        if not isinstance(node_id, str) or not node_id.strip():
            errors.append('%s id 必须是非空字符串' % prefix)
        else:
            if node_id in ids:
                errors.append('%s id 重复: %s (另见 nodes[%d])'
                              % (prefix, _safe_diagnostic(node_id), ids[node_id]))
            else:
                ids[node_id] = i

        for key in (
                'text', 'type', 'fill', 'stroke', 'strokeWidth', 'dashed',
                'opacity', 'gradient', 'rotation', 'fontFamily', 'fontSize',
                'fontColor', 'bold', 'italic', 'underline', 'align', 'valign',
                'geometry'):
            if key in node and node[key] is None:
                errors.append('%s(%s) %s 不能是 null；请省略该字段以使用默认值'
                              % (prefix, display_id, key))

        if 'text' in node and node['text'] is not None:
            if not isinstance(node['text'], str):
                errors.append('%s(%s) text 必须是字符串' % (prefix, display_id))
            elif not _is_xml_1_0_text(node['text']):
                errors.append('%s.text 包含 XML 1.0 不允许的字符' % prefix)

        for key in ('x', 'y', 'w', 'h'):
            if key not in node:
                errors.append('%s(%s) 缺少 %s' % (prefix, display_id, key))
            elif not _is_finite_number(node[key]):
                errors.append('%s(%s) %s 必须是有限数字且不能是布尔值'
                              % (prefix, display_id, key))
            elif key in ('w', 'h') and node[key] <= 0:
                errors.append('%s(%s) %s 必须 > 0' % (prefix, display_id, key))

        if node.get('geometry') is None:
            shape_type = node.get('type', 'rect')
            if not isinstance(shape_type, str) or shape_type not in _SHAPE_GEO:
                errors.append('%s(%s) type 未知形状 "%s"（可用: %s）'
                              % (prefix, display_id, _safe_diagnostic(shape_type),
                                 ' '.join(sorted(_SHAPE_GEO))))
        else:
            if 'type' in node and not isinstance(node['type'], str):
                errors.append('%s(%s) type 必须是字符串' % (prefix, display_id))
            elif 'type' in node and node['type'] not in _SHAPE_GEO:
                errors.append('%s(%s) type 未知形状 "%s"（可用: %s）'
                              % (prefix, display_id,
                                 _safe_diagnostic(node['type']),
                                 ' '.join(sorted(_SHAPE_GEO))))
            canonical_geometry, geometry_errors = _normalize_geometry(
                node['geometry'],
                node.get('w'),
                node.get('h'),
                '%s.geometry' % prefix,
            )
            errors.extend(geometry_errors)
            if canonical_geometry is not None and isinstance(normalized.get('nodes'), list):
                normalized['nodes'][i]['geometry'] = canonical_geometry

        for key, allowed in (('align', ('left', 'center', 'right')),
                             ('valign', ('top', 'middle', 'bottom'))):
            if node.get(key) is not None and node[key] not in allowed:
                errors.append('%s(%s) %s 必须是 %s'
                              % (prefix, display_id, key, '/'.join(allowed)))
        fill = node.get('fill')
        if fill is not None and (
                not isinstance(fill, str)
                or (fill.lower() not in ('none', 'transparent')
                    and not _HEX_RE.fullmatch(fill))):
            errors.append('%s(%s) fill 必须是 #RRGGBB、none 或 transparent'
                          % (prefix, display_id))
        for key in ('stroke', 'gradient', 'fontColor'):
            value = node.get(key)
            if value is not None and (
                    not isinstance(value, str) or not _HEX_RE.fullmatch(value)):
                errors.append('%s(%s) %s 必须是 #RRGGBB'
                              % (prefix, display_id, key))
        if node.get('fontFamily') is not None and node['fontFamily'] not in FONTS:
            errors.append('%s(%s) fontFamily 必须是 %s'
                          % (prefix, display_id, '/'.join(FONTS)))
        for key in ('strokeWidth', 'fontSize'):
            value = node.get(key)
            if value is not None and (
                    not _is_finite_number(value) or value <= 0):
                errors.append('%s(%s) %s 必须是正的有限数字'
                              % (prefix, display_id, key))
        opacity = node.get('opacity')
        if opacity is not None and (
                not _is_finite_number(opacity) or not 0 <= opacity <= 100):
            errors.append('%s(%s) opacity 必须是 0-100 的有限数字'
                          % (prefix, display_id))
        rotation = node.get('rotation')
        if rotation is not None and not _is_finite_number(rotation):
            errors.append('%s(%s) rotation 必须是有限数字且不能是布尔值'
                          % (prefix, display_id))
        for key in ('dashed', 'bold', 'italic', 'underline'):
            if node.get(key) is not None and not isinstance(node[key], bool):
                errors.append('%s(%s) %s 必须是布尔值'
                              % (prefix, display_id, key))

    if 'edges' not in data:
        normalized['edges'] = []
        edges = []
    else:
        raw_edges = data['edges']
        edges = raw_edges if isinstance(raw_edges, list) else []
        if not isinstance(raw_edges, list):
            errors.append('edges 必须是数组')

    for i, edge in enumerate(edges):
        prefix = 'edges[%d]' % i
        if not isinstance(edge, dict):
            errors.append('%s 必须是对象' % prefix)
            continue
        errors.extend(_unknown_field_errors(edge, _EDGE_FIELDS, prefix))
        for key in (
                'id', 'label', 'fromSide', 'toSide', 'lineColor', 'strokeWidth',
                'dashed', 'startArrow', 'endArrow', 'fontFamily', 'fontSize',
                'labelColor', 'points'):
            if key in edge and edge[key] is None:
                errors.append('%s %s 不能是 null；请省略该字段以使用默认值'
                              % (prefix, key))
        if 'id' in edge and edge['id'] is not None \
                and (not isinstance(edge['id'], str) or not edge['id']):
            errors.append('%s id 必须是非空字符串' % prefix)
        if 'label' in edge and edge['label'] is not None:
            if not isinstance(edge['label'], str):
                errors.append('%s label 必须是字符串' % prefix)
            elif not _is_xml_1_0_text(edge['label']):
                errors.append('%s.label 包含 XML 1.0 不允许的字符' % prefix)
        endpoints = {}
        for key in ('from', 'to'):
            value = edge.get(key)
            if not isinstance(value, str) or not value:
                errors.append('%s %s 必须是非空节点 id' % (prefix, key))
            else:
                endpoints[key] = value
                if value not in ids:
                    errors.append('%s %s 引用了不存在的节点 "%s"'
                                  % (prefix, key, _safe_diagnostic(value)))
        if endpoints.get('from') == endpoints.get('to') and 'from' in endpoints:
            errors.append('%s 自环不受支持: from == to' % prefix)

        for key in ('fromSide', 'toSide'):
            if edge.get(key) is not None and edge[key] not in (
                    'top', 'bottom', 'left', 'right'):
                errors.append('%s %s 必须是 top/bottom/left/right' % (prefix, key))
        for key in ('startArrow', 'endArrow'):
            if edge.get(key) is not None and (
                    not isinstance(edge[key], str)
                    or edge[key] not in PUBLIC_ARROW_VALUES):
                errors.append('%s %s 必须是 %s'
                              % (prefix, key,
                                 '/'.join(sorted(PUBLIC_ARROW_VALUES))))
        if edge.get('fontFamily') is not None and edge['fontFamily'] not in FONTS:
            errors.append('%s fontFamily 必须是 %s'
                          % (prefix, '/'.join(FONTS)))
        if edge.get('routing') is not None and edge['routing'] not in (
                'auto', 'straight', 'elbow'):
            errors.append('%s routing 必须是 auto/straight/elbow' % prefix)
        for key in ('lineColor', 'labelColor'):
            value = edge.get(key)
            if value is not None and (
                    not isinstance(value, str) or not _HEX_RE.fullmatch(value)):
                errors.append('%s %s 必须是 #RRGGBB' % (prefix, key))
        for key in ('strokeWidth', 'fontSize'):
            value = edge.get(key)
            if value is not None and (
                    not _is_finite_number(value) or value <= 0):
                errors.append('%s %s 必须是正的有限数字' % (prefix, key))
        if edge.get('dashed') is not None and not isinstance(edge['dashed'], bool):
            errors.append('%s dashed 必须是布尔值' % prefix)
        if 'points' in edge:
            points = edge['points']
            if not isinstance(points, list) or any(
                    not isinstance(point, list) or len(point) != 2
                    or not all(_is_finite_number(coordinate) for coordinate in point)
                    for point in points):
                errors.append('%s points 必须是 [[x,y],...] 有限数字对数组' % prefix)
    return normalized, errors


def validate_input(data):
    return _validate_and_normalize(data)[1]


def normalize_input(data):
    normalized, errors = _validate_and_normalize(data)
    if errors:
        raise InputValidationError('输入校验失败:\n  - ' + '\n  - '.join(errors))
    return normalized


def _make_palette(nodes, edges):
    """Return dict hex->index, appending custom colors after the default 56."""
    palette = {c: i for i, c in enumerate(DEFAULT_PALETTE)}
    wanted = []
    for n in nodes:
        fill = (n.get('fill') or '#FFFFFF').upper()
        if fill not in ('NONE', 'TRANSPARENT'):
            wanted.append(fill)
        wanted.append((n.get('stroke') or '#000000').upper())
        if n.get('gradient'):
            wanted.append(n['gradient'].upper())
        if n.get('fontColor'):
            wanted.append(n['fontColor'].upper())
    for e in edges:
        if e.get('lineColor'):
            wanted.append(e['lineColor'].upper())
        if e.get('labelColor'):
            wanted.append(e['labelColor'].upper())
    next_idx = len(DEFAULT_PALETTE)
    for c in wanted:
        if c not in palette:
            palette[c] = next_idx
            next_idx += 1
    return palette


# ---------------------------------------------------------------- geometry

def _geo_rows_rect(w, h):
    return [('MoveTo', [(0, 0)]), ('LineTo', [(w, 0)]), ('LineTo', [(w, h)]),
            ('LineTo', [(0, h)]), ('LineTo', [(0, 0)])]


def _geo_rows_diamond(w, h):
    return [('MoveTo', [(w / 2, 0)]), ('LineTo', [(w, h / 2)]),
            ('LineTo', [(w / 2, h)]), ('LineTo', [(0, h / 2)]),
            ('LineTo', [(w / 2, 0)])]


def _geo_rows_ellipse(w, h):
    # Ellipse row: X,Y center; A,B and C,D two points on the ellipse
    return [('Ellipse', [(w / 2, h / 2, w, h / 2, w / 2, h)])]


def _geo_rows_process(w, h):
    r = min(w, h) * 0.1
    rows = [('MoveTo', [(r, 0)]), ('LineTo', [(w - r, 0)])]
    for x, y in ((w, r), (w, h - r), (w - r, h), (r, h), (0, h - r), (0, r)):
        if x == w and y == r:
            rows.append(('ArcTo', [(w, r, r)]))
        elif x == w - r and y == h:
            rows.append(('ArcTo', [(w - r, h, r)]))
        elif x == 0 and y == h - r:
            rows.append(('ArcTo', [(0, h - r, r)]))
        elif x == r and y == 0:
            rows.append(('ArcTo', [(r, 0, r)]))
        else:
            rows.append(('LineTo', [(x, y)]))
    return rows


def _geo_rows_cylinder(w, h):
    cap = min(h * 0.1, w * 0.1)
    if cap == 0.0:
        cap = min(h, w)
    return [('MoveTo', [(0, cap)]), ('ArcTo', [(w, cap, cap)]),
            ('LineTo', [(w, h - cap)]),
            ('ArcTo', [(0, h - cap, cap)]),
            ('LineTo', [(0, cap)])]


def _geo_rows_document(w, h):
    f = min(w, h) * 0.15
    return [('MoveTo', [(0, 0)]), ('LineTo', [(w - f, 0)]),
            ('LineTo', [(w, f)]), ('LineTo', [(w, h)]),
            ('LineTo', [(0, h)]), ('LineTo', [(0, 0)])]


def _geo_rows_triangle(w, h):
    return [('MoveTo', [(w / 2, 0)]), ('LineTo', [(w, h)]),
            ('LineTo', [(0, h)]), ('LineTo', [(w / 2, 0)])]


def _geo_rows_pentagon(w, h):
    return [('MoveTo', [(w / 2, 0)]), ('LineTo', [(w, 0.4 * h)]),
            ('LineTo', [(0.8 * w, h)]), ('LineTo', [(0.2 * w, h)]),
            ('LineTo', [(0, 0.4 * h)]), ('LineTo', [(w / 2, 0)])]


def _geo_rows_hexagon(w, h):
    return [('MoveTo', [(0.25 * w, 0)]), ('LineTo', [(0.75 * w, 0)]),
            ('LineTo', [(w, 0.5 * h)]), ('LineTo', [(0.75 * w, h)]),
            ('LineTo', [(0.25 * w, h)]), ('LineTo', [(0, 0.5 * h)]),
            ('LineTo', [(0.25 * w, 0)])]


def _geo_rows_parallelogram(w, h):
    return [('MoveTo', [(0.25 * w, 0)]), ('LineTo', [(w, 0)]),
            ('LineTo', [(0.75 * w, h)]), ('LineTo', [(0, h)]),
            ('LineTo', [(0.25 * w, 0)])]


def _geo_rows_trapezoid(w, h):
    return [('MoveTo', [(0.2 * w, 0)]), ('LineTo', [(0.8 * w, 0)]),
            ('LineTo', [(w, h)]), ('LineTo', [(0, h)]),
            ('LineTo', [(0.2 * w, 0)])]


def _geo_rows_arrow(w, h):
    return [('MoveTo', [(0, 0.2 * h)]), ('LineTo', [(0.55 * w, 0.2 * h)]),
            ('LineTo', [(0.55 * w, 0)]), ('LineTo', [(w, 0.5 * h)]),
            ('LineTo', [(0.55 * w, h)]), ('LineTo', [(0.55 * w, 0.8 * h)]),
            ('LineTo', [(0, 0.8 * h)]), ('LineTo', [(0, 0.2 * h)])]


def _geo_rows_left_arrow(w, h):
    return [('MoveTo', [(0.45 * w, 0.2 * h)]), ('LineTo', [(0.45 * w, 0)]),
            ('LineTo', [(0, 0.5 * h)]), ('LineTo', [(0.45 * w, h)]),
            ('LineTo', [(0.45 * w, 0.8 * h)]), ('LineTo', [(w, 0.8 * h)]),
            ('LineTo', [(w, 0.2 * h)]), ('LineTo', [(0.45 * w, 0.2 * h)])]


def _geo_rows_up_arrow(w, h):
    return [('MoveTo', [(0.2 * w, 0)]), ('LineTo', [(0.2 * w, 0.45 * h)]),
            ('LineTo', [(0, 0.45 * h)]), ('LineTo', [(w / 2, h)]),
            ('LineTo', [(w, 0.45 * h)]), ('LineTo', [(0.8 * w, 0.45 * h)]),
            ('LineTo', [(0.8 * w, 0)]), ('LineTo', [(0.2 * w, 0)])]


def _geo_rows_down_arrow(w, h):
    return [('MoveTo', [(0.2 * w, h)]), ('LineTo', [(0.8 * w, h)]),
            ('LineTo', [(0.8 * w, 0.55 * h)]), ('LineTo', [(w, 0.55 * h)]),
            ('LineTo', [(w / 2, 0)]), ('LineTo', [(0, 0.55 * h)]),
            ('LineTo', [(0.2 * w, 0.55 * h)]), ('LineTo', [(0.2 * w, h)])]


def _geo_rows_star(w, h):
    cx, cy, R = w / 2, h / 2, min(w, h) / 2
    r = R * 0.4
    pts = []
    for i in range(10):
        ang = math.radians(-90 + i * 36)
        rad = R if i % 2 == 0 else r
        pts.append((cx + rad * math.cos(ang), cy + rad * math.sin(ang)))
    rows = [('MoveTo', [pts[0]])]
    for p in pts[1:]:
        rows.append(('LineTo', [p]))
    return rows

# row type -> cell names (in order). IMPORTANT: Visio standard cell names are
# UPPERCASE (X/Y/A/B/C/D/E) - draw.io's importer matches them case-sensitively
# (RowFactory switch); lowercase names silently drop the values.
_ROW_CELLS = MappingProxyType({
    'MoveTo': ('X', 'Y'),
    'LineTo': ('X', 'Y'),
    'ArcTo': ('X', 'Y', 'A'),
    'Ellipse': ('X', 'Y', 'A', 'B', 'C', 'D'),
    'EllipticalArcTo': ('X', 'Y', 'A', 'B', 'C', 'D'),
    'InfiniteLine': ('X', 'Y', 'A', 'B'),
    'NURBSTo': ('X', 'Y', 'A', 'B', 'C', 'D', 'E'),
    'PolylineTo': ('X', 'Y', 'A'),
    'RelCubBezTo': ('X', 'Y', 'A', 'B', 'C', 'D'),
    'RelEllipticalArcTo': ('X', 'Y', 'A', 'B', 'C', 'D'),
    'RelLineTo': ('X', 'Y'),
    'RelMoveTo': ('X', 'Y'),
    'RelQuadBezTo': ('X', 'Y', 'A', 'B'),
    'SplineStart': ('X', 'Y', 'A', 'B', 'C', 'D'),
    'SplineKnot': ('X', 'Y', 'A'),
})

_SHAPE_GEO = {
    'rect': _geo_rows_rect, 'diamond': _geo_rows_diamond,
    'ellipse': _geo_rows_ellipse, 'process': _geo_rows_process,
    'cylinder': _geo_rows_cylinder, 'document': _geo_rows_document,
    'note': _geo_rows_document, 'triangle': _geo_rows_triangle,
    'pentagon': _geo_rows_pentagon, 'hexagon': _geo_rows_hexagon,
    'parallelogram': _geo_rows_parallelogram, 'trapezoid': _geo_rows_trapezoid,
    'arrow': _geo_rows_arrow, 'leftArrow': _geo_rows_left_arrow,
    'upArrow': _geo_rows_up_arrow, 'downArrow': _geo_rows_down_arrow,
    'star': _geo_rows_star,
}


def _geom_section(rows, no_fill=0):
    """rows: list of (rowType, cells) where cells is one of:
       - dict {cellName: value}               (custom geometry JSON)
       - list of (name, value) pairs          (normalized custom geometry)
       - list of one value-tuple [(x, y, ...)] (built-in shape builders)
       - flat tuple/list of values (x, y, a, ...)
    """
    geom = _el(V('Section'), N='Geometry', IX='0')
    geom.append(_cell('NoFill', no_fill))
    for ix, (rtype, cells) in enumerate(rows, start=1):
        row = _el(V('Row'), T=rtype, IX=str(ix))
        if isinstance(cells, dict):
            # Custom JSON has already been normalized; preserve its canonical
            # order and values without another lossy key conversion.
            cells = list(cells.items())
        if cells and isinstance(cells[0], (list, tuple)) \
                and isinstance(cells[0][0], str):
            for name, val in cells:
                row.append(_cell(name, val))
        elif cells and isinstance(cells[0], (list, tuple)):
            # built-in builders pass [(x, y, ...)]: flatten the point tuple
            vals = [v for t in cells for v in t]
            for name, val in zip(_ROW_CELLS[rtype], vals):
                row.append(_cell(name, val))
        else:
            for name, val in zip(_ROW_CELLS[rtype], cells):
                row.append(_cell(name, val))
        geom.append(row)
    return geom


def _node_geometry(n, w, h):
    if n.get('geometry'):
        rows = []
        for entry in n['geometry']:
            rtype = entry[0]
            vals = entry[1]
            if isinstance(vals, dict):
                # Normalization has already produced canonical uppercase keys.
                cells = list(vals.items())
            else:
                cells = vals
            rows.append((rtype, cells))
        return _geom_section(rows)
    builder = _SHAPE_GEO.get(n.get('type', 'rect'), _geo_rows_rect)
    return _geom_section(builder(w, h))


def _char_section(font_name, size_in, color_idx, style_bits=0):
    sec = _el(V('Section'), N='Character')
    row = _el(V('Row'), IX='0')
    row.append(_cell('Font', font_name))
    row.append(_cell('Size', size_in))
    row.append(_cell('Color', color_idx))
    if style_bits:
        row.append(_cell('Style', style_bits))
    sec.append(row)
    return sec


def _para_section(horz_align):
    sec = _el(V('Section'), N='Paragraph')
    row = _el(V('Row'), IX='0')
    row.append(_cell('HorzAlign', horz_align))
    sec.append(row)
    return sec


def _build_shape(n, sid, palette):
    w, h = float(n['w']), float(n['h'])
    shape_type = n.get('type', 'rect')
    name_u = shape_type.capitalize() if shape_type in _SHAPE_GEO else 'Rectangle'
    shape = _el(V('Shape'), ID=str(sid), NameU=name_u,
                Type='Shape', FillStyle='1', TextStyle='1', LineStyle='1')
    shape.append(_cell('PinX', n['x']))
    shape.append(_cell('PinY', n['y']))
    # LocPin = 局部旋转/缩放中心；draw.io 导入器用它把 Pin（中心）换算成
    # 左上角: x = PinX - LocPinX, y = pageH - (PinY + h - LocPinY)。
    # 缺失时导入器按 0 处理，形状会整体偏右 w/2、偏上 h/2。
    shape.append(_cell('LocPinX', w / 2))
    shape.append(_cell('LocPinY', h / 2))
    shape.append(_cell('Width', w))
    shape.append(_cell('Height', h))
    if n.get('rotation'):
        shape.append(_cell('Angle', math.radians(float(n['rotation']))))

    fill = (n.get('fill') or '#FFFFFF').upper()
    stroke = (n.get('stroke') or '#000000').upper()
    if isinstance(n.get('fill'), str) and \
            n['fill'].lower() in ('none', 'transparent'):
        shape.append(_cell('FillPattern', 0))
    else:
        shape.append(_cell('FillPattern', 1))
        shape.append(_cell('FillForegnd', fill))
    if n.get('gradient'):
        shape.append(_cell('FillGradientEnabled', 1))
        shape.append(_cell('FillPattern', 25))
        shape.append(_cell('FillBkgnd', n['gradient'].upper()))
    if n.get('opacity') is not None and 0 <= float(n['opacity']) < 100:
        # VSDX stores transparency as a 0..1 fraction; the importer computes
        # opacity = 100 - trans*100
        shape.append(_cell('FillForegndTrans', (100.0 - float(n['opacity'])) / 100.0))
    shape.append(_cell('LinePattern', 2 if n.get('dashed') else 1))
    shape.append(_cell('LineColor', stroke))
    shape.append(_cell('LineWeight', n.get('strokeWidth', 0.01)))
    shape.append(_cell('VerticalAlign', {'top': 0, 'middle': 1, 'bottom': 2}
                      .get(n.get('valign', 'middle'), 1)))
    shape.append(_node_geometry(n, w, h))

    font = n.get('fontFamily', 'Microsoft YaHei')
    size_in = round(float(n.get('fontSize', 12)) / 72.0, 4)
    font_color = (n.get('fontColor') or '#000000').upper()
    style_bits = (1 if n.get('bold') else 0) | (2 if n.get('italic') else 0) \
        | (4 if n.get('underline') else 0)
    shape.append(_char_section(font, size_in, font_color, style_bits))
    shape.append(_para_section({'left': 0, 'center': 1, 'right': 2}
                               .get(n.get('align', 'center'), 1)))
    text = _el(V('Text'))
    text.text = n.get('text', '')
    shape.append(text)
    return shape


def _rotate_point(point, center, angle):
    dx, dy = point[0] - center[0], point[1] - center[1]
    cosine, sine = math.cos(angle), math.sin(angle)
    return (
        center[0] + dx * cosine - dy * sine,
        center[1] + dx * sine + dy * cosine,
    )


def _anchor(n, side):
    x, y = round(float(n['x']), 4), round(float(n['y']), 4)
    w2 = round(float(n['w']), 4) / 2
    h2 = round(float(n['h']), 4) / 2
    anchor = {
        'left': (round(x - w2, 4), y),
        'right': (round(x + w2, 4), y),
        'top': (x, round(y + h2, 4)),
        'bottom': (x, round(y - h2, 4)),
    }[side]
    rotation = float(n.get('rotation', 0) or 0)
    if not rotation:
        return anchor
    return tuple(
        round(value, 4)
        for value in _rotate_point(anchor, (x, y), math.radians(rotation))
    )


def _default_sides(f, t):
    dx, dy = t['x'] - f['x'], t['y'] - f['y']
    if abs(dy) >= abs(dx):
        return ('bottom', 'top') if dy < 0 else ('top', 'bottom')
    return ('right', 'left') if dx > 0 else ('left', 'right')


def _resolve_edges(nodes, edges):
    nodes_by_id = {node['id']: node for node in nodes}
    node_shape_ids = {
        node['id']: sid for sid, node in enumerate(nodes, start=1)
    }
    resolved = []
    for sid, edge in enumerate(edges, start=len(nodes) + 1):
        source = nodes_by_id[edge['from']]
        target = nodes_by_id[edge['to']]
        source_side, target_side = _default_sides(source, target)
        source_side = edge.get('fromSide', source_side)
        target_side = edge.get('toSide', target_side)
        resolved_edge = dict(edge)
        begin = _anchor(source, source_side)
        end = _anchor(target, target_side)
        if 'points' not in resolved_edge:
            dx = end[0] - begin[0]
            dy = end[1] - begin[1]
            diagonal = abs(dx) > 1e-6 and abs(dy) > 1e-6
            routing = resolved_edge.get('routing', 'auto')
            if diagonal and routing != 'straight':
                elbow = _elbow_points(begin, end, target_side)
                forced = routing == 'elbow'
                if forced or not _elbow_path_crosses_any(
                        begin, elbow, end, nodes,
                        {edge['from'], edge['to']}):
                    resolved_edge['points'] = elbow
        resolved.append({
            'edge': resolved_edge,
            'connector_id': sid,
            'source_shape_id': node_shape_ids[edge['from']],
            'target_shape_id': node_shape_ids[edge['to']],
            'begin': begin,
            'end': end,
        })
    return resolved


def _elbow_points(begin, end, target_side):
    """Two orthogonal Z waypoints between a diagonal begin/end pair.

    The final segment approaches the target perpendicular to its side, so a
    top/bottom target ends vertically and a left/right target ends
    horizontally.
    """
    bx, by = begin
    ex, ey = end
    if target_side in ('top', 'bottom'):
        y_mid = (by + ey) / 2.0
        return [(bx, y_mid), (ex, y_mid)]
    x_mid = (bx + ex) / 2.0
    return [(x_mid, by), (x_mid, ey)]


def _segment_intersects_box(p1, p2, box, eps=1e-6):
    """True when segment p1->p2 touches the axis-aligned box."""
    x1, y1 = p1
    x2, y2 = p2
    bx1, by1, bx2, by2 = box
    if (max(x1, x2) < bx1 - eps or min(x1, x2) > bx2 + eps
            or max(y1, y2) < by1 - eps or min(y1, y2) > by2 + eps):
        return False

    def cross(a, b, c):
        return (
            (b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0])
        )

    def segments_cross(a, b, c, d):
        d1 = cross(c, d, a)
        d2 = cross(c, d, b)
        d3 = cross(a, b, c)
        d4 = cross(a, b, d)
        return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)

    edges = (
        ((bx1, by1), (bx2, by1)), ((bx2, by1), (bx2, by2)),
        ((bx2, by2), (bx1, by2)), ((bx1, by2), (bx1, by1)),
    )
    for e1, e2 in edges:
        if segments_cross((x1, y1), (x2, y2), e1, e2):
            return True
    return (
        bx1 - eps <= x1 <= bx2 + eps and by1 - eps <= y1 <= by2 + eps
        and bx1 - eps <= x2 <= bx2 + eps and by1 - eps <= y2 <= by2 + eps
    )


def _elbow_path_crosses_any(begin, pts, end, nodes, exclude_ids):
    """True when the elbow path touches any node other than the endpoints."""
    path = [begin] + list(pts) + [end]
    boxes = tuple(
        (
            node['id'],
            (node['x'] - node['w'] / 2.0, node['y'] - node['h'] / 2.0,
             node['x'] + node['w'] / 2.0, node['y'] + node['h'] / 2.0),
        )
        for node in nodes
    )
    for index in range(len(path) - 1):
        for node_id, box in boxes:
            if node_id in exclude_ids:
                continue
            if _segment_intersects_box(path[index], path[index + 1], box):
                return True
    return False


def _expected_connector_semantics(resolved_edges):
    return tuple({
        'connector_id': str(edge['connector_id']),
        'begin_to_sheet': str(edge['source_shape_id']),
        'end_to_sheet': str(edge['target_shape_id']),
        'begin': edge['begin'],
        'end': edge['end'],
    } for edge in resolved_edges)


def _polyline_midpoint(points):
    segments = tuple(
        math.hypot(second[0] - first[0], second[1] - first[1])
        for first, second in zip(points, points[1:])
    )
    total_length = sum(segments)
    if total_length == 0:
        return points[0]
    remaining = total_length / 2.0
    for first, second, length in zip(points, points[1:], segments):
        if remaining <= length:
            factor = 0.0 if length == 0 else remaining / length
            return (
                first[0] + (second[0] - first[0]) * factor,
                first[1] + (second[1] - first[1]) * factor,
            )
        remaining -= length
    return points[-1]


def _build_edge(e, sid, palette, source_sid, target_sid, begin, end):
    bx, by = begin
    ex, ey = end
    pts = [(round(float(p[0]), 4), round(float(p[1]), 4))
           for p in e.get('points', [])]

    dx, dy = ex - bx, ey - by
    dist = math.hypot(dx, dy)
    if dist == 0:
        ux, uy, dist = 0.0, 1.0, 0.001
    else:
        ux, uy = dx / dist, dy / dist

    shape = _el(V('Shape'), ID=str(sid), NameU='Connector',
                Type='Shape', FillStyle='1', TextStyle='1', LineStyle='1')
    shape.append(_cell('BeginX', bx))
    shape.append(_cell('BeginY', by))
    shape.append(_cell('EndX', ex))
    shape.append(_cell('EndY', ey))
    shape.append(_cell_formula('PinX', (bx + ex) / 2.0, '(BeginX+EndX)*0.5'))
    shape.append(_cell_formula('PinY', (by + ey) / 2.0, '(BeginY+EndY)*0.5'))
    shape.append(_cell_formula('Width', ex - bx, 'EndX-BeginX'))
    shape.append(_cell_formula('Height', ey - by, 'EndY-BeginY'))
    shape.append(_cell_formula('LocPinX', (ex - bx) / 2.0, '(EndX-BeginX)/2'))
    shape.append(_cell_formula('LocPinY', (ey - by) / 2.0, '(EndY-BeginY)/2'))
    shape.append(_cell('Angle', 0))
    shape.append(_cell('FlipX', 0))
    shape.append(_cell('FlipY', 0))
    shape.append(_cell_formula(
        'BegTrigger', 2, '_XFTRIGGER(Sheet.%d!EventXFMod)' % source_sid
    ))
    shape.append(_cell_formula(
        'EndTrigger', 2, '_XFTRIGGER(Sheet.%d!EventXFMod)' % target_sid
    ))
    for cname, cval in (
            ('GlueType', 2), ('ConFixedCode', 3), ('DynFeedback', 2),
            ('NoLiveDynamics', 1), ('ConLineRouteExt', 1),
            ('ShapeRouteStyle', 16), ('FillPattern', 0),
            ('ObjType', 2), ('NoAlignBox', 1), ('ShapeSplittable', 1),
            ('IsTextEditTarget', 0), ('DontMoveChildren', 0),
            ('LockMoveX', 0)):
        shape.append(_cell(cname, cval))
    shape.append(_cell('BeginArrow', ARROWS.get(e.get('startArrow', 'none'), 0)))
    shape.append(_cell('EndArrow', ARROWS.get(e.get('endArrow', 'block'), 4)))
    shape.append(_cell('LinePattern', 2 if e.get('dashed') else 1))
    line_color = (e.get('lineColor') or '#000000').upper()
    shape.append(_cell('LineColor', line_color))
    shape.append(_cell('LineWeight', e.get('strokeWidth', 0.01)))
    # Connector geometry is relative to BeginX/BeginY in page coordinates.
    geom = _el(V('Section'), N='Geometry', IX='0')
    geom.append(_cell('NoFill', 1))
    geom.append(_cell('NoLine', 0))
    row = _el(V('Row'), T='MoveTo', IX='1')
    row.append(_cell('X', 0))
    row.append(_cell('Y', 0))
    geom.append(row)
    local_pts = pts + [(ex, ey)]
    for i, (px, py) in enumerate(local_pts, start=2):
        row = _el(V('Row'), T='LineTo', IX=str(i))
        row.append(_cell('X', round(px - bx, 4)))
        row.append(_cell('Y', round(py - by, 4)))
        geom.append(row)
    shape.append(geom)
    conn = _el(V('Section'), N='Connection')
    for i, x in enumerate((0, dist), start=1):
        row = _el(V('Row'), IX=str(i))
        row.append(_cell('X', x))
        row.append(_cell('Y', 0))
        row.append(_cell('DirX', round(ux, 4)))
        row.append(_cell('DirY', round(uy, 4)))
        conn.append(row)
    shape.append(conn)
    if e.get('label') is not None:
        label_point = _polyline_midpoint([(bx, by)] + pts + [(ex, ey)])
        shape.append(_cell('TxtPinX', round(label_point[0] - bx, 4)))
        shape.append(_cell(
            'TxtPinY', round(label_point[1] - by, 4)
        ))
        for cell_name in ('TxtLocPinX', 'TxtLocPinY', 'TxtWidth', 'TxtHeight'):
            shape.append(_cell(cell_name, 0))
        font = e.get('fontFamily', 'Microsoft YaHei')
        size_in = round(float(e.get('fontSize', 9)) / 72.0, 4)
        lc = (e.get('labelColor') or '#000000').upper()
        shape.append(_char_section(font, size_in, lc))
        text = _el(V('Text'))
        text.text = e.get('label', '')
        shape.append(text)
    # FromPart 9/12 are Visio's BeginX/EndX endpoint parts; ToPart 3 glues
    # each endpoint to the target shape represented by its PinX cell.
    connects = (
        _el(V('Connect'), FromSheet=sid, FromCell='BeginX', FromPart='9',
            ToSheet=source_sid, ToCell='PinX', ToPart='3'),
        _el(V('Connect'), FromSheet=sid, FromCell='EndX', FromPart='12',
            ToSheet=target_sid, ToCell='PinX', ToPart='3'),
    )
    return shape, connects


# ---------------------------------------------------------------- document

DOCUMENT_SETTING_ATTRIBUTES = {
    'TopPage': '0',
    'DefaultTextStyle': '0',
    'DefaultLineStyle': '0',
    'DefaultFillStyle': '0',
    'DefaultGuideStyle': '0',
}
DOCUMENT_SETTING_CHILDREN = (
    ('GlueSettings', '9'),
    ('SnapSettings', '65847'),
    ('DynamicGridEnabled', '1'),
)
STYLE_CELLS = (
    ('EnableLineProps', '1'),
    ('EnableFillProps', '1'),
    ('EnableTextProps', '1'),
    ('LineWeight', '0.01'),
    ('LineColor', '#000000'),
    ('LinePattern', '1'),
    ('LineCap', '0'),
    ('BeginArrow', '0'),
    ('EndArrow', '0'),
    ('BeginArrowSize', '2'),
    ('EndArrowSize', '2'),
    ('FillForegnd', '#FFFFFF'),
    ('FillBkgnd', '#FFFFFF'),
    ('FillPattern', '1'),
    ('ShdwPattern', '0'),
    ('ShapeShdwShow', '0'),
    ('VerticalAlign', '1'),
    ('LeftMargin', '0.04'),
    ('RightMargin', '0.04'),
    ('TopMargin', '0.04'),
    ('BottomMargin', '0.04'),
)
STYLE_CHARACTER_CELLS = (
    ('Font', 'Arial'),
    ('Color', '#000000'),
    ('Style', '0'),
    ('Size', '0.1666666666666667'),
    ('AsianFont', 'Microsoft YaHei'),
    ('LangID', 'zh-CN'),
)
STYLE_PARAGRAPH_CELLS = (
    ('HorzAlign', '1'),
    ('SpLine', '-1.2'),
)


def _colors_xml(palette):
    colors = _el(V('Colors'))
    for i, c in enumerate(DEFAULT_PALETTE):
        colors.append(_el(V('ColorEntry'), IX=str(i), RGB=c))
    custom = {i: c for c, i in palette.items() if i >= len(DEFAULT_PALETTE)}
    for i in sorted(custom):
        colors.append(_el(V('ColorEntry'), IX=str(i), RGB=custom[i]))
    return colors


def _face_names_xml():
    faces = _el(V('FaceNames'))
    for fname in FONTS:
        faces.append(_el(V('FaceName'), NameU=fname))
    return faces


def _style_sheets_xml():
    styles = _el(V('StyleSheets'))
    for sid, name in ((0, 'No Style'), (1, 'Basic')):
        ss = _el(V('StyleSheet'), ID=str(sid), Name=name, NameU=name)
        for cname, cval in STYLE_CELLS:
            ss.append(_cell(cname, cval))
        char = _el(V('Section'), N='Character')
        char_row = _el(V('Row'), IX='0')
        for cname, cval in STYLE_CHARACTER_CELLS:
            char_row.append(_cell(cname, cval))
        char.append(char_row)
        ss.append(char)
        para = _el(V('Section'), N='Paragraph')
        para_row = _el(V('Row'), IX='0')
        for cname, cval in STYLE_PARAGRAPH_CELLS:
            para_row.append(_cell(cname, cval))
        para.append(para_row)
        ss.append(para)
        styles.append(ss)
    return styles


def _document_xml(palette):
    doc = _el(V('VisioDocument'))
    settings = _el(V('DocumentSettings'), **DOCUMENT_SETTING_ATTRIBUTES)
    for name, value in DOCUMENT_SETTING_CHILDREN:
        child = _el(V(name))
        child.text = value
        settings.append(child)
    doc.append(settings)
    doc.append(_colors_xml(palette))
    doc.append(_face_names_xml())
    doc.append(_style_sheets_xml())
    # NOTE: no <Pages> section here on purpose. draw.io's importer walks
    # document.xml with importNodes() using a LIVE getElementsByTagName
    # NodeList; a Pages>Page>Rel section makes it re-match its own appended
    # nodes and loop forever. Page content is discovered via
    # initPages -> parseNodes -> resolveRel (pages.xml + its rels), which
    # needs no Rel elements in document.xml.
    return doc


PAGE_CELLS = (
    ('ShdwOffsetX', '0.125'),
    ('ShdwOffsetY', '-0.125'),
    ('PageScale', '1'),
    ('DrawingScale', '1'),
    ('DrawingSizeType', '0'),
    ('DrawingScaleType', '0'),
    ('InhibitSnap', '0'),
    ('PageLockReplace', '0'),
    ('PageLockDuplicate', '0'),
    ('UIVisibility', '0'),
    ('ShdwType', '0'),
    ('ShdwObliqueAngle', '0'),
    ('ShdwScaleFactor', '1'),
    ('DrawingResizeType', '2'),
    ('PageShapeSplit', '1'),
    ('PageLeftMargin', '0'),
    ('PageRightMargin', '0'),
    ('PageTopMargin', '0'),
    ('PageBottomMargin', '0'),
    ('PrintPageOrientation', '2'),
)
PAGE_POINT_UNIT_CELLS = frozenset(('PageScale', 'DrawingScale'))


def _fmt(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _pages_xml(page_cfg):
    pages = _el(V('Pages'))
    p = _el(V('Page'), ID='0', Name=page_cfg['name'], NameU=page_cfg['name'],
            ViewScale='1',
            ViewCenterX=_fmt(page_cfg['width'] / 2.0),
            ViewCenterY=_fmt(page_cfg['height'] / 2.0))
    sheet = _el(V('PageSheet'))
    sheet.append(_cell('PageWidth', page_cfg['width']))
    sheet.append(_cell('PageHeight', page_cfg['height']))
    for cname, cval in PAGE_CELLS:
        if cname in PAGE_POINT_UNIT_CELLS:
            sheet.append(_el(V('Cell'), N=cname, V=cval, U='PT'))
        else:
            sheet.append(_cell(cname, cval))
    p.append(sheet)
    p.append(_el(V('Rel'), **{RID_ATTR: 'rId1'}))
    pages.append(p)
    return pages


def _windows_xml(page_cfg):
    windows = _el(
        V('Windows'), ClientHeight='590', ClientWidth='1438',
        **{'{http://www.w3.org/XML/1998/namespace}space': 'preserve'},
    )
    window = _el(
        V('Window'), WindowState='1073741824', WindowType='Drawing', ID='0',
        ViewScale='-1',
        ViewCenterX=_fmt(page_cfg['width'] / 2.0),
        ViewCenterY=_fmt(page_cfg['height'] / 2.0),
        ContainerType='Page', WindowWidth='1454', WindowLeft='-8',
        Page='0', WindowTop='-30', WindowHeight='628',
    )
    for name, value in (
            ('ShowRulers', '1'), ('ShowGrid', '0'), ('ShowPageBreaks', '0'),
            ('ShowGuides', '1'), ('ShowConnectionPoints', '1'),
            ('GlueSettings', '9'), ('SnapSettings', '294'),
            ('SnapExtensions', '34'), ('DynamicGridEnabled', '1'),
            ('TabSplitterPos', '0.5')):
        child = _el(V(name))
        child.text = value
        window.append(child)
    windows.append(window)
    return windows


def _page_xml(nodes, edges, palette):
    root = _el(V('PageContents'))
    shapes = _el(V('Shapes'))
    connects = _el(V('Connects'))
    for sid, n in enumerate(nodes, start=1):
        shapes.append(_build_shape(n, sid, palette))
    for edge in _resolve_edges(nodes, edges):
        shape, edge_connects = _build_edge(
            edge['edge'], edge['connector_id'], palette,
            edge['source_shape_id'], edge['target_shape_id'],
            edge['begin'], edge['end'],
        )
        shapes.append(shape)
        connects.extend(edge_connects)
    root.append(shapes)
    root.append(connects)
    return root


def _content_types_xml():
    types = _el(CT('Types'))
    types.append(_el(CT('Default'), Extension='rels',
                     ContentType='application/vnd.openxmlformats-package.relationships+xml'))
    types.append(_el(CT('Default'), Extension='xml', ContentType='application/xml'))
    for part, ct in REQUIRED_CONTENT_TYPES:
        types.append(_el(CT('Override'), PartName=part, ContentType=ct))
    return types


def _root_rels_xml():
    rels = _el(REL('Relationships'))
    for rid, rtype, target in REQUIRED_ROOT_RELATIONSHIPS:
        rels.append(_el(REL('Relationship'), Id=rid, Type=rtype, Target=target))
    return rels


def _document_rels_xml():
    rels = _el(REL('Relationships'))
    for rid, rtype, target in REQUIRED_DOCUMENT_RELATIONSHIPS:
        rels.append(_el(REL('Relationship'), Id=rid, Type=rtype, Target=target))
    return rels


def _pages_rels_xml():
    rels = _el(REL('Relationships'))
    rels.append(_el(REL('Relationship'), Id='rId1',
                    Type='http://schemas.microsoft.com/visio/2010/relationships/page',
                    Target='page1.xml'))
    return rels


def _doc_props_core(title):
    cp = _el('{%s}coreProperties' % NS_CP)
    t = ET.Element('{%s}title' % NS_DC)
    t.text = title
    c = ET.Element('{%s}creator' % NS_DC)
    c.text = 'vsdx-gen'
    d = ET.Element('{%s}created' % NS_DCTERMS)
    d.set('{%s}type' % NS_XSI, 'dcterms:W3CDTF')
    d.text = '2026-01-01T00:00:00Z'
    cp.append(t)
    cp.append(c)
    cp.append(d)
    return cp


def _doc_props_app():
    properties = _el('{%s}Properties' % NS_EP)
    application = _el('{%s}Application' % NS_EP)
    application.text = 'vsdx-gen'
    properties.append(application)
    return properties


def _serialize(root):
    xml = ET.tostring(root, encoding='utf-8', xml_declaration=True).decode('utf-8')
    # Each part has exactly one auto-prefixed namespace (ET quirk). Rewrite its
    # declaration only in the root start tag, then strip prefixes from markup;
    # ET escapes '<' in text/attributes, so these tag-boundary matches cannot
    # alter user content.
    root_match = re.match(
        r'(?P<header><\?xml[^>]*\?>\s*<)'
        r'(?P<prefix>ns\d+:)[^>]*>',
        xml,
        flags=re.DOTALL,
    )
    if root_match:
        prefix = root_match.group('prefix')[:-1]
        root_start = root_match.group(0)
        root_start = re.sub(
            r'\s+xmlns:%s="([^"]+)"' % re.escape(prefix),
            r' xmlns="\1"',
            root_start,
            count=1,
        )
        root_start = root_start.replace('<%s:' % prefix, '<', 1)
        xml = root_start + xml[root_match.end():]
    xml = re.sub(r'(<\/?)ns\d+:', r'\1', xml)
    return xml.encode('utf-8')


_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_COMPRESSION = zipfile.ZIP_DEFLATED
_ZIP_COMPRESSLEVEL = 9
_ZIP_CREATE_SYSTEM = 0
_ZIP_EXTERNAL_ATTR = 0x20
_ZIP_INTERNAL_ATTR = 0
_ZIP_FLAG_BITS = 0


def _zip_info(name):
    info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
    info.compress_type = _ZIP_COMPRESSION
    info.create_system = _ZIP_CREATE_SYSTEM
    info.external_attr = _ZIP_EXTERNAL_ATTR
    info.internal_attr = _ZIP_INTERNAL_ATTR
    info.flag_bits = _ZIP_FLAG_BITS
    return info


_TEMP_NAME_ATTEMPTS = 32


def _create_atomic_temp_path(output_path):
    """Create an exclusive sibling temp file with bounded collision retries."""
    output_path = os.fspath(output_path)
    parent = os.path.dirname(output_path)
    basename = os.path.basename(output_path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    for _ in range(_TEMP_NAME_ATTEMPTS):
        candidate = os.path.join(
            parent, ".%s.%s.tmp" % (basename, secrets.token_hex(8))
        )
        try:
            descriptor = os.open(candidate, flags, 0o600)
        except FileExistsError:
            continue
        try:
            os.close(descriptor)
        except BaseException:
            try:
                os.unlink(candidate)
            except OSError:
                pass
            raise
        return candidate
    raise FileExistsError(
        errno.EEXIST, "no usable temporary file name found", output_path
    )


def generate(data, out_path):
    data = normalize_input(data)
    page_cfg = data['page']
    nodes, edges = data['nodes'], data['edges']
    palette = _make_palette(nodes, edges)
    expected_connector_semantics = _expected_connector_semantics(
        _resolve_edges(nodes, edges)
    )

    parts = [
        ('[Content_Types].xml', _serialize(_content_types_xml())),
        ('_rels/.rels', _serialize(_root_rels_xml())),
        ('docProps/core.xml',
         _serialize(_doc_props_core(page_cfg.get('title', page_cfg['name'])))),
        ('docProps/app.xml', _serialize(_doc_props_app())),
        ('visio/document.xml', _serialize(_document_xml(palette))),
        ('visio/windows.xml', _serialize(_windows_xml(page_cfg))),
        ('visio/_rels/document.xml.rels', _serialize(_document_rels_xml())),
        ('visio/pages/pages.xml', _serialize(_pages_xml(page_cfg))),
        ('visio/pages/_rels/pages.xml.rels', _serialize(_pages_rels_xml())),
        ('visio/pages/page1.xml', _serialize(_page_xml(nodes, edges, palette))),
    ]
    requested_path = os.fspath(out_path)
    absolute_path = os.path.abspath(requested_path)
    output_parent = os.path.realpath(os.path.dirname(absolute_path))
    output_path = os.path.join(output_parent, os.path.basename(absolute_path))
    temp_path = None
    try:
        temp_path = _create_atomic_temp_path(output_path)
        with zipfile.ZipFile(
                temp_path, 'w', compression=_ZIP_COMPRESSION,
                compresslevel=_ZIP_COMPRESSLEVEL) as zf:
            for name, blob in parts:
                zf.writestr(
                    _zip_info(name), blob,
                    compress_type=_ZIP_COMPRESSION,
                    compresslevel=_ZIP_COMPRESSLEVEL,
                )
        errors = validate(
            temp_path,
            expected_connector_semantics=expected_connector_semantics,
        )
        if errors:
            raise PackageValidationError(errors)
        os.replace(temp_path, output_path)
        return out_path
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


def _paths_alias(first, second):
    first_path = os.path.normcase(os.path.realpath(os.path.abspath(first)))
    second_path = os.path.normcase(os.path.realpath(os.path.abspath(second)))
    if first_path == second_path:
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


_COORDINATE_TOLERANCE = 1e-9


def _direct_cells(element):
    return {
        cell.get('N'): cell
        for cell in element.findall(V('Cell'))
        if cell.get('N') is not None
    }


def _finite_cell(cells, name, error, errors):
    cell = cells.get(name)
    try:
        value = float(cell.get('V'))
    except (AttributeError, TypeError, ValueError, OverflowError):
        value = None
    if value is None or not math.isfinite(value):
        if error not in errors:
            errors.append(error)
        return None
    return value


def _points_close(first, second):
    return math.isclose(first[0], second[0], rel_tol=0.0,
                        abs_tol=_COORDINATE_TOLERANCE) and \
        math.isclose(first[1], second[1], rel_tol=0.0,
                     abs_tol=_COORDINATE_TOLERANCE)


def _xml_metadata_display(value):
    return '<missing>' if value is None else str(value)


def _connector_semantic_errors(
        page, expected_connector_count=None, expected_connector_semantics=None):
    errors = []
    shapes = page.findall('.//' + V('Shape'))
    shape_by_id = {}
    connector_shapes = []
    for shape in shapes:
        sid = shape.get('ID')
        if sid is not None and sid not in shape_by_id:
            shape_by_id[sid] = shape
        if shape.get('NameU') == 'Connector':
            if sid is None:
                errors.append('connector shape is missing ID')
            else:
                connector_shapes.append((sid, shape))
    connector_ids = {sid for sid, _ in connector_shapes}
    expected_by_id = {}
    if expected_connector_semantics is not None:
        expected_by_id = {
            edge['connector_id']: edge for edge in expected_connector_semantics
        }
        expected_connector_ids = set(expected_by_id)
        if len(connector_ids) != len(expected_connector_ids):
            errors.append('connector count %d does not match expected %d'
                          % (len(connector_ids), len(expected_connector_ids)))
    elif expected_connector_count is not None:
        node_count = len(shape_by_id) - len(connector_ids)
        expected_connector_ids = {
            str(sid) for sid in range(
                node_count + 1, node_count + expected_connector_count + 1
            )
        }
        if len(connector_ids) != expected_connector_count:
            errors.append('connector count %d does not match expected %d'
                          % (len(connector_ids), expected_connector_count))
    else:
        expected_connector_ids = None
    if expected_connector_ids is not None:
        missing_ids = sorted(expected_connector_ids - connector_ids)
        unexpected_ids = sorted(connector_ids - expected_connector_ids)
        if missing_ids:
            errors.append('missing expected connector shape IDs: %s' % missing_ids)
        if unexpected_ids:
            errors.append('unexpected connector shape IDs: %s' % unexpected_ids)

    connects_element = page.find(V('Connects'))
    connect_records = (connects_element.findall(V('Connect'))
                       if connects_element is not None else [])
    records_by_connector = {sid: [] for sid in connector_ids}
    for connect in connect_records:
        role = connect.get('FromCell') or '<missing>'
        from_sid = connect.get('FromSheet')
        from_display = from_sid if from_sid is not None else '<missing>'
        if from_sid not in shape_by_id:
            errors.append('connect %s references unknown FromSheet %s'
                          % (role, from_display))
            continue
        if from_sid not in connector_ids:
            errors.append('connect %s FromSheet %s is not a connector'
                          % (role, from_sid))
            continue
        records_by_connector[from_sid].append(connect)
        if role not in ('BeginX', 'EndX'):
            errors.append('connector %s has unsupported endpoint role %s'
                          % (from_sid, role))
            continue
        expected_attributes = {
            'FromPart': '9' if role == 'BeginX' else '12',
            'ToCell': 'PinX',
            'ToPart': '3',
        }
        for attribute, expected in expected_attributes.items():
            if connect.get(attribute) != expected:
                errors.append('connector %s %s %s must be %s'
                              % (from_sid, role, attribute, expected))
        to_sid = connect.get('ToSheet')
        expected_edge = expected_by_id.get(from_sid)
        if expected_edge is not None:
            expected_to_sid = expected_edge[
                'begin_to_sheet' if role == 'BeginX' else 'end_to_sheet'
            ]
            if to_sid != expected_to_sid:
                errors.append('connector %s %s ToSheet %s does not match expected %s'
                              % (from_sid, role,
                                 _xml_metadata_display(to_sid), expected_to_sid))
        to_display = to_sid if to_sid is not None else '<missing>'
        if to_sid not in shape_by_id:
            errors.append('connector %s %s references unknown ToSheet %s'
                          % (from_sid, role, to_display))
        elif to_sid in connector_ids:
            errors.append('connector %s %s targets connector shape %s'
                          % (from_sid, role, to_sid))

    for sid, connector in connector_shapes:
        records = records_by_connector.get(sid, [])
        if len(records) != 2:
            errors.append('connector %s has %d endpoint connect records (expected 2)'
                          % (sid, len(records)))
        role_records = {}
        for role in ('BeginX', 'EndX'):
            matching = [record for record in records
                        if record.get('FromCell') == role]
            role_records[role] = matching
            if len(matching) != 1:
                errors.append('connector %s has %d %s connect records (expected 1)'
                              % (sid, len(matching), role))

        connector_cells = _direct_cells(connector)
        endpoint_values = {}
        for name in ('BeginX', 'BeginY', 'EndX', 'EndY'):
            endpoint_values[name] = _finite_cell(
                connector_cells, name,
                'connector %s cell %s must be a finite number' % (sid, name),
                errors,
            )

        geometry_rows = []
        for section in connector.findall(V('Section')):
            if section.get('N') == 'Geometry':
                geometry_rows.extend(section.findall(V('Row')))
        geometry_values = []
        for row in geometry_rows:
            row_cells = _direct_cells(row)
            geometry_values.append(tuple(
                _finite_cell(
                    row_cells, name,
                    'connector %s Geometry %s must be a finite number'
                    % (sid, name), errors,
                )
                for name in ('X', 'Y')
            ))
        if not geometry_rows:
            errors.append('connector %s has no Geometry endpoint row' % sid)

        begin = (endpoint_values['BeginX'], endpoint_values['BeginY'])
        end = (endpoint_values['EndX'], endpoint_values['EndY'])
        expected_edge = expected_by_id.get(sid)
        if expected_edge is not None:
            for point_names, point, expected_name in (
                    ('BeginX/BeginY', begin, 'begin'),
                    ('EndX/EndY', end, 'end')):
                expected_point = expected_edge[expected_name]
                if all(value is not None for value in point) and \
                        not _points_close(point, expected_point):
                    errors.append(
                        'connector %s %s does not match expected anchor %s'
                        % (sid, point_names, expected_point)
                    )
        final_geometry = geometry_values[-1] if geometry_values else (None, None)
        if all(value is not None for value in begin + end + final_geometry):
            reconstructed = (begin[0] + final_geometry[0],
                             begin[1] + final_geometry[1])
            if not _points_close(reconstructed, end):
                errors.append('connector %s final geometry point does not match '
                              'EndX/EndY' % sid)

        for role, point in (('BeginX', begin), ('EndX', end)):
            matching = role_records[role]
            if len(matching) != 1 or any(value is None for value in point):
                continue
            target_sid = matching[0].get('ToSheet')
            target = shape_by_id.get(target_sid)
            if target is None or target_sid in connector_ids:
                continue
            target_cells = _direct_cells(target)
            metrics = {}
            for name in ('PinX', 'PinY', 'Width', 'Height'):
                metrics[name] = _finite_cell(
                    target_cells, name,
                    'node shape %s cell %s must be a finite number'
                    % (target_sid, name), errors,
                )
            if any(value is None for value in metrics.values()):
                continue
            pin_x, pin_y = metrics['PinX'], metrics['PinY']
            half_width = metrics['Width'] / 2
            half_height = metrics['Height'] / 2
            anchors = (
                (round(pin_x - half_width, 4), pin_y),
                (round(pin_x + half_width, 4), pin_y),
                (pin_x, round(pin_y + half_height, 4)),
                (pin_x, round(pin_y - half_height, 4)),
            )
            if 'Angle' in target_cells:
                angle = _finite_cell(
                    target_cells, 'Angle',
                    'node shape %s cell Angle must be a finite number'
                    % target_sid,
                    errors,
                )
                if angle is None:
                    continue
                anchors = tuple(
                    tuple(
                        round(value, 4)
                        for value in _rotate_point(
                            anchor, (pin_x, pin_y), angle
                        )
                    )
                    for anchor in anchors
                )
            if not any(_points_close(point, anchor) for anchor in anchors):
                point_names = ('BeginX/BeginY' if role == 'BeginX'
                               else 'EndX/EndY')
                errors.append('connector %s %s is not on boundary anchor of shape %s'
                              % (sid, point_names, target_sid))
    return errors


REQUIRED_PARTS = ('[Content_Types].xml', '_rels/.rels',
                  'docProps/core.xml', 'docProps/app.xml',
                  'visio/document.xml',
                  'visio/windows.xml',
                  'visio/_rels/document.xml.rels', 'visio/pages/pages.xml',
                  'visio/pages/_rels/pages.xml.rels', 'visio/pages/page1.xml')

REQUIRED_CONTENT_TYPES = (
    ('/docProps/core.xml',
     'application/vnd.openxmlformats-package.core-properties+xml'),
    ('/docProps/app.xml',
     'application/vnd.openxmlformats-officedocument.extended-properties+xml'),
    ('/visio/document.xml', 'application/vnd.ms-visio.drawing.main+xml'),
    ('/visio/windows.xml', 'application/vnd.ms-visio.windows+xml'),
    ('/visio/pages/pages.xml', 'application/vnd.ms-visio.pages+xml'),
    ('/visio/pages/page1.xml', 'application/vnd.ms-visio.page+xml'),
)

REQUIRED_ROOT_RELATIONSHIPS = (
    ('rId1', 'http://schemas.microsoft.com/visio/2010/relationships/document',
     'visio/document.xml'),
    ('rId2',
     'http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties',
     'docProps/core.xml'),
    ('rId3',
     'http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties',
     'docProps/app.xml'),
)

REQUIRED_DOCUMENT_RELATIONSHIPS = (
    ('rId1',
     'http://schemas.microsoft.com/visio/2010/relationships/pages',
     'pages/pages.xml'),
    ('rId2',
     'http://schemas.microsoft.com/visio/2010/relationships/windows',
     'windows.xml'),
)

DOCUMENT_CHILD_ORDER = ('DocumentSettings', 'Colors', 'FaceNames', 'StyleSheets')


def _validate_content_types(content_types):
    errors = []
    overrides = {
        node.get('PartName'): node.get('ContentType')
        for node in content_types
        if node.tag == CT('Override')
    }
    for part, expected in REQUIRED_CONTENT_TYPES:
        actual = overrides.get(part)
        if actual is None:
            errors.append('[Content_Types].xml missing override for %s' % part)
        elif actual != expected:
            errors.append(
                '[Content_Types].xml override %s has content type %r, '
                'expected %r' % (part, actual, expected)
            )
    return errors


def _validate_root_relationships(root_rels):
    errors = []
    rels = {node.get('Id'): node for node in root_rels}
    for rid, expected_type, expected_target in REQUIRED_ROOT_RELATIONSHIPS:
        rel = rels.get(rid)
        if rel is None:
            errors.append('_rels/.rels missing relationship %s' % rid)
            continue
        actual_type = rel.get('Type')
        if actual_type != expected_type:
            errors.append(
                '_rels/.rels %s type is %r, expected %r'
                % (rid, actual_type, expected_type)
            )
        actual_target = rel.get('Target')
        if actual_target != expected_target:
            errors.append(
                '_rels/.rels %s target is %r, expected %r'
                % (rid, actual_target, expected_target)
            )
    return errors


def _validate_document_relationships(document_rels):
    errors = []
    rels = {node.get('Id'): node for node in document_rels}
    for rid, expected_type, expected_target in REQUIRED_DOCUMENT_RELATIONSHIPS:
        rel = rels.get(rid)
        if rel is None:
            errors.append(
                'visio/_rels/document.xml.rels missing relationship %s' % rid
            )
            continue
        actual_type = rel.get('Type')
        if actual_type != expected_type:
            errors.append(
                'visio/_rels/document.xml.rels %s type is %r, expected %r'
                % (rid, actual_type, expected_type)
            )
        actual_target = rel.get('Target')
        if actual_target != expected_target:
            errors.append(
                'visio/_rels/document.xml.rels %s target is %r, expected %r'
                % (rid, actual_target, expected_target)
            )
    return errors


def _validate_windows_contract(windows):
    errors = []
    if windows.tag != V('Windows'):
        errors.append(
            'visio/windows.xml root is %r, expected Windows'
            % windows.tag
        )
        return errors
    window = windows.find(V('Window'))
    if window is None:
        errors.append('visio/windows.xml has no Window element')
    else:
        if window.get('WindowType') != 'Drawing':
            errors.append(
                'visio/windows.xml WindowType is %r, expected Drawing'
                % window.get('WindowType')
            )
        if window.get('ID') is None:
            errors.append('visio/windows.xml Window is missing ID')
    return errors


def _validate_document_contract(document):
    errors = []
    names = [child.tag.rsplit('}', 1)[-1] for child in document]
    if names != list(DOCUMENT_CHILD_ORDER):
        errors.append(
            'visio/document.xml child order is %s, expected %s'
            % (names, list(DOCUMENT_CHILD_ORDER))
        )
    settings = document.find(V('DocumentSettings'))
    if settings is None:
        errors.append('visio/document.xml missing DocumentSettings')
    else:
        for attr, expected in DOCUMENT_SETTING_ATTRIBUTES.items():
            actual = settings.get(attr)
            if actual != expected:
                errors.append(
                    'DocumentSettings %s is %r, expected %r'
                    % (attr, actual, expected)
                )
        children = [
            (child.tag.rsplit('}', 1)[-1], child.text)
            for child in settings
        ]
        if children != list(DOCUMENT_SETTING_CHILDREN):
            errors.append(
                'DocumentSettings children are %r, expected %r'
                % (children, list(DOCUMENT_SETTING_CHILDREN))
            )
    faces = [face.get('NameU') for face in document.findall('.//' + V('FaceName'))]
    if faces != FONTS:
        errors.append(
            'FaceNames must be %s in order, got %s' % (FONTS, faces)
        )
    for face in document.findall('.//' + V('FaceName')):
        if sorted(face.attrib) != ['NameU']:
            errors.append(
                'FaceName %r must carry only the NameU attribute, got %r'
                % (face.get('NameU'), sorted(face.attrib))
            )
    styles = document.findall('.//' + V('StyleSheet'))
    style_ids = [
        (style.get('ID'), style.get('Name'), style.get('NameU'))
        for style in styles
    ]
    if style_ids != [('0', 'No Style', 'No Style'), ('1', 'Basic', 'Basic')]:
        errors.append(
            'StyleSheet ID/Name/NameU values are %r, expected '
            "[('0', 'No Style', 'No Style'), ('1', 'Basic', 'Basic')]"
            % style_ids
        )
    for style in styles:
        sid = style.get('ID')
        direct = {
            cell.get('N'): cell.get('V')
            for cell in style.findall(V('Cell'))
        }
        if direct != dict(STYLE_CELLS):
            errors.append(
                'StyleSheet %s cells are %r, expected %r'
                % (sid, direct, dict(STYLE_CELLS))
            )
        sections = {sec.get('N'): sec for sec in style.findall(V('Section'))}
        if set(sections) != {'Character', 'Paragraph'}:
            errors.append(
                'StyleSheet %s sections are %r, expected Character/Paragraph'
                % (sid, sorted(sections))
            )
        else:
            for sec_name, expected_cells in (
                    ('Character', dict(STYLE_CHARACTER_CELLS)),
                    ('Paragraph', dict(STYLE_PARAGRAPH_CELLS))):
                cells = {
                    cell.get('N'): cell.get('V')
                    for row in sections[sec_name].findall(V('Row'))
                    for cell in row.findall(V('Cell'))
                }
                if cells != expected_cells:
                    errors.append(
                        'StyleSheet %s %s cells are %r, expected %r'
                        % (sid, sec_name, cells, expected_cells)
                    )
    return errors


def _validate_page_index_contract(pages):
    errors = []
    page = pages.find('.//' + V('Page'))
    if page is None:
        errors.append('pages.xml has no Page element')
        return errors
    sheet = page.find('.//' + V('PageSheet'))
    if sheet is None:
        errors.append('pages.xml PageSheet is missing')
        return errors
    cells = {
        cell.get('N'): cell
        for cell in sheet.findall(V('Cell'))
    }
    for need in ('PageWidth', 'PageHeight'):
        if need not in cells:
            errors.append('PageSheet missing %s cell' % need)
    if 'PageWidth' in cells and 'PageHeight' in cells:
        try:
            expected_x = _fmt(float(cells['PageWidth'].get('V')) / 2.0)
            expected_y = _fmt(float(cells['PageHeight'].get('V')) / 2.0)
        except (TypeError, ValueError):
            errors.append('PageSheet PageWidth/PageHeight must be numbers')
        else:
            for attr, expected in (
                    ('ViewCenterX', expected_x), ('ViewCenterY', expected_y)):
                actual = page.get(attr)
                if actual != expected:
                    errors.append(
                        'Page %s is %r, expected %r' % (attr, actual, expected)
                    )
    if page.get('ViewScale') != '1':
        errors.append(
            'Page ViewScale is %r, expected "1"' % page.get('ViewScale')
        )
    for name, expected in PAGE_CELLS:
        cell = cells.get(name)
        if cell is None:
            errors.append('PageSheet missing %s cell' % name)
        elif cell.get('V') != expected:
            errors.append(
                'PageSheet %s is %r, expected %r'
                % (name, cell.get('V'), expected)
            )
    for name, cell in cells.items():
        unit = cell.get('U')
        if name in PAGE_POINT_UNIT_CELLS:
            if unit != 'PT':
                errors.append(
                    'PageSheet %s U is %r, expected PT' % (name, unit)
                )
        elif unit is not None:
            errors.append(
                'PageSheet %s has unexpected U=%r' % (name, unit)
            )
    return errors


def _validate_character_fonts(page, declared_fonts):
    errors = []
    declared = set(declared_fonts)
    for cell in page.findall('.//' + V('Cell')):
        if cell.get('N') != 'Font':
            continue
        value = cell.get('V')
        if value not in declared:
            errors.append(
                'Character Font references undeclared font %r '
                '(declared: %s)' % (value, '/'.join(declared_fonts))
            )
    return errors


CONNECTOR_REQUIRED_CELLS = (
    ('PinX', 'F', '(BeginX+EndX)*0.5'),
    ('PinY', 'F', '(BeginY+EndY)*0.5'),
    ('Width', 'F', 'EndX-BeginX'),
    ('Height', 'F', 'EndY-BeginY'),
    ('LocPinX', 'F', '(EndX-BeginX)/2'),
    ('LocPinY', 'F', '(EndY-BeginY)/2'),
    ('GlueType', 'V', '2'),
    ('ConFixedCode', 'V', '3'),
    ('DynFeedback', 'V', '2'),
    ('NoLiveDynamics', 'V', '1'),
    ('ConLineRouteExt', 'V', '1'),
    ('ShapeRouteStyle', 'V', '16'),
    ('ObjType', 'V', '2'),
    ('NoAlignBox', 'V', '1'),
    ('ShapeSplittable', 'V', '1'),
    ('IsTextEditTarget', 'V', '0'),
    ('DontMoveChildren', 'V', '0'),
)


def _validate_connector_contract(page):
    errors = []
    for shape in page.findall('.//' + V('Shape')):
        cells = {
            cell.get('N'): cell
            for cell in shape.findall(V('Cell'))
        }
        if 'BeginX' not in cells:
            continue
        sid = shape.get('ID')
        for name, attr, expected in CONNECTOR_REQUIRED_CELLS:
            cell = cells.get(name)
            if cell is None:
                errors.append(
                    'connector %s is missing %s cell' % (sid, name)
                )
            elif cell.get(attr) != expected:
                errors.append(
                    'connector %s %s %s is %r, expected %r'
                    % (sid, name, attr, cell.get(attr), expected)
                )
        for trigger in ('BegTrigger', 'EndTrigger'):
            cell = cells.get(trigger)
            if cell is None:
                errors.append(
                    'connector %s is missing %s cell' % (sid, trigger)
                )
            elif cell.get('V') != '2' or '_XFTRIGGER(' not in (cell.get('F') or ''):
                errors.append(
                    'connector %s %s must be V=2 with an _XFTRIGGER formula'
                    % (sid, trigger)
                )
    return errors


SHAPE_COLOR_CELL_NAMES = ('FillForegnd', 'FillBkgnd', 'LineColor', 'Color')
_HEX_COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{6}$')


def _validate_shape_colors(page):
    errors = []
    for cell in page.findall('.//' + V('Cell')):
        if cell.get('N') not in SHAPE_COLOR_CELL_NAMES:
            continue
        value = cell.get('V') or ''
        if not _HEX_COLOR_RE.match(value):
            errors.append(
                'shape color cell %s is %r, expected #RRGGBB hex'
                % (cell.get('N'), _xml_metadata_display(value))
            )
    return errors


def validate(out_path, expected_connector_count=None,
             expected_connector_semantics=None):
    """Structural + semantic checks on the generated package."""
    errors = []
    with zipfile.ZipFile(out_path) as zf:
        bad = zf.testzip()
        if bad:
            errors.append('corrupt member: %s' % bad)
        names = zf.namelist()
        name_set = set(names)
        if not names or names[0] != '[Content_Types].xml':
            errors.append('[Content_Types].xml must be the first zip entry')
        for part in REQUIRED_PARTS:
            if part not in name_set:
                errors.append('missing required part: %s' % part)
        parsed_parts = {}
        for name in names:
            if name.endswith('.xml') or name.endswith('.rels'):
                try:
                    parsed_parts[name] = ET.fromstring(zf.read(name))
                except ET.ParseError as e:
                    errors.append('bad XML %s: %s' % (name, e))
        for name in names:
            if name.endswith('.rels') and name in parsed_parts:
                root = parsed_parts[name]
                for rel in root:
                    tgt = rel.get('Target')
                    if not tgt:
                        continue
                    # base of a rels file = dir of its source part:
                    # 'visio/_rels/document.xml.rels' -> 'visio'
                    base = '/'.join(name.split('/')[:-2])
                    full = (base + '/' + tgt) if base else tgt
                    if full not in name_set:
                        errors.append('%s -> missing target %s' % (name, full))
        # page relationship type must end with "/page" (importer requirement)
        if 'visio/pages/_rels/pages.xml.rels' in parsed_parts:
            root = parsed_parts['visio/pages/_rels/pages.xml.rels']
            for rel in root:
                if rel.get('Target') == 'page1.xml' and \
                        not rel.get('Type', '').endswith('/page'):
                    errors.append('pages.xml.rels type must end with /page')
        if '[Content_Types].xml' in parsed_parts:
            errors.extend(_validate_content_types(
                parsed_parts['[Content_Types].xml']
            ))
        if '_rels/.rels' in parsed_parts:
            errors.extend(_validate_root_relationships(
                parsed_parts['_rels/.rels']
            ))
        if 'visio/_rels/document.xml.rels' in parsed_parts:
            errors.extend(_validate_document_relationships(
                parsed_parts['visio/_rels/document.xml.rels']
            ))
        if 'visio/windows.xml' in parsed_parts:
            errors.extend(_validate_windows_contract(
                parsed_parts['visio/windows.xml']
            ))
        if 'visio/document.xml' in parsed_parts:
            errors.extend(_validate_document_contract(
                parsed_parts['visio/document.xml']
            ))
        if 'visio/pages/pages.xml' in parsed_parts:
            errors.extend(_validate_page_index_contract(
                parsed_parts['visio/pages/pages.xml']
            ))
        declared_fonts = None
        if 'visio/document.xml' in parsed_parts:
            declared_fonts = [
                face.get('NameU')
                for face in parsed_parts['visio/document.xml'].findall(
                    './/' + V('FaceName')
                )
            ]
        if 'visio/pages/page1.xml' in parsed_parts and declared_fonts is not None:
            errors.extend(_validate_character_fonts(
                parsed_parts['visio/pages/page1.xml'], declared_fonts
            ))
        if 'visio/pages/page1.xml' in parsed_parts:
            errors.extend(_validate_connector_contract(
                parsed_parts['visio/pages/page1.xml']
            ))
        if 'visio/pages/page1.xml' in parsed_parts:
            errors.extend(_validate_shape_colors(
                parsed_parts['visio/pages/page1.xml']
            ))
        # PageSheet must carry PageWidth/PageHeight for coordinate conversion
        if 'visio/pages/pages.xml' in parsed_parts:
            pages = parsed_parts['visio/pages/pages.xml']
            page = pages.find('.//' + V('Page'))
            if page is None:
                errors.append('pages.xml has no Page element')
            else:
                sheet = page.find(V('PageSheet'))
                cells = {c.get('N') for c in sheet.findall(V('Cell'))} if sheet is not None else set()
                for need in ('PageWidth', 'PageHeight'):
                    if need not in cells:
                        errors.append('PageSheet missing %s cell' % need)
        # shape IDs must be unique within the page
        if 'visio/pages/page1.xml' in parsed_parts:
            page = parsed_parts['visio/pages/page1.xml']
            sids = [s.get('ID') for s in page.findall('.//' + V('Shape'))]
            dupes = {d for d in sids if sids.count(d) > 1}
            if dupes:
                displayed_dupes = sorted(
                    {_xml_metadata_display(value) for value in dupes}
                )
                errors.append('duplicate shape IDs: %s' % displayed_dupes)
            # geometry row cells must use Visio's UPPERCASE names; the draw.io
            # importer matches them case-sensitively (lowercase = values lost)
            legal = {'X', 'Y', 'A', 'B', 'C', 'D', 'E'}
            bad_names = set()
            for sec in page.findall('.//' + V('Section')):
                if sec.get('N') != 'Geometry':
                    continue
                for row in sec.findall(V('Row')):
                    for cell in row.findall(V('Cell')):
                        if cell.get('N') not in legal:
                            bad_names.add(_xml_metadata_display(cell.get('N')))
            for name in sorted(bad_names):
                errors.append('geometry row cell N="%s" must be uppercase (%s)'
                              % (name, '/'.join(sorted(legal))))
            errors.extend(_connector_semantic_errors(
                page, expected_connector_count=expected_connector_count,
                expected_connector_semantics=expected_connector_semantics,
            ))
    return errors


def main(argv=None):
    args = sys.argv[1:] if argv is None else list(argv)
    if len(args) != 2:
        _print_safe(
            'usage: python "<skill-dir>\\scripts\\vsdx_gen.py" '
            '"<input.json>" "<output.vsdx>"'
        )
        return 2
    input_path, output_path = args
    try:
        if _paths_alias(input_path, output_path):
            _print_safe('错误: 输入 JSON 和输出 VSDX 不能是同一路径或文件别名')
            return 2
        with open(input_path, encoding='utf-8') as f:
            data = json.load(f)
        out = generate(data, output_path)
        size = os.path.getsize(out)
    except json.JSONDecodeError as e:
        _print_safe('错误: %s 不是合法 JSON: %s' % (input_path, e))
        return 2
    except UnicodeError as e:
        _print_safe('错误: 无法按 UTF-8 读取 %s: %s' % (input_path, e))
        return 2
    except InputValidationError as e:
        _print_safe(str(e))
        return 2
    except PackageValidationError as e:
        _print_safe('VALIDATION ERRORS:')
        for error in e.errors:
            _print_safe('  - %s' % error)
        return 1
    except OSError as e:
        _print_safe('错误: 文件操作失败: %s' % e)
        return 2
    _print_safe('wrote %s (%d bytes)' % (out, size))
    _print_safe('structure OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
