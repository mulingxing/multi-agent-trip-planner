"""多智能体旅行规划系统"""

import json
import re
from datetime import date, timedelta
from pydantic import ValidationError
from hello_agents import SimpleAgent
from ..services.llm_service import get_llm
from ..services.amap_service import get_amap_mcp_tool
from ..services.plan_validation import verify_attraction_locations
from ..models.schemas import TripRequest, TripPlan


class TripPlanningError(RuntimeError):
    """可向用户展示的规划失败，不用占位数据伪装成功。"""

# ============ Agent提示词 ============

ATTRACTION_AGENT_PROMPT = """你是景点搜索专家。你的任务是根据城市和用户偏好搜索合适的景点。

**重要提示:**
你必须使用工具来搜索景点!不要自己编造景点信息!

**工具调用格式:**
使用maps_text_search工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=景点关键词,city=城市名]`

**示例:**
用户: "搜索北京的历史文化景点"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=历史文化,city=北京]

用户: "搜索上海的公园"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=公园,city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 参数用逗号分隔
4. 搜索结束后返回候选景点的原始name、id和address。完整保留高德原名，不自行添加别名或改写名称。
5. 结合用户额外要求选择搜索关键词，搜索足够数量的候选景点。
"""

WEATHER_AGENT_PROMPT = """你是天气查询专家。你的任务是查询指定城市的天气信息。

**重要提示:**
你必须使用工具来查询天气!不要自己编造天气信息!

**工具调用格式:**
使用maps_weather工具时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_weather:city=城市名]`

**示例:**
用户: "查询北京天气"
你的回复: [TOOL_CALL:amap_maps_weather:city=北京]

用户: "上海的天气怎么样"
你的回复: [TOOL_CALL:amap_maps_weather:city=上海]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
"""

HOTEL_AGENT_PROMPT = """你是酒店推荐专家。你的任务是根据城市和景点位置推荐合适的酒店。

**重要提示:**
你必须使用工具来搜索酒店!不要自己编造酒店信息!

**工具调用格式:**
使用maps_text_search工具搜索酒店时,必须严格按照以下格式:
`[TOOL_CALL:amap_maps_text_search:keywords=酒店,city=城市名]`

**示例:**
用户: "搜索北京的酒店"
你的回复: [TOOL_CALL:amap_maps_text_search:keywords=酒店,city=北京]

**注意:**
1. 必须使用工具,不要直接回答
2. 格式必须完全正确,包括方括号和冒号
3. 关键词使用"酒店"或"宾馆"
"""

FOOD_AGENT_PROMPT = """你是餐饮推荐专家。使用amap_maps_text_search搜索目的地城市的真实餐馆。
工具格式：[TOOL_CALL:amap_maps_text_search:keywords=当地特色餐饮,city=城市名]
结合景点位置推荐早餐、午餐和晚餐，给出餐馆名称、特色菜或当地具体食物、地址。
只能使用查到的餐馆；查不到时明确说明，不编造店名。不要返回“早餐推荐”等占位文字。
"""

PLANNER_AGENT_PROMPT = """你是行程规划专家。根据提供的景点、天气、酒店和餐饮信息生成行程。

请严格按照以下JSON格式返回旅行计划:
```json
{
  "city": "城市名称",
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD",
  "days": [
    {
      "date": "YYYY-MM-DD",
      "day_index": 0,
      "description": "第1天行程概述",
      "transportation": "交通方式",
      "accommodation": "住宿类型",
      "hotel": {
        "name": "酒店名称",
        "address": "酒店地址",
        "location": {"longitude": 116.397128, "latitude": 39.916527},
        "price_range": "300-500元",
        "rating": "4.5",
        "distance": "距离景点2公里",
        "type": "经济型酒店",
        "estimated_cost": 400
      },
      "attractions": [
        {
          "name": "景点名称",
          "poi_id": "搜索结果中的景点id",
          "address": "详细地址",
          "location": {"longitude": 116.397128, "latitude": 39.916527},
          "visit_duration": 120,
          "description": "景点详细描述",
          "category": "景点类别",
          "ticket_price": 60
        }
      ],
      "meals": [
        {"type": "breakfast", "name": "早餐推荐", "description": "早餐描述", "estimated_cost": 30},
        {"type": "lunch", "name": "午餐推荐", "description": "午餐描述", "estimated_cost": 50},
        {"type": "dinner", "name": "晚餐推荐", "description": "晚餐描述", "estimated_cost": 80}
      ]
    }
  ],
  "weather_info": [
    {
      "date": "YYYY-MM-DD",
      "day_weather": "晴",
      "night_weather": "多云",
      "day_temp": 25,
      "night_temp": 15,
      "wind_direction": "南风",
      "wind_power": "1-3级"
    }
  ],
  "overall_suggestions": "总体建议",
  "budget": {
    "total_attractions": 180,
    "total_hotels": 1200,
    "total_meals": 480,
    "total_transportation": 200,
    "total": 2060
  }
}
```

**重要提示:**
1. weather_info只包含工具实际查到的日期，超出预报范围的日期不编造天气
2. 温度必须是纯数字(不要带°C等单位)
3. 每天安排2-3个景点
4. 考虑景点之间的距离和游览时间
5. 每天必须包含早中晚三餐
6. 提供实用的旅行建议
7. **必须包含预算信息**:
   - 景点门票价格(ticket_price)
   - 餐饮预估费用(estimated_cost)
   - 酒店预估费用(estimated_cost)
   - 预算汇总(budget)包含各项总费用
8. 景点使用搜索结果的原始name和poi_id，禁止改名、合并景点或自行追加括号别名；酒店也用真实名称。
   不输出“城市景点1”等占位名称。示例中的坐标不能照抄，后端将通过高德详情核验。
9. 每餐写具体餐馆或当地食物，以及推荐菜品；禁止“第1天早餐”“午餐推荐”等空泛文字。
10. 金额是整数的估算值，在建议中注明仅供参考；评分用字符串。只输出JSON对象。
"""


class MultiAgentTripPlanner:
    """多智能体旅行规划系统"""

    def __init__(self):
        """初始化多智能体系统"""
        print("🔄 开始初始化多智能体旅行规划系统...")

        try:
            self.llm = get_llm()

            # 创建共享的MCP工具(只创建一次)
            print("  - 创建共享MCP工具...")
            self.amap_tool = get_amap_mcp_tool()
            self.amap_tool.expandable=True

            # 创建景点搜索Agent
            print("  - 创建景点搜索Agent...")
            self.attraction_agent = SimpleAgent(
                name="景点搜索专家",
                llm=self.llm,
                system_prompt=ATTRACTION_AGENT_PROMPT
            )
            self.attraction_agent.add_tool(self.amap_tool)

            # 创建天气查询Agent
            print("  - 创建天气查询Agent...")
            self.weather_agent = SimpleAgent(
                name="天气查询专家",
                llm=self.llm,
                system_prompt=WEATHER_AGENT_PROMPT
            )
            self.weather_agent.add_tool(self.amap_tool)

            # 创建酒店推荐Agent
            print("  - 创建酒店推荐Agent...")
            self.hotel_agent = SimpleAgent(
                name="酒店推荐专家",
                llm=self.llm,
                system_prompt=HOTEL_AGENT_PROMPT
            )
            self.hotel_agent.add_tool(self.amap_tool)

            self.food_agent = SimpleAgent(
                name="餐饮推荐专家", llm=self.llm, system_prompt=FOOD_AGENT_PROMPT
            )
            self.food_agent.add_tool(self.amap_tool)

            # 创建行程规划Agent(不需要工具)
            print("  - 创建行程规划Agent...")
            self.planner_agent = SimpleAgent(
                name="行程规划专家",
                llm=self.llm,
                system_prompt=PLANNER_AGENT_PROMPT
            )

            print(f"✅ 多智能体系统初始化成功")
            print(f"   景点搜索Agent: {len(self.attraction_agent.list_tools())} 个工具")
            print(f"   天气查询Agent: {len(self.weather_agent.list_tools())} 个工具")
            print(f"   酒店推荐Agent: {len(self.hotel_agent.list_tools())} 个工具")

        except Exception as e:
            raise TripPlanningError(
                "初始化失败：请检查 backend/.env 的模型配置，以及高德MCP工具能否启动。"
            ) from e
    
    def plan_trip(self, request: TripRequest) -> TripPlan:
        """
        使用多智能体协作生成旅行计划

        Args:
            request: 旅行请求

        Returns:
            旅行计划
        """
        stage = "搜索景点"
        try:
            print(f"\n{'='*60}")
            print(f"🚀 开始多智能体协作规划旅行...")
            print(f"目的地: {request.city}")
            print(f"日期: {request.start_date} 至 {request.end_date}")
            print(f"天数: {request.travel_days}天")
            print(f"偏好: {', '.join(request.preferences) if request.preferences else '无'}")
            print(f"{'='*60}\n")

            # 步骤1: 景点搜索Agent搜索景点
            print("📍 步骤1: 搜索景点...")
            attraction_query = self._build_attraction_query(request)
            attraction_response = self.attraction_agent.run(attraction_query)
            print(f"景点搜索结果: {attraction_response[:200]}...\n")

            # 步骤2: 天气查询Agent查询天气
            stage = "查询天气"
            print("🌤️  步骤2: 查询天气...")
            weather_query = f"请查询{request.city}的天气信息"
            weather_response = self.weather_agent.run(weather_query)
            print(f"天气查询结果: {weather_response[:200]}...\n")

            # 步骤3: 酒店推荐Agent搜索酒店
            stage = "搜索酒店"
            print("🏨 步骤3: 搜索酒店...")
            hotel_query = f"请搜索{request.city}的{request.accommodation}酒店"
            hotel_response = self.hotel_agent.run(hotel_query)
            print(f"酒店搜索结果: {hotel_response[:200]}...\n")

            stage = "搜索餐饮"
            print("🍜 步骤4: 搜索餐饮...")
            food_response = self.food_agent.run(
                f"请搜索{request.city}的特色餐馆，为{request.travel_days}天行程推荐具体三餐。"
                f"景点参考：{attraction_response}"
            )

            stage = "生成行程JSON"
            print("📋 步骤5: 生成行程计划...")
            planner_query = self._build_planner_query(request, attraction_response, weather_response, hotel_response)
            planner_query += f"\n**真实餐饮信息:**\n{food_response}"
            # 格式/字段错误最多修正一次，失败时明确报错。
            for attempt in range(2):
                planner_response = self.planner_agent.run(
                    planner_query,
                    response_format={"type": "json_object"},
                    max_tokens=min(16000, max(4096, request.travel_days * 2200)),
                )
                try:
                    trip_plan = self._parse_response(planner_response, request)
                    break
                except ValueError as exc:
                    if attempt:
                        raise
                    print(f"⚠️ 行程格式校验未通过，正在修正：{exc}")
                    planner_query = (
                        f"上次JSON校验失败：{exc}。请修正并返回完整JSON对象。"
                        "保留已查到的真实景点和餐饮，不能编造占位数据。"
                    )

            stage = "核验景点坐标"
            print("📍 步骤6: 使用高德POI详情核验景点坐标...")
            verify_attraction_locations(trip_plan, request, self.amap_tool)

            print(f"{'='*60}")
            print(f"✅ 旅行计划生成完成!")
            print(f"{'='*60}\n")

            return trip_plan

        except Exception as e:
            if isinstance(e, ValueError):
                reason = str(e)
            elif "401" in str(e) or "Authentication" in str(e):
                reason = "模型鉴权失败，请检查 backend/.env 中的 LLM_API_KEY。"
            elif "429" in str(e):
                reason = "模型服务限流或额度不足，请检查账户后稍后重试。"
            elif "timeout" in str(e).lower() or "timed out" in str(e).lower():
                reason = "上游服务请求超时，请稍后重试。"
            else:
                reason = f"上游服务调用失败（{type(e).__name__}），请检查模型和地图服务配置。"
            message = f"{stage}失败：{reason}"
            print(f"❌ {message}")
            raise TripPlanningError(message) from e
    
    def _build_attraction_query(self, request: TripRequest) -> str:
        """构建景点搜索查询 - 直接包含工具调用"""
        keywords = []
        if request.preferences:
            # 只取第一个偏好作为关键词
            keywords = request.preferences[0]
        else:
            keywords = "景点"

        # 直接返回工具调用格式
        query = f"请使用amap_maps_text_search工具搜索{request.city}的{keywords}相关景点。\n[TOOL_CALL:amap_maps_text_search:keywords={keywords},city={request.city}]"
        query += f"\n需要规划{request.travel_days}天，请提供足够的真实候选景点，保留高德原始name、id、address。"
        if request.free_text_input:
            query += f"\n用户额外要求：{request.free_text_input}"
        return query

    def _build_planner_query(self, request: TripRequest, attractions: str, weather: str, hotels: str = "") -> str:
        """构建行程规划查询"""
        query = f"""请根据以下信息生成{request.city}的{request.travel_days}天旅行计划:

**基本信息:**
- 城市: {request.city}
- 日期: {request.start_date} 至 {request.end_date}
- 天数: {request.travel_days}天
- 交通方式: {request.transportation}
- 住宿: {request.accommodation}
- 偏好: {', '.join(request.preferences) if request.preferences else '无'}

**景点信息:**
{attractions}

**天气信息:**
{weather}

**酒店信息:**
{hotels}

**要求:**
1. 每天安排2-3个景点
2. 每天必须包含早中晚三餐
3. 每天推荐一个具体的酒店(从酒店信息中选择)
3. 考虑景点之间的距离和交通方式
4. 返回完整的JSON格式数据
5. 景点的经纬度坐标要真实准确
"""
        if request.free_text_input:
            query += f"\n**额外要求:** {request.free_text_input}"

        return query
    
    def _parse_response(self, response: str, request: TripRequest) -> TripPlan:
        """
        解析Agent响应
        
        Args:
            response: Agent响应文本
            request: 原始请求
            
        Returns:
            旅行计划
        """
        try:
            # 尝试从响应中提取JSON
            # 查找JSON代码块
            if not isinstance(response, str) or not response.strip():
                raise ValueError("模型返回空内容，请检查模型输出设置。")
            json_start = response.find("{")
            if json_start < 0:
                raise ValueError("响应中未找到JSON数据")
            
            # 解析JSON
            # raw_decode允许JSON后的说明/围栏；未闭合围栏不会再截掉最后一个字符。
            data, _ = json.JSONDecoder().raw_decode(response[json_start:])
            
            # 转换为TripPlan对象
            trip_plan = TripPlan(**data)
            self._validate_plan(trip_plan, request)
            
            return trip_plan
            
        except json.JSONDecodeError as e:
            raise ValueError(f"模型返回的JSON不完整或格式错误（第{e.lineno}行）。") from e
        except ValidationError as e:
            fields = "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['type']}"
                for err in e.errors(include_input=False)[:8]
            )
            raise ValueError(f"行程字段不符合要求：{fields}") from e

    @staticmethod
    def _validate_plan(plan: TripPlan, request: TripRequest) -> None:
        """拒绝空行程、占位三餐和与请求不符的计划。"""
        if plan.city.strip().removesuffix("市") != request.city.strip().removesuffix("市"):
            raise ValueError("行程城市与请求目的地不一致。")
        if (plan.start_date, plan.end_date) != (request.start_date, request.end_date):
            raise ValueError("行程起止日期与请求不一致。")
        if len(plan.days) != request.travel_days:
            raise ValueError("每日行程数量与请求天数不一致。")
        start = date.fromisoformat(request.start_date)
        for index, day in enumerate(plan.days):
            if day.date != (start + timedelta(days=index)).isoformat() or day.day_index != index:
                raise ValueError(f"第{index + 1}天日期或序号不正确。")
            if not day.attractions:
                raise ValueError(f"第{index + 1}天缺少真实景点。")
            for attraction in day.attractions:
                if not attraction.name.strip() or re.fullmatch(r".*景点\d+|景点名称", attraction.name.strip()):
                    raise ValueError("景点名称仍是占位文字，必须使用搜索到的真实名称。")
            if not {"breakfast", "lunch", "dinner"}.issubset({meal.type for meal in day.meals}):
                raise ValueError(f"第{index + 1}天缺少早餐、午餐或晚餐。")
            for meal in day.meals:
                if not meal.name.strip() or re.fullmatch(
                    r"第\d+天[早午晚]餐|[早午晚]餐推荐|当地特色[早午晚]餐", meal.name.strip()
                ):
                    raise ValueError("餐饮仍是占位文字，请填写具体餐馆或当地食物。")


def get_trip_planner_agent() -> MultiAgentTripPlanner:
    """每次请求创建独立Agent对话，避免不同城市的历史互相污染。"""
    return MultiAgentTripPlanner()

