"""旅行规划离线回归：模拟所有Agent和MCP入口，不读取.env或调用外部服务。"""

from contextlib import redirect_stdout
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


BACKEND_PATH = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_PATH))

from app.models.schemas import TripRequest


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TripPlannerTests(unittest.TestCase):
    def setUp(self):
        # 隔离会间接加载app.config或启动工具的依赖。
        fake_agents = types.ModuleType("hello_agents")
        fake_agents.SimpleAgent = Mock()
        fake_llm = types.ModuleType("app.services.llm_service")
        fake_llm.get_llm = Mock()
        fake_amap = types.ModuleType("app.services.amap_service")
        fake_amap.get_amap_mcp_tool = Mock()
        with patch.dict(sys.modules, {
            "hello_agents": fake_agents,
            "app.services.llm_service": fake_llm,
            "app.services.amap_service": fake_amap,
        }):
            self.module = load_module(
                "app.agents._trip_planner_under_test",
                BACKEND_PATH / "app" / "agents" / "trip_planner_agent.py",
            )
        self.planner = self.module.MultiAgentTripPlanner.__new__(
            self.module.MultiAgentTripPlanner
        )
        self.request = TripRequest(
            city="杭州", start_date="2026-10-12", end_date="2026-10-12",
            travel_days=1, transportation="公共交通", accommodation="酒店",
        )
        self.plan_data = {
            "city": "杭州",
            "start_date": self.request.start_date,
            "end_date": self.request.end_date,
            "overall_suggestions": "费用为估算，仅供参考。",
            "days": [{
                "date": self.request.start_date,
                "day_index": 0,
                "description": "西湖游览",
                "transportation": "公共交通",
                "accommodation": "酒店",
                "attractions": [{
                    "name": "西湖", "address": "杭州市西湖区",
                    "location": {"longitude": 120.121358, "latitude": 30.222692},
                    "visit_duration": 120, "description": "环湖步行",
                }],
                "meals": [
                    {"type": "breakfast", "name": "片儿川"},
                    {"type": "lunch", "name": "葱包桧"},
                    {"type": "dinner", "name": "东坡肉"},
                ],
            }],
        }

    def parse(self, data):
        return self.planner._parse_response(
            json.dumps(data, ensure_ascii=False), self.request
        )

    def configure_agents(self):
        for name in ("attraction_agent", "weather_agent", "hotel_agent", "food_agent"):
            setattr(self.planner, name, Mock())
            getattr(self.planner, name).run.return_value = "测试搜索结果"
        self.planner.planner_agent = Mock()
        self.planner.planner_agent.run.return_value = json.dumps(
            self.plan_data, ensure_ascii=False
        )
        self.planner.amap_tool = Mock()

    def test_complete_json_without_closing_markdown_fence_is_accepted(self):
        response = "```json\n" + json.dumps(self.plan_data, ensure_ascii=False)

        plan = self.planner._parse_response(response, self.request)

        self.assertEqual(plan.city, "杭州")
        self.assertEqual(plan.days[0].attractions[0].name, "西湖")
        self.assertEqual(len(plan.days[0].meals), 3)

    def test_truncated_json_is_rejected_instead_of_returning_a_placeholder(self):
        response = json.dumps(self.plan_data, ensure_ascii=False)[:-1]
        with self.assertRaisesRegex(ValueError, "JSON不完整|格式错误"):
            self.planner._parse_response(response, self.request)

    def test_each_required_meal_must_be_present(self):
        for meal_type in ("breakfast", "lunch", "dinner"):
            with self.subTest(missing=meal_type):
                data = deepcopy(self.plan_data)
                data["days"][0]["meals"] = [
                    meal for meal in data["days"][0]["meals"] if meal["type"] != meal_type
                ]
                with self.assertRaisesRegex(ValueError, "缺少早餐、午餐或晚餐"):
                    self.parse(data)

    def test_placeholder_attractions_are_rejected(self):
        for name in ("杭州景点1", "景点名称", " "):
            with self.subTest(name=name):
                data = deepcopy(self.plan_data)
                data["days"][0]["attractions"][0]["name"] = name
                with self.assertRaisesRegex(ValueError, "占位"):
                    self.parse(data)

    def test_placeholder_meals_are_rejected(self):
        for name in ("第1天早餐", "早餐推荐", "当地特色早餐", " "):
            with self.subTest(name=name):
                data = deepcopy(self.plan_data)
                data["days"][0]["meals"][0]["name"] = name
                with self.assertRaisesRegex(ValueError, "占位"):
                    self.parse(data)

    def test_destination_and_date_range_must_match_request(self):
        for field, value in (
            ("city", "北京"),
            ("start_date", "2026-10-11"),
            ("end_date", "2026-10-13"),
        ):
            with self.subTest(field=field):
                data = deepcopy(self.plan_data)
                data[field] = value
                with self.assertRaisesRegex(ValueError, "不一致"):
                    self.parse(data)

    def test_day_count_must_match_request(self):
        for days in ([], self.plan_data["days"] * 2):
            with self.subTest(day_count=len(days)):
                data = deepcopy(self.plan_data)
                data["days"] = days
                with self.assertRaisesRegex(ValueError, "天数不一致"):
                    self.parse(data)

    def test_day_dates_and_indices_must_follow_the_requested_sequence(self):
        for field, value in (("date", "2026-10-13"), ("day_index", 1)):
            with self.subTest(field=field):
                data = deepcopy(self.plan_data)
                data["days"][0][field] = value
                with self.assertRaisesRegex(ValueError, "日期或序号不正确"):
                    self.parse(data)

    def test_upstream_agent_exceptions_always_raise_planning_error(self):
        for failing_agent in (
            "attraction_agent", "weather_agent", "hotel_agent", "food_agent", "planner_agent",
        ):
            with self.subTest(agent=failing_agent):
                self.configure_agents()
                getattr(self.planner, failing_agent).run.side_effect = RuntimeError("offline failure")
                with patch.object(self.module, "verify_attraction_locations") as verify:
                    with redirect_stdout(io.StringIO()):
                        with self.assertRaises(self.module.TripPlanningError):
                            self.planner.plan_trip(self.request)
                    verify.assert_not_called()

    def test_invalid_plan_is_retried_once_and_never_returned_as_success(self):
        self.configure_agents()
        self.planner.planner_agent.run.return_value = "不是JSON"
        with patch.object(self.module, "verify_attraction_locations") as verify:
            with redirect_stdout(io.StringIO()):
                with self.assertRaises(self.module.TripPlanningError):
                    self.planner.plan_trip(self.request)
            verify.assert_not_called()
        self.assertEqual(self.planner.planner_agent.run.call_count, 2)

    def test_success_is_returned_only_after_location_verification(self):
        self.configure_agents()
        with patch.object(self.module, "verify_attraction_locations") as verify:
            with redirect_stdout(io.StringIO()):
                plan = self.planner.plan_trip(self.request)
        verify.assert_called_once_with(plan, self.request, self.planner.amap_tool)
        self.assertEqual(plan.days[0].attractions[0].name, "西湖")

    def test_location_verification_failure_is_not_a_success(self):
        self.configure_agents()
        with patch.object(
            self.module, "verify_attraction_locations", side_effect=ValueError("景点跨市")
        ):
            with redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(self.module.TripPlanningError, "景点跨市"):
                    self.planner.plan_trip(self.request)

    def test_factory_creates_independent_planners_per_request(self):
        first, second = object(), object()
        with patch.object(self.module, "MultiAgentTripPlanner", side_effect=[first, second]) as factory:
            result_one = self.module.get_trip_planner_agent()
            result_two = self.module.get_trip_planner_agent()
        self.assertIs(result_one, first)
        self.assertIs(result_two, second)
        self.assertIsNot(result_one, result_two)
        self.assertEqual(factory.call_count, 2)

    def test_api_reports_planning_failure_as_502_without_plan_data(self):
        with patch.dict(sys.modules, {"app.agents.trip_planner_agent": self.module}):
            route = load_module(
                "app.api.routes._trip_route_under_test",
                BACKEND_PATH / "app" / "api" / "routes" / "trip.py",
            )
        app = FastAPI()
        app.include_router(route.router, prefix="/api")
        failure = "搜索景点失败：上游服务请求超时，请稍后重试。"
        planner = Mock()
        planner.plan_trip.side_effect = self.module.TripPlanningError(failure)
        with patch.object(route, "get_trip_planner_agent", return_value=planner):
            with redirect_stdout(io.StringIO()), TestClient(app) as client:
                response = client.post("/api/trip/plan", json=self.request.model_dump())

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"], failure)
        self.assertNotIn("data", response.json())
        self.assertNotEqual(response.json().get("success"), True)


if __name__ == "__main__":
    unittest.main()
