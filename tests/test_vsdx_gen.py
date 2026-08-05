import copy
import importlib.util
import io
import json
import math
import os
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest import mock
import zipfile
import zlib
from xml.etree import ElementTree as ET


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "vsdx_gen.py"
SPEC = importlib.util.spec_from_file_location("vsdx_gen_under_test", SCRIPT_PATH)
vsdx_gen = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vsdx_gen)


EXPECTED_PUBLIC_SHAPES = frozenset((
    "rect", "diamond", "ellipse", "process", "cylinder", "document",
    "note", "triangle", "pentagon", "hexagon", "parallelogram",
    "trapezoid", "arrow", "leftArrow", "upArrow", "downArrow", "star",
))
EXPECTED_PUBLIC_FONTS = frozenset((
    "Microsoft YaHei", "SimSun", "SimHei", "KaiTi", "Arial", "MS Gothic",
))


class InputContractTests(unittest.TestCase):
    @staticmethod
    def node(node_id="A", **overrides):
        value = {"id": node_id, "x": 1.0, "y": 2.0, "w": 1.5, "h": 0.75}
        value.update(overrides)
        return value

    @classmethod
    def valid_data(cls, **overrides):
        value = {"nodes": [cls.node()], "edges": []}
        value.update(overrides)
        return value

    def assert_field_invalid(self, data, field):
        errors = vsdx_gen.validate_input(data)
        self.assertTrue(errors, "expected invalid input for %s" % field)
        self.assertTrue(
            any(field in error for error in errors),
            "expected an error mentioning %r, got %r" % (field, errors),
        )

    def run_cli(self, input_path, output_path, io_encoding="utf-8"):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = io_encoding
        return subprocess.run(
            [sys.executable, str(SCRIPT_PATH), str(input_path), str(output_path)],
            cwd=str(SKILL_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            check=False,
        )

    @staticmethod
    def temporary_directory():
        return tempfile.TemporaryDirectory(prefix="task2-", dir=SKILL_ROOT / "tests")

    def test_page_defaults_for_missing_null_empty_and_partial_page(self):
        default_page = {"name": "Page-1", "width": 8.5, "height": 11}
        cases = (
            (self.valid_data(), default_page),
            (self.valid_data(page=None), default_page),
            (self.valid_data(page={}), default_page),
            (
                self.valid_data(page={"name": "  Diagram  ", "width": 14}),
                {"name": "Diagram", "width": 14, "height": 11},
            ),
            (
                self.valid_data(page={"title": "Overview", "height": 7}),
                {"name": "Page-1", "title": "Overview", "width": 8.5, "height": 7},
            ),
        )
        for data, expected_page in cases:
            with self.subTest(page=data.get("page", "missing")):
                normalized = vsdx_gen.normalize_input(data)
                self.assertEqual(normalized["page"], expected_page)
                self.assertEqual(normalized["edges"], [])

    def test_normalization_does_not_mutate_or_alias_caller_input(self):
        data = self.valid_data(
            page={"name": "  Page A  "},
            edges=[{"from": "A", "to": "B", "points": [[1, 2]]}],
            nodes=[self.node(), self.node("B", x=4)],
        )
        before = copy.deepcopy(data)

        normalized = vsdx_gen.normalize_input(data)

        self.assertEqual(data, before)
        self.assertIsNot(normalized, data)
        self.assertIsNot(normalized["page"], data["page"])
        self.assertIsNot(normalized["nodes"], data["nodes"])
        self.assertIsNot(normalized["edges"][0]["points"], data["edges"][0]["points"])

    def test_rejects_non_object_root_and_unknown_top_level_keys(self):
        for value in (None, [], "diagram", 1, True):
            with self.subTest(root=value):
                errors = vsdx_gen.validate_input(value)
                self.assertTrue(errors)
                self.assertTrue(any("root" in error or "顶层" in error for error in errors))

        self.assert_field_invalid(
            {"nodes": [self.node()], "edges": [], "metadata": {}},
            "metadata",
        )

    def test_rejects_unknown_page_node_and_edge_fields(self):
        data = self.valid_data(
            page={"widht": 8.5},
            nodes=[
                self.node("A", rotatoin=45, fillColor="#FFFFFF"),
                self.node("B", x=4),
            ],
            edges=[{
                "from": "A",
                "to": "B",
                "fromSdie": "right",
                "lineColour": "#000000",
            }],
        )

        self.assertEqual(
            [error for error in vsdx_gen.validate_input(data)
             if "未知字段" in error],
            [
                "page 未知字段: widht",
                "nodes[0](A) 未知字段: fillColor",
                "nodes[0](A) 未知字段: rotatoin",
                "edges[0] 未知字段: fromSdie",
                "edges[0] 未知字段: lineColour",
            ],
        )

    def test_unknown_field_diagnostics_are_stable_and_safely_escaped(self):
        keys = ("zUnknown", "bad\nkey", "aUnknown")
        messages = []
        for order in (keys, tuple(reversed(keys))):
            node = self.node()
            for key in order:
                node[key] = True
            messages.append([
                error for error in vsdx_gen.validate_input(
                    self.valid_data(nodes=[node])
                )
                if "未知字段" in error
            ])

        expected = [
            "nodes[0](A) 未知字段: aUnknown",
            "nodes[0](A) 未知字段: bad\\nkey",
            "nodes[0](A) 未知字段: zUnknown",
        ]
        self.assertEqual(messages, [expected, expected])
        self.assertNotIn("bad\nkey", "\n".join(messages[0]))

    def test_custom_geometry_rejects_unknown_type_but_allows_omission(self):
        geometry = [["MoveTo", {"x": 0.5, "y": 0.5}]]
        self.assertEqual(
            vsdx_gen.validate_input(
                self.valid_data(nodes=[self.node(geometry=geometry)])
            ),
            [],
        )

        errors = vsdx_gen.validate_input(self.valid_data(nodes=[self.node(
            type="not-a-shape",
            geometry=geometry,
        )]))
        self.assertTrue(any("type 未知形状" in error for error in errors), errors)

    def test_requires_non_empty_nodes_array_and_array_edges(self):
        for data in (
            {},
            {"nodes": None},
            {"nodes": {}},
            {"nodes": []},
        ):
            with self.subTest(data=data):
                self.assert_field_invalid(data, "nodes")

        normalized = vsdx_gen.normalize_input({"nodes": [self.node()]})
        self.assertEqual(normalized["edges"], [])
        for edges in (None, {}, "A -> B", ()):
            with self.subTest(edges=edges):
                self.assert_field_invalid(self.valid_data(edges=edges), "edges")

    def test_validates_page_name_title_and_positive_finite_dimensions(self):
        invalid_fields = {
            "name": (None, "", "   ", 1),
            "title": (None, 1, []),
            "width": (None, 0, -1, math.nan, math.inf, -math.inf, True),
            "height": (None, 0, -1, math.nan, math.inf, -math.inf, False),
        }
        for field, values in invalid_fields.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_field_invalid(
                        self.valid_data(page={field: value}),
                        "page.%s" % field,
                    )

        normalized = vsdx_gen.normalize_input(
            self.valid_data(page={"name": " Page ", "title": "", "width": 1, "height": 2.5})
        )
        self.assertEqual(
            normalized["page"],
            {"name": "Page", "title": "", "width": 1, "height": 2.5},
        )

    def test_rejects_xml_1_0_invalid_characters_in_serialized_free_form_strings(self):
        field_cases = (
            (
                "page.name",
                lambda value: self.valid_data(page={"name": "Page" + value}),
            ),
            (
                "page.title",
                lambda value: self.valid_data(page={"title": "Title" + value}),
            ),
            (
                "nodes[0].text",
                lambda value: self.valid_data(nodes=[self.node(text="Node" + value)]),
            ),
            (
                "edges[0].label",
                lambda value: self.valid_data(
                    nodes=[self.node("A"), self.node("B", x=4)],
                    edges=[{"from": "A", "to": "B", "label": "Edge" + value}],
                ),
            ),
            (
                "nodes[0].geometry[0]",
                lambda value: self.valid_data(
                    nodes=[self.node(
                        geometry=[["PolylineTo", {"x": 1, "y": 0, "a": "1" + value}]],
                    )],
                ),
            ),
        )
        invalid_values = (
            ("nul", "\x00"),
            ("control", "\x08"),
            ("vertical-tab", "\x0b"),
            ("form-feed", "\x0c"),
            ("control-0e", "\x0e"),
            ("unit-separator", "\x1f"),
            ("high-surrogate", "\ud800"),
            ("low-surrogate", "\udfff"),
            ("fffe", "\ufffe"),
            ("ffff", "\uffff"),
        )

        for field, build_data in field_cases:
            for value_name, value in invalid_values:
                with self.subTest(field=field, value=value_name):
                    errors = vsdx_gen.validate_input(build_data(value))
                    message = "\n".join(errors)
                    self.assertTrue(errors, "expected %s to reject %s" % (field, value_name))
                    self.assertIn(field, message)
                    self.assertNotIn(value, message)

    def test_accepts_xml_1_0_legal_character_boundaries(self):
        legal = "\t\n\r \ud7ff\ue000\ufffd\U00010000\U0010ffff"
        data = self.valid_data(
            page={"name": "Page" + legal, "title": "Title" + legal},
            nodes=[
                self.node(
                    "A",
                    text="Node" + legal,
                    geometry=[
                        ["MoveTo", {"x": 0, "y": 0}],
                        ["PolylineTo", {"x": 1, "y": 0, "a": "1\t 2\n3"}],
                    ],
                ),
                self.node("B", x=4),
            ],
            edges=[{"from": "A", "to": "B", "label": "Edge" + legal}],
        )
        self.assertEqual(vsdx_gen.validate_input(data), [])

    def test_validation_errors_escape_unsafe_dynamic_values(self):
        surrogate = "\ud800"
        missing_x = self.node(surrogate)
        del missing_x["x"]
        cases = (
            (
                "unknown-top-level",
                {"nodes": [self.node()], "edges": [], surrogate: {}},
            ),
            ("node-display-id", self.valid_data(nodes=[missing_x])),
            (
                "duplicate-node-id",
                self.valid_data(nodes=[self.node(surrogate), self.node(surrogate, x=4)]),
            ),
            (
                "unknown-shape",
                self.valid_data(nodes=[self.node(type=surrogate)]),
            ),
            (
                "geometry-key",
                self.valid_data(nodes=[self.node(
                    geometry=[["MoveTo", {"x": 0, "y": 0, surrogate: 1}]],
                )]),
            ),
            (
                "dangling-edge",
                self.valid_data(
                    nodes=[self.node("A"), self.node("B", x=4)],
                    edges=[{"from": surrogate, "to": "B"}],
                ),
            ),
        )

        for case_name, data in cases:
            with self.subTest(case=case_name):
                message = "\n".join(vsdx_gen.validate_input(data))
                self.assertTrue(message)
                self.assertNotIn(surrogate, message)
                self.assertIn("\\ud800", message)

    def test_nonserialized_surrogate_ids_remain_valid_when_references_match(self):
        node_id = "\ud800"
        edge_id = "\udfff"
        data = self.valid_data(
            nodes=[self.node(node_id), self.node("B", x=4)],
            edges=[{"id": edge_id, "from": node_id, "to": "B"}],
        )

        self.assertEqual(vsdx_gen.validate_input(data), [])
        normalized = vsdx_gen.normalize_input(data)
        self.assertEqual(normalized["nodes"][0]["id"], node_id)
        self.assertEqual(normalized["edges"][0]["id"], edge_id)

        with self.temporary_directory() as temp_dir:
            output = Path(temp_dir) / "surrogate-ids.vsdx"
            self.assertEqual(Path(vsdx_gen.generate(data, output)), output)
            self.assertEqual(vsdx_gen.validate(output), [])

    def test_validation_diagnostics_escape_controls_and_unicode(self):
        for value, escaped in (
            ("line\n\t\x1b", "line\\n\\t\\x1b"),
            ("节点", "\\u8282\\u70b9"),
        ):
            with self.subTest(value=ascii(value)):
                message = "\n".join(vsdx_gen.validate_input(
                    self.valid_data(nodes=[self.node(type=value)])
                ))
                self.assertNotIn(value, message)
                self.assertIn(escaped, message)

        unicode_id = "节点-A"
        data = self.valid_data(
            nodes=[self.node(unicode_id), self.node("B", x=4)],
            edges=[{"from": unicode_id, "to": "B"}],
        )
        self.assertEqual(vsdx_gen.validate_input(data), [])
        self.assertEqual(vsdx_gen.normalize_input(data)["nodes"][0]["id"], unicode_id)

    def test_requires_node_id_coordinates_and_dimensions(self):
        for field in ("id", "x", "y", "w", "h"):
            node = self.node()
            del node[field]
            with self.subTest(field=field):
                self.assert_field_invalid(self.valid_data(nodes=[node]), field)

        for node_id in (None, "", "   ", 7):
            with self.subTest(node_id=node_id):
                self.assert_field_invalid(
                    self.valid_data(nodes=[self.node(node_id)]),
                    "id",
                )

    def test_rejects_non_positive_node_dimensions_and_duplicate_ids(self):
        for field in ("w", "h"):
            for value in (0, -0.1):
                with self.subTest(field=field, value=value):
                    self.assert_field_invalid(
                        self.valid_data(nodes=[self.node(**{field: value})]),
                        field,
                    )

        errors = vsdx_gen.validate_input(
            self.valid_data(nodes=[self.node("A"), self.node("A", x=3)])
        )
        self.assertTrue(any("A" in error and ("duplicate" in error or "重复" in error) for error in errors))

    def test_rejects_dangling_edge_references_and_self_loops(self):
        nodes = [self.node("A"), self.node("B", x=4)]
        for edge, field in (
            ({"to": "B"}, "from"),
            ({"from": "A"}, "to"),
            ({"from": "missing", "to": "B"}, "from"),
            ({"from": "A", "to": "missing"}, "to"),
        ):
            with self.subTest(edge=edge):
                self.assert_field_invalid(self.valid_data(nodes=nodes, edges=[edge]), field)

        errors = vsdx_gen.validate_input(
            self.valid_data(nodes=nodes, edges=[{"from": "A", "to": "A"}])
        )
        self.assertTrue(errors)
        self.assertTrue(any("self" in error or "自环" in error or "from == to" in error for error in errors))

    def test_validates_explicit_sides_arrows_fonts_alignment_and_colors(self):
        nodes = [self.node("A"), self.node("B", x=4)]
        invalid_node_fields = {
            "type": "unknown-shape",
            "fontFamily": "Comic Sans",
            "align": "justify",
            "valign": "baseline",
            "fill": "red",
            "stroke": "none",
            "gradient": "transparent",
            "fontColor": "#12345G",
        }
        for field, value in invalid_node_fields.items():
            with self.subTest(node_field=field):
                self.assert_field_invalid(
                    self.valid_data(nodes=[self.node(**{field: value})]),
                    field,
                )

        invalid_edge_fields = {
            "fromSide": "center",
            "toSide": "north",
            "startArrow": "triangle",
            "endArrow": "triangle",
            "fontFamily": "Comic Sans",
            "lineColor": "none",
            "labelColor": "blue",
        }
        for field, value in invalid_edge_fields.items():
            edge = {"from": "A", "to": "B", field: value}
            with self.subTest(edge_field=field):
                self.assert_field_invalid(
                    self.valid_data(nodes=nodes, edges=[edge]),
                    field,
                )

    def test_rejects_non_string_shape_and_arrow_values_without_crashing(self):
        for shape_type in ([], {}):
            with self.subTest(shape_type=shape_type):
                self.assert_field_invalid(
                    self.valid_data(nodes=[self.node(type=shape_type)]),
                    "type",
                )

        nodes = [self.node("A"), self.node("B", x=4)]
        for arrow in ([], {}):
            edge = {"from": "A", "to": "B", "endArrow": arrow}
            with self.subTest(arrow=arrow):
                self.assert_field_invalid(
                    self.valid_data(nodes=nodes, edges=[edge]),
                    "endArrow",
                )

    def test_rejects_explicit_null_optional_fields_and_non_string_labels(self):
        node_fields = (
            "text", "type", "fill", "stroke", "strokeWidth", "dashed",
            "opacity", "gradient", "rotation", "fontFamily", "fontSize",
            "fontColor", "bold", "italic", "underline", "align", "valign",
            "geometry",
        )
        for field in node_fields:
            with self.subTest(node_null_field=field):
                self.assert_field_invalid(
                    self.valid_data(nodes=[self.node(**{field: None})]),
                    field,
                )

        with self.subTest(node_text_type=int):
            self.assert_field_invalid(
                self.valid_data(nodes=[self.node(text=1)]),
                "text",
            )

        nodes = [self.node("A"), self.node("B", x=4)]
        edge_fields = (
            "id", "label", "fromSide", "toSide", "lineColor", "strokeWidth",
            "dashed", "startArrow", "endArrow", "fontFamily", "fontSize",
            "labelColor", "points",
        )
        for field in edge_fields:
            edge = {"from": "A", "to": "B", field: None}
            with self.subTest(edge_null_field=field):
                self.assert_field_invalid(
                    self.valid_data(nodes=nodes, edges=[edge]),
                    field,
                )

        with self.subTest(edge_label_type=int):
            self.assert_field_invalid(
                self.valid_data(nodes=nodes, edges=[{"from": "A", "to": "B", "label": 1}]),
                "label",
            )

        geometry_node = self.node(
            type=[],
            geometry=[["MoveTo", {}]],
        )
        with self.subTest(geometry_type=list):
            self.assert_field_invalid(
                self.valid_data(nodes=[geometry_node]),
                "type",
            )

    def test_preserves_documented_shape_and_style_values(self):
        self.assertEqual(frozenset(vsdx_gen._SHAPE_GEO), EXPECTED_PUBLIC_SHAPES)
        self.assertEqual(frozenset(vsdx_gen.FONTS), EXPECTED_PUBLIC_FONTS)
        self.assertEqual(len(vsdx_gen.FONTS), len(EXPECTED_PUBLIC_FONTS))

        for shape_type in EXPECTED_PUBLIC_SHAPES:
            with self.subTest(shape_type=shape_type):
                self.assertEqual(
                    vsdx_gen.validate_input(
                        self.valid_data(nodes=[self.node(type=shape_type)])
                    ),
                    [],
                )

        for fill in ("none", "transparent", "#abcdef"):
            with self.subTest(fill=fill):
                self.assertEqual(
                    vsdx_gen.validate_input(
                        self.valid_data(nodes=[self.node(fill=fill)])
                    ),
                    [],
                )

        nodes = [self.node("A"), self.node("B", x=4)]
        for side in ("top", "bottom", "left", "right"):
            edge = {"from": "A", "to": "B", "fromSide": side, "toSide": side}
            with self.subTest(side=side):
                self.assertEqual(vsdx_gen.validate_input(self.valid_data(nodes=nodes, edges=[edge])), [])

        for font in EXPECTED_PUBLIC_FONTS:
            edge = {"from": "A", "to": "B", "fontFamily": font}
            with self.subTest(font=font):
                data = self.valid_data(nodes=[self.node("A", fontFamily=font), nodes[1]], edges=[edge])
                self.assertEqual(vsdx_gen.validate_input(data), [])

    def test_accepts_only_documented_public_arrow_values(self):
        public_values = (
            "none", "open", "block", "classic", "oval", "diamond",
            "blockThin", "dash",
        )
        internal_values = (
            "openAsync", "blockThin2", "classic2", "oval2", "diamond2",
        )
        nodes = [self.node("A"), self.node("B", x=4)]
        for arrow in public_values:
            edge = {"from": "A", "to": "B", "startArrow": arrow, "endArrow": arrow}
            with self.subTest(public_arrow=arrow):
                self.assertEqual(
                    vsdx_gen.validate_input(self.valid_data(nodes=nodes, edges=[edge])),
                    [],
                )

        for arrow in internal_values:
            for field in ("startArrow", "endArrow"):
                edge = {"from": "A", "to": "B", field: arrow}
                with self.subTest(internal_arrow=arrow, field=field):
                    self.assert_field_invalid(
                        self.valid_data(nodes=nodes, edges=[edge]),
                        field,
                    )

    def test_no_fill_tokens_never_become_document_palette_colors(self):
        data = self.valid_data(
            nodes=[
                self.node("A", fill="none"),
                self.node("B", x=4, fill="transparent"),
            ]
        )
        with self.temporary_directory() as temp_dir:
            output = Path(temp_dir) / "no-fill.vsdx"
            vsdx_gen.generate(data, output)
            self.assertEqual(vsdx_gen.validate(output), [])
            with zipfile.ZipFile(output) as package:
                document = ET.fromstring(package.read("visio/document.xml"))
                page = ET.fromstring(package.read("visio/pages/page1.xml"))

        shapes = page.findall(".//" + vsdx_gen.V("Shape"))
        self.assertEqual(len(shapes), 2)
        for shape in shapes:
            cells = {
                cell.get("N"): cell.get("V")
                for cell in shape.findall(vsdx_gen.V("Cell"))
            }
            self.assertEqual(cells.get("FillPattern"), "0")

        rgb_values = [
            entry.get("RGB")
            for entry in document.findall(".//" + vsdx_gen.V("ColorEntry"))
        ]
        self.assertTrue(rgb_values)
        for rgb in rgb_values:
            with self.subTest(rgb=rgb):
                self.assertRegex(rgb, r"^#[0-9A-Fa-f]{6}$")
        self.assertNotIn("NONE", rgb_values)
        self.assertNotIn("TRANSPARENT", rgb_values)

    def test_omitted_fill_defaults_to_the_same_solid_white_as_explicit_white(self):
        packages = {}
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            for name, node in (
                ("omitted", self.node("A")),
                ("explicit", self.node("A", fill="#FFFFFF")),
            ):
                output = temp / (name + ".vsdx")
                vsdx_gen.generate(self.valid_data(nodes=[node]), output)
                with zipfile.ZipFile(output) as package:
                    page = ET.fromstring(package.read("visio/pages/page1.xml"))
                shape = page.find(".//" + vsdx_gen.V("Shape"))
                packages[name] = {
                    cell.get("N"): cell.get("V")
                    for cell in shape.findall(vsdx_gen.V("Cell"))
                    if cell.get("N", "").startswith("Fill")
                }

        self.assertEqual(packages["omitted"], packages["explicit"])
        self.assertEqual(packages["omitted"]["FillPattern"], "1")
        self.assertIn("FillForegnd", packages["omitted"])

    def test_namespace_like_text_round_trips_in_page_title_node_and_edge(self):
        page_title = 'title xmlns:ns2="x"'
        node_text = 'node xmlns:ns1="x"'
        edge_label = 'edge xmlns:ns3="x"'
        data = self.valid_data(
            page={"title": page_title},
            nodes=[
                self.node("A", text=node_text),
                self.node("B", x=4),
            ],
            edges=[{"from": "A", "to": "B", "label": edge_label}],
        )

        with self.temporary_directory() as temp_dir:
            output = Path(temp_dir) / "namespace-like-text.vsdx"
            vsdx_gen.generate(data, output)
            self.assertEqual(vsdx_gen.validate(output), [])
            with zipfile.ZipFile(output) as package:
                core = ET.fromstring(package.read("docProps/core.xml"))
                page = ET.fromstring(package.read("visio/pages/page1.xml"))

        title = core.find("{%s}title" % vsdx_gen.NS_DC)
        self.assertIsNotNone(title)
        self.assertEqual(title.text, page_title)

        text_by_shape_id = {}
        for shape in page.findall(".//" + vsdx_gen.V("Shape")):
            text = shape.find(vsdx_gen.V("Text"))
            if text is not None:
                text_by_shape_id[shape.get("ID")] = text.text or ""
        self.assertEqual(text_by_shape_id["1"], node_text)
        self.assertEqual(text_by_shape_id["3"], edge_label)

    def test_validates_boolean_style_fields(self):
        for field in ("dashed", "bold", "italic", "underline"):
            with self.subTest(node_field=field):
                self.assert_field_invalid(
                    self.valid_data(nodes=[self.node(**{field: 1})]),
                    field,
                )

        nodes = [self.node("A"), self.node("B", x=4)]
        self.assert_field_invalid(
            self.valid_data(nodes=nodes, edges=[{"from": "A", "to": "B", "dashed": "yes"}]),
            "dashed",
        )

    def test_validates_line_width_font_size_opacity_and_rotation(self):
        positive_fields = ("strokeWidth", "fontSize")
        for field in positive_fields:
            for value in (0, -1, math.nan, math.inf, True):
                with self.subTest(node_field=field, value=value):
                    self.assert_field_invalid(
                        self.valid_data(nodes=[self.node(**{field: value})]),
                        field,
                    )

        for value in (-1, 101, math.nan, math.inf, True):
            with self.subTest(opacity=value):
                self.assert_field_invalid(
                    self.valid_data(nodes=[self.node(opacity=value)]),
                    "opacity",
                )

        for value in (math.nan, math.inf, -math.inf, True):
            with self.subTest(rotation=value):
                self.assert_field_invalid(
                    self.valid_data(nodes=[self.node(rotation=value)]),
                    "rotation",
                )

        nodes = [self.node("A"), self.node("B", x=4)]
        for field in positive_fields:
            for value in (0, -1, math.nan, math.inf, False):
                edge = {"from": "A", "to": "B", field: value}
                with self.subTest(edge_field=field, value=value):
                    self.assert_field_invalid(
                        self.valid_data(nodes=nodes, edges=[edge]),
                        field,
                    )

        valid = self.valid_data(
            nodes=[self.node(strokeWidth=0.02, fontSize=10, opacity=0, rotation=-45)]
        )
        self.assertEqual(vsdx_gen.validate_input(valid), [])

    def test_rejects_nan_infinity_and_bool_as_numbers(self):
        for field in ("x", "y", "w", "h"):
            for value in (math.nan, math.inf, -math.inf, True, False):
                with self.subTest(field=field, value=value):
                    self.assert_field_invalid(
                        self.valid_data(nodes=[self.node(**{field: value})]),
                        field,
                    )

    def test_points_are_exactly_finite_json_number_pairs(self):
        nodes = [self.node("A"), self.node("B", x=4)]
        invalid_points = (
            None,
            {},
            [(1, 2)],
            [[1]],
            [[1, 2, 3]],
            [[1, "2"]],
            [[True, 2]],
            [[math.nan, 2]],
            [[math.inf, 2]],
        )
        for points in invalid_points:
            edge = {"from": "A", "to": "B", "points": points}
            with self.subTest(points=points):
                self.assert_field_invalid(
                    self.valid_data(nodes=nodes, edges=[edge]),
                    "points",
                )

        for points in ([], [[1, 2]], [[-1.5, 0], [3, 4.25]]):
            edge = {"from": "A", "to": "B", "points": points}
            with self.subTest(valid_points=points):
                self.assertEqual(vsdx_gen.validate_input(self.valid_data(nodes=nodes, edges=[edge])), [])

    def test_generate_accepts_a_partial_page(self):
        data = self.valid_data(page={"title": "Partial page"})
        with self.temporary_directory() as temp_dir:
            output = Path(temp_dir) / "partial.vsdx"
            result = vsdx_gen.generate(data, output)
            self.assertEqual(Path(result), output)
            self.assertEqual(vsdx_gen.validate(output), [])

    def test_cli_returns_2_without_traceback_for_malformed_json_and_input(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            cases = {
                "malformed.json": "{not json",
                "invalid.json": json.dumps({"nodes": []}),
                "invalid-text.json": json.dumps(
                    {"nodes": [self.node(text=1)]}
                ),
                "invalid-null-side.json": json.dumps(
                    {
                        "nodes": [self.node("A"), self.node("B", x=4)],
                        "edges": [{"from": "A", "to": "B", "fromSide": None}],
                    }
                ),
            }
            for name, contents in cases.items():
                input_path = temp / name
                input_path.write_text(contents, encoding="utf-8")
                with self.subTest(name=name):
                    result = self.run_cli(input_path, temp / (name + ".vsdx"))
                    self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                    self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_cli_rejects_xml_invalid_json_escapes_without_creating_output(self):
        field_cases = (
            (
                "page-name",
                lambda value: self.valid_data(page={"name": "Page" + value}),
            ),
            (
                "page-title",
                lambda value: self.valid_data(page={"title": "Title" + value}),
            ),
            (
                "node-text",
                lambda value: self.valid_data(nodes=[self.node(text="Node" + value)]),
            ),
            (
                "edge-label",
                lambda value: self.valid_data(
                    nodes=[self.node("A"), self.node("B", x=4)],
                    edges=[{"from": "A", "to": "B", "label": "Edge" + value}],
                ),
            ),
            (
                "geometry-cell",
                lambda value: self.valid_data(
                    nodes=[self.node(
                        geometry=[["PolylineTo", {"x": 1, "y": 0, "a": "1" + value}]],
                    )],
                ),
            ),
        )
        invalid_values = (
            ("nul", "\x00", "\\u0000"),
            ("surrogate", "\ud800", "\\ud800"),
        )

        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            for field_name, build_data in field_cases:
                for value_name, value, escaped_value in invalid_values:
                    case_name = "%s-%s" % (field_name, value_name)
                    input_path = temp / (case_name + ".json")
                    output_path = temp / (case_name + ".vsdx")
                    contents = json.dumps(build_data(value), ensure_ascii=True)
                    self.assertIn(escaped_value, contents.lower())
                    input_path.write_text(contents, encoding="ascii")

                    with self.subTest(field=field_name, value=value_name):
                        result = self.run_cli(input_path, output_path)
                        combined_output = result.stdout + result.stderr
                        self.assertEqual(result.returncode, 2, combined_output)
                        self.assertNotIn("Traceback", combined_output)
                        self.assertFalse(output_path.exists())

    def test_cli_escapes_surrogate_identifiers_under_utf8_and_ascii(self):
        surrogate = "\ud800"
        cases = (
            (
                "dangling-ref",
                self.valid_data(
                    nodes=[self.node("A"), self.node("B", x=4)],
                    edges=[{"from": surrogate, "to": "B"}],
                ),
            ),
            (
                "unknown-shape",
                self.valid_data(nodes=[self.node(type=surrogate)]),
            ),
            (
                "unknown-top-level",
                {"nodes": [self.node()], "edges": [], surrogate: {}},
            ),
        )

        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            for io_encoding in ("utf-8", "ascii"):
                for case_name, data in cases:
                    name = "%s-%s" % (case_name, io_encoding)
                    input_path = temp / (name + ".json")
                    output_path = temp / (name + ".vsdx")
                    contents = json.dumps(data, ensure_ascii=True)
                    self.assertIn("\\ud800", contents.lower())
                    input_path.write_text(contents, encoding="ascii")

                    with self.subTest(case=case_name, io_encoding=io_encoding):
                        result = self.run_cli(input_path, output_path, io_encoding)
                        combined_output = result.stdout + result.stderr
                        self.assertEqual(result.returncode, 2, combined_output)
                        self.assertNotIn("Traceback", combined_output)
                        self.assertIn("\\ud800", combined_output.lower())
                        self.assertFalse(output_path.exists())

    def test_cli_returns_2_without_traceback_for_os_error(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            result = self.run_cli(temp / "missing.json", temp / "out.vsdx")
            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_main_returns_1_when_generated_package_validation_reports_errors(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "output.vsdx"
            input_path.write_text(json.dumps(self.valid_data()), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(
                vsdx_gen, "validate", return_value=["broken package"]
            ) as validate_mock:
                with redirect_stdout(stdout):
                    result = vsdx_gen.main([str(input_path), str(output_path)])
            self.assertEqual(result, 1)
            self.assertIn("VALIDATION ERRORS:", stdout.getvalue())
            self.assertIn("broken package", stdout.getvalue())
            self.assertFalse(output_path.exists())
            validate_mock.assert_called_once()
            validated_path = Path(validate_mock.call_args.args[0])
            self.assertEqual(validated_path.parent.resolve(), temp.resolve())
            self.assertNotEqual(validated_path.resolve(), output_path.resolve())
            self.assertFalse(validated_path.exists())
            self.assertEqual({path.name for path in temp.iterdir()}, {input_path.name})

    def test_main_returns_0_for_success_and_2_for_output_os_error(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps(self.valid_data()), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(vsdx_gen.main([str(input_path), str(temp / "ok.vsdx")]), 0)
                self.assertEqual(
                    vsdx_gen.main([str(input_path), str(temp / "missing" / "out.vsdx")]),
                    2,
                )

    def test_main_does_not_swallow_unexpected_programming_errors(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps(self.valid_data()), encoding="utf-8")
            with mock.patch.object(vsdx_gen, "generate", side_effect=RuntimeError("bug")):
                with self.assertRaisesRegex(RuntimeError, "bug"):
                    with redirect_stdout(io.StringIO()):
                        vsdx_gen.main([str(input_path), str(temp / "out.vsdx")])


class OutputSafetyTests(unittest.TestCase):
    """Deterministic package metadata and atomic destination replacement."""

    PARTS = (
        "[Content_Types].xml",
        "_rels/.rels",
        "docProps/core.xml",
        "docProps/app.xml",
        "visio/document.xml",
        "visio/windows.xml",
        "visio/_rels/document.xml.rels",
        "visio/pages/pages.xml",
        "visio/pages/_rels/pages.xml.rels",
        "visio/pages/page1.xml",
    )
    FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
    FIXED_CREATE_SYSTEM = 0
    FIXED_EXTERNAL_ATTR = 0x20
    FIXED_INTERNAL_ATTR = 0
    FIXED_FLAG_BITS = 0
    FIXED_COMPRESSLEVEL = 9

    @staticmethod
    def valid_data():
        return InputContractTests.valid_data(
            page={"name": "Stable", "title": "Deterministic package"},
            nodes=[
                InputContractTests.node(
                    "A", text="Start", fill="#DAE8FC", stroke="#6C8EBF"
                ),
                InputContractTests.node(
                    "B", x=4, text="Finish", fill="#D5E8D4", stroke="#82B366"
                ),
            ],
            edges=[{"from": "A", "to": "B", "label": "next"}],
        )

    @staticmethod
    def temporary_directory():
        return tempfile.TemporaryDirectory(prefix="task4-", dir=SKILL_ROOT / "tests")

    def invoke_cli(self, input_path, output_path):
        output = io.StringIO()
        with redirect_stdout(output):
            code = vsdx_gen.main([str(input_path), str(output_path)])
        return code, output.getvalue()

    def write_input(self, path):
        content = json.dumps(self.valid_data()).encode("utf-8")
        path.write_bytes(content)
        return content

    def test_cli_rejects_same_and_resolved_alias_paths_without_modifying_json(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            cases = (
                ("same", lambda path: path),
                ("resolved", lambda path: path.parent / "child" / ".." / path.name),
            )
            (temp / "child").mkdir()
            for name, output_for in cases:
                with self.subTest(alias=name):
                    input_path = temp / (name + ".json")
                    original = self.write_input(input_path)
                    output_path = output_for(input_path)

                    code, output = self.invoke_cli(input_path, output_path)

                    self.assertEqual(code, 2)
                    self.assertIn("输入", output)
                    self.assertIn("输出", output)
                    self.assertEqual(input_path.read_bytes(), original)

    @unittest.skipUnless(
        os.path.normcase("alias") == os.path.normcase("ALIAS"),
        "case-only path aliases require a case-insensitive filesystem",
    )
    def test_cli_rejects_case_only_path_alias_without_modifying_json(self):
        with self.temporary_directory() as temp_dir:
            input_path = Path(temp_dir) / "case.json"
            original = self.write_input(input_path)

            code, output = self.invoke_cli(
                input_path, input_path.with_name("CASE.JSON")
            )

            self.assertEqual(code, 2)
            self.assertIn("输入", output)
            self.assertIn("输出", output)
            self.assertEqual(input_path.read_bytes(), original)

    def test_cli_rejects_hardlink_alias_without_replacing_either_name(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "hardlink.vsdx"
            original = self.write_input(input_path)
            os.link(input_path, output_path)
            self.assertTrue(os.path.samefile(input_path, output_path))

            code, output = self.invoke_cli(input_path, output_path)

            self.assertEqual(code, 2)
            self.assertIn("输入", output)
            self.assertIn("输出", output)
            self.assertTrue(os.path.samefile(input_path, output_path))
            self.assertEqual(input_path.read_bytes(), original)
            self.assertEqual(output_path.read_bytes(), original)

    def test_cli_rejects_symlink_alias_without_replacing_the_link_or_target(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output_path = temp / "symlink.vsdx"
            original = self.write_input(input_path)
            try:
                os.symlink(input_path, output_path)
            except OSError as error:
                self.skipTest("symlinks are unavailable: %s" % error)

            code, output = self.invoke_cli(input_path, output_path)

            self.assertEqual(code, 2)
            self.assertIn("输入", output)
            self.assertIn("输出", output)
            self.assertTrue(output_path.is_symlink())
            self.assertEqual(input_path.read_bytes(), original)
            self.assertEqual(output_path.read_bytes(), original)

    def test_identical_normalized_input_produces_identical_vsdx_bytes(self):
        normalized = vsdx_gen.normalize_input(self.valid_data())
        old_time = (2020, 2, 3, 4, 5, 6, 0, 0, -1)
        new_time = (2030, 7, 8, 9, 10, 12, 0, 0, -1)
        changing_archive_times = [old_time] * len(self.PARTS) + [new_time] * len(self.PARTS)

        with self.temporary_directory() as temp_dir:
            first = Path(temp_dir) / "first.vsdx"
            second = Path(temp_dir) / "second.vsdx"
            with mock.patch.object(
                zipfile.time, "localtime", side_effect=changing_archive_times
            ):
                vsdx_gen.generate(normalized, first)
                vsdx_gen.generate(normalized, second)

            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_zip_entries_have_fixed_order_metadata_compression_and_valid_crc(self):
        write_options = []
        original_writestr = zipfile.ZipFile.writestr

        def recording_writestr(
            archive, zinfo, data, compress_type=None, compresslevel=None
        ):
            write_options.append((compress_type, compresslevel))
            return original_writestr(
                archive,
                zinfo,
                data,
                compress_type=compress_type,
                compresslevel=compresslevel,
            )

        with self.temporary_directory() as temp_dir:
            output = Path(temp_dir) / "metadata.vsdx"
            with mock.patch.object(zipfile.ZipFile, "writestr", new=recording_writestr):
                vsdx_gen.generate(self.valid_data(), output)

            with zipfile.ZipFile(output) as package:
                infos = package.infolist()
                self.assertEqual(tuple(info.filename for info in infos), self.PARTS)
                self.assertIsNone(package.testzip())
                for info in infos:
                    with self.subTest(part=info.filename):
                        self.assertEqual(info.date_time, self.FIXED_TIMESTAMP)
                        self.assertEqual(info.create_system, self.FIXED_CREATE_SYSTEM)
                        self.assertEqual(info.external_attr, self.FIXED_EXTERNAL_ATTR)
                        self.assertEqual(info.internal_attr, self.FIXED_INTERNAL_ATTR)
                        self.assertEqual(info.flag_bits, self.FIXED_FLAG_BITS)
                        self.assertEqual(info.compress_type, zipfile.ZIP_DEFLATED)
                        content = package.read(info)
                        self.assertEqual(info.CRC, zlib.crc32(content) & 0xFFFFFFFF)
                        if info.filename.endswith((".xml", ".rels")):
                            ET.fromstring(content)

        self.assertEqual(
            write_options,
            [(zipfile.ZIP_DEFLATED, self.FIXED_COMPRESSLEVEL)] * len(self.PARTS),
        )

    def test_serialization_exception_preserves_destination_and_leaves_no_temp(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            output = temp / "existing.vsdx"
            sentinel = b"existing destination"
            output.write_bytes(sentinel)
            before = {path.name for path in temp.iterdir()}

            with mock.patch.object(
                vsdx_gen, "_serialize", side_effect=RuntimeError("serialize failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "serialize failed"):
                    vsdx_gen.generate(self.valid_data(), output)

            self.assertEqual(output.read_bytes(), sentinel)
            self.assertEqual({path.name for path in temp.iterdir()}, before)

    def test_write_exception_preserves_destination_and_leaves_no_temp(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            output = temp / "existing.vsdx"
            sentinel = b"existing destination"
            output.write_bytes(sentinel)
            before = {path.name for path in temp.iterdir()}

            with mock.patch.object(
                zipfile.ZipFile, "writestr", side_effect=RuntimeError("write failed")
            ):
                with self.assertRaisesRegex(RuntimeError, "write failed"):
                    vsdx_gen.generate(self.valid_data(), output)

            self.assertEqual(output.read_bytes(), sentinel)
            self.assertEqual({path.name for path in temp.iterdir()}, before)

    def test_temp_creation_propagates_permission_error_without_retrying(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            output = temp / "output.vsdx"
            with mock.patch.object(
                vsdx_gen.os,
                "open",
                side_effect=PermissionError("permission denied"),
            ) as open_mock:
                with self.assertRaisesRegex(PermissionError, "permission denied"):
                    vsdx_gen._create_atomic_temp_path(output)

            open_mock.assert_called_once()

            input_path = temp / "input.json"
            input_path.write_text(json.dumps(self.valid_data()), encoding="utf-8")
            stdout = io.StringIO()
            with mock.patch.object(
                vsdx_gen,
                "_create_atomic_temp_path",
                side_effect=PermissionError("permission denied"),
            ):
                with redirect_stdout(stdout):
                    result = vsdx_gen.main([str(input_path), str(output)])
            self.assertEqual(result, 2)
            self.assertIn("文件操作失败", stdout.getvalue())

    def test_validation_failure_preserves_destination_and_raises_documented_error(self):
        error_type = getattr(vsdx_gen, "PackageValidationError", None)
        self.assertIsNotNone(error_type, "PackageValidationError must document this failure path")

        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            output = temp / "existing.vsdx"
            sentinel = b"existing destination"
            output.write_bytes(sentinel)
            before = {path.name for path in temp.iterdir()}

            with mock.patch.object(
                vsdx_gen, "validate", return_value=["bad relationship", "bad XML"]
            ) as validate_mock:
                with self.assertRaises(error_type) as caught:
                    vsdx_gen.generate(self.valid_data(), output)

            self.assertEqual(caught.exception.errors, ("bad relationship", "bad XML"))
            self.assertIn("bad relationship", str(caught.exception))
            self.assertEqual(output.read_bytes(), sentinel)
            validate_mock.assert_called_once()
            validated_path = Path(validate_mock.call_args.args[0])
            self.assertEqual(validated_path.parent.resolve(), temp.resolve())
            self.assertNotEqual(validated_path.resolve(), output.resolve())
            self.assertFalse(validated_path.exists())
            self.assertEqual({path.name for path in temp.iterdir()}, before)

    def test_success_validates_temp_then_atomically_replaces_destination(self):
        real_validate = vsdx_gen.validate
        real_replace = os.replace
        events = []

        def recording_validate(
            path, expected_connector_count=None, expected_connector_semantics=None
        ):
            events.append(
                (
                    "validate",
                    Path(path),
                    expected_connector_count,
                    expected_connector_semantics,
                )
            )
            return real_validate(
                path,
                expected_connector_count=expected_connector_count,
                expected_connector_semantics=expected_connector_semantics,
            )

        def recording_replace(source, destination):
            events.append(("replace", Path(source), Path(destination)))
            return real_replace(source, destination)

        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            output = temp / "existing.vsdx"
            output.write_bytes(b"existing destination")

            with mock.patch.object(vsdx_gen, "validate", side_effect=recording_validate):
                with mock.patch.object(vsdx_gen.os, "replace", side_effect=recording_replace):
                    result = vsdx_gen.generate(self.valid_data(), output)

            self.assertEqual(Path(result).resolve(), output.resolve())
            self.assertEqual([event[0] for event in events], ["validate", "replace"])
            self.assertIsNone(events[0][2])
            self.assertEqual(
                events[0][3],
                (
                    {
                        "connector_id": "3",
                        "begin_to_sheet": "1",
                        "end_to_sheet": "2",
                        "begin": (1.75, 2.0),
                        "end": (3.25, 2.0),
                    },
                ),
            )
            validated_temp = events[0][1]
            replaced_temp, replaced_output = events[1][1:]
            self.assertEqual(validated_temp.resolve(), replaced_temp.resolve())
            self.assertEqual(replaced_temp.parent.resolve(), output.parent.resolve())
            self.assertEqual(replaced_output.resolve(), output.resolve())
            self.assertFalse(replaced_temp.exists())
            self.assertEqual({path.name for path in temp.iterdir()}, {output.name})
            self.assertEqual(vsdx_gen.validate(output), [])

    def test_replace_exception_preserves_destination_and_leaves_no_temp(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            output = temp / "existing.vsdx"
            sentinel = b"existing destination"
            output.write_bytes(sentinel)
            before = {path.name for path in temp.iterdir()}

            with mock.patch.object(
                vsdx_gen.os, "replace", side_effect=OSError("replace failed")
            ):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    vsdx_gen.generate(self.valid_data(), output)

            self.assertEqual(output.read_bytes(), sentinel)
            self.assertEqual({path.name for path in temp.iterdir()}, before)

    def test_missing_output_directory_raises_file_error_and_cli_returns_2(self):
        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            missing_parent = temp / "missing"
            output = missing_parent / "output.vsdx"

            with self.assertRaises((FileNotFoundError, OSError)):
                vsdx_gen.generate(self.valid_data(), output)
            self.assertFalse(missing_parent.exists())

            input_path = temp / "input.json"
            input_path.write_text(json.dumps(self.valid_data()), encoding="utf-8")
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                result = vsdx_gen.main([str(input_path), str(output)])
            self.assertEqual(result, 2)
            self.assertIn("文件操作失败", stdout.getvalue())
            self.assertFalse(missing_parent.exists())
            self.assertEqual({path.name for path in temp.iterdir()}, {input_path.name})

    def test_cli_success_validates_only_the_temporary_package(self):
        real_validate = vsdx_gen.validate

        with self.temporary_directory() as temp_dir:
            temp = Path(temp_dir)
            input_path = temp / "input.json"
            output = temp / "output.vsdx"
            input_path.write_text(json.dumps(self.valid_data()), encoding="utf-8")

            with mock.patch.object(vsdx_gen, "validate", wraps=real_validate) as validate_mock:
                with redirect_stdout(io.StringIO()):
                    result = vsdx_gen.main([str(input_path), str(output)])

            self.assertEqual(result, 0)
            validate_mock.assert_called_once()
            validated_path = Path(validate_mock.call_args.args[0])
            self.assertNotEqual(validated_path.resolve(), output.resolve())
            self.assertEqual(validated_path.parent.resolve(), output.parent.resolve())
            self.assertFalse(validated_path.exists())
            self.assertEqual({path.name for path in temp.iterdir()}, {input_path.name, output.name})


NS_EP = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"


class VisioCompatibilityTests(unittest.TestCase):
    """Package and document contract required for Microsoft Visio to open files."""

    @staticmethod
    def generated_package(directory, data=None, name="visio-compatible.vsdx"):
        output = Path(directory) / name
        vsdx_gen.generate(data or OutputSafetyTests.valid_data(), output)
        return output

    def test_extended_properties_part_and_relationship_chain(self):
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            output = self.generated_package(temp)
            with zipfile.ZipFile(output) as package:
                self.assertIn("docProps/app.xml", package.namelist())
                app = ET.fromstring(package.read("docProps/app.xml"))
                content_types = ET.fromstring(package.read("[Content_Types].xml"))
                root_rels = ET.fromstring(package.read("_rels/.rels"))

            self.assertEqual(app.tag, "{%s}Properties" % NS_EP)
            self.assertEqual(app.find("{%s}Application" % NS_EP).text, "vsdx-gen")

            overrides = {
                node.get("PartName"): node.get("ContentType")
                for node in content_types
                if node.tag == vsdx_gen.CT("Override")
            }
            self.assertEqual(
                overrides["/docProps/app.xml"],
                "application/vnd.openxmlformats-officedocument.extended-properties+xml",
            )
            rels = {
                node.get("Id"): (node.get("Type"), node.get("Target"))
                for node in root_rels
            }
            self.assertEqual(
                rels["rId3"],
                (
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties",
                    "docProps/app.xml",
                ),
            )

    def test_document_skeleton_matches_visio_contract(self):
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            output = self.generated_package(temp)
            with zipfile.ZipFile(output) as package:
                document = ET.fromstring(package.read("visio/document.xml"))

        self.assertEqual(
            [child.tag.rsplit("}", 1)[-1] for child in document],
            ["DocumentSettings", "Colors", "FaceNames", "StyleSheets"],
        )
        settings = document.find(vsdx_gen.V("DocumentSettings"))
        self.assertEqual(
            settings.attrib,
            {
                "TopPage": "0",
                "DefaultTextStyle": "0",
                "DefaultLineStyle": "0",
                "DefaultFillStyle": "0",
                "DefaultGuideStyle": "0",
            },
        )
        self.assertEqual(
            {child.tag.rsplit("}", 1)[-1]: child.text for child in settings},
            {"GlueSettings": "9", "SnapSettings": "65847", "DynamicGridEnabled": "1"},
        )
        faces = document.findall(".//" + vsdx_gen.V("FaceName"))
        self.assertEqual([face.get("NameU") for face in faces], vsdx_gen.FONTS)
        for face in faces:
            self.assertEqual(dict(face.attrib), {"NameU": face.get("NameU")})

        styles = document.findall(".//" + vsdx_gen.V("StyleSheet"))
        self.assertEqual(
            [
                (style.get("ID"), style.get("Name"), style.get("NameU"))
                for style in styles
            ],
            [("0", "No Style", "No Style"), ("1", "Basic", "Basic")],
        )
        expected_cells = {
            "EnableLineProps": "1", "EnableFillProps": "1", "EnableTextProps": "1",
            "LineWeight": "0.01", "LineColor": "#000000", "LinePattern": "1",
            "LineCap": "0", "BeginArrow": "0", "EndArrow": "0",
            "BeginArrowSize": "2", "EndArrowSize": "2",
            "FillForegnd": "#FFFFFF", "FillBkgnd": "#FFFFFF", "FillPattern": "1",
            "ShdwPattern": "0", "ShapeShdwShow": "0", "VerticalAlign": "1",
            "LeftMargin": "0.04", "RightMargin": "0.04",
            "TopMargin": "0.04", "BottomMargin": "0.04",
        }
        for style in styles:
            with self.subTest(style_id=style.get("ID")):
                direct = {
                    cell.get("N"): cell.get("V")
                    for cell in style.findall(vsdx_gen.V("Cell"))
                }
                self.assertEqual(direct, expected_cells)
                sections = style.findall(vsdx_gen.V("Section"))
                self.assertEqual(
                    {section.get("N") for section in sections},
                    {"Character", "Paragraph"},
                )
                for section in sections:
                    cells = {
                        cell.get("N"): cell.get("V")
                        for row in section.findall(vsdx_gen.V("Row"))
                        for cell in row.findall(vsdx_gen.V("Cell"))
                    }
                    if section.get("N") == "Character":
                        self.assertEqual(
                            cells,
                            {
                                "Font": "Arial",
                                "Color": "#000000",
                                "Style": "0",
                                "Size": "0.1666666666666667",
                                "AsianFont": "Microsoft YaHei",
                                "LangID": "zh-CN",
                            },
                        )
                    else:
                        self.assertEqual(
                            cells, {"HorzAlign": "1", "SpLine": "-1.2"}
                        )

    def test_character_fonts_use_declared_face_names(self):
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            for font in vsdx_gen.FONTS:
                with self.subTest(font=font):
                    nodes = [
                        InputContractTests.node("A", fontFamily=font),
                        InputContractTests.node("B", x=4, fontFamily=font),
                    ]
                    edges = [
                        {
                            "from": "A",
                            "to": "B",
                            "label": "edge",
                            "fontFamily": font,
                        }
                    ]
                    data = {
                        "page": {"name": "Fonts", "width": 14, "height": 7},
                        "nodes": nodes,
                        "edges": edges,
                    }
                    output = Path(temp) / (
                        "fonts-%s.vsdx" % font.replace(" ", "-")
                    )
                    vsdx_gen.generate(data, output)
                    with zipfile.ZipFile(output) as package:
                        page = ET.fromstring(package.read("visio/pages/page1.xml"))
                        document = ET.fromstring(
                            package.read("visio/document.xml")
                        )

                    declared = {
                        face.get("NameU")
                        for face in document.findall(".//" + vsdx_gen.V("FaceName"))
                    }
                    font_cells = [
                        cell.get("V")
                        for cell in page.findall(".//" + vsdx_gen.V("Cell"))
                        if cell.get("N") == "Font"
                    ]
                    self.assertTrue(font_cells)
                    for value in font_cells:
                        self.assertEqual(value, font)
                        self.assertIn(value, declared)
                    for shape in page.findall(".//" + vsdx_gen.V("Shape")):
                        self.assertEqual(shape.get("LineStyle"), "1")
                        self.assertEqual(shape.get("FillStyle"), "1")
                        self.assertEqual(shape.get("TextStyle"), "1")

    def test_page_defaults_match_reference_template(self):
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            data = {
                "page": {"name": "Page", "width": 14, "height": 7},
                "nodes": [InputContractTests.node("A")],
                "edges": [],
            }
            output = self.generated_package(temp, data)
            with zipfile.ZipFile(output) as package:
                pages = ET.fromstring(package.read("visio/pages/pages.xml"))

        page = pages.find(vsdx_gen.V("Page"))
        self.assertEqual(
            {key: page.get(key) for key in ("ViewScale", "ViewCenterX", "ViewCenterY")},
            {"ViewScale": "1", "ViewCenterX": "7", "ViewCenterY": "3.5"},
        )
        sheet = page.find(vsdx_gen.V("PageSheet"))
        cells = {
            cell.get("N"): cell.get("V")
            for cell in sheet.findall(vsdx_gen.V("Cell"))
        }
        self.assertEqual(cells["PageWidth"], "14")
        self.assertEqual(cells["PageHeight"], "7")
        self.assertNotIn("ShowPageBreaks", cells)
        expected = {
            "ShdwOffsetX": "0.125", "ShdwOffsetY": "-0.125",
            "PageScale": "1", "DrawingScale": "1",
            "DrawingSizeType": "0", "DrawingScaleType": "0", "InhibitSnap": "0",
            "PageLockReplace": "0", "PageLockDuplicate": "0", "UIVisibility": "0",
            "ShdwType": "0", "ShdwObliqueAngle": "0", "ShdwScaleFactor": "1",
            "DrawingResizeType": "2", "PageShapeSplit": "1",
            "PageLeftMargin": "0", "PageRightMargin": "0",
            "PageTopMargin": "0", "PageBottomMargin": "0",
            "PrintPageOrientation": "2",
        }
        self.assertEqual(
            cells,
            dict(PageWidth="14", PageHeight="7", **expected),
        )
        unit_cells = {
            cell.get("N"): cell.get("U")
            for cell in sheet.findall(vsdx_gen.V("Cell"))
            if cell.get("U") is not None
        }
        self.assertEqual(
            unit_cells, {"PageScale": "PT", "DrawingScale": "PT"}
        )

    @staticmethod
    def mutate_package(package_path, omit=None, xml_mutators=None):
        """Copy a VSDX with parts omitted or XML parts mutated; return new path."""
        target = package_path.with_name(package_path.stem + "-mutated.vsdx")
        omit = set(omit or ())
        with zipfile.ZipFile(package_path) as source:
            infos = source.infolist()
            blobs = {info.filename: source.read(info) for info in infos}
        for part, mutator in (xml_mutators or {}).items():
            root = ET.fromstring(blobs[part])
            mutator(root)
            blobs[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(target, "w") as archive:
            for info in infos:
                if info.filename in omit:
                    continue
                archive.writestr(info, blobs[info.filename])
        return target

    def test_validate_rejects_broken_extended_properties_contract(self):
        def remove_app_override(root):
            root.remove(
                next(
                    node
                    for node in root
                    if node.get("PartName") == "/docProps/app.xml"
                )
            )

        def remove_r_id3(root):
            root.remove(next(node for node in root if node.get("Id") == "rId3"))

        def change_r_id3_type(root):
            next(
                node for node in root if node.get("Id") == "rId3"
            ).set("Type", "http://example.com/wrong")

        def change_r_id3_target(root):
            next(
                node for node in root if node.get("Id") == "rId3"
            ).set("Target", "docProps/other.xml")

        cases = (
            (
                "override-removed",
                {"[Content_Types].xml": remove_app_override},
                "[Content_Types].xml",
                "docProps/app.xml",
            ),
            (
                "rId3-removed",
                {"_rels/.rels": remove_r_id3},
                "_rels/.rels",
                "rId3",
            ),
            (
                "rId3-type",
                {"_rels/.rels": change_r_id3_type},
                "_rels/.rels",
                "rId3",
            ),
            (
                "rId3-target",
                {"_rels/.rels": change_r_id3_target},
                "_rels/.rels",
                "rId3",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            for name, xml_mutators, part, subject in cases:
                with self.subTest(case=name):
                    output = self.generated_package(
                        temp, name="app-" + name + ".vsdx"
                    )
                    mutated = self.mutate_package(
                        output, xml_mutators=xml_mutators
                    )
                    errors = vsdx_gen.validate(mutated)
                    self.assertTrue(any(part in error for error in errors), errors)
                    self.assertTrue(any(subject in error for error in errors), errors)

    def test_validate_rejects_broken_document_contract(self):
        document = "visio/document.xml"

        def remove_glue(root):
            settings = root.find(vsdx_gen.V("DocumentSettings"))
            settings.remove(
                next(
                    child
                    for child in settings
                    if child.tag.rsplit("}", 1)[-1] == "GlueSettings"
                )
            )

        def change_glue(root):
            next(
                child
                for child in root.find(vsdx_gen.V("DocumentSettings"))
                if child.tag.rsplit("}", 1)[-1] == "GlueSettings"
            ).text = "8"

        def insert_event_list(root):
            root.append(vsdx_gen._el(vsdx_gen.V("EventList")))

        def move_face_names_first(root):
            faces = root.find(vsdx_gen.V("FaceNames"))
            root.remove(faces)
            root.insert(0, faces)

        cases = (
            (
                "glue-removed",
                remove_glue,
                "GlueSettings",
            ),
            (
                "glue-changed",
                change_glue,
                "GlueSettings",
            ),
            (
                "event-list",
                insert_event_list,
                "EventList",
            ),
            (
                "face-names-first",
                move_face_names_first,
                "child order",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            for name, mutator, expected in cases:
                with self.subTest(case=name):
                    output = self.generated_package(
                        temp, name="doc-" + name + ".vsdx"
                    )
                    mutated = self.mutate_package(
                        output, xml_mutators={document: mutator}
                    )
                    errors = vsdx_gen.validate(mutated)
                    self.assertTrue(
                        any(expected in error for error in errors), errors
                    )

    def test_validate_rejects_unresolved_fonts_and_styles(self):
        document = "visio/document.xml"

        def remove_face(root):
            root.find(vsdx_gen.V("FaceNames")).remove(
                next(
                    face
                    for face in root.findall(".//" + vsdx_gen.V("FaceName"))
                    if face.get("NameU") == "Microsoft YaHei"
                )
            )

        def remove_style(root):
            root.find(vsdx_gen.V("StyleSheets")).remove(
                next(
                    style
                    for style in root.findall(".//" + vsdx_gen.V("StyleSheet"))
                    if style.get("ID") == "1"
                )
            )

        def change_style_name(root):
            next(
                style
                for style in root.findall(".//" + vsdx_gen.V("StyleSheet"))
                if style.get("ID") == "1"
            ).set("NameU", "Wrong")

        def remove_enable_text_props(root):
            style = next(
                style
                for style in root.findall(".//" + vsdx_gen.V("StyleSheet"))
                if style.get("ID") == "1"
            )
            style.remove(
                next(
                    cell
                    for cell in style.findall(vsdx_gen.V("Cell"))
                    if cell.get("N") == "EnableTextProps"
                )
            )

        def change_page_font(root):
            next(
                cell
                for cell in root.findall(".//" + vsdx_gen.V("Cell"))
                if cell.get("N") == "Font"
            ).set("V", "Missing Font")

        cases = (
            (
                "face-removed",
                {document: remove_face},
                "Microsoft YaHei",
            ),
            (
                "style-removed",
                {document: remove_style},
                "Basic",
            ),
            (
                "style-name-changed",
                {document: change_style_name},
                "NameU",
            ),
            (
                "enable-text-removed",
                {document: remove_enable_text_props},
                "EnableTextProps",
            ),
            (
                "font-missing",
                {"visio/pages/page1.xml": change_page_font},
                "Missing Font",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            for name, xml_mutators, expected in cases:
                with self.subTest(case=name):
                    output = self.generated_package(
                        temp, name="style-" + name + ".vsdx"
                    )
                    mutated = self.mutate_package(
                        output, xml_mutators=xml_mutators
                    )
                    errors = vsdx_gen.validate(mutated)
                    self.assertTrue(
                        any(expected in error for error in errors), errors
                    )

    def test_validate_rejects_broken_page_defaults(self):
        pages = "visio/pages/pages.xml"
        page_sheet = ".//" + vsdx_gen.V("PageSheet")

        def remove_view_center_x(root):
            root.find(".//" + vsdx_gen.V("Page")).attrib.pop("ViewCenterX")

        def change_drawing_size_type(root):
            next(
                cell
                for cell in root.find(page_sheet).findall(vsdx_gen.V("Cell"))
                if cell.get("N") == "DrawingSizeType"
            ).set("V", "3")

        def remove_page_scale_unit(root):
            next(
                cell
                for cell in root.find(page_sheet).findall(vsdx_gen.V("Cell"))
                if cell.get("N") == "PageScale"
            ).attrib.pop("U")

        cases = (
            (
                "view-center-x-removed",
                remove_view_center_x,
                "ViewCenterX",
            ),
            (
                "drawing-size-type",
                change_drawing_size_type,
                "DrawingSizeType",
            ),
            (
                "page-scale-unit",
                remove_page_scale_unit,
                "PageScale",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            for name, mutator, expected in cases:
                with self.subTest(case=name):
                    output = self.generated_package(
                        temp, name="page-" + name + ".vsdx"
                    )
                    mutated = self.mutate_package(
                        output, xml_mutators={pages: mutator}
                    )
                    errors = vsdx_gen.validate(mutated)
                    self.assertTrue(
                        any(expected in error for error in errors), errors
                    )

    def test_windows_part_and_relationship_chain(self):
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            output = self.generated_package(temp)
            with zipfile.ZipFile(output) as package:
                self.assertIn("visio/windows.xml", package.namelist())
                windows = ET.fromstring(package.read("visio/windows.xml"))
                content_types = ET.fromstring(package.read("[Content_Types].xml"))
                document_rels = ET.fromstring(
                    package.read("visio/_rels/document.xml.rels")
                )

        self.assertEqual(windows.tag, vsdx_gen.V("Windows"))
        window = windows.find(vsdx_gen.V("Window"))
        self.assertIsNotNone(window)
        self.assertEqual(window.get("WindowType"), "Drawing")
        overrides = {
            node.get("PartName"): node.get("ContentType")
            for node in content_types
            if node.tag == vsdx_gen.CT("Override")
        }
        self.assertEqual(
            overrides["/visio/windows.xml"],
            "application/vnd.ms-visio.windows+xml",
        )
        rels = {
            node.get("Id"): (node.get("Type"), node.get("Target"))
            for node in document_rels
        }
        self.assertEqual(
            rels["rId2"],
            (
                "http://schemas.microsoft.com/visio/2010/relationships/windows",
                "windows.xml",
            ),
        )

    def test_validate_rejects_missing_windows_part(self):
        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            output = self.generated_package(temp)
            mutated = self.mutate_package(output, omit={"visio/windows.xml"})
            errors = vsdx_gen.validate(mutated)
        self.assertTrue(any("visio/windows.xml" in error for error in errors))

    def test_validate_rejects_broken_windows_relationship(self):
        def change_windows_type(root):
            next(
                node
                for node in root
                if node.get("Id") == "rId2"
            ).set("Type", "http://example.invalid/windows")

        with tempfile.TemporaryDirectory(prefix="visio-contract-", dir=SKILL_ROOT / "tests") as temp:
            output = self.generated_package(temp)
            mutated = self.mutate_package(
                output,
                xml_mutators={
                    "visio/_rels/document.xml.rels": change_windows_type,
                },
            )
            errors = vsdx_gen.validate(mutated)
        self.assertTrue(any("windows" in error for error in errors))


class EdgeSemanticsTests(unittest.TestCase):
    """Connector anchors, geometry paths, glue records, and validation."""

    @staticmethod
    def node(node_id, x, y, w=2.0, h=1.0):
        return {"id": node_id, "x": x, "y": y, "w": w, "h": h}

    @classmethod
    def data(cls, edges=None, nodes=None):
        return {
            "nodes": nodes or [cls.node("A", 2.0, 2.0), cls.node("B", 6.0, 2.0)],
            "edges": edges or [{"from": "A", "to": "B"}],
        }

    @staticmethod
    def temporary_directory():
        return tempfile.TemporaryDirectory(prefix="task5-", dir=SKILL_ROOT / "tests")

    @staticmethod
    def page(data):
        normalized = vsdx_gen.normalize_input(data)
        return vsdx_gen._page_xml(
            normalized["nodes"],
            normalized["edges"],
            vsdx_gen._make_palette(normalized["nodes"], normalized["edges"]),
        )

    @staticmethod
    def cell_value(shape, name):
        cell = shape.find("%s[@N='%s']" % (vsdx_gen.V("Cell"), name))
        if cell is None:
            raise AssertionError("shape %s has no %s cell" % (shape.get("ID"), name))
        return float(cell.get("V"))

    @classmethod
    def connector_endpoints(cls, page):
        connectors = page.findall(
            ".//%s[@NameU='Connector']" % vsdx_gen.V("Shape")
        )
        return [
            (
                cls.cell_value(shape, "BeginX"),
                cls.cell_value(shape, "BeginY"),
                cls.cell_value(shape, "EndX"),
                cls.cell_value(shape, "EndY"),
            )
            for shape in connectors
        ]

    @staticmethod
    def rewrite_page(package_path, mutate):
        with zipfile.ZipFile(package_path) as source:
            entries = [(info, source.read(info.filename)) for info in source.infolist()]
        page_index = next(
            index
            for index, (info, _) in enumerate(entries)
            if info.filename == "visio/pages/page1.xml"
        )
        info, page_bytes = entries[page_index]
        page = ET.fromstring(page_bytes)
        mutate(page)
        entries[page_index] = (info, vsdx_gen._serialize(page))
        rewritten = Path(package_path).with_suffix(".rewritten.vsdx")
        with zipfile.ZipFile(rewritten, "w") as destination:
            for entry_info, content in entries:
                destination.writestr(entry_info, content)
        os.replace(rewritten, package_path)

    def generated_package(self, directory, data=None, name="diagram.vsdx"):
        source_data = data or self.data()
        output = Path(directory) / name
        vsdx_gen.generate(source_data, output)

        normalized = vsdx_gen.normalize_input(source_data)

        def seed_pre_fix_connects(page):
            if page.find(vsdx_gen.V("Connects")) is not None:
                return
            node_shape_ids = {
                node["id"]: str(index)
                for index, node in enumerate(normalized["nodes"], start=1)
            }
            connects = ET.SubElement(page, vsdx_gen.V("Connects"))
            connector_id = len(normalized["nodes"]) + 1
            for edge in normalized["edges"]:
                for role, from_part, endpoint in (
                    ("BeginX", "9", edge["from"]),
                    ("EndX", "12", edge["to"]),
                ):
                    ET.SubElement(
                        connects,
                        vsdx_gen.V("Connect"),
                        {
                            "FromSheet": str(connector_id),
                            "FromCell": role,
                            "FromPart": from_part,
                            "ToSheet": node_shape_ids[endpoint],
                            "ToCell": "PinX",
                            "ToPart": "3",
                        },
                    )
                connector_id += 1

        self.rewrite_page(output, seed_pre_fix_connects)
        return output

    def test_waypoints_preserve_true_boundary_endpoints_and_path_order(self):
        stress = json.loads((SKILL_ROOT / "examples" / "stress-flow.json").read_text(
            encoding="utf-8"
        ))
        edge = next(item for item in stress["edges"] if item.get("id") == "e5")
        node_ids = {edge["from"], edge["to"]}
        nodes = [node for node in stress["nodes"] if node["id"] in node_ids]
        page = self.page({"nodes": nodes, "edges": [edge]})
        connector = page.find(".//%s[@NameU='Connector']" % vsdx_gen.V("Shape"))

        self.assertEqual(self.connector_endpoints(page), [(11.0, 14.15, 1.0, 12.85)])
        geometry = next(
            section
            for section in connector.findall(vsdx_gen.V("Section"))
            if section.get("N") == "Geometry"
        )
        page_points = []
        for row in geometry.findall(vsdx_gen.V("Row")):
            x = self.cell_value(row, "X") + 11.0
            y = self.cell_value(row, "Y") + 14.15
            page_points.append((x, y))
        expected = [(11.0, 14.15), (6.0, 13.5), (1.0, 12.85)]
        for actual, wanted in zip(page_points, expected):
            self.assertAlmostEqual(actual[0], wanted[0], places=9)
            self.assertAlmostEqual(actual[1], wanted[1], places=9)
        self.assertEqual(len(page_points), len(expected))

    def test_connector_label_text_pin_tracks_polyline_arclength_midpoint(self):
        page = self.page({
            "nodes": [
                self.node("A", 1.0, 1.0, w=1.0, h=1.0),
                self.node("B", 5.0, 1.0, w=1.0, h=1.0),
            ],
            "edges": [{
                "from": "A",
                "to": "B",
                "label": "U label",
                "points": [[1.5, 3.0], [4.5, 3.0]],
            }],
        })
        connector = page.find(
            ".//%s[@NameU='Connector']" % vsdx_gen.V("Shape")
        )

        self.assertEqual(self.cell_value(connector, "TxtPinX"), 1.5)
        self.assertEqual(self.cell_value(connector, "TxtPinY"), 2.0)
        for cell_name in ("TxtLocPinX", "TxtLocPinY", "TxtWidth", "TxtHeight"):
            self.assertEqual(self.cell_value(connector, cell_name), 0.0)

    def test_explicit_source_and_target_sides_independently_override_defaults(self):
        anchors = {
            "left": (1.0, 2.0),
            "right": (3.0, 2.0),
            "top": (2.0, 2.5),
            "bottom": (2.0, 1.5),
        }
        target_anchors = {
            "left": (5.0, 2.0),
            "right": (7.0, 2.0),
            "top": (6.0, 2.5),
            "bottom": (6.0, 1.5),
        }
        edges = []
        for side in ("left", "right", "top", "bottom"):
            edges.append({"from": "A", "to": "B", "fromSide": side})
        for side in ("left", "right", "top", "bottom"):
            edges.append({"from": "A", "to": "B", "toSide": side})

        endpoints = self.connector_endpoints(self.page(self.data(edges=edges)))

        for index, side in enumerate(("left", "right", "top", "bottom")):
            self.assertEqual(endpoints[index][:2], anchors[side])
            self.assertEqual(endpoints[index][2:], target_anchors["left"])
            self.assertEqual(endpoints[index + 4][:2], anchors["right"])
            self.assertEqual(endpoints[index + 4][2:], target_anchors[side])

    def test_ninety_degree_rotation_maps_all_four_visual_side_anchors(self):
        node = self.node("A", 10.0, 20.0, w=4.0, h=2.0)
        node["rotation"] = 90
        expected = {
            "left": (10.0, 18.0),
            "right": (10.0, 22.0),
            "top": (9.0, 20.0),
            "bottom": (11.0, 20.0),
        }

        for side, wanted in expected.items():
            with self.subTest(side=side):
                actual = vsdx_gen._anchor(node, side)
                self.assertAlmostEqual(actual[0], wanted[0], places=9)
                self.assertAlmostEqual(actual[1], wanted[1], places=9)

    def test_rotated_source_connector_uses_the_visual_side_anchor(self):
        source = self.node("A", 4.45, 5.0, w=1.2, h=0.75)
        source["rotation"] = 30
        target = self.node("B", 6.05, 5.0, w=1.3, h=0.75)
        page = self.page(self.data(
            nodes=[source, target],
            edges=[{
                "from": "A", "to": "B",
                "fromSide": "right", "toSide": "left",
            }],
        ))

        begin_x, begin_y, end_x, end_y = self.connector_endpoints(page)[0]
        self.assertAlmostEqual(begin_x, 4.969615, delta=0.00005)
        self.assertAlmostEqual(begin_y, 5.3, places=9)
        self.assertEqual((end_x, end_y), (5.4, 5.0))

    def test_validate_accepts_a_rotated_visual_boundary_anchor(self):
        source = self.node("A", 4.45, 5.0, w=1.2, h=0.75)
        source["rotation"] = 30
        target = self.node("B", 6.05, 5.0, w=1.3, h=0.75)
        data = self.data(
            nodes=[source, target],
            edges=[{
                "from": "A", "to": "B",
                "fromSide": "right", "toSide": "left",
            }],
        )
        rotated_begin = (4.9696, 5.3)
        end = (5.4, 5.0)

        def rotate_generated_connector(page):
            connector = page.find(
                ".//%s[@NameU='Connector']" % vsdx_gen.V("Shape")
            )
            cells = {
                cell.get("N"): cell
                for cell in connector.findall(vsdx_gen.V("Cell"))
            }
            cells["BeginX"].set("V", str(rotated_begin[0]))
            cells["BeginY"].set("V", str(rotated_begin[1]))
            geometry = next(
                section
                for section in connector.findall(vsdx_gen.V("Section"))
                if section.get("N") == "Geometry"
            )
            final_cells = {
                cell.get("N"): cell
                for cell in geometry.findall(vsdx_gen.V("Row"))[-1].findall(
                    vsdx_gen.V("Cell")
                )
            }
            final_cells["X"].set("V", str(round(end[0] - rotated_begin[0], 4)))
            final_cells["Y"].set("V", str(round(end[1] - rotated_begin[1], 4)))

        with self.temporary_directory() as temp_dir:
            package = self.generated_package(temp_dir, data=data)
            self.rewrite_page(package, rotate_generated_connector)

            self.assertEqual(vsdx_gen.validate(package), [])

    def test_generation_validator_expects_the_rotated_anchor(self):
        source = self.node("A", 4.45, 5.0, w=1.2, h=0.75)
        source["rotation"] = 30
        target = self.node("B", 6.05, 5.0, w=1.3, h=0.75)
        data = self.data(
            nodes=[source, target],
            edges=[{
                "from": "A", "to": "B",
                "fromSide": "right", "toSide": "left",
            }],
        )
        real_page_xml = vsdx_gen._page_xml

        def restore_unrotated_begin(nodes, edges, palette):
            page = real_page_xml(nodes, edges, palette)
            connector = page.find(
                ".//%s[@NameU='Connector']" % vsdx_gen.V("Shape")
            )
            cells = {
                cell.get("N"): cell
                for cell in connector.findall(vsdx_gen.V("Cell"))
            }
            cells["BeginX"].set("V", "5.05")
            cells["BeginY"].set("V", "5.0")
            return page

        with self.temporary_directory() as temp_dir:
            output = Path(temp_dir) / "unrotated-anchor.vsdx"
            with mock.patch.object(
                vsdx_gen, "_page_xml", side_effect=restore_unrotated_begin
            ):
                with self.assertRaises(vsdx_gen.PackageValidationError) as caught:
                    vsdx_gen.generate(data, output)

        self.assertTrue(any(
            "BeginX/BeginY does not match expected anchor" in error
            and "4.9696" in error
            for error in caught.exception.errors
        ), caught.exception.errors)

    def test_automatic_side_selection_covers_horizontal_vertical_and_ties(self):
        origin = {"x": 0.0, "y": 0.0}
        cases = (
            ({"x": 4.0, "y": 1.0}, ("right", "left")),
            ({"x": -4.0, "y": 1.0}, ("left", "right")),
            ({"x": 1.0, "y": 4.0}, ("top", "bottom")),
            ({"x": 1.0, "y": -4.0}, ("bottom", "top")),
            ({"x": 3.0, "y": 3.0}, ("top", "bottom")),
            ({"x": -3.0, "y": -3.0}, ("bottom", "top")),
        )
        for target, expected in cases:
            with self.subTest(target=target):
                self.assertEqual(vsdx_gen._default_sides(origin, target), expected)

    def test_connects_follow_shapes_with_stable_node_and_connector_ids(self):
        data = self.data(
            nodes=[
                self.node("A", 2.0, 2.0),
                self.node("B", 6.0, 2.0),
                self.node("C", 4.0, 5.0),
            ],
            edges=[
                {"from": "A", "to": "B"},
                {"from": "A", "to": "C"},
                {"from": "C", "to": "B"},
                {"from": "A", "to": "B"},
            ],
        )
        page = self.page(data)
        self.assertEqual(
            [child.tag for child in page],
            [vsdx_gen.V("Shapes"), vsdx_gen.V("Connects")],
        )
        shapes = page.find(vsdx_gen.V("Shapes")).findall(vsdx_gen.V("Shape"))
        self.assertEqual([shape.get("ID") for shape in shapes], ["1", "2", "3", "4", "5", "6", "7"])
        self.assertEqual(
            [shape.get("NameU") for shape in shapes[3:]],
            ["Connector", "Connector", "Connector", "Connector"],
        )

        actual = [connect.attrib for connect in page.findall(".//" + vsdx_gen.V("Connect"))]
        expected = []
        for connector_id, source_id, target_id in (
            ("4", "1", "2"),
            ("5", "1", "3"),
            ("6", "3", "2"),
            ("7", "1", "2"),
        ):
            expected.extend((
                {
                    "FromSheet": connector_id,
                    "FromCell": "BeginX",
                    "FromPart": "9",
                    "ToSheet": source_id,
                    "ToCell": "PinX",
                    "ToPart": "3",
                },
                {
                    "FromSheet": connector_id,
                    "FromCell": "EndX",
                    "FromPart": "12",
                    "ToSheet": target_id,
                    "ToCell": "PinX",
                    "ToPart": "3",
                },
            ))
        self.assertEqual(actual, expected)

    def test_validate_reports_missing_and_duplicate_endpoint_records(self):
        cases = (
            (
                "missing",
                lambda page: page.find(vsdx_gen.V("Connects")).remove(
                    next(
                        connect
                        for connect in page.findall(".//" + vsdx_gen.V("Connect"))
                        if connect.get("FromCell") == "EndX"
                    )
                ),
                "connector 3 has 0 EndX connect records (expected 1)",
            ),
            (
                "duplicate",
                lambda page: page.find(vsdx_gen.V("Connects")).append(
                    copy.deepcopy(
                        next(
                            connect
                            for connect in page.findall(".//" + vsdx_gen.V("Connect"))
                            if connect.get("FromCell") == "BeginX"
                        )
                    )
                ),
                "connector 3 has 2 BeginX connect records (expected 1)",
            ),
        )
        with self.temporary_directory() as temp_dir:
            for name, mutate, expected in cases:
                with self.subTest(case=name):
                    package = self.generated_package(temp_dir, name=name + ".vsdx")
                    self.rewrite_page(package, mutate)
                    self.assertIn(expected, vsdx_gen.validate(package))

    def test_validate_reports_unknown_and_connector_endpoint_shape_references(self):
        def change_connect(page, role, attribute, value):
            connect = next(
                item
                for item in page.findall(".//" + vsdx_gen.V("Connect"))
                if item.get("FromCell") == role
            )
            connect.set(attribute, value)

        cases = (
            (
                "unknown-from",
                lambda page: change_connect(page, "BeginX", "FromSheet", "999"),
                "connect BeginX references unknown FromSheet 999",
            ),
            (
                "unknown-to",
                lambda page: change_connect(page, "EndX", "ToSheet", "999"),
                "connector 3 EndX references unknown ToSheet 999",
            ),
            (
                "connector-target",
                lambda page: change_connect(page, "EndX", "ToSheet", "3"),
                "connector 3 EndX targets connector shape 3",
            ),
        )
        with self.temporary_directory() as temp_dir:
            for name, mutate, expected in cases:
                with self.subTest(case=name):
                    package = self.generated_package(temp_dir, name=name + ".vsdx")
                    self.rewrite_page(package, mutate)
                    self.assertIn(expected, vsdx_gen.validate(package))

    def test_generate_rejects_missing_or_renumbered_expected_connectors(self):
        real_page_xml = vsdx_gen._page_xml

        def corrupt_page(mode):
            def build(nodes, edges, palette):
                page = real_page_xml(nodes, edges, palette)
                shapes = page.find(vsdx_gen.V("Shapes"))
                connector = shapes.find("%s[@NameU='Connector']" % vsdx_gen.V("Shape"))
                connects = page.find(vsdx_gen.V("Connects"))
                if mode == "missing":
                    shapes.remove(connector)
                    connects.clear()
                else:
                    connector.set("ID", "9")
                    for connect in connects.findall(vsdx_gen.V("Connect")):
                        connect.set("FromSheet", "9")
                return page
            return build

        cases = (
            ("missing", "missing expected connector shape IDs: ['3']"),
            ("renumbered", "unexpected connector shape IDs: ['9']"),
        )
        with self.temporary_directory() as temp_dir:
            for mode, expected in cases:
                with self.subTest(mode=mode):
                    output = Path(temp_dir) / (mode + ".vsdx")
                    with mock.patch.object(
                        vsdx_gen, "_page_xml", side_effect=corrupt_page(mode)
                    ):
                        with self.assertRaises(vsdx_gen.PackageValidationError) as caught:
                            vsdx_gen.generate(self.data(), output)
                    self.assertIn(expected, caught.exception.errors)
                    self.assertFalse(output.exists())

    def test_generate_rejects_input_mismatched_rewire_and_selected_anchor(self):
        real_page_xml = vsdx_gen._page_xml
        data = self.data(
            nodes=[
                self.node("A", 2.0, 2.0),
                self.node("B", 6.0, 2.0),
                self.node("C", 2.0, 5.0),
            ],
            edges=[{"from": "A", "to": "B"}],
        )

        def connector_parts(page):
            connector = page.find(".//%s[@NameU='Connector']" % vsdx_gen.V("Shape"))
            cells = {
                cell.get("N"): cell
                for cell in connector.findall(vsdx_gen.V("Cell"))
            }
            geometry = next(
                section
                for section in connector.findall(vsdx_gen.V("Section"))
                if section.get("N") == "Geometry"
            )
            final_cells = {
                cell.get("N"): cell
                for cell in geometry.findall(vsdx_gen.V("Row"))[-1].findall(
                    vsdx_gen.V("Cell")
                )
            }
            connects = {
                connect.get("FromCell"): connect
                for connect in page.findall(".//" + vsdx_gen.V("Connect"))
            }
            return cells, final_cells, connects

        def corrupt_page(mode):
            def build(nodes, edges, palette):
                page = real_page_xml(nodes, edges, palette)
                cells, final_cells, connects = connector_parts(page)
                if mode == "source-rewire":
                    connects["BeginX"].set("ToSheet", "3")
                    cells["BeginX"].set("V", "3")
                    cells["BeginY"].set("V", "5")
                    final_cells["X"].set("V", "2")
                    final_cells["Y"].set("V", "-3")
                else:
                    cells["EndX"].set("V", "6")
                    cells["EndY"].set("V", "2.5")
                    final_cells["X"].set("V", "3")
                    final_cells["Y"].set("V", "0.5")
                return page
            return build

        cases = (
            (
                "source-rewire",
                (
                    "connector 4 BeginX ToSheet 3 does not match expected 1",
                    "connector 4 BeginX/BeginY does not match expected anchor (3.0, 2.0)",
                ),
            ),
            (
                "target-anchor",
                (
                    "connector 4 EndX/EndY does not match expected anchor (5.0, 2.0)",
                ),
            ),
        )
        with self.temporary_directory() as temp_dir:
            for mode, expected_errors in cases:
                with self.subTest(mode=mode):
                    output = Path(temp_dir) / (mode + ".vsdx")
                    with mock.patch.object(
                        vsdx_gen, "_page_xml", side_effect=corrupt_page(mode)
                    ):
                        with self.assertRaises(vsdx_gen.PackageValidationError) as caught:
                            vsdx_gen.generate(data, output)
                    for expected in expected_errors:
                        self.assertIn(expected, caught.exception.errors)
                    self.assertFalse(output.exists())

    def test_validate_reports_nonstandard_glue_attributes(self):
        def change_attribute(page, role, attribute, value):
            connect = next(
                item
                for item in page.findall(".//" + vsdx_gen.V("Connect"))
                if item.get("FromCell") == role
            )
            if value is None:
                connect.attrib.pop(attribute, None)
            else:
                connect.set(attribute, value)

        cases = (
            (
                "begin-from-part",
                lambda page: change_attribute(page, "BeginX", "FromPart", "12"),
                "connector 3 BeginX FromPart must be 9",
            ),
            (
                "end-from-part",
                lambda page: change_attribute(page, "EndX", "FromPart", None),
                "connector 3 EndX FromPart must be 12",
            ),
            (
                "to-cell",
                lambda page: change_attribute(page, "BeginX", "ToCell", "Width"),
                "connector 3 BeginX ToCell must be PinX",
            ),
            (
                "to-part",
                lambda page: change_attribute(page, "EndX", "ToPart", "0"),
                "connector 3 EndX ToPart must be 3",
            ),
        )
        with self.temporary_directory() as temp_dir:
            for name, mutate, expected in cases:
                with self.subTest(case=name):
                    package = self.generated_package(temp_dir, name=name + ".vsdx")
                    self.rewrite_page(package, mutate)
                    self.assertIn(expected, vsdx_gen.validate(package))

    def test_validate_reports_non_finite_connector_and_geometry_coordinates(self):
        def set_shape_cell(page, name, value):
            connector = page.find(".//%s[@NameU='Connector']" % vsdx_gen.V("Shape"))
            connector.find("%s[@N='%s']" % (vsdx_gen.V("Cell"), name)).set("V", value)

        def set_last_geometry_cell(page, name, value):
            connector = page.find(".//%s[@NameU='Connector']" % vsdx_gen.V("Shape"))
            geometry = next(
                section
                for section in connector.findall(vsdx_gen.V("Section"))
                if section.get("N") == "Geometry"
            )
            rows = geometry.findall(vsdx_gen.V("Row"))
            rows[-1].find("%s[@N='%s']" % (vsdx_gen.V("Cell"), name)).set("V", value)

        cases = (
            (
                "endpoint",
                lambda page: set_shape_cell(page, "EndY", "Infinity"),
                "connector 3 cell EndY must be a finite number",
            ),
            (
                "geometry",
                lambda page: set_last_geometry_cell(page, "X", "NaN"),
                "connector 3 Geometry X must be a finite number",
            ),
        )
        with self.temporary_directory() as temp_dir:
            for name, mutate, expected in cases:
                with self.subTest(case=name):
                    package = self.generated_package(temp_dir, name=name + ".vsdx")
                    self.rewrite_page(package, mutate)
                    self.assertIn(expected, vsdx_gen.validate(package))

    def test_validate_reports_wrong_final_geometry_and_boundary_anchors(self):
        def connector_parts(page):
            connector = page.find(".//%s[@NameU='Connector']" % vsdx_gen.V("Shape"))
            cells = {
                cell.get("N"): cell
                for cell in connector.findall(vsdx_gen.V("Cell"))
            }
            geometry = next(
                section
                for section in connector.findall(vsdx_gen.V("Section"))
                if section.get("N") == "Geometry"
            )
            final_cells = {
                cell.get("N"): cell
                for cell in geometry.findall(vsdx_gen.V("Row"))[-1].findall(
                    vsdx_gen.V("Cell")
                )
            }
            return cells, final_cells

        def wrong_final(page):
            _, final_cells = connector_parts(page)
            final_cells["X"].set("V", "0")

        def wrong_begin_anchor(page):
            cells, final_cells = connector_parts(page)
            cells["BeginY"].set("V", "2.1")
            final_cells["Y"].set("V", "-0.1")

        def wrong_end_anchor(page):
            cells, final_cells = connector_parts(page)
            cells["EndY"].set("V", "2.1")
            final_cells["Y"].set("V", "0.1")

        cases = (
            (
                "final",
                wrong_final,
                "connector 3 final geometry point does not match EndX/EndY",
            ),
            (
                "begin-anchor",
                wrong_begin_anchor,
                "connector 3 BeginX/BeginY is not on boundary anchor of shape 1",
            ),
            (
                "end-anchor",
                wrong_end_anchor,
                "connector 3 EndX/EndY is not on boundary anchor of shape 2",
            ),
        )
        with self.temporary_directory() as temp_dir:
            for name, mutate, expected in cases:
                with self.subTest(case=name):
                    package = self.generated_package(temp_dir, name=name + ".vsdx")
                    self.rewrite_page(package, mutate)
                    self.assertIn(expected, vsdx_gen.validate(package))

    def test_validate_safely_reports_mixed_missing_geometry_names_and_shape_ids(self):
        data = self.data(
            nodes=[
                self.node("A", 2.0, 2.0),
                self.node("B", 6.0, 2.0),
                self.node("C", 2.0, 5.0),
            ],
            edges=[{"from": "A", "to": "B"}],
        )

        def corrupt_metadata(page):
            shapes = page.find(vsdx_gen.V("Shapes")).findall(vsdx_gen.V("Shape"))
            shapes[0].attrib.pop("ID")
            shapes[1].attrib.pop("ID")
            shapes[2].set("ID", "7")
            shapes[3].set("ID", "7")
            connector = shapes[3]
            geometry = next(
                section
                for section in connector.findall(vsdx_gen.V("Section"))
                if section.get("N") == "Geometry"
            )
            final_cells = geometry.findall(vsdx_gen.V("Row"))[-1].findall(
                vsdx_gen.V("Cell")
            )
            final_cells[0].attrib.pop("N")
            final_cells[1].set("N", "x")

        with self.temporary_directory() as temp_dir:
            package = self.generated_package(temp_dir, data=data)
            self.rewrite_page(package, corrupt_metadata)
            try:
                errors = vsdx_gen.validate(package)
            except Exception as error:
                self.fail("validate raised %s: %s" % (type(error).__name__, error))

        self.assertIn("duplicate shape IDs: ['7', '<missing>']", errors)
        legal = "A/B/C/D/E/X/Y"
        self.assertIn(
            'geometry row cell N="<missing>" must be uppercase (%s)' % legal,
            errors,
        )
        self.assertIn(
            'geometry row cell N="x" must be uppercase (%s)' % legal,
            errors,
        )


class BuiltInShapeTests(unittest.TestCase):
    """Built-in geometry stays finite, bounded, and directionally correct."""

    WIDTH = 2.0
    HEIGHT = 1.0

    @staticmethod
    def point_rows(rows):
        points = []
        for row_type, cells in rows:
            values = []
            for value in cells:
                if isinstance(value, (list, tuple)):
                    values.extend(value)
                else:
                    values.append(value)
            points.append((row_type, tuple(values)))
        return points

    def test_every_public_shape_has_finite_bounded_geometry_points(self):
        self.assertEqual(set(vsdx_gen._SHAPE_GEO), set(EXPECTED_PUBLIC_SHAPES))
        for shape_name in sorted(EXPECTED_PUBLIC_SHAPES):
            with self.subTest(shape=shape_name):
                rows = self.point_rows(
                    vsdx_gen._SHAPE_GEO[shape_name](self.WIDTH, self.HEIGHT)
                )
                self.assertTrue(rows)
                for row_type, values in rows:
                    self.assertGreaterEqual(len(values), 2)
                    for value in values:
                        self.assertTrue(math.isfinite(value))
                    x, y = values[:2]
                    self.assertGreaterEqual(x, 0.0)
                    self.assertLessEqual(x, self.WIDTH)
                    self.assertGreaterEqual(y, 0.0)
                    self.assertLessEqual(y, self.HEIGHT)

    def test_cylinder_arc_control_is_the_cap_sagitta_not_a_radius(self):
        rows = self.point_rows(
            vsdx_gen._SHAPE_GEO["cylinder"](self.WIDTH, self.HEIGHT)
        )
        arc_values = [values for row_type, values in rows if row_type == "ArcTo"]
        expected_cap = min(self.WIDTH * 0.1, self.HEIGHT * 0.1) or 0.05
        self.assertEqual(len(arc_values), 2)
        self.assertEqual([values[2] for values in arc_values], [expected_cap] * 2)

    def test_cylinder_arc_midpoints_stay_inside_the_shape_bounds(self):
        rows = self.point_rows(
            vsdx_gen._SHAPE_GEO["cylinder"](self.WIDTH, self.HEIGHT)
        )
        previous = None
        arc_midpoints = []
        for row_type, values in rows:
            point = values[:2]
            if row_type == "ArcTo":
                self.assertIsNotNone(previous)
                self.assertEqual(previous[1], point[1])
                direction = point[0] - previous[0]
                self.assertNotEqual(direction, 0.0)
                arc_midpoints.append(
                    point[1] - math.copysign(values[2], direction)
                )
            previous = point

        self.assertEqual(len(arc_midpoints), 2)
        self.assertTrue(all(0.0 <= y <= self.HEIGHT for y in arc_midpoints))
        self.assertEqual(arc_midpoints, [0.0, self.HEIGHT])

    def test_cylinder_geometry_stays_bounded_for_smallest_positive_dimensions(self):
        tiny = math.nextafter(0.0, 1.0)
        rows = self.point_rows(vsdx_gen._SHAPE_GEO["cylinder"](tiny, tiny))

        for _, values in rows:
            x, y = values[:2]
            self.assertGreaterEqual(x, 0.0)
            self.assertLessEqual(x, tiny)
            self.assertGreaterEqual(y, 0.0)
            self.assertLessEqual(y, tiny)
        arc_values = [values for row_type, values in rows if row_type == "ArcTo"]
        self.assertTrue(all(0.0 <= values[2] <= tiny for values in arc_values))

    def test_up_arrow_tip_is_at_the_top_in_y_up_coordinates(self):
        up_rows = self.point_rows(
            vsdx_gen._SHAPE_GEO["upArrow"](self.WIDTH, self.HEIGHT)
        )
        up_points = [values[:2] for _, values in up_rows]
        self.assertIn((self.WIDTH / 2, self.HEIGHT), up_points)

    def test_down_arrow_tip_is_at_the_bottom_in_y_up_coordinates(self):
        down_rows = self.point_rows(
            vsdx_gen._SHAPE_GEO["downArrow"](self.WIDTH, self.HEIGHT)
        )
        down_points = [values[:2] for _, values in down_rows]
        self.assertIn((self.WIDTH / 2, 0.0), down_points)


class CustomGeometryContractTests(unittest.TestCase):
    """Contract tests for the strict, JSON-shaped custom geometry escape hatch."""

    SCHEMA = (
        ("MoveTo", ("X", "Y")),
        ("LineTo", ("X", "Y")),
        ("ArcTo", ("X", "Y", "A")),
        ("Ellipse", ("X", "Y", "A", "B", "C", "D")),
        ("EllipticalArcTo", ("X", "Y", "A", "B", "C", "D")),
        ("InfiniteLine", ("X", "Y", "A", "B")),
        ("NURBSTo", ("X", "Y", "A", "B", "C", "D", "E")),
        ("PolylineTo", ("X", "Y", "A")),
        ("RelCubBezTo", ("X", "Y", "A", "B", "C", "D")),
        ("RelEllipticalArcTo", ("X", "Y", "A", "B", "C", "D")),
        ("RelLineTo", ("X", "Y")),
        ("RelMoveTo", ("X", "Y")),
        ("RelQuadBezTo", ("X", "Y", "A", "B")),
        ("SplineStart", ("X", "Y", "A", "B", "C", "D")),
        ("SplineKnot", ("X", "Y", "A")),
    )
    ROW_TYPES = tuple(row_type for row_type, _ in SCHEMA)
    RELATIVE_TYPES = frozenset(
        row_type for row_type in ROW_TYPES if row_type.startswith("Rel")
    )
    ABSOLUTE_TYPES = frozenset(ROW_TYPES) - RELATIVE_TYPES

    @staticmethod
    def node(geometry):
        return {
            "id": "A",
            "x": 1.0,
            "y": 2.0,
            "w": 2.0,
            "h": 3.0,
            "geometry": geometry,
        }

    @classmethod
    def data_for(cls, geometry):
        return {"nodes": [cls.node(geometry)], "edges": []}

    @classmethod
    def params_for(cls, row_type):
        cells = dict(cls.SCHEMA)[row_type]
        params = {cell.lower(): 0.5 for cell in cells}
        if row_type == "PolylineTo":
            params["a"] = "1 2"
        return params

    @classmethod
    def rows_for(cls, row_type, params=None):
        params = cls.params_for(row_type) if params is None else params
        row = [row_type, params]
        if row_type == "MoveTo":
            return [row]
        return [["MoveTo", cls.params_for("MoveTo")], row]

    @classmethod
    def all_rows(cls):
        return [[row_type, cls.params_for(row_type)] for row_type in cls.ROW_TYPES]

    def assert_invalid(self, geometry, fragment="geometry"):
        errors = vsdx_gen.validate_input(self.data_for(geometry))
        self.assertTrue(errors, "expected invalid geometry: %r" % (geometry,))
        self.assertTrue(
            any(fragment in error for error in errors),
            "expected %r in errors, got %r" % (fragment, errors),
        )

    def test_schema_is_immutable_and_exactly_the_approved_fifteen_rows(self):
        expected = dict(self.SCHEMA)
        self.assertIsInstance(vsdx_gen._ROW_CELLS, MappingProxyType)
        self.assertEqual(dict(vsdx_gen._ROW_CELLS), expected)
        self.assertEqual(tuple(vsdx_gen._ROW_CELLS), self.ROW_TYPES)
        for values in vsdx_gen._ROW_CELLS.values():
            self.assertIsInstance(values, tuple)
        with self.assertRaises(TypeError):
            vsdx_gen._ROW_CELLS["MoveTo"] = ("X",)

    def test_accepts_every_row_type_and_normalizes_keys_to_uppercase(self):
        raw_rows = self.all_rows()
        normalized = vsdx_gen.normalize_input(self.data_for(raw_rows))
        geometry = normalized["nodes"][0]["geometry"]
        self.assertEqual(tuple(row[0] for row in geometry), self.ROW_TYPES)
        for row, expected_cells in zip(geometry, self.SCHEMA):
            row_type, params = row
            self.assertIsInstance(row, list)
            self.assertEqual(row_type, expected_cells[0])
            self.assertIsInstance(params, dict)
            self.assertEqual(tuple(params), expected_cells[1])
            self.assertTrue(all(key.isupper() for key in params))

    def test_requires_nonempty_list_rows_with_exact_json_arity_and_types(self):
        cases = (
            ([], "empty"),
            ((["MoveTo", {"x": 0, "y": 0}],), "geometry tuple"),
            (["MoveTo", {"x": 0, "y": 0}], "row tuple"),
            ([["MoveTo"]], "row arity 1"),
            ([["MoveTo", {"x": 0, "y": 0}, "extra"]], "row arity 3"),
            ([[1, {"x": 0, "y": 0}]], "row type number"),
            ([[[], {"x": 0, "y": 0}]], "row type array"),
            ([["MoveTo", []]], "params array"),
            ([["MoveTo", ("x", 0)]], "params tuple"),
            ([{"rowType": "MoveTo", "params": {"x": 0, "y": 0}}], "direct object"),
        )
        for geometry, label in cases:
            with self.subTest(case=label):
                self.assert_invalid(geometry)

    def test_row_type_is_known_case_sensitive_and_first_row_must_be_moveto(self):
        for row_type in ("moveto", "MOVETO", "NotARow"):
            with self.subTest(row_type=row_type):
                self.assert_invalid(
                    [[row_type, {"x": 0, "y": 0}]],
                    "geometry",
                )
        self.assert_invalid(
            [["LineTo", {"x": 0, "y": 0}], ["MoveTo", {"x": 0, "y": 0}]],
            "geometry",
        )

    def test_rejects_every_missing_required_cell_and_every_unknown_extra_cell(self):
        for row_type, cells in self.SCHEMA:
            for missing in cells:
                params = self.params_for(row_type)
                del params[missing.lower()]
                with self.subTest(row_type=row_type, missing=missing):
                    self.assert_invalid(self.rows_for(row_type, params), "geometry")
            params = self.params_for(row_type)
            params["z"] = 0
            with self.subTest(row_type=row_type, extra="z"):
                self.assert_invalid(self.rows_for(row_type, params), "geometry")

    def test_parameter_keys_must_be_strings_and_case_insensitive_duplicates_are_rejected(self):
        self.assert_invalid(
            [["MoveTo", {1: 0, "y": 0}]],
            "geometry",
        )
        self.assert_invalid(
            [["MoveTo", {"x": 0, "X": 0, "y": 0}]],
            "geometry",
        )

    def test_rejects_nonfinite_non_numeric_required_cells_and_bool_values(self):
        invalid_values = (True, False, None, "1", math.nan, math.inf, -math.inf)
        for row_type, cells in self.SCHEMA:
            for cell in cells:
                if row_type == "PolylineTo" and cell == "A":
                    continue
                for value in invalid_values:
                    params = self.params_for(row_type)
                    params[cell.lower()] = value
                    with self.subTest(row_type=row_type, cell=cell, value=repr(value)):
                        self.assert_invalid(self.rows_for(row_type, params), "geometry")

    def test_absolute_and_relative_xy_bounds_are_inclusive_but_reject_out_of_bounds(self):
        for row_type in self.ABSOLUTE_TYPES:
            for x, y in ((0, 0), (2.0, 3.0)):
                params = self.params_for(row_type)
                params.update(x=x, y=y)
                with self.subTest(row_type=row_type, boundary=(x, y)):
                    self.assertEqual(vsdx_gen.validate_input(self.data_for(self.rows_for(row_type, params))), [])
            for field, value in (("x", -0.001), ("x", 2.001), ("y", -0.001), ("y", 3.001)):
                params = self.params_for(row_type)
                params[field] = value
                with self.subTest(row_type=row_type, field=field, value=value):
                    self.assert_invalid(self.rows_for(row_type, params), "geometry")

        for row_type in self.RELATIVE_TYPES:
            for x, y in ((0, 0), (1.0, 1.0)):
                params = self.params_for(row_type)
                params.update(x=x, y=y)
                with self.subTest(row_type=row_type, boundary=(x, y)):
                    self.assertEqual(vsdx_gen.validate_input(self.data_for(self.rows_for(row_type, params))), [])
            for field, value in (("x", -0.001), ("x", 1.001), ("y", -0.001), ("y", 1.001)):
                params = self.params_for(row_type)
                params[field] = value
                with self.subTest(row_type=row_type, field=field, value=value):
                    self.assert_invalid(self.rows_for(row_type, params), "geometry")

    def test_control_cells_have_no_bounds_but_must_remain_finite(self):
        for row_type, cells in self.SCHEMA:
            params = self.params_for(row_type)
            for cell in cells:
                if cell in ("X", "Y"):
                    continue
                if row_type == "PolylineTo" and cell == "A":
                    params["a"] = "1e300 -1e300"
                else:
                    params[cell.lower()] = 1e300
            with self.subTest(row_type=row_type):
                self.assertEqual(
                    vsdx_gen.validate_input(self.data_for(self.rows_for(row_type, params))),
                    [],
                )

    def test_polyline_a_requires_finite_numeric_tokens_and_canonicalizes_whitespace(self):
        invalid_values = (
            "", "   ", "\t\n", ",", "1,2", "1 2,", "1 junk", "nan",
            "NaN", "inf", "Infinity", "-inf", "1e309", "1e309 2",
            True, False, None, 1, [], {},
        )
        for value in invalid_values:
            params = self.params_for("PolylineTo")
            params["a"] = value
            with self.subTest(value=repr(value)):
                self.assert_invalid(self.rows_for("PolylineTo", params), "geometry")

        raw_value = " \t+01.20  \n -0.5e+2\t3. "
        expected_value = "+01.20 -0.5e+2 3."
        params = self.params_for("PolylineTo")
        params["a"] = raw_value
        data = self.data_for(self.rows_for("PolylineTo", params))
        normalized = vsdx_gen.normalize_input(data)
        self.assertEqual(normalized["nodes"][0]["geometry"][1][1]["A"], expected_value)

        with tempfile.TemporaryDirectory(prefix="geometry-contract-", dir=Path(__file__).parent) as temp_dir:
            output = Path(temp_dir) / "polyline.vsdx"
            vsdx_gen.generate(data, output)
            with zipfile.ZipFile(output) as package:
                page = ET.fromstring(package.read("visio/pages/page1.xml"))
            rows = page.findall(".//" + vsdx_gen.V("Section") + "/" + vsdx_gen.V("Row"))
            polyline = next(row for row in rows if row.get("T") == "PolylineTo")
            cells = {cell.get("N"): cell.get("V") for cell in polyline.findall(vsdx_gen.V("Cell"))}
            self.assertEqual(cells["A"], expected_value)

    def test_generation_emits_only_canonical_uppercase_cells_for_all_rows(self):
        data = self.data_for(self.all_rows())
        with tempfile.TemporaryDirectory(prefix="geometry-contract-", dir=Path(__file__).parent) as temp_dir:
            output = Path(temp_dir) / "all-rows.vsdx"
            vsdx_gen.generate(data, output)
            with zipfile.ZipFile(output) as package:
                page = ET.fromstring(package.read("visio/pages/page1.xml"))
        section = page.find(".//" + vsdx_gen.V("Section") + "[@N='Geometry']")
        self.assertIsNotNone(section)
        for row in section.findall(vsdx_gen.V("Row")):
            row_type = row.get("T")
            names = tuple(cell.get("N") for cell in row.findall(vsdx_gen.V("Cell")))
            self.assertEqual(names, dict(self.SCHEMA)[row_type])
            self.assertTrue(all(name in {"X", "Y", "A", "B", "C", "D", "E"} for name in names))

    def test_normalization_does_not_mutate_or_alias_custom_geometry(self):
        geometry = self.all_rows()
        geometry[0][1]["x"] = 0
        geometry[7][1]["a"] = " 1\t2 "
        data = self.data_for(geometry)
        before = copy.deepcopy(data)
        normalized = vsdx_gen.normalize_input(data)
        self.assertEqual(data, before)
        self.assertIsNot(normalized, data)
        self.assertIsNot(normalized["nodes"], data["nodes"])
        self.assertIsNot(normalized["nodes"][0]["geometry"], data["nodes"][0]["geometry"])
        self.assertIsNot(normalized["nodes"][0]["geometry"][0][1], data["nodes"][0]["geometry"][0][1])
        self.assertEqual(normalized["nodes"][0]["geometry"][7][1]["A"], "1 2")


if __name__ == "__main__":
    unittest.main()
