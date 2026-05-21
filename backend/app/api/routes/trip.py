"""旅行规划API路由 .\venv\Scripts\Activate.ps1"""

import uuid
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from ...models.schemas import TripRequest, TripPlanResponse, ErrorResponse
from ...agents.test import get_trip_planner_agent
from ...memory.episodic_manager import EpisodicMemoryManager

router = APIRouter(prefix="/trip", tags=["旅行规划"])


@router.post("/plan", summary="首次生成旅行计划")
async def plan_trip(request: TripRequest):
    try:
        print(f"\n{'='*60}")
        print(f"📥 收到旅行规划请求: {request.city} | {request.spending_tier}")
        print(f"{'='*60}\n")

        agent = await get_trip_planner_agent()
        session_id = str(uuid.uuid4())
        result = await agent.plan_trip(request, session_id)

        return {
            "success": True,
            "message": "旅行计划生成成功",
            "session_id": result["session_id"],
            "user_id": result.get("user_id"),
            "data": result["plan"],
            "critic_scores": result.get("critic_scores"),
            "consumption_tier": result.get("consumption_tier")
        }
    except Exception as e:
        print(f"❌ 生成旅行计划失败: {str(e)}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"生成旅行计划失败: {str(e)}")


class RefineRequest(BaseModel):
    session_id: str
    message: str


@router.post("/refine", summary="多轮精修旅行计划")
async def refine_trip(req: RefineRequest):
    """用户发送精修指令，系统从对应节点重新执行"""
    try:
        agent = await get_trip_planner_agent()
        result = await agent.refine_trip(req.session_id, req.message)
        return {
            "success": True,
            "message": "精修完成",
            "session_id": result["session_id"],
            "user_id": result.get("user_id"),
            "data": result["plan"],
            "critic_scores": result.get("critic_scores"),
            "consumption_tier": result.get("consumption_tier")
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"精修失败: {str(e)}")


@router.get("/history/{session_id}", summary="获取对话历史")
async def get_history(session_id: str):
    try:
        agent = await get_trip_planner_agent()
        history = await agent.get_chat_history(session_id)
        return {"success": True, "session_id": session_id, "history": history}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取历史失败: {str(e)}")


@router.get("/health", summary="健康检查")
async def health_check():
    return {"status": "healthy", "service": "trip-planner"}


class FeedbackRequest(BaseModel):
    user_id: str
    city: str
    rating: int
    feedback_text: str


@router.post("/feedback")
async def submit_trip_feedback(req: FeedbackRequest):
    try:
        memory_manager = EpisodicMemoryManager()
        memory_manager.save_experience(
            user_id=req.user_id,
            city=req.city,
            rating=req.rating,
            feedback_text=req.feedback_text
        )
        return {"status": "success", "message": "记忆已成功刻入双引擎存储库。"}
    except Exception as e:
        return {"status": "error", "message": str(e)}



