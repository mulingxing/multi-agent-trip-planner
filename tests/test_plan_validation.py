"""用模拟MCP结果检查景点核验，不读取.env或调用外部API。"""

import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models.schemas import Attraction, DayPlan, Location, TripPlan, TripRequest
from app.services.plan_validation import verify_attraction_locations


def wrapped(data):
    return "工具执行结果:\n" + json.dumps(data, ensure_ascii=False)


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.request = TripRequest(
            city="杭州", start_date="2026-10-12", end_date="2026-10-12",
            travel_days=1, transportation="公共交通", accommodation="酒店",
        )
        self.search = {"pois": [{
            "id": "POI-WEST-LAKE", "name": "杭州西湖风景名胜区",
            "address": "西湖区", "typecode": "110202",
        }]}
        self.detail = {
            "id": "POI-WEST-LAKE", "name": "杭州西湖风景名胜区",
            "city": "杭州市", "address": "西湖区龙井路1号",
            "location": "120.121358,30.222692",
        }

    def plan(self, *names):
        return TripPlan(
            city="杭州", start_date=self.request.start_date, end_date=self.request.end_date,
            overall_suggestions="测试", days=[DayPlan(
                date=self.request.start_date, day_index=0, description="测试",
                transportation="公共交通", accommodation="酒店",
                attractions=[Attraction(
                    name=name, address="模型猜测的地址",
                    location=Location(longitude=116.4, latitude=39.9),
                    visit_duration=120, description="测试景点",
                ) for name in names],
            )],
        )

    def test_overwrites_fake_coordinates_and_caches_repeated_names(self):
        plan = self.plan("西湖", "西湖")
        tool = Mock()
        tool.run.side_effect = [wrapped(self.search), wrapped(self.detail)]

        verify_attraction_locations(plan, self.request, tool)

        self.assertEqual(tool.run.call_count, 2)
        self.assertEqual(tool.run.call_args_list[0].args[0]["arguments"], {
            "keywords": "西湖", "city": "杭州", "citylimit": "true",
        })
        for attraction in plan.days[0].attractions:
            self.assertEqual(attraction.name, self.detail["name"])
            self.assertEqual(attraction.address, self.detail["address"])
            self.assertEqual(attraction.poi_id, self.detail["id"])
            self.assertAlmostEqual(attraction.location.longitude, 120.121358)
            self.assertAlmostEqual(attraction.location.latitude, 30.222692)

    def test_does_not_substitute_an_unrelated_first_search_result(self):
        plan = self.plan("灵隐寺")
        tool = Mock()
        tool.run.return_value = wrapped(self.search)
        with self.assertRaisesRegex(ValueError, "名称匹配"):
            verify_attraction_locations(plan, self.request, tool)
        self.assertEqual(tool.run.call_count, 1)
        self.assertEqual(plan.days[0].attractions[0].location.longitude, 116.4)

    def test_rejects_matching_name_from_another_city(self):
        self.detail["city"] = "北京市"
        tool = Mock()
        tool.run.side_effect = [wrapped(self.search), wrapped(self.detail)]
        with self.assertRaisesRegex(ValueError, "跨市"):
            verify_attraction_locations(self.plan("西湖"), self.request, tool)

    def test_rejects_missing_or_invalid_coordinates(self):
        for coordinates in (None, "", "nan,30", "120,100", "0,0", "120,30,1"):
            with self.subTest(coordinates=coordinates):
                self.detail["location"] = coordinates
                tool = Mock()
                tool.run.side_effect = [wrapped(self.search), wrapped(self.detail)]
                with self.assertRaisesRegex(ValueError, "有效经纬度"):
                    verify_attraction_locations(self.plan("西湖"), self.request, tool)

    def test_rejects_placeholder_without_making_a_request(self):
        tool = Mock()
        with self.assertRaisesRegex(ValueError, "占位景点"):
            verify_attraction_locations(self.plan("杭州景点1"), self.request, tool)
        tool.run.assert_not_called()

    def test_rejects_a_business_using_the_attraction_name(self):
        self.search["pois"][0].update(name="西湖", typecode="050100")
        tool = Mock()
        tool.run.return_value = wrapped(self.search)
        with self.assertRaisesRegex(ValueError, "名称匹配"):
            verify_attraction_locations(self.plan("西湖"), self.request, tool)

    def test_rejects_ambiguous_names(self):
        self.search["pois"].append({**self.search["pois"][0], "id": "OTHER-POI"})
        tool = Mock()
        tool.run.return_value = wrapped(self.search)
        with self.assertRaisesRegex(ValueError, "多个同名"):
            verify_attraction_locations(self.plan("西湖"), self.request, tool)

    def test_parenthetical_alias_prefers_primary_name_over_alias_and_scenic_area(self):
        plan = self.plan("岳飞墓（岳王庙·西湖风景名胜区）")
        search = {"pois": [
            {"id": "TEMPLE", "name": "杭州西湖风景名胜区-岳王庙", "typecode": "110205"},
            {"id": "LAKE", "name": "杭州西湖风景名胜区", "typecode": "110202"},
            {"id": "TOMB", "name": "杭州西湖风景名胜区-岳飞墓", "typecode": "110200"},
        ]}
        detail = {**self.detail, "id": "TOMB", "name": search["pois"][2]["name"]}
        tool = Mock()
        tool.run.side_effect = [wrapped(search), wrapped(detail)]

        verify_attraction_locations(plan, self.request, tool)

        self.assertEqual(plan.days[0].attractions[0].poi_id, "TOMB")
        self.assertEqual(tool.run.call_count, 2)
        self.assertEqual(tool.run.call_args_list[1].args[0]["arguments"], {"id": "TOMB"})

    def test_retries_base_name_when_decorated_search_returns_no_results(self):
        plan = self.plan("西湖（西湖风景名胜区）")
        tool = Mock()
        tool.run.side_effect = [wrapped({"pois": []}), wrapped(self.search), wrapped(self.detail)]

        verify_attraction_locations(plan, self.request, tool)

        self.assertEqual(tool.run.call_args_list[1].args[0]["arguments"]["keywords"], "西湖")
        self.assertEqual(plan.days[0].attractions[0].poi_id, "POI-WEST-LAKE")

    def test_accepts_explicit_alias_after_checking_primary_name(self):
        plan = self.plan("岳王庙（岳庙）")
        search = {"pois": [{"id": "TEMPLE", "name": "西湖风景名胜区-岳庙", "typecode": "110205"}]}
        detail = {**self.detail, "id": "TEMPLE", "name": search["pois"][0]["name"]}
        tool = Mock()
        tool.run.side_effect = [wrapped(search), wrapped({"pois": []}), wrapped(detail)]

        verify_attraction_locations(plan, self.request, tool)

        self.assertEqual(plan.days[0].attractions[0].poi_id, "TEMPLE")
        self.assertEqual(tool.run.call_args_list[1].args[0]["arguments"]["keywords"], "岳王庙")

    def test_does_not_replace_child_attraction_with_parenthetical_scenic_area(self):
        tool = Mock()
        tool.run.return_value = wrapped(self.search)
        with self.assertRaisesRegex(ValueError, "名称匹配"):
            verify_attraction_locations(self.plan("岳飞墓（西湖风景名胜区）"), self.request, tool)
        self.assertEqual(tool.run.call_count, 2)

    def test_does_not_match_arbitrary_substrings_or_a_different_museum_branch(self):
        cases = [
            ("岳飞", "杭州西湖风景名胜区-岳飞墓"),
            ("西湖", "杭州西湖风景名胜区-岳飞墓"),
            ("浙江省博物馆（之江馆区）", "浙江省博物馆（孤山馆区）"),
        ]
        for query, candidate in cases:
            with self.subTest(query=query):
                tool = Mock()
                tool.run.return_value = wrapped({"pois": [{
                    "id": "UNRELATED", "name": candidate, "typecode": "110200",
                }]})
                with self.assertRaisesRegex(ValueError, "名称匹配"):
                    verify_attraction_locations(self.plan(query), self.request, tool)

    def test_rejects_shared_terminal_with_conflicting_hierarchy_parents(self):
        for query, candidate in [
            ("西湖-南门", "灵隐寺-南门"),
            ("浙江省博物馆-南门", "杭州植物园-南门"),
            ("西湖-岳王庙-南门", "灵隐寺-岳王庙-南门"),
            ("南门", "杭州植物园-南门"),
            ("杭州植物园-游客中心", "游客中心"),
        ]:
            with self.subTest(query=query, candidate=candidate):
                tool = Mock()
                tool.run.return_value = wrapped({"pois": [{
                    "id": "WRONG-PARENT", "name": candidate, "typecode": "110200",
                }]})
                with self.assertRaisesRegex(ValueError, "名称匹配"):
                    verify_attraction_locations(self.plan(query), self.request, tool)
                self.assertEqual(tool.run.call_count, 1)

    def test_accepts_compatible_hierarchy_and_independent_terminal_names(self):
        for query, candidate in [
            ("西湖-南门", "杭州西湖风景名胜区-南门"),
            ("岳王庙-南门", "杭州西湖风景名胜区-岳王庙-南门"),
            ("断桥残雪", "杭州西湖风景名胜区-断桥残雪"),
            ("杭州西湖风景名胜区-断桥残雪", "断桥残雪"),
        ]:
            with self.subTest(query=query, candidate=candidate):
                search = {"pois": [{"id": "CORRECT-POI", "name": candidate, "typecode": "110200"}]}
                detail = {**self.detail, "id": "CORRECT-POI", "name": candidate}
                plan = self.plan(query)
                tool = Mock()
                tool.run.side_effect = [wrapped(search), wrapped(detail)]

                verify_attraction_locations(plan, self.request, tool)

                self.assertEqual(plan.days[0].attractions[0].poi_id, "CORRECT-POI")

    def test_uses_provided_id_only_to_disambiguate_matching_search_candidates(self):
        self.search["pois"].append({**self.search["pois"][0], "id": "OTHER-POI"})
        plan = self.plan("西湖")
        plan.days[0].attractions[0].poi_id = "OTHER-POI"
        tool = Mock()
        tool.run.side_effect = [wrapped(self.search), wrapped({**self.detail, "id": "OTHER-POI"})]

        verify_attraction_locations(plan, self.request, tool)

        self.assertEqual(plan.days[0].attractions[0].poi_id, "OTHER-POI")
        self.assertEqual(tool.run.call_args_list[1].args[0]["arguments"], {"id": "OTHER-POI"})

    def test_rejects_unverified_id_when_names_are_ambiguous(self):
        self.search["pois"].append({**self.search["pois"][0], "id": "OTHER-POI"})
        plan = self.plan("西湖")
        plan.days[0].attractions[0].poi_id = "MODEL-INVENTED-ID"
        tool = Mock()
        tool.run.return_value = wrapped(self.search)
        with self.assertRaisesRegex(ValueError, "多个同名"):
            verify_attraction_locations(plan, self.request, tool)
        self.assertEqual(tool.run.call_count, 1)

    def test_cache_does_not_override_disambiguation_for_distinct_ids(self):
        self.search["pois"].append({**self.search["pois"][0], "id": "OTHER-POI"})
        plan = self.plan("西湖", "西湖")
        plan.days[0].attractions[0].poi_id = "POI-WEST-LAKE"
        plan.days[0].attractions[1].poi_id = "OTHER-POI"
        tool = Mock()
        tool.run.side_effect = [
            wrapped(self.search), wrapped(self.detail),
            wrapped(self.search), wrapped({**self.detail, "id": "OTHER-POI"}),
        ]

        verify_attraction_locations(plan, self.request, tool)

        self.assertEqual([attraction.poi_id for attraction in plan.days[0].attractions], [
            "POI-WEST-LAKE", "OTHER-POI",
        ])

    def test_keeps_original_plan_if_a_later_attraction_fails(self):
        plan = self.plan("西湖", "未收录地点")
        before = plan.model_dump()
        tool = Mock()
        tool.run.side_effect = [wrapped(self.search), wrapped(self.detail), wrapped({"pois": []})]
        with self.assertRaisesRegex(ValueError, "未在杭州查到"):
            verify_attraction_locations(plan, self.request, tool)
        self.assertEqual(plan.model_dump(), before)

    def test_upstream_errors_do_not_expose_credentials(self):
        secret_error = "request failed: https://example.invalid/?key=test-secret"
        for response in (wrapped({"error": secret_error}), RuntimeError(secret_error)):
            with self.subTest(response_type=type(response).__name__):
                tool = Mock()
                if isinstance(response, Exception):
                    tool.run.side_effect = response
                else:
                    tool.run.return_value = response
                with self.assertRaises(ValueError) as caught:
                    verify_attraction_locations(self.plan("西湖"), self.request, tool)
                self.assertNotIn("test-secret", str(caught.exception))
                self.assertNotIn("https://", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
