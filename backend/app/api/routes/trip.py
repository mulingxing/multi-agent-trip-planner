"""旅行规划API路由"""

from fastapi import APIRouter, HTTPException
from ...models.schemas import (
    TripRequest,
    TripPlanResponse,
    ErrorResponse
)
from ...agents.trip_planner_agent import get_trip_planner_agent, TripPlanningError

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post(
    "/plan",
    response_model=TripPlanResponse,
    summary="生成旅行计划",
    description="根据用户输入的旅行需求,生成详细的旅行计划"
)
def plan_trip(request: TripRequest):
    """
    生成旅行计划

    Args:
        request: 旅行请求参数

    Returns:
        旅行计划响应
    """
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求:")
        print(f"   城市: {request.city}")
        print(f"   日期: {request.start_date} - {request.end_date}")
        print(f"   天数: {request.travel_days}")
        print(f"{'='*60}\n")

        # 获取Agent实例
        print("🔄 获取多智能体系统实例...")
        agent = get_trip_planner_agent()

        # 生成旅行计划
        print("🚀 开始生成旅行计划...")
        trip_plan = agent.plan_trip(request)

        print("✅ 旅行计划生成成功,准备返回响应\n")

        return TripPlanResponse(
            success=True,
            message="旅行计划生成成功",
            data=trip_plan
        )

    except TripPlanningError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        print(f"❌ 生成旅行计划失败（{type(e).__name__}）")
        raise HTTPException(
            status_code=500,
            detail="旅行规划服务发生异常，请检查后端配置后重试。"
        ) from e


@router.get(
    "/health",
    summary="健康检查",
    description="检查旅行规划服务是否正常"
)
def health_check():
    """健康检查"""
    try:
        # 检查Agent是否可用
        agent = get_trip_planner_agent()
        
        return {
            "status": "healthy",
            "service": "trip-planner",
            "agent_name": "MultiAgentTripPlanner",
            "tools_count": len(agent.attraction_agent.list_tools())
        }
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="旅行规划依赖初始化失败，请检查模型配置和高德MCP服务。"
        )

