import importlib.util
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest


SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = SKILL_ROOT / "scripts" / "verify_layout.py"
FIXTURES = Path(__file__).parent / "fixtures"
SPEC = importlib.util.spec_from_file_location("verify_layout_under_test", SCRIPT_PATH)
verify_layout = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify_layout
SPEC.loader.exec_module(verify_layout)


def vertex(node_id, x, y, width, height, label=None, style=""):
    value = "" if label is None else label
    return (
        '<mxCell parent="1" vertex="1" value="%s" id="%s" style="%s">'
        '<mxGeometry height="%s" x="%s" as="geometry" width="%s" y="%s"/>'
        '</mxCell>'
    ) % (value, node_id, style, height, x, width, y)


def edge(
    edge_id,
    source,
    target,
    points,
    label="",
    offset=None,
    style="",
    label_position=None,
):
    source_attr = "" if source is None else ' source="%s"' % source
    target_attr = "" if target is None else ' target="%s"' % target
    source_point = points[0]
    target_point = points[-1]
    route_points = points[1:-1]
    route_xml = ""
    if route_points:
        route_xml = '<Array as="points">%s</Array>' % "".join(
            '<mxPoint y="%s" x="%s"/>' % (y, x) for x, y in route_points
        )
    offset_xml = ""
    if offset is not None:
        offset_xml = '<mxPoint y="%s" as="offset" x="%s"/>' % (
            offset[1], offset[0]
        )
    label_position_xml = ""
    if label_position is not None:
        label_position_xml = ' x="%s" y="%s"' % label_position
    return (
        '<mxCell value="%s" edge="1" id="%s" parent="1" style="%s"%s%s>'
        '<mxGeometry as="geometry" relative="1"%s>'
        '<mxPoint y="%s" x="%s" as="sourcePoint"/>'
        '%s'
        '<mxPoint x="%s" as="targetPoint" y="%s"/>'
        '%s'
        '</mxGeometry></mxCell>'
    ) % (
        label,
        edge_id,
        style,
        source_attr,
        target_attr,
        label_position_xml,
        source_point[1],
        source_point[0],
        route_xml,
        target_point[0],
        target_point[1],
        offset_xml,
    )


def graph(*cells, page_width="863.6", page_height="1117.6"):
    page_attributes = ""
    if page_width is not None:
        page_attributes += ' pageWidth="%s"' % page_width
    if page_height is not None:
        page_attributes += ' pageHeight="%s"' % page_height
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<mxfile host="unit-test"><diagram name="Page-1">'
        '<mxGraphModel%s><root><mxCell id="0"/><mxCell parent="0" id="1"/>'
        '%s</root></mxGraphModel></diagram></mxfile>'
    ) % (page_attributes, "".join(cells))


class VerifyLayoutTests(unittest.TestCase):
    def analyze(self, path, **kwargs):
        function = getattr(verify_layout, "analyze_layout", None)
        self.assertTrue(callable(function), "analyze_layout(path, ...) API is missing")
        return function(path, **kwargs)

    def analyze_xml(self, xml, **kwargs):
        with tempfile.TemporaryDirectory(prefix="verify-layout-", dir=Path(__file__).parent) as temp_dir:
            path = Path(temp_dir) / "diagram.drawio"
            path.write_text(xml, encoding="utf-8")
            return self.analyze(path, **kwargs)

    def run_main(self, argv):
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            try:
                code = verify_layout.main(argv)
            except TypeError as exc:
                self.fail("main(argv) API is missing: %s" % exc)
            except SystemExit as exc:
                self.fail("main(argv) must return an exit code, raised %r" % (exc.code,))
        return code, output.getvalue(), error.getvalue()

    def assert_problem(self, report, fragment):
        self.assertTrue(
            any(fragment in problem for problem in report.problems),
            "expected problem containing %r, got %r" % (fragment, report.problems),
        )

    def test_structured_parser_handles_wrappers_direct_cells_attribute_order_and_numbers(self):
        report = self.analyze(FIXTURES / "valid.drawio", expect_nodes=2, expect_edges=1)

        self.assertEqual(report.node_count, 2)
        self.assertEqual(report.edge_count, 1)
        self.assertEqual([node.id for node in report.nodes], ["A", "B"])
        self.assertEqual([node.label for node in report.nodes], ["Alpha & One", "Beta"])
        self.assertEqual(report.nodes[0].box, (0.0, 101.6, 101.6, 203.2))
        self.assertEqual(
            report.edges[0].points,
            ((101.6, 152.4), (101.6, 50.8), (101.6, 152.4), (203.2, 152.4)),
        )
        self.assertEqual(report.problems, ())

    def test_structured_parser_uses_wrapper_ids_when_cells_omit_them(self):
        xml = graph(
            '<UserObject id="A" label="Alpha"><mxCell parent="1" vertex="1">'
            '<mxGeometry x="0" y="0" width="10" height="10" as="geometry"/>'
            '</mxCell></UserObject>',
            '<UserObject id="B" label="Beta"><mxCell parent="1" vertex="1">'
            '<mxGeometry x="20" y="0" width="10" height="10" as="geometry"/>'
            '</mxCell></UserObject>',
            '<UserObject id="E"><mxCell parent="1" edge="1" source="A" target="B">'
            '<mxGeometry relative="1" as="geometry">'
            '<mxPoint x="10" y="5" as="sourcePoint"/>'
            '<mxPoint x="20" y="5" as="targetPoint"/>'
            '</mxGeometry></mxCell></UserObject>',
        )

        report = self.analyze_xml(xml, expect_nodes=2, expect_edges=1)

        self.assertEqual([node.id for node in report.nodes], ["A", "B"])
        self.assertEqual([edge.id for edge in report.edges], ["E"])
        self.assertEqual(report.problems, ())

    def test_one_node_and_zero_edges_is_valid(self):
        report = self.analyze_xml(
            graph(vertex("A", 0, 0, 10, 10, "Only")),
            expect_nodes=1,
            expect_edges=0,
        )

        self.assertEqual(report.node_count, 1)
        self.assertEqual(report.edge_count, 0)
        self.assertEqual(report.problems, ())

    def test_invalid_and_non_drawio_xml_are_input_errors(self):
        cases = (
            ("broken.drawio", "<mxGraphModel><root>"),
            ("other.xml", "<document><root/></document>"),
            (
                "embedded-lookalike.xml",
                '<document><mxGraphModel pageHeight="1117.6"><root>'
                '<mxCell id="0"/><mxCell id="1" parent="0"/>'
                + vertex("A", 0, 0, 10, 10)
                + "</root></mxGraphModel></document>",
            ),
            (
                "foreign-namespace.xml",
                '<foreign:mxGraphModel xmlns:foreign="urn:not-drawio" '
                'pageHeight="1117.6"><foreign:root>'
                '<foreign:mxCell id="0"/><foreign:mxCell id="1" parent="0"/>'
                '<foreign:mxCell id="A" parent="1" vertex="1">'
                '<foreign:mxGeometry x="0" y="0" width="10" height="10" '
                'as="geometry"/></foreign:mxCell>'
                "</foreign:root></foreign:mxGraphModel>",
            ),
        )
        with tempfile.TemporaryDirectory(prefix="verify-layout-", dir=Path(__file__).parent) as temp_dir:
            for name, xml in cases:
                with self.subTest(name=name):
                    path = Path(temp_dir) / name
                    path.write_text(xml, encoding="utf-8")
                    code, output, error = self.run_main([str(path)])
                    self.assertEqual(code, 2)
                    self.assertIn("错误", error)
                    self.assertNotIn("Traceback", output + error)

    def test_zero_nodes_is_a_layout_failure(self):
        xml = graph(page_height="1117.6")
        report = self.analyze_xml(xml)
        self.assert_problem(report, "零节点")

        with tempfile.TemporaryDirectory(prefix="verify-layout-", dir=Path(__file__).parent) as temp_dir:
            path = Path(temp_dir) / "empty.drawio"
            path.write_text(xml, encoding="utf-8")
            code, output, error = self.run_main([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("零节点", output)
        self.assertEqual(error, "")

    def test_expected_counts_are_exact_layout_checks(self):
        report = self.analyze(
            FIXTURES / "valid.drawio",
            expect_nodes=3,
            expect_edges=2,
        )

        self.assert_problem(report, "期望节点数 3，实际 2")
        self.assert_problem(report, "期望边数 2，实际 1")

    def test_edge_references_must_be_present_and_resolve_to_nodes(self):
        unbound = self.analyze(FIXTURES / "unbound-edge.drawio")
        self.assert_problem(unbound, "target 未绑定")

        dangling = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 20, 20),
                vertex("B", 100, 0, 20, 20),
                edge("E", "A", "missing", [(20, 10), (100, 10)]),
            )
        )
        self.assert_problem(dangling, "target 引用不存在")

        both_missing = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 20, 20),
                edge("E", None, None, [(20, 10), (100, 10)]),
            )
        )
        self.assert_problem(both_missing, "source 未绑定")
        self.assert_problem(both_missing, "target 未绑定")

    def test_missing_page_height_uses_and_reports_the_documented_default(self):
        report = self.analyze_xml(
            graph(vertex("A", 0, 101.6, 101.6, 101.6, "Default page"), page_height=None)
        )

        self.assertTrue(report.page_height_defaulted)
        self.assertEqual(report.page_height_px, 1117.6)
        self.assertEqual(report.summary[0].center_inches, (0.5, 9.5))

        with tempfile.TemporaryDirectory(prefix="verify-layout-", dir=Path(__file__).parent) as temp_dir:
            path = Path(temp_dir) / "default-height.drawio"
            path.write_text(
                graph(vertex("A", 0, 101.6, 101.6, 101.6), page_height=None),
                encoding="utf-8",
            )
            code, output, _ = self.run_main([str(path)])
        self.assertEqual(code, 0)
        self.assertIn("pageHeight 缺失", output)
        self.assertIn("1117.6px (11.00in)", output)

    def test_page_dimensions_parse_explicit_values_and_documented_defaults(self):
        explicit = self.analyze_xml(
            graph(vertex("A", 0, 0, 10, 10), page_width="1422.4", page_height="2336.8")
        )
        self.assertEqual(explicit.page_width_px, 1422.4)
        self.assertEqual(explicit.page_height_px, 2336.8)
        self.assertFalse(explicit.page_width_defaulted)
        self.assertFalse(explicit.page_height_defaulted)

        defaulted = self.analyze_xml(
            graph(vertex("A", 0, 0, 10, 10), page_width=None, page_height=None)
        )
        self.assertEqual(defaulted.page_width_px, 8.5 * verify_layout.PX)
        self.assertEqual(defaulted.page_height_px, 11.0 * verify_layout.PX)
        self.assertTrue(defaulted.page_width_defaulted)
        self.assertTrue(defaulted.page_height_defaulted)

    def test_expected_page_dimensions_are_paired_positive_and_strict_by_default(self):
        matching = self.analyze_xml(
            graph(vertex("A", 10, 10, 10, 10)),
            expected_page_width_in=8.5,
            expected_page_height_in=11.0,
        )
        self.assertAlmostEqual(matching.expected_page_width_px, 863.6)
        self.assertAlmostEqual(matching.expected_page_height_px, 1117.6)
        self.assertFalse(any("页面尺寸不匹配" in item for item in matching.problems))

        mismatch = self.analyze_xml(
            graph(vertex("A", 10, 10, 10, 10)),
            expected_page_width_in=14.0,
            expected_page_height_in=23.0,
        )
        self.assert_problem(mismatch, "页面尺寸不匹配")

        for kwargs in (
            {"expected_page_width_in": 8.5},
            {"expected_page_height_in": 11.0},
            {"expected_page_width_in": 0, "expected_page_height_in": 11.0},
            {"expected_page_width_in": 8.5, "expected_page_height_in": float("inf")},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(verify_layout.LayoutInputError):
                    self.analyze_xml(graph(vertex("A", 10, 10, 10, 10)), **kwargs)

    def test_tiled_paper_allows_dimension_mismatch_and_uses_source_bounds(self):
        xml = graph(vertex("A", 1200, 1800, 100, 100, "Lower lane"))
        strict = self.analyze_xml(
            xml,
            expected_page_width_in=14.0,
            expected_page_height_in=23.0,
        )
        self.assert_problem(strict, "页面尺寸不匹配")
        self.assert_problem(strict, "节点 Lower lane 超出页面边界")

        tiled = self.analyze_xml(
            xml,
            expected_page_width_in=14.0,
            expected_page_height_in=23.0,
            allow_tiled_paper=True,
        )
        self.assertEqual(tiled.problems, ())
        self.assertTrue(tiled.tiled_paper)
        self.assertTrue(tiled.tiled_size_mismatch)
        self.assertEqual(tiled.bounds_width_px, 14.0 * verify_layout.PX)
        self.assertEqual(tiled.bounds_height_px, 23.0 * verify_layout.PX)
        self.assertEqual(tiled.summary[0].center_inches, (12.303149606299, 4.791338582677))

        with self.assertRaises(verify_layout.LayoutInputError):
            self.analyze_xml(xml, allow_tiled_paper=True)

    def test_page_bounds_cover_rotated_nodes_edge_points_and_label_centers(self):
        rotated = self.analyze_xml(
            graph(vertex("A", 0, 0, 10, 10, "Rotated", style="rotation=45;"))
        )
        self.assert_problem(rotated, "节点 Rotated 超出页面边界")

        edge_point = self.analyze_xml(
            graph(
                vertex("A", 10, 10, 20, 20),
                vertex("B", 100, 10, 20, 20),
                edge("E", "A", "B", [(30, 20), (900, 20), (100, 20)]),
            )
        )
        self.assert_problem(edge_point, "边 E 的路径点超出页面边界")

        label = self.analyze_xml(
            graph(
                vertex("A", 10, 10, 20, 20),
                vertex("B", 100, 10, 20, 20),
                edge(
                    "E", "A", "B", [(30, 20), (100, 20)],
                    label="Outside", offset=(800, 0),
                ),
            )
        )
        self.assert_problem(label, '标签 "Outside" 中心超出页面边界')

        epsilon_ok = self.analyze_xml(
            graph(vertex("A", -verify_layout.EPSILON_PX, 0, 10, 10, "Epsilon"))
        )
        self.assertFalse(any("超出页面边界" in item for item in epsilon_ok.problems))
        epsilon_bad = self.analyze_xml(
            graph(vertex("A", -verify_layout.EPSILON_PX - 0.001, 0, 10, 10, "Beyond"))
        )
        self.assert_problem(epsilon_bad, "节点 Beyond 超出页面边界")

    def test_coordinate_summary_uses_model_page_height_for_11_and_16_inches(self):
        for page_inches, expected_y in ((11.0, 9.5), (16.0, 14.5)):
            with self.subTest(page_inches=page_inches):
                report = self.analyze_xml(
                    graph(
                        vertex("A", 101.6, 101.6, 203.2, 101.6, "Summary"),
                        page_height=page_inches * 101.6,
                    )
                )
                summary = report.summary[0]
                self.assertFalse(report.page_height_defaulted)
                self.assertEqual(summary.center_inches, (2.0, expected_y))
                self.assertEqual(summary.size_inches, (2.0, 1.0))

    def test_negative_coordinate_overlap_is_detected(self):
        report = self.analyze(FIXTURES / "negative-overlap.drawio")
        self.assert_problem(report, "节点重叠")

    def test_node_overlap_requires_more_than_half_a_pixel_on_both_axes(self):
        cases = (
            (10.0, False, "boundary contact"),
            (9.5, False, "exact epsilon"),
            (9.4999, True, "over epsilon"),
        )
        for second_x, expected_problem, name in cases:
            with self.subTest(name=name):
                report = self.analyze_xml(
                    graph(
                        vertex("A", 0, 0, 10, 10),
                        vertex("B", second_x, 0, 10, 10),
                    )
                )
                has_overlap = any("节点重叠" in problem for problem in report.problems)
                self.assertEqual(has_overlap, expected_problem)

    def test_rotated_node_overlap_uses_visual_polygons(self):
        report = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 100, 20, "Rotated", style="rotation=45;"),
                vertex("B", 72.5, 42.5, 5, 5, "Inside rotated outline"),
            )
        )

        self.assert_problem(report, "节点重叠: Rotated <-> Inside rotated outline")

    def test_rotated_node_overlap_preserves_half_pixel_epsilon(self):
        cosine = 2 ** -0.5
        cases = (
            (10.0, False, "boundary contact"),
            (9.5, False, "exact epsilon"),
            (9.4999, True, "over epsilon"),
        )
        for distance, expected_problem, name in cases:
            with self.subTest(name=name):
                offset = distance * cosine
                report = self.analyze_xml(
                    graph(
                        vertex("A", 0, 0, 10, 10, style="rotation=45;"),
                        vertex(
                            "B", offset, offset, 10, 10,
                            style="rotation=45;",
                        ),
                    )
                )
                has_overlap = any("节点重叠" in problem for problem in report.problems)
                self.assertEqual(has_overlap, expected_problem)

    def test_edge_crossing_a_third_node_is_detected_but_boundary_contact_is_allowed(self):
        cells = (
            vertex("A", 0, 0, 20, 20),
            vertex("B", 100, 0, 20, 20),
            vertex("C", 50, 0, 20, 20, "Middle"),
        )
        crossing = self.analyze_xml(
            graph(*cells, edge("E", "A", "B", [(20, 10), (100, 10)]))
        )
        self.assert_problem(crossing, "穿过节点 Middle")

        boundary = self.analyze_xml(
            graph(*cells, edge("E", "A", "B", [(20, 0), (100, 0)]))
        )
        self.assertFalse(any("穿过节点 Middle" in p for p in boundary.problems))

    def test_style_top_anchors_are_used_when_checking_third_node_crossings(self):
        report = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 20, 20),
                vertex("B", 100, 0, 20, 20),
                vertex("C", 50, -5, 20, 10, "Top crossing"),
                edge(
                    "E",
                    "A",
                    "B",
                    [(20, 10), (100, 10)],
                    style="exitX=0.5;exitY=0;entryX=0.5;entryY=0;",
                ),
            )
        )

        self.assertEqual(report.edges[0].points, ((10.0, 0.0), (110.0, 0.0)))
        self.assert_problem(report, "穿过节点 Top crossing")

    def test_edge_crossing_rotated_third_node_uses_visual_polygon(self):
        report = self.analyze_xml(
            graph(
                vertex("A", 0, 40, 10, 10),
                vertex("B", 160, 40, 10, 10),
                vertex(
                    "C", 40, 0, 100, 20, "Rotated crossing",
                    style="rotation=45;",
                ),
                edge("E", "A", "B", [(10, 45), (160, 45)]),
            )
        )

        self.assert_problem(report, "穿过节点 Rotated crossing")

    def test_style_anchor_offsets_are_applied_to_both_terminals(self):
        report = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 20, 20),
                vertex("B", 100, 0, 20, 20),
                edge(
                    "E",
                    "A",
                    "B",
                    [(20, 10), (100, 10)],
                    style=(
                        "exitX=0.5;exitY=0;exitDx=3;exitDy=-2;"
                        "entryX=0.5;entryY=1;entryDx=-4;entryDy=5;"
                    ),
                ),
            )
        )

        self.assertEqual(report.edges[0].points, ((13.0, -2.0), (106.0, 25.0)))

    def test_style_anchor_and_offsets_rotate_with_the_terminal_node(self):
        report = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 20, 10, style="rotation=90;"),
                vertex("B", 100, 0, 20, 10),
                edge(
                    "E",
                    "A",
                    "B",
                    [(20, 5), (100, 5)],
                    style="exitX=1;exitY=0.5;exitDx=3;exitDy=-2;",
                ),
            )
        )

        self.assertEqual(report.edges[0].points[0], (12.0, 18.0))

    def test_style_target_anchor_exposes_last_segment_entering_target(self):
        route_only_edge = (
            '<mxCell edge="1" id="E" parent="1" source="A" target="B" '
            'style="exitX=0.5;exitY=1;exitDx=0;exitDy=0;'
            'entryX=0.5;entryY=0;entryDx=0;entryDy=0;">'
            '<mxGeometry as="geometry" relative="1">'
            '<Array as="points">'
            '<mxPoint x="1244" y="1610"/>'
            '<mxPoint x="1244" y="187.6"/>'
            '</Array>'
            '</mxGeometry></mxCell>'
        )
        report = self.analyze_xml(
            graph(
                vertex("A", 1041, 1539, 152, 71, "Source"),
                vertex("B", 1295, 116, 152, 71, "Target"),
                route_only_edge,
            )
        )

        self.assertEqual(
            report.edges[0].points,
            (
                (1117.0, 1610.0),
                (1244.0, 1610.0),
                (1244.0, 187.6),
                (1371.0, 116.0),
            ),
        )
        self.assert_problem(report, "末段穿入 target 节点 Target")

    def test_rotated_source_uses_and_consumes_first_route_point_as_terminal(self):
        route_only_edge = (
            '<mxCell edge="1" id="E" parent="1" source="A" target="B">'
            '<mxGeometry as="geometry" relative="1">'
            '<Array as="points">'
            '<mxPoint x="24.1421356" y="10"/>'
            '<mxPoint x="50" y="30"/>'
            '</Array>'
            '<mxPoint x="100" y="10" as="targetPoint"/>'
            '</mxGeometry></mxCell>'
        )
        report = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 20, 20, style="rotation=45;"),
                vertex("B", 100, 0, 20, 20),
                route_only_edge,
            )
        )

        self.assertEqual(
            report.edges[0].points,
            ((24.1421356, 10.0), (50.0, 30.0), (100.0, 10.0)),
        )

    def test_invalid_or_incomplete_terminal_styles_are_input_errors(self):
        cases = (
            (
                "invalid rotation",
                graph(vertex("A", 0, 0, 20, 20, style="rotation=sideways;")),
                "节点 A.*rotation.*有效数字",
            ),
            (
                "incomplete exit",
                graph(
                    vertex("A", 0, 0, 20, 20),
                    vertex("B", 100, 0, 20, 20),
                    edge("E", "A", "B", [(20, 10), (100, 10)], style="exitX=0.5;"),
                ),
                "边 E.*exit.*不完整",
            ),
            (
                "invalid entry",
                graph(
                    vertex("A", 0, 0, 20, 20),
                    vertex("B", 100, 0, 20, 20),
                    edge(
                        "E", "A", "B", [(20, 10), (100, 10)],
                        style="entryX=nan;entryY=0.5;",
                    ),
                ),
                "边 E.*entryX.*有限数字",
            ),
            (
                "offset without anchor",
                graph(
                    vertex("A", 0, 0, 20, 20),
                    vertex("B", 100, 0, 20, 20),
                    edge("E", "A", "B", [(20, 10), (100, 10)], style="exitDx=2;"),
                ),
                "边 E.*exit.*不完整",
            ),
        )
        for name, xml, message in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(verify_layout.LayoutInputError, message):
                    self.analyze_xml(xml)

    def test_first_source_and_last_target_contact_are_allowed(self):
        report = self.analyze_xml(
            graph(
                vertex("A", 0, 0, 20, 20, "Source"),
                vertex("B", 100, 0, 20, 20, "Target"),
                edge("E", "A", "B", [(20, 10), (50, 30), (100, 10)]),
            )
        )

        self.assertEqual(report.problems, ())

    def test_later_source_reentry_and_early_target_entry_are_detected(self):
        nodes = (
            vertex("A", 0, 0, 20, 20, "Source"),
            vertex("B", 100, 0, 20, 20, "Target"),
        )
        source_reentry = self.analyze_xml(
            graph(
                *nodes,
                edge("E", "A", "B", [(20, 10), (40, 30), (10, 10), (40, 30), (100, 10)]),
            )
        )
        self.assert_problem(source_reentry, "重新进入 source 节点 Source")

        target_early = self.analyze_xml(
            graph(
                *nodes,
                edge("E", "A", "B", [(20, 10), (110, 10), (80, 30), (100, 10)]),
            )
        )
        self.assert_problem(target_early, "提前进入 target 节点 Target")

    def test_polyline_label_does_not_use_endpoint_midpoint(self):
        report = self.analyze(FIXTURES / "label-overlap.drawio")

        self.assertEqual(
            verify_layout._edge_label_center(report.edges[0]),
            (60.0, 50.0),
        )
        self.assertFalse(any('标签 "approval"' in problem for problem in report.problems))

    def test_default_edge_label_uses_polyline_arclength_midpoint(self):
        report = self.analyze_xml(
            graph(
                vertex("A", -20, -5, 20, 10),
                vertex("B", 100, -5, 20, 10),
                vertex("C", 49, 99, 2, 2, "Arc midpoint"),
                edge(
                    "E",
                    "A",
                    "B",
                    [(0, 0), (0, 100), (100, 100), (100, 0)],
                    label="U label",
                ),
            )
        )

        self.assertEqual(
            verify_layout._edge_label_center(report.edges[0]),
            (50.0, 100.0),
        )
        self.assert_problem(report, '标签 "U label" 落在节点 Arc midpoint 内')

    def test_relative_label_position_and_offset_follow_drawio_geometry(self):
        report = self.analyze_xml(
            graph(
                vertex("A", -20, -5, 20, 10),
                vertex("B", 100, -5, 20, 10),
                vertex("C", 12, 78, 2, 2, "Moved label"),
                edge(
                    "E",
                    "A",
                    "B",
                    [(0, 0), (0, 100), (100, 100), (100, 0)],
                    label="positioned",
                    label_position=(-0.5, 10),
                    offset=(3, 4),
                ),
            )
        )

        self.assertEqual(
            verify_layout._edge_label_center(report.edges[0]),
            (13.0, 79.0),
        )
        self.assert_problem(report, '标签 "positioned" 落在节点 Moved label 内')

    def test_invalid_relative_label_coordinates_are_input_errors(self):
        cases = (
            (("sideways", 0), "x.*有效数字"),
            (("nan", 0), "x.*有限数字"),
            ((0, "inf"), "y.*有限数字"),
            (("", 0), "缺少数值属性 x"),
        )
        for position, message in cases:
            with self.subTest(position=position):
                xml = graph(
                    vertex("A", 0, 0, 10, 10),
                    vertex("B", 100, 0, 10, 10),
                    edge(
                        "E", "A", "B", [(10, 5), (100, 5)],
                        label="invalid", label_position=position,
                    ),
                )
                with self.assertRaisesRegex(
                    verify_layout.LayoutInputError,
                    "边 E.*mxGeometry.*" + message,
                ):
                    self.analyze_xml(xml)

    def test_label_offset_and_half_pixel_shrunken_boundary_are_applied(self):
        cells = (
            vertex("A", 0, 40, 20, 20),
            vertex("B", 100, 40, 20, 20),
            vertex("C", 50, 0, 20, 20, "Host"),
        )
        no_offset = self.analyze_xml(
            graph(*cells, edge("E", "A", "B", [(20, 50), (100, 50)], label="label"))
        )
        self.assertFalse(any("标签" in problem for problem in no_offset.problems))

        inside = self.analyze_xml(
            graph(
                *cells,
                edge("E", "A", "B", [(20, 50), (100, 50)], label="label", offset=(0, -40)),
            )
        )
        self.assert_problem(inside, '标签 "label" 落在节点 Host 内')

        exact_boundary = self.analyze_xml(
            graph(
                *cells,
                edge(
                    "E", "A", "B", [(20, 50), (100, 50)],
                    label="label", offset=(-9.5, -40),
                ),
            )
        )
        self.assertFalse(any("标签" in problem for problem in exact_boundary.problems))

        beyond_boundary = self.analyze_xml(
            graph(
                *cells,
                edge(
                    "E", "A", "B", [(20, 50), (100, 50)],
                    label="label", offset=(-9.4999, -40),
                ),
            )
        )
        self.assert_problem(beyond_boundary, '标签 "label" 落在节点 Host 内')

    def test_cli_exit_codes_and_portable_diagnostics(self):
        valid = FIXTURES / "valid.drawio"
        code, output, error = self.run_main(
            [str(valid), "--expect-nodes", "2", "--expect-edges", "1"]
        )
        self.assertEqual(code, 0)
        self.assertIn("节点数: 2", output)
        self.assertIn("边数: 1", output)
        self.assertIn("布局检查全部通过", output)
        self.assertEqual(error, "")

        code, output, error = self.run_main([
            str(valid),
            "--expect-page-width-in", "14",
            "--expect-page-height-in", "23",
        ])
        self.assertEqual(code, 1)
        self.assertIn("页面尺寸不匹配", output)
        self.assertEqual(error, "")

        code, output, error = self.run_main([
            str(valid),
            "--expect-page-width-in", "14",
            "--expect-page-height-in", "23",
            "--allow-tiled-paper",
        ])
        self.assertEqual(code, 0)
        self.assertIn("平铺纸张模式", output)
        self.assertEqual(error, "")

        code, output, error = self.run_main([
            str(valid), "--expect-page-width-in", "8.5",
        ])
        self.assertEqual(code, 2)
        self.assertIn("错误", error)

        code, output, error = self.run_main(
            [str(valid), "--expect-nodes", "3", "--expect-edges", "1"]
        )
        self.assertEqual(code, 1)
        self.assertIn("期望节点数 3，实际 2", output)
        self.assertEqual(error, "")

        missing = Path(tempfile.gettempdir()) / "verify-layout-file-does-not-exist.drawio"
        code, output, error = self.run_main([str(missing)])
        self.assertEqual(code, 2)
        self.assertIn("错误", error)
        self.assertNotIn("Traceback", output + error)

        code, output, error = self.run_main([str(valid), "--expect-nodes", "-1"])
        self.assertEqual(code, 2)
        self.assertIn("错误", error)
        self.assertNotIn("Traceback", output + error)


if __name__ == "__main__":
    unittest.main()
