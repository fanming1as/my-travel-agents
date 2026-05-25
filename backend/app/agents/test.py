"""Travel planner backend agent.

This version restores the Qdrant-backed memory flow. If Qdrant is not
available, initialization will fail as originally designed.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, TypedDict

import httpx
from dotenv import load_dotenv
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from ..memory.episodic_manager import EpisodicMemoryManager
from ..memory.profile_manager import ProfileMemoryManager
from ..memory.qdrant_manager import QdrantSemanticMemory
from ..memory.sqlite_checkpointer import SQLiteCheckpointSaver
from ..models.schemas import (
    Attraction,
    Budget,
    DayPlan,
    Hotel,
    Location,
    Meal,
    TripPlan,
    TripRequest,
    WeatherInfo,
)

load_dotenv()

_graph_planner = None
_AMAP_PLACE_SEARCH_URL = "https://restapi.amap.com/v3/place/text"
_AMAP_WEATHER_URL = "https://restapi.amap.com/v3/weather/weatherInfo"
_POI_TYPE_KEYWORDS = {
    "attractions": "风景名胜",
    "restaurants": "餐饮服务",
    "hotels": "住宿服务",
}
_POI_CATEGORY_LABELS = {
    "attractions": "景点",
    "restaurants": "餐厅",
    "hotels": "酒店",
}
_POI_RESULT_LIMITS = {
    "attractions": 5,
    "restaurants": 5,
    "hotels": 3,
}



class TripGraphState(TypedDict, total=False):
    request: TripRequest
    user_id: str
    chat_history: List[Any]
    spending_tier: str
    rag_knowledge: str
    user_memory: str
    user_profile: Dict[str, Any]
    user_profile_context: str
    poi_search_terms: Dict[str, List[str]]
    poi_candidates: Dict[str, List[Dict[str, Any]]]
    selected_pois: Dict[str, List[str]]
    selected_poi_details: Dict[str, List[Dict[str, Any]]]
    poi_selection_warnings: List[str]
    mcp_data: Dict[str, Any]
    final_plan: Dict[str, Any]
    critic_feedback: str
    critic_scores: Dict[str, Any]
    revision_count: int
    resume_from: str


State = TripGraphState


class GraphTripPlanner:
    def __init__(self):
        self.semantic_memory = QdrantSemanticMemory(collection_name="travel_guide")
        self.episodic_memory = EpisodicMemoryManager()
        self.profile_memory = ProfileMemoryManager()
        self.sessions: Dict[str, State] = {}
        self.checkpointer = SQLiteCheckpointSaver()
        self.graph_app = self._build_plan_graph()


    def _build_plan_graph(self):
        workflow = StateGraph(TripGraphState)
        workflow.add_node("profile_update", self._node_profile_update)
        workflow.add_node("profile_update_after_refinement", self._node_profile_update)
        workflow.add_node("knowledge_retrieval", self._node_knowledge_retrieval)
        workflow.add_node("poi_selector", self._node_poi_selector)
        workflow.add_node("gather_info", self._node_gather_info)
        workflow.add_node("planner", self._node_planner)
        workflow.add_node("image_enricher", self._node_image_enricher)
        workflow.add_node("qa_auditor", self._node_qa_auditor)
        workflow.add_node("await_refinement", self._node_await_refinement)
        workflow.add_node("refine_agent", self._node_refine_agent)

        workflow.set_entry_point("profile_update")
        workflow.add_edge("profile_update", "knowledge_retrieval")
        workflow.add_edge("knowledge_retrieval", "poi_selector")
        workflow.add_edge("poi_selector", "gather_info")
        workflow.add_edge("gather_info", "planner")
        workflow.add_edge("planner", "image_enricher")
        workflow.add_edge("image_enricher", "qa_auditor")
        workflow.add_conditional_edges(
            "qa_auditor",
            self._route_after_qa,
            {
                "poi_selector": "poi_selector",
                "await_refinement": "await_refinement",
            },
        )
        workflow.add_edge("await_refinement", "profile_update_after_refinement")
        workflow.add_edge("profile_update_after_refinement", "refine_agent")
        workflow.add_conditional_edges(
            "refine_agent",
            self._route_after_refine,
            {
                "knowledge_retrieval": "knowledge_retrieval",
                "poi_selector": "poi_selector",
                "planner": "planner",
                "await_refinement": "await_refinement",
                END: END,
            },
        )
        graph = workflow.compile(checkpointer=self.checkpointer)
        return graph

    async def _run_graph(
        self,
        state: State,
        entrypoint: Optional[str] = None,
        thread_id: Optional[str] = None,
    ) -> State:
        if entrypoint and entrypoint != "profile_update":
            raise ValueError("LangGraph runner only supports the configured entrypoint.")
        config = {"configurable": {"thread_id": thread_id or f"adhoc-{id(state)}"}}
        result = await self.graph_app.ainvoke(state, config=config)
        return result

    #把用户的新反馈送回上一次 interrupt(...) 暂停的位置，让 LangGraph 继续跑
    async def _resume_graph(self, thread_id: str, user_message: str) -> State:
        return await self.graph_app.ainvoke(
            Command(resume=user_message),
            config={"configurable": {"thread_id": thread_id}},
        )

    def _route_after_qa(self, state: State) -> str:
        critic_scores = state.get("critic_scores") or {}
        should_revise = bool(critic_scores.get("should_revise"))
        revision_count = state.get("revision_count", 0)
        if should_revise and revision_count < 2:
            return "poi_selector"
        return "await_refinement"

    def _route_after_refine(self, state: State) -> str:
        resume_from = state.get("resume_from") or "await_refinement"
        if resume_from in {"knowledge_retrieval", "poi_selector", "planner", "await_refinement", END}:
            return resume_from
        return "await_refinement"

    def _coerce_trip_request(self, request: Any) -> Any:
        if isinstance(request, TripRequest) or request is None:
            return request
        if isinstance(request, dict):
            try:
                return TripRequest(**request)
            except Exception:
                return request
        return request

    def _user_id_from_request(self, request: Any) -> str:
        if isinstance(request, dict):
            return request.get("user_id", "default_guest")
        return getattr(request, "user_id", "default_guest")

    def _restore_session_from_checkpoint(self, session_id: str) -> Optional[State]:
        checkpoint_tuple = self.checkpointer.get_tuple(
            {"configurable": {"thread_id": session_id}}
        )
        if checkpoint_tuple is None:
            return None

        persisted_state = dict(checkpoint_tuple.checkpoint.get("channel_values") or {})
        request = self._coerce_trip_request(persisted_state.get("request"))
        if request is not None:
            persisted_state["request"] = request

        session: State = {
            "request": request,
            "chat_history": list(persisted_state.get("chat_history") or []),
            "final_plan": persisted_state.get("final_plan") or {},
            "critic_scores": persisted_state.get("critic_scores"),
            "spending_tier": persisted_state.get("spending_tier"),
            "revision_count": persisted_state.get("revision_count", 0),
            "user_profile": persisted_state.get("user_profile"),
            "user_profile_context": persisted_state.get("user_profile_context"),
        }
        self.sessions[session_id] = session
        return session

    def _get_or_restore_session(self, session_id: str) -> Optional[State]:
        return self.sessions.get(session_id) or self._restore_session_from_checkpoint(session_id)

    #根据用户的旅行需求，生成一个空的、标准的旅行行程框架
    def _create_basic_plan(self, request: TripRequest) -> TripPlan:
        start = datetime.strptime(request.start_date, "%Y-%m-%d")
        days: List[DayPlan] = []

        for index in range(request.travel_days):
            day_date = (start + timedelta(days=index)).strftime("%Y-%m-%d")
            days.append(
                DayPlan(
                    date=day_date,
                    day_index=index,
                    description=f"{request.city} 第{index + 1}天基础行程",
                    transportation=request.transportation,
                    accommodation=request.accommodation,
                    attractions=[],
                    meals=[],
                )
            )

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=[],
            overall_suggestions="已生成基础行程。",
            exclusive_tips="暂无",
            budget=None,
        )

    def _create_demo_plan(self, request: TripRequest) -> TripPlan:
        start = datetime.strptime(request.start_date, "%Y-%m-%d")
        attraction_templates = self._get_city_attractions(request.city)
        hotel_cost = self._estimate_hotel_cost(request.spending_tier)
        meal_cost = self._estimate_meal_cost(request.spending_tier)
        days: List[DayPlan] = []

        for index in range(request.travel_days):
            day_date = (start + timedelta(days=index)).strftime("%Y-%m-%d")
            templates = attraction_templates[index * 2 : index * 2 + 2] or attraction_templates[:2]
            attractions = [
                self._build_attraction(template, attr_index)
                for attr_index, template in enumerate(templates)
            ]
            days.append(
                DayPlan(
                    date=day_date,
                    day_index=index,
                    description=f"{request.city}第{index + 1}天行程：围绕核心景点安排游览，节奏按{request.spending_tier}控制。",
                    transportation=request.transportation,
                    accommodation=request.accommodation,
                    hotel=self._build_hotel(request.city, request.accommodation, hotel_cost),
                    attractions=attractions,
                    meals=self._build_meals(request.city, meal_cost),
                )
            )

        total_attractions = sum(
            attraction.ticket_price
            for day in days
            for attraction in day.attractions
        )
        total_hotels = hotel_cost * request.travel_days
        total_meals = meal_cost * 3 * request.travel_days
        total_transportation = self._estimate_transportation_cost(
            request.transportation,
            request.travel_days,
        )

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=self._build_weather(request.start_date, request.travel_days),
            overall_suggestions=f"已生成{request.city}{request.travel_days}天基础行程，可继续通过对话调整景点、预算或节奏。",
            exclusive_tips="当前返回的是本地基础模板数据；后续接入真实 POI、天气和大模型后可生成更细的路线。",
            budget=Budget(
                total_attractions=total_attractions,
                total_hotels=total_hotels,
                total_meals=total_meals,
                total_transportation=total_transportation,
                total=total_attractions + total_hotels + total_meals + total_transportation,
            ),
        )

    def _get_city_attractions(self, city: str) -> List[Dict[str, Any]]:
        city_templates = {
            "北京": [
                ("故宫博物院", "北京市东城区景山前街4号", 116.397026, 39.918058, "明清皇家宫殿建筑群，适合历史文化主题游览。", 240, 60),
                ("景山公园", "北京市西城区景山西街44号", 116.396757, 39.925079, "登高俯瞰北京中轴线和故宫全景。", 90, 2),
                ("天坛公园", "北京市东城区天坛东路甲1号", 116.410886, 39.881949, "古代皇家祭天建筑群，空间开阔，适合慢游。", 180, 34),
                ("前门大街", "北京市东城区前门大街", 116.397957, 39.899181, "老字号餐饮和历史街区集中，适合晚间散步。", 120, 0),
                ("颐和园", "北京市海淀区新建宫门路19号", 116.275536, 39.999667, "皇家园林代表，昆明湖和万寿山景观丰富。", 240, 30),
                ("南锣鼓巷", "北京市东城区南锣鼓巷", 116.403242, 39.937149, "胡同街区与小店聚集，适合轻松逛吃。", 120, 0),
            ],
            "上海": [
                ("外滩", "上海市黄浦区中山东一路", 121.490317, 31.239692, "黄浦江经典城市天际线观景地。", 120, 0),
                ("豫园", "上海市黄浦区福佑路168号", 121.49291, 31.227235, "江南园林和城隍庙商圈，适合文化与美食体验。", 180, 40),
                ("上海博物馆", "上海市黄浦区人民大道201号", 121.475663, 31.229037, "馆藏文物丰富，适合文化主题行程。", 180, 0),
                ("陆家嘴", "上海市浦东新区陆家嘴", 121.507834, 31.243453, "现代城市地标区域，适合夜景与观景。", 120, 0),
            ],
        }
        fallback = [
            (f"{city}城市公园", f"{city}市中心区域", 116.397026, 39.918058, "适合作为抵达后的轻松游览点。", 120, 0),
            (f"{city}博物馆", f"{city}主城区", 116.407526, 39.90403, "了解当地历史文化的基础景点。", 150, 0),
            (f"{city}老街区", f"{city}老城区", 116.387026, 39.908058, "适合步行、拍照和体验本地餐饮。", 120, 0),
            (f"{city}观景地", f"{city}核心景区", 116.417026, 39.928058, "适合安排傍晚或夜景时段。", 90, 0),
        ]
        return [
            {
                "name": name,
                "address": address,
                "longitude": longitude,
                "latitude": latitude,
                "description": description,
                "visit_duration": visit_duration,
                "ticket_price": ticket_price,
            }
            for name, address, longitude, latitude, description, visit_duration, ticket_price
            in city_templates.get(city, fallback)
        ]

    def _build_attraction(self, template: Dict[str, Any], index: int) -> Attraction:
        return Attraction(
            name=template["name"],
            address=template["address"],
            location=Location(
                longitude=template["longitude"],
                latitude=template["latitude"],
            ),
            visit_duration=template["visit_duration"],
            description=template["description"],
            category="景点",
            rating=4.6 - min(index, 3) * 0.1,
            ticket_price=template["ticket_price"],
        )

    def _build_meals(self, city: str, meal_cost: int) -> List[Meal]:
        return [
            Meal(type="breakfast", name=f"{city}本地早餐", description="就近选择酒店周边早餐。", estimated_cost=meal_cost),
            Meal(type="lunch", name=f"{city}特色午餐", description="安排在当日景点附近，减少往返时间。", estimated_cost=meal_cost),
            Meal(type="dinner", name=f"{city}风味晚餐", description="结合夜间活动区域安排。", estimated_cost=meal_cost),
        ]

    def _build_hotel(self, city: str, accommodation: str, daily_cost: int) -> Hotel:
        return Hotel(
            name=f"{city}{accommodation}推荐酒店",
            address=f"{city}核心交通便利区域",
            location=None,
            price_range=f"约{daily_cost}元/晚",
            rating="4.5",
            distance="靠近地铁或主要景区",
            type=accommodation,
            estimated_cost=daily_cost,
        )

    def _build_weather(self, start_date: str, travel_days: int) -> List[WeatherInfo]:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        return [
            WeatherInfo(
                date=(start + timedelta(days=index)).strftime("%Y-%m-%d"),
                day_weather="多云",
                night_weather="晴",
                day_temp=24,
                night_temp=16,
                wind_direction="东北风",
                wind_power="1-3级",
            )
            for index in range(travel_days)
        ]

    def _estimate_hotel_cost(self, spending_tier: str) -> int:
        if "经济" in spending_tier:
            return 260
        if "奢" in spending_tier:
            return 1000
        return 520

    def _estimate_meal_cost(self, spending_tier: str) -> int:
        if "经济" in spending_tier:
            return 40
        if "奢" in spending_tier:
            return 180
        return 90

    def _estimate_transportation_cost(self, transportation: str, travel_days: int) -> int:
        if "自驾" in transportation or "打车" in transportation:
            return 180 * travel_days
        return 50 * travel_days

    def _extract_profile_update_heuristic(self, text: str) -> Dict[str, Any]:
        patch: Dict[str, Any] = {
            "diet_avoid": [],
            "travel_with": [],
            "avoid": [],
            "likes": [],
        }
        if not text:
            return patch

        diet_patterns = [
            r"不吃([\u4e00-\u9fa5A-Za-z0-9]{1,12})",
            r"不喜欢吃([\u4e00-\u9fa5A-Za-z0-9]{1,12})",
            r"对([\u4e00-\u9fa5A-Za-z0-9]{1,12})过敏",
            r"([\u4e00-\u9fa5A-Za-z0-9]{1,12})过敏",
        ]
        for pattern in diet_patterns:
            for match in re.findall(pattern, text):
                item = str(match).strip("，。,.、；; ")
                if item and item not in patch["diet_avoid"]:
                    patch["diet_avoid"].append(item)

        if any(word in text for word in ("带小孩", "带孩子", "亲子", "儿童", "宝宝")):
            patch["travel_with"].append("children")
            patch["avoid"].append("高强度徒步")
            patch["avoid"].append("太赶的行程")
            patch["likes"].append("亲子友好")
            patch["pace_preference"] = "relaxed"

        if any(word in text for word in ("带老人", "老人同行", "父母同行")):
            patch["travel_with"].append("elderly")
            patch["avoid"].append("高强度徒步")
            patch["avoid"].append("长时间步行")
            patch["pace_preference"] = "relaxed"

        if any(word in text for word in ("轻松", "慢一点", "别太累", "不要太累", "不想太赶", "别太赶", "午休")):
            patch["pace_preference"] = "relaxed"
            patch["avoid"].append("太赶的行程")

        if any(word in text for word in ("特种兵", "高强度", "徒步", "爬山")) and any(
            neg in text for neg in ("不", "别", "不要", "避免", "排雷")
        ):
            patch["avoid"].append("高强度徒步")

        if any(word in text for word in ("省钱", "便宜", "经济", "低预算", "穷游")):
            patch["budget_preference"] = "经济型"
        elif any(word in text for word in ("高端", "豪华", "奢侈")):
            patch["budget_preference"] = "奢侈型"

        if "民宿" in text:
            patch["hotel_preference"] = "民宿"
        elif "经济型酒店" in text:
            patch["hotel_preference"] = "经济型酒店"
        elif "豪华酒店" in text:
            patch["hotel_preference"] = "豪华酒店"

        return patch

    def _normalize_profile_update(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(raw, dict):
            return {}
        allowed_list_fields = ("diet_avoid", "travel_with", "avoid", "likes")
        allowed_scalar_fields = ("pace_preference", "hotel_preference", "budget_preference")
        normalized: Dict[str, Any] = {}
        for field in allowed_list_fields:
            values = raw.get(field, [])
            if isinstance(values, str):
                values = [values]
            if isinstance(values, list):
                normalized[field] = [
                    str(value).strip()
                    for value in values
                    if value is not None and str(value).strip()
                ][:10]
        for field in allowed_scalar_fields:
            value = raw.get(field)
            if isinstance(value, str) and value.strip():
                normalized[field] = value.strip()[:50]
        return normalized

    async def _extract_profile_update_with_llm(
        self,
        text: str,
        current_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not text.strip():
            return {}

        prompt = f"""
请从用户文本中抽取适合长期保存的旅行画像。只保存稳定偏好，不要保存目的地、日期、一次性安排。
只输出 JSON，不要输出解释、Markdown 或代码块。

字段：
- diet_avoid: 数组，用户明确不吃、忌口、过敏的食物
- travel_with: 数组，例如 children、elderly、couple、solo
- pace_preference: relaxed / normal / intense / null
- avoid: 数组，用户长期想避免的内容，例如高强度徒步、太赶的行程
- likes: 数组，用户长期喜欢的旅行内容，例如博物馆、亲子友好
- hotel_preference: 字符串或 null
- budget_preference: 经济型 / 舒适型 / 奢侈型 / null

当前画像：{json.dumps(current_profile, ensure_ascii=False)}
用户文本：{text}

输出示例：
{{"diet_avoid":["香菜"],"travel_with":["children"],"pace_preference":"relaxed","avoid":["高强度徒步"],"likes":["亲子友好"],"hotel_preference":null,"budget_preference":null}}
""".strip()

        raw_patch = await self._request_llm_json(
            system_prompt="你只负责抽取长期用户画像，并且只返回合法 JSON。",
            user_prompt=prompt,
            max_tokens=500,
        )
        return self._normalize_profile_update(raw_patch)

    def _merge_profile_patches(self, *patches: Dict[str, Any]) -> Dict[str, Any]:
        merged: Dict[str, Any] = {
            "diet_avoid": [],
            "travel_with": [],
            "avoid": [],
            "likes": [],
        }
        for patch in patches:
            patch = self._normalize_profile_update(patch)
            for key in ("diet_avoid", "travel_with", "avoid", "likes"):
                for value in patch.get(key, []):
                    if value not in merged[key]:
                        merged[key].append(value)
            for key in ("pace_preference", "hotel_preference", "budget_preference"):
                if patch.get(key):
                    merged[key] = patch[key]
        return merged

    def _to_location(self, raw_location: Any) -> Optional[Location]:
        if not isinstance(raw_location, dict):
            return None
        try:
            longitude = float(raw_location["longitude"])
            latitude = float(raw_location["latitude"])
        except (KeyError, TypeError, ValueError):
            return None
        return Location(longitude=longitude, latitude=latitude)

    def _to_float_rating(self, raw_rating: Any) -> Optional[float]:
        if raw_rating in (None, ""):
            return None
        try:
            return float(raw_rating)
        except (TypeError, ValueError):
            return None

    def _build_attraction_from_poi(self, poi: Dict[str, Any]) -> Optional[Attraction]:
        location = self._to_location(poi.get("location"))
        if location is None:
            return None

        poi_type = poi.get("type") or poi.get("category") or "景点"
        description = f"推荐游览 {poi.get('name', '该景点')}，类型为{poi_type}。"
        if poi.get("business_area"):
            description += f" 位于{poi['business_area']}附近。"

        return Attraction(
            name=poi.get("name") or "推荐景点",
            address=poi.get("address") or "地址待确认",
            location=location,
            visit_duration=120,
            description=description,
            category=poi.get("category") or "景点",
            rating=self._to_float_rating(poi.get("rating")),
            ticket_price=0,
            poi_id=poi.get("id") or "",
        )

    def _build_hotel_from_poi(self, poi: Dict[str, Any], request: TripRequest) -> Hotel:
        daily_cost = self._estimate_hotel_cost(request.spending_tier)
        rating = poi.get("rating")
        return Hotel(
            name=poi.get("name") or f"{request.city}{request.accommodation}推荐酒店",
            address=poi.get("address") or f"{request.city}核心交通便利区域",
            location=self._to_location(poi.get("location")),
            price_range=f"约{daily_cost}元/晚",
            rating=str(rating) if rating not in (None, "") else "待确认",
            distance=(poi.get("business_area") or "靠近主要景点和交通枢纽"),
            type=request.accommodation,
            estimated_cost=daily_cost,
        )

    def _build_meals_from_pois(
        self,
        city: str,
        meal_cost: int,
        restaurant_pois: List[Dict[str, Any]],
        day_index: int,
    ) -> List[Meal]:
        breakfast = Meal(
            type="breakfast",
            name=f"{city}本地早餐",
            description="就近选择酒店周边早餐。",
            estimated_cost=meal_cost,
        )
        if not restaurant_pois:
            return [breakfast, *self._build_meals(city, meal_cost)[1:]]

        lunch_poi = restaurant_pois[(day_index * 2) % len(restaurant_pois)]
        dinner_poi = restaurant_pois[(day_index * 2 + 1) % len(restaurant_pois)]
        return [
            breakfast,
            Meal(
                type="lunch",
                name=lunch_poi.get("name") or f"{city}特色午餐",
                address=lunch_poi.get("address"),
                location=self._to_location(lunch_poi.get("location")),
                description=f"午餐安排在{lunch_poi.get('business_area') or '当日活动区'}附近。",
                estimated_cost=meal_cost,
            ),
            Meal(
                type="dinner",
                name=dinner_poi.get("name") or f"{city}风味晚餐",
                address=dinner_poi.get("address"),
                location=self._to_location(dinner_poi.get("location")),
                description=f"晚餐结合{dinner_poi.get('business_area') or '夜间活动区'}安排。",
                estimated_cost=meal_cost,
            ),
        ]

    def _build_weather_info_list(
        self,
        request: TripRequest,
        weather_items: List[Dict[str, Any]],
    ) -> List[WeatherInfo]:
        if not weather_items:
            return self._build_weather(request.start_date, request.travel_days)

        fallback_weather = self._build_weather(request.start_date, request.travel_days)
        result: List[WeatherInfo] = []
        for index in range(request.travel_days):
            if index < len(weather_items):
                result.append(WeatherInfo(**weather_items[index]))
            else:
                result.append(fallback_weather[index])
        return result

    def _distance_km_between_locations(
        self,
        first: Optional[Location],
        second: Optional[Location],
    ) -> Optional[float]:
        if first is None or second is None:
            return None

        radius_km = 6371.0
        lat1 = math.radians(first.latitude)
        lat2 = math.radians(second.latitude)
        delta_lat = math.radians(second.latitude - first.latitude)
        delta_lon = math.radians(second.longitude - first.longitude)
        haversine = (
            math.sin(delta_lat / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
        )
        return 2 * radius_km * math.asin(math.sqrt(haversine))

    def _sort_attractions_by_geo(
        self,
        attractions: List[Attraction],
        start_location: Optional[Location],
    ) -> List[Attraction]:
        if len(attractions) <= 1:
            return attractions

        remaining = attractions[:]
        ordered: List[Attraction] = []
        current_location = start_location or remaining[0].location

        while remaining:
            nearest_index = min(
                range(len(remaining)),
                key=lambda index: self._distance_km_between_locations(
                    current_location,
                    remaining[index].location,
                )
                or float("inf"),
            )
            next_attraction = remaining.pop(nearest_index)
            ordered.append(next_attraction)
            current_location = next_attraction.location

        return ordered

    def _split_attractions_by_day(
        self,
        attractions: List[Attraction],
        travel_days: int,
    ) -> List[List[Attraction]]:
        groups: List[List[Attraction]] = []
        attraction_index = 0

        for day_index in range(travel_days):
            if attraction_index >= len(attractions):
                groups.append([])
                continue

            days_left = travel_days - day_index
            remaining = len(attractions) - attraction_index
            count_for_day = max(1, math.ceil(remaining / days_left))
            groups.append(attractions[attraction_index : attraction_index + count_for_day])
            attraction_index += count_for_day

        return groups

    def _restaurant_key(self, poi: Dict[str, Any]) -> str:
        return str(poi.get("id") or poi.get("name") or id(poi))

    def _nearest_restaurant_poi(
        self,
        restaurant_pois: List[Dict[str, Any]],
        target_location: Optional[Location],
        used_keys: set,
    ) -> Optional[Dict[str, Any]]:
        if not restaurant_pois:
            return None

        unused = [
            poi for poi in restaurant_pois if self._restaurant_key(poi) not in used_keys
        ]
        candidates = unused or restaurant_pois
        if target_location is None:
            return candidates[0]

        def sort_key(poi: Dict[str, Any]) -> float:
            distance = self._distance_km_between_locations(
                target_location,
                self._to_location(poi.get("location")),
            )
            return distance if distance is not None else float("inf")

        return min(candidates, key=sort_key)

    def _build_meal_from_poi(
        self,
        meal_type: str,
        poi: Dict[str, Any],
        meal_cost: int,
        anchor_name: str,
    ) -> Meal:
        business_area = poi.get("business_area") or anchor_name or "当日活动区域"
        return Meal(
            type=meal_type,
            name=poi.get("name") or ("午餐推荐" if meal_type == "lunch" else "晚餐推荐"),
            address=poi.get("address"),
            location=self._to_location(poi.get("location")),
            description=f"安排在{business_area}附近，减少当天绕路。",
            estimated_cost=meal_cost,
        )

    def _build_meals_near_day_attractions(
        self,
        city: str,
        meal_cost: int,
        restaurant_pois: List[Dict[str, Any]],
        day_attractions: List[Attraction],
        hotel: Optional[Hotel],
        used_restaurant_keys: set,
    ) -> List[Meal]:
        breakfast_area = hotel.name if hotel is not None else city
        breakfast = Meal(
            type="breakfast",
            name=f"{city}早餐",
            description=f"建议在{breakfast_area}或周边解决早餐。",
            estimated_cost=meal_cost,
        )
        if not restaurant_pois:
            return [breakfast, *self._build_meals(city, meal_cost)[1:]]

        lunch_anchor = day_attractions[0] if day_attractions else None
        dinner_anchor = day_attractions[-1] if day_attractions else None
        lunch_poi = self._nearest_restaurant_poi(
            restaurant_pois,
            lunch_anchor.location if lunch_anchor else None,
            used_restaurant_keys,
        )
        if lunch_poi is not None:
            used_restaurant_keys.add(self._restaurant_key(lunch_poi))

        dinner_target = (
            dinner_anchor.location
            if dinner_anchor
            else hotel.location if hotel is not None else None
        )
        dinner_poi = self._nearest_restaurant_poi(
            restaurant_pois,
            dinner_target,
            used_restaurant_keys,
        )
        if (
            lunch_poi is not None
            and dinner_poi is not None
            and self._restaurant_key(lunch_poi) == self._restaurant_key(dinner_poi)
            and len(restaurant_pois) > 1
        ):
            alternatives = [
                poi
                for poi in restaurant_pois
                if self._restaurant_key(poi) != self._restaurant_key(lunch_poi)
            ]
            dinner_poi = self._nearest_restaurant_poi(
                alternatives,
                dinner_target,
                set(),
            )
        if dinner_poi is not None:
            used_restaurant_keys.add(self._restaurant_key(dinner_poi))

        meals = [breakfast]
        if lunch_poi is not None:
            meals.append(
                self._build_meal_from_poi(
                    "lunch",
                    lunch_poi,
                    meal_cost,
                    lunch_anchor.name if lunch_anchor else city,
                )
            )
        if dinner_poi is not None:
            meals.append(
                self._build_meal_from_poi(
                    "dinner",
                    dinner_poi,
                    meal_cost,
                    dinner_anchor.name if dinner_anchor else breakfast_area,
                )
            )

        return meals

    def _build_day_description(
        self,
        request: TripRequest,
        day_index: int,
        day_attractions: List[Attraction],
        meals: List[Meal],
    ) -> str:
        attraction_names = "、".join(item.name for item in day_attractions[:3])
        meal_names = "、".join(
            meal.name for meal in meals if meal.type in {"lunch", "dinner"}
        )
        if attraction_names:
            return (
                f"{request.city}第{day_index + 1}天：按地理位置顺序游览{attraction_names}，"
                f"餐饮优先安排在当天景点附近"
                f"{'，推荐' + meal_names if meal_names else ''}。"
            )
        return (
            f"{request.city}第{day_index + 1}天：景点候选不足，安排轻松机动行程，"
            "可结合天气和体力补充附近休闲点。"
        )

    def _compact_plan_for_llm(self, plan: TripPlan) -> Dict[str, Any]:
        return {
            "city": plan.city,
            "start_date": plan.start_date,
            "end_date": plan.end_date,
            "days": [
                {
                    "day_index": day.day_index,
                    "date": day.date,
                    "description": day.description,
                    "attractions": [item.name for item in day.attractions],
                    "meals": [item.name for item in day.meals],
                    "hotel": day.hotel.name if day.hotel else "",
                }
                for day in plan.days
            ],
        }

    async def _refine_plan_structure_with_llm(
        self,
        request: TripRequest,
        plan: TripPlan,
        mcp_data: Dict[str, Any],
        profile_context: str = "",
    ) -> TripPlan:
        """Apply structural refinements without inventing new POIs."""
        free_text = getattr(request, "free_text_input", "") or ""
        if "用户精修要求：" not in free_text:
            return plan

        attraction_lookup = {
            attraction.name: attraction
            for day in plan.days
            for attraction in day.attractions
        }
        allowed_attraction_names = list(attraction_lookup.keys())
        if not allowed_attraction_names:
            return plan

        prompt = f"""
你是旅行行程结构精修助手。请根据用户精修要求，重新分配每天的景点结构。

要求：
1. 只输出 JSON，不要输出解释、Markdown 或代码块。
2. 只能使用“可用景点”中的名字，不能编造新景点。
3. 可以删除部分景点来降低强度，也可以把景点移动到其他天。
4. 不要重复安排同一个景点。
5. days 必须覆盖 0 到 {request.travel_days - 1} 的每一天。
6. 如果用户要求某天更轻松，应减少该天景点数量。
7. 如果用户没有要求改变某天，尽量保持该天原有安排。

城市：{request.city}
旅行天数：{request.travel_days}
用户偏好：{"、".join(getattr(request, "preferences", []) or []) or "无"}
用户补充需求：{free_text}
长期画像：{profile_context or "该用户暂无长期画像。"}
当前行程：{json.dumps(self._compact_plan_for_llm(plan), ensure_ascii=False)}
可用景点：{json.dumps(allowed_attraction_names, ensure_ascii=False)}

输出示例：
{{"days":[{{"day_index":0,"attractions":["故宫","景山公园"]}},{{"day_index":1,"attractions":["南锣鼓巷"]}}]}}
""".strip()

        raw_update = await self._request_llm_json(
            system_prompt="你只负责修改旅行行程结构，并且只返回合法 JSON。",
            user_prompt=prompt,
            max_tokens=900,
        )
        raw_days = raw_update.get("days") if isinstance(raw_update, dict) else None
        if not isinstance(raw_days, list):
            return plan

        day_assignments: Dict[int, List[Attraction]] = {}
        used_names: set[str] = set()

        for item in raw_days:
            if not isinstance(item, dict):
                continue
            try:
                day_index = int(item.get("day_index"))
            except (TypeError, ValueError):
                continue
            if day_index < 0 or day_index >= request.travel_days:
                continue

            raw_names = item.get("attractions")
            if not isinstance(raw_names, list):
                continue

            selected: List[Attraction] = []
            for raw_name in raw_names:
                if not isinstance(raw_name, str):
                    continue
                name = raw_name.strip()
                if not name or name in used_names or name not in attraction_lookup:
                    continue
                selected.append(attraction_lookup[name])
                used_names.add(name)
            day_assignments[day_index] = selected

        if not day_assignments:
            return plan

        refined_plan = plan.model_copy(deep=True)
        restaurant_pois = [
            poi
            for poi in ((mcp_data or {}).get("pois") or {}).get("restaurants", [])
            if isinstance(poi, dict)
        ]
        meal_cost = self._estimate_meal_cost(request.spending_tier)
        used_restaurant_keys: set = set()

        for day in refined_plan.days:
            day_attractions = day_assignments.get(day.day_index, day.attractions)
            day.attractions = day_attractions
            day.meals = self._build_meals_near_day_attractions(
                request.city,
                meal_cost,
                restaurant_pois,
                day_attractions,
                day.hotel,
                used_restaurant_keys,
            )
            day.description = self._build_day_description(
                request,
                day.day_index,
                day_attractions,
                day.meals,
            )

        total_attractions = sum(
            attraction.ticket_price
            for day in refined_plan.days
            for attraction in day.attractions
        )
        total_hotels = sum(
            day.hotel.estimated_cost
            for day in refined_plan.days
            if day.hotel is not None
        )
        total_meals = sum(
            meal.estimated_cost
            for day in refined_plan.days
            for meal in day.meals
        )
        total_transportation = self._estimate_transportation_cost(
            request.transportation,
            request.travel_days,
        )
        refined_plan.budget = Budget(
            total_attractions=total_attractions,
            total_hotels=total_hotels,
            total_meals=total_meals,
            total_transportation=total_transportation,
            total=(
                total_attractions
                + total_hotels
                + total_meals
                + total_transportation
            ),
        )
        return refined_plan

    async def _optimize_plan_text_with_llm(
        self,
        request: TripRequest,
        plan: TripPlan,
        mcp_data: Dict[str, Any],
        profile_context: str = "",
    ) -> TripPlan:
        prefs_text = "、".join(getattr(request, "preferences", []) or []) or "无"
        free_text = getattr(request, "free_text_input", "") or "无"
        weather_payload = (mcp_data or {}).get("weather") or []
        prompt = f"""
你是旅行行程编排助手。请只优化每天行程描述和整体建议，不要新增、删除、改名或重排任何 POI。

要求：
1. 只输出 JSON，不要输出解释、Markdown 或代码块。
2. JSON 可以包含 overall_suggestions、exclusive_tips、days。
3. days 是数组，每项包含 day_index 和 description。
4. description 要体现上午/下午/晚上节奏、路线顺序、就近用餐和交通节奏。
5. 不要编造不在输入中的景点、餐厅、酒店名称。
6. 如果天气或接口 warning 信息不足，只做温和提示。

城市：{request.city}
旅行天数：{request.travel_days}
交通方式：{request.transportation}
住宿偏好：{request.accommodation}
消费层级：{request.spending_tier}
用户偏好：{prefs_text}
用户补充需求：{free_text}
长期画像：{profile_context or "该用户暂无长期画像。"}
天气：{json.dumps(weather_payload, ensure_ascii=False)}
当前行程：{json.dumps(self._compact_plan_for_llm(plan), ensure_ascii=False)}

输出示例：
{{"overall_suggestions":"...","exclusive_tips":"...","days":[{{"day_index":0,"description":"..."}}]}}
""".strip()

        raw_update = await self._request_llm_json(
            system_prompt="你只负责优化旅行行程文案，并且只返回合法 JSON。",
            user_prompt=prompt,
            max_tokens=1200,
        )
        if not raw_update:
            return plan

        day_updates = raw_update.get("days")
        if isinstance(day_updates, list):
            descriptions: Dict[int, str] = {}
            for item in day_updates:
                if not isinstance(item, dict):
                    continue
                try:
                    day_index = int(item.get("day_index"))
                except (TypeError, ValueError):
                    continue
                description = item.get("description")
                if isinstance(description, str) and description.strip():
                    descriptions[day_index] = description.strip()[:500]

            for day in plan.days:
                if day.day_index in descriptions:
                    day.description = descriptions[day.day_index]

        overall_suggestions = raw_update.get("overall_suggestions")
        if isinstance(overall_suggestions, str) and overall_suggestions.strip():
            plan.overall_suggestions = overall_suggestions.strip()[:800]

        exclusive_tips = raw_update.get("exclusive_tips")
        if isinstance(exclusive_tips, str) and exclusive_tips.strip():
            plan.exclusive_tips = exclusive_tips.strip()[:800]

        return plan

    def _create_plan_from_mcp_data(self, request: TripRequest, mcp_data: Dict[str, Any]) -> Optional[TripPlan]:
        poi_groups = (mcp_data or {}).get("pois") or {}
        raw_attractions = poi_groups.get("attractions") or []
        attractions = [
            attraction
            for attraction in (
                self._build_attraction_from_poi(poi)
                for poi in raw_attractions
                if isinstance(poi, dict)
            )
            if attraction is not None
        ]
        if not attractions:
            return None

        raw_hotels = poi_groups.get("hotels") or []
        hotel_poi = next((poi for poi in raw_hotels if isinstance(poi, dict)), {})
        hotel = self._build_hotel_from_poi(hotel_poi, request)
        attractions = self._sort_attractions_by_geo(attractions, hotel.location)
        restaurant_pois = [
            poi for poi in (poi_groups.get("restaurants") or []) if isinstance(poi, dict)
        ]

        start = datetime.strptime(request.start_date, "%Y-%m-%d")
        meal_cost = self._estimate_meal_cost(request.spending_tier)
        days: List[DayPlan] = []
        attractions_by_day = self._split_attractions_by_day(
            attractions,
            request.travel_days,
        )
        used_restaurant_keys: set = set()

        for day_index in range(request.travel_days):
            day_date = (start + timedelta(days=day_index)).strftime("%Y-%m-%d")
            day_attractions = (
                attractions_by_day[day_index]
                if day_index < len(attractions_by_day)
                else []
            )
            day_meals = self._build_meals_near_day_attractions(
                request.city,
                meal_cost,
                restaurant_pois,
                day_attractions,
                hotel,
                used_restaurant_keys,
            )

            attraction_names = "、".join(item.name for item in day_attractions[:3])
            days.append(
                DayPlan(
                    date=day_date,
                    day_index=day_index,
                    description=(
                        f"{request.city}第{day_index + 1}天行程："
                        f"重点游览{attraction_names}，节奏按{request.spending_tier}控制。"
                    ),
                    transportation=request.transportation,
                    accommodation=request.accommodation,
                    hotel=hotel,
                    attractions=day_attractions,
                    meals=day_meals,
                )
            )
            days[-1].description = self._build_day_description(
                request,
                day_index,
                day_attractions,
                day_meals,
            )

        weather_info = self._build_weather_info_list(
            request,
            (mcp_data or {}).get("weather") or [],
        )
        total_attractions = sum(
            attraction.ticket_price
            for day in days
            for attraction in day.attractions
        )
        total_hotels = hotel.estimated_cost * request.travel_days
        total_meals = meal_cost * 3 * request.travel_days
        total_transportation = self._estimate_transportation_cost(
            request.transportation,
            request.travel_days,
        )
        warnings = (mcp_data or {}).get("warnings") or []
        exclusive_tips = "；".join(warnings[:3]) if warnings else "已优先使用真实 POI 与天气信息生成行程。"

        return TripPlan(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            days=days,
            weather_info=weather_info,
            overall_suggestions=(
                f"已基于{request.city}真实 POI 候选生成{request.travel_days}天行程，"
                "可继续通过对话调整景点、预算或节奏。"
            ),
            exclusive_tips=exclusive_tips,
            budget=Budget(
                total_attractions=total_attractions,
                total_hotels=total_hotels,
                total_meals=total_meals,
                total_transportation=total_transportation,
                total=total_attractions + total_hotels + total_meals + total_transportation,
            ),
        )

    #从用户当前请求和最近一句话里，提取长期偏好，并更新用户画像。
    async def _node_profile_update(self, state: Dict[str, Any]):
        request = state["request"]
        user_id = state.get("user_id") or getattr(request, "user_id", "default_guest")
        current_profile = self.profile_memory.get_profile(user_id)

        message_parts = [
            " ".join(getattr(request, "preferences", []) or []),
            getattr(request, "free_text_input", "") or "",
        ]
        chat_history = state.get("chat_history") or []
        if chat_history:
            last_message = chat_history[-1]
            if isinstance(last_message, dict) and last_message.get("role") == "human":
                message_parts.append(str(last_message.get("content", "")))

        profile_source_text = "\n".join(part for part in message_parts if part).strip()
        heuristic_patch = self._extract_profile_update_heuristic(profile_source_text)
        llm_patch = await self._extract_profile_update_with_llm(profile_source_text, current_profile)
        patch = self._merge_profile_patches(heuristic_patch, llm_patch)

        if self.profile_memory.has_profile_data(patch):
            current_profile = self.profile_memory.update_profile(user_id, patch)

        profile_context = self.profile_memory.format_profile(current_profile)
        return {
            "user_profile": current_profile,
            "user_profile_context": profile_context,
        }

    #先回忆用户历史偏好，再去知识库检索相关旅行内容，把两种上下文补进 state
    async def _node_knowledge_retrieval(self, state: Dict[str, Any]):
        request = state["request"]
        user_id = state.get("user_id", "default_guest")
        city = getattr(request, "city", "")
        prefs = getattr(request, "preferences", [])
        free_text = getattr(request, "free_text_input", "")
        profile_context = state.get("user_profile_context") or self.profile_memory.format_profile(
            self.profile_memory.get_profile(user_id)
        )

        #基于用户当前需求的文本描述，捞取本人历史上最相关的吐槽或经验。（向量检索）
        user_memory = self.episodic_memory.recall_lessons(
            user_id, f"去{city}，偏好:{prefs}，要求:{free_text}，画像:{profile_context}"
        )

        query_text = f"{city} 景点 餐厅 酒店 {' '.join(prefs)} {free_text} {profile_context} {user_memory}"
        #基于用户需求文本和历史记忆，从知识库捞取相关的旅行内容（RAG检索）
        results = await self.semantic_memory.search_knowledge_async(query_text, k=4)
        rag_knowledge = "\n\n".join(content for _, content in results)

        return {
            "rag_knowledge": rag_knowledge,
            "user_memory": user_memory,
            "user_profile_context": profile_context,
        }

    def _fallback_pois(self, request: TripRequest) -> Dict[str, List[str]]:
        city = request.city
        prefs_text = " ".join(getattr(request, "preferences", []) or [])
        free_text = getattr(request, "free_text_input", "") or ""
        user_text = f"{prefs_text} {free_text}"

        attractions = [f"{city}城市地标", f"{city}历史文化街区", f"{city}夜景打卡点"]
        if any(word in user_text for word in ("亲子", "儿童", "孩子", "迪士尼", "乐园")):
            attractions.insert(0, f"{city}亲子主题乐园")
        if any(word in user_text for word in ("博物馆", "历史", "文化", "展览")):
            attractions.insert(0, f"{city}博物馆")
        if any(word in user_text for word in ("自然", "公园", "徒步", "风景")):
            attractions.insert(0, f"{city}公园绿地")

        restaurants = [f"{city}本地小吃", f"{city}特色餐厅", f"{city}老字号餐馆"]
        hotels = [f"{city}{request.accommodation}", f"{city}交通便利酒店", f"{city}核心景区周边住宿"]
        return {"attractions": attractions[:5], "restaurants": restaurants[:5], "hotels": hotels[:5]}

    def _append_unique_terms(self, terms: List[str], values: List[str], limit: int) -> List[str]:
        for value in values:
            if value and value not in terms:
                terms.append(value)
            if len(terms) >= limit:
                break
        return terms

    def _build_poi_search_terms(self, request: TripRequest, context: str) -> Dict[str, List[str]]:
        prefs_text = " ".join(getattr(request, "preferences", []) or [])
        free_text = getattr(request, "free_text_input", "") or ""
        user_text = f"{prefs_text} {free_text} {context}"

        attractions: List[str] = []
        restaurants: List[str] = []
        hotels: List[str] = []

        if any(word in user_text for word in ("博物馆", "历史", "文化", "展览", "人文")):
            self._append_unique_terms(attractions, ["博物馆", "历史文化景点"], 5)
        if any(word in user_text for word in ("亲子", "儿童", "孩子", "乐园", "迪士尼", "children")):
            self._append_unique_terms(attractions, ["亲子乐园", "主题乐园"], 5)
        avoid_hiking = any(word in user_text for word in ("避免：高强度徒步", "需要避免：高强度徒步", "避免高强度徒步", "不要徒步", "别徒步"))
        nature_positive = any(word in user_text for word in ("自然", "公园", "风景", "山水")) or (
            "徒步" in user_text and not avoid_hiking
        )
        if nature_positive:
            self._append_unique_terms(attractions, ["公园", "自然风景区"], 5)
        if any(word in user_text for word in ("夜景", "夜游", "晚上", "灯光")):
            self._append_unique_terms(attractions, ["夜景", "观景台"], 5)
        if any(word in user_text for word in ("古镇", "古街", "老街", "街区", "胡同")):
            self._append_unique_terms(attractions, ["古街", "历史街区"], 5)

        self._append_unique_terms(attractions, ["热门景点", "风景名胜"], 5)

        if any(word in user_text for word in ("美食", "小吃", "吃", "本地", "特色")):
            self._append_unique_terms(restaurants, ["本地小吃", "特色餐厅"], 5)
        if any(word in user_text for word in ("老字号", "传统", "经典")):
            self._append_unique_terms(restaurants, ["老字号餐厅"], 5)
        if "火锅" in user_text:
            self._append_unique_terms(restaurants, ["火锅"], 5)
        if any(word in user_text for word in ("咖啡", "下午茶")):
            self._append_unique_terms(restaurants, ["咖啡馆", "下午茶"], 5)

        self._append_unique_terms(restaurants, ["热门餐厅", "本地菜"], 5)

        self._append_unique_terms(
            hotels,
            [
                request.accommodation,
                "交通便利酒店",
                "景区附近酒店",
            ],
            3,
        )

        return {
            "attractions": attractions[:5],
            "restaurants": restaurants[:5],
            "hotels": hotels[:3],
        }

    def _extract_json_object(self, text: str) -> Dict[str, Any]:
        decoder = json.JSONDecoder()
        start = text.find("{")
        while start != -1:
            try:
                value, _ = decoder.raw_decode(text[start:])
            except json.JSONDecodeError:
                start = text.find("{", start + 1)
                continue
            return value if isinstance(value, dict) else {}
        return {}

    async def _request_llm_json(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 700,
    ) -> Dict[str, Any]:
        api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = (
            os.getenv("LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or "https://api.openai.com/v1"
        ).rstrip("/")
        model = os.getenv("LLM_MODEL_ID") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"

        if not api_key:
            return {}

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }
        timeout = float(os.getenv("LLM_TIMEOUT", "30"))

        started_at = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                content = data["choices"][0]["message"]["content"]


        return self._extract_json_object(content)

    #大模型输出的安全过滤器
    def _normalize_poi_search_terms(
        self,
        raw_terms: Dict[str, Any],
        fallback_terms: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        limits = {
            "attractions": 5,
            "restaurants": 5,
            "hotels": 3,
        }
        normalized: Dict[str, List[str]] = {}

        for group, limit in limits.items():
            values = raw_terms.get(group, [])
            if not isinstance(values, list):
                values = []

            terms: List[str] = []
            for value in values:
                if not isinstance(value, str):
                    continue
                term = value.strip().strip("，,;；")
                if not term:
                    continue
                if len(term) > 20:
                    term = term[:20]
                self._append_unique_terms(terms, [term], limit)

            self._append_unique_terms(terms, fallback_terms.get(group, []), limit)
            normalized[group] = terms[:limit]

        return normalized

    async def _build_poi_search_terms_with_llm(
        self,
        request: TripRequest,
        context: str,
    ) -> Dict[str, List[str]]:
        fallback_terms = self._build_poi_search_terms(request, context)
        prefs_text = "、".join(getattr(request, "preferences", []) or []) or "无"
        free_text = getattr(request, "free_text_input", "") or "无"
        prompt = f"""
你是旅行 POI 搜索词生成器。请根据用户需求生成适合地图 POI 检索的关键词。

要求：
1. 只输出 JSON，不要输出解释、Markdown 或代码块。
2. JSON 必须包含 attractions、restaurants、hotels 三个数组。
3. attractions 最多 5 个，restaurants 最多 5 个，hotels 最多 3 个。
4. 关键词要适合在地图服务里搜索，例如“博物馆”“历史街区”“本地小吃”“交通便利酒店”。
5. 不要输出完整句子，不要输出不存在或过细的店名，除非用户明确指定。
6. 如果用户表达了“不想去”“避开”等否定偏好，不要生成对应关键词。

城市：{request.city}
住宿偏好：{request.accommodation}
消费层级：{request.spending_tier}
用户偏好：{prefs_text}
用户补充需求：{free_text}
参考上下文：{context or "无"}

输出示例：
{{"attractions":["博物馆","历史文化景点"],"restaurants":["本地小吃"],"hotels":["交通便利酒店"]}}
""".strip()

        raw_terms = await self._request_llm_json(
            system_prompt="你只负责把旅行需求转换成地图 POI 搜索关键词，并且只返回合法 JSON。",
            user_prompt=prompt,
            max_tokens=500,
        )
        if not raw_terms:
            return fallback_terms

        return self._normalize_poi_search_terms(raw_terms, fallback_terms)

    #根据前面生成的搜索关键词，去高德地图批量查询候选 POI，并进行简单的去重和过滤，最终返回给大模型做选择
    async def _search_amap_poi_candidates(
        self,
        request: TripRequest,
        search_terms: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        city = request.city
        api_key = os.getenv("AMAP_API_KEY", "")
        candidates: Dict[str, List[Dict[str, Any]]] = {
            "attractions": [],
            "restaurants": [],
            "hotels": [],
        }
        warnings: List[str] = []

        if not api_key:
            warnings.append("AMAP_API_KEY 未配置，无法查询真实 POI 候选。")
            return {"candidates": candidates, "warnings": warnings}

        async with httpx.AsyncClient(timeout=6.0) as client:
            poi_tasks = []
            poi_keys = []
            for group, terms in search_terms.items():
                per_term_limit = 3 if group != "hotels" else 2
                for keyword in terms:
                    poi_keys.append(group)
                    poi_tasks.append(
                        self._search_amap_pois(
                            client,
                            api_key,
                            city,
                            keyword,
                            group,
                            per_term_limit,
                        )
                    )

            poi_batches = await asyncio.gather(*poi_tasks) if poi_tasks else []
            seen_by_group: Dict[str, set] = {
                "attractions": set(),
                "restaurants": set(),
                "hotels": set(),
            }
            candidate_limits = {
                "attractions": 12,
                "restaurants": 12,
                "hotels": 6,
            }

            for group, batch in zip(poi_keys, poi_batches):
                if batch.get("warning"):
                    warnings.append(batch["warning"])
                for poi_info in batch.get("items", []):
                    dedupe_key = poi_info.get("id") or poi_info.get("name")
                    if dedupe_key in seen_by_group.setdefault(group, set()):
                        continue
                    if len(candidates.setdefault(group, [])) >= candidate_limits.get(group, 10):
                        continue
                    candidates[group].append(poi_info)
                    seen_by_group[group].add(dedupe_key)

        return {"candidates": candidates, "warnings": warnings}

    def _normalize_selected_pois(
        self,
        raw_selection: Dict[str, Any],
        candidates: Dict[str, List[Dict[str, Any]]],
        fallback_pois: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        selected_names: Dict[str, List[str]] = {}
        selected_details: Dict[str, List[Dict[str, Any]]] = {}

        for group, limit in _POI_RESULT_LIMITS.items():
            group_candidates = candidates.get(group, [])
            selected_group_details: List[Dict[str, Any]] = []
            selected_keys: set = set()
            refs = raw_selection.get(group, [])
            if not isinstance(refs, list):
                refs = []

            by_id = {
                item.get("id"): item
                for item in group_candidates
                if item.get("id")
            }
            by_name = {
                item.get("name"): item
                for item in group_candidates
                if item.get("name")
            }

            for ref in refs:
                if isinstance(ref, dict):
                    ref_text = str(ref.get("id") or ref.get("name") or "").strip()
                elif isinstance(ref, str):
                    ref_text = ref.strip()
                else:
                    continue
                if not ref_text:
                    continue

                item = by_id.get(ref_text) or by_name.get(ref_text)
                if item is None:
                    item = next(
                        (
                            candidate
                            for candidate in group_candidates
                            if ref_text in str(candidate.get("name", ""))
                        ),
                        None,
                    )
                if item is None:
                    continue

                dedupe_key = item.get("id") or item.get("name")
                if dedupe_key in selected_keys:
                    continue
                selected_group_details.append(item)
                selected_keys.add(dedupe_key)
                if len(selected_group_details) >= limit:
                    break

            for item in group_candidates:
                if len(selected_group_details) >= limit:
                    break
                dedupe_key = item.get("id") or item.get("name")
                if dedupe_key in selected_keys:
                    continue
                selected_group_details.append(item)
                selected_keys.add(dedupe_key)

            selected_details[group] = selected_group_details[:limit]
            if selected_group_details:
                selected_names[group] = [
                    item.get("name", "")
                    for item in selected_group_details[:limit]
                    if item.get("name")
                ]
            else:
                selected_names[group] = fallback_pois.get(group, [])[:limit]

        return {
            "selected_pois": selected_names,
            "selected_poi_details": selected_details,
        }

    async def _select_pois_with_llm(
        self,
        request: TripRequest,
        context: str,
        candidates: Dict[str, List[Dict[str, Any]]],
        fallback_pois: Dict[str, List[str]],
    ) -> Dict[str, Any]:
        if not any(candidates.get(group) for group in _POI_RESULT_LIMITS):
            return {
                "selected_pois": {
                    group: fallback_pois.get(group, [])[:limit]
                    for group, limit in _POI_RESULT_LIMITS.items()
                },
                "selected_poi_details": {
                    "attractions": [],
                    "restaurants": [],
                    "hotels": [],
                },
            }

        candidate_payload = {}
        for group, items in candidates.items():
            candidate_payload[group] = [
                {
                    "id": item.get("id") or "",
                    "name": item.get("name") or "",
                    "type": item.get("type") or "",
                    "address": item.get("address") or "",
                    "rating": item.get("rating"),
                    "business_area": item.get("business_area") or "",
                }
                for item in items
            ]

        prefs_text = "、".join(getattr(request, "preferences", []) or []) or "无"
        free_text = getattr(request, "free_text_input", "") or "无"
        prompt = f"""
你是旅行 POI 筛选器。请只从候选 POI 中选择最符合用户需求的地点。

要求：
1. 只输出 JSON，不要输出解释、Markdown 或代码块。
2. JSON 必须包含 attractions、restaurants、hotels 三个数组。
3. 数组里只放候选 POI 的 id；如果某个候选没有 id，则放它的 name。
4. attractions 最多 5 个，restaurants 最多 5 个，hotels 最多 3 个。
5. 优先匹配用户偏好、补充需求、交通便利性、评分和地址合理性。
6. 不要选择候选列表之外的 POI。

城市：{request.city}
住宿偏好：{request.accommodation}
消费层级：{request.spending_tier}
用户偏好：{prefs_text}
用户补充需求：{free_text}
参考上下文：{context or "无"}

候选 POI：
{json.dumps(candidate_payload, ensure_ascii=False)}

输出示例：
{{"attractions":["B000A8UIN8"],"restaurants":["本地小吃店"],"hotels":["B0FFG12345"]}}
""".strip()

        raw_selection = await self._request_llm_json(
            system_prompt="你只负责从候选旅行 POI 中做筛选，并且只返回合法 JSON。",
            user_prompt=prompt,
            max_tokens=700,
        )
        return self._normalize_selected_pois(raw_selection, candidates, fallback_pois)

    #用大模型理解用户想要什么，查询高德候选，再筛选出真实 POI
    async def _node_poi_selector(self, state: Dict[str, Any]):
        request = state["request"]
        rag_knowledge = state.get("rag_knowledge", "")
        user_memory = state.get("user_memory", "")
        profile_context = state.get("user_profile_context", "")

        context = "\n".join(part for part in (profile_context, rag_knowledge, user_memory) if part)
        fallback = self._fallback_pois(request)   #当大模型不可用或输出不合理时的兜底选项
        search_terms = await self._build_poi_search_terms_with_llm(request, context)
        candidate_result = await self._search_amap_poi_candidates(request, search_terms)
        #重排序 + 筛选
        selection = await self._select_pois_with_llm(
            request,
            context,
            candidate_result["candidates"],
            fallback,
        )

        return {
            "poi_search_terms": search_terms,
            "poi_candidates": candidate_result["candidates"],
            "selected_pois": selection["selected_pois"],
            "selected_poi_details": selection["selected_poi_details"],
            "poi_selection_warnings": candidate_result["warnings"],
        }

    def _build_basic_poi_info(self, name: str, city: str, group: str) -> Dict[str, Any]:
        category = _POI_CATEGORY_LABELS.get(group, "POI")
        return {
            "id": "",
            "name": name,
            "category": category,
            "type": category,
            "address": f"{city}，具体地址待确认",
            "location": None,
            "tel": None,
            "rating": None,
            "business_area": "",
            "source": "fallback",
        }

    def _format_amap_poi(self, raw: Dict[str, Any], fallback_name: str, city: str, group: str) -> Dict[str, Any]:
        location = None
        raw_location = raw.get("location") or ""
        if "," in raw_location:
            longitude, latitude = raw_location.split(",", 1)
            try:
                location = {
                    "longitude": float(longitude),
                    "latitude": float(latitude),
                }
            except ValueError:
                location = None

        biz_ext = raw.get("biz_ext")
        rating = biz_ext.get("rating") if isinstance(biz_ext, dict) else None

        return {
            "id": raw.get("id", ""),
            "name": raw.get("name") or fallback_name,
            "category": _POI_CATEGORY_LABELS.get(group, "POI"),
            "type": raw.get("type") or _POI_CATEGORY_LABELS.get(group, "POI"),
            "address": raw.get("address") or f"{city}，具体地址待确认",
            "location": location,
            "tel": raw.get("tel") or None,
            "rating": rating,
            "business_area": raw.get("business_area", ""),
            "source": "amap",
        }

    async def _search_amap_pois(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        city: str,
        keyword: str,
        group: str,
        limit: int,
    ) -> Dict[str, Any]:
        params = {
            "key": api_key,
            "keywords": keyword,
            "city": city,
            "citylimit": "true",
            "offset": limit,
            "page": 1,
            "extensions": "all",
        }
        poi_type = _POI_TYPE_KEYWORDS.get(group)
        if poi_type:
            params["types"] = poi_type

        try:
            response = await client.get(_AMAP_PLACE_SEARCH_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return {"items": [], "warning": f"{keyword}: 高德 POI 查询失败: {exc}"}

        pois = payload.get("pois") or []
        if payload.get("status") == "1" and pois:
            return {
                "items": [
                    self._format_amap_poi(poi, keyword, city, group)
                    for poi in pois[:limit]
                ],
                "warning": "",
            }

        return {
            "items": [],
            "warning": f"{keyword}: {payload.get('info') or '未查询到匹配 POI'}",
        }

    async def _fetch_amap_weather(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        request: TripRequest,
    ) -> List[Dict[str, Any]]:
        params = {
            "key": api_key,
            "city": request.city,
            "extensions": "all",
        }
        try:
            response = await client.get(_AMAP_WEATHER_URL, params=params)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return [item.model_dump() for item in self._build_weather(request.start_date, request.travel_days)]

        forecasts = payload.get("forecasts") or []
        casts = forecasts[0].get("casts") if forecasts else []
        if payload.get("status") != "1" or not casts:
            return [item.model_dump() for item in self._build_weather(request.start_date, request.travel_days)]

        weather: List[Dict[str, Any]] = []
        for index in range(request.travel_days):
            if index < len(casts):
                cast = casts[index]
                weather.append(
                    {
                        "date": cast.get("date", ""),
                        "day_weather": cast.get("dayweather", ""),
                        "night_weather": cast.get("nightweather", ""),
                        "day_temp": cast.get("daytemp", 0),
                        "night_temp": cast.get("nighttemp", 0),
                        "wind_direction": cast.get("daywind", ""),
                        "wind_power": cast.get("daypower", ""),
                    }
                )
            else:
                fallback = self._build_weather(request.start_date, request.travel_days)[index]
                weather.append(fallback.model_dump())
        return weather

    async def _node_gather_info(self, state: Dict[str, Any]):
        request = state["request"]
        city = request.city
        selected_pois = state.get("selected_pois") or self._fallback_pois(request)
        selected_poi_details = state.get("selected_poi_details") or {}
        search_terms = state.get("poi_search_terms") or selected_pois
        api_key = os.getenv("AMAP_API_KEY", "")

        mcp_data: Dict[str, Any] = {
            "city": city,
            "pois": {
                "attractions": [],
                "restaurants": [],
                "hotels": [],
            },
            "weather": [],
            "warnings": [],
        }

        for warning in state.get("poi_selection_warnings") or []:
            if warning:
                mcp_data["warnings"].append(warning)

        if any(selected_poi_details.get(group) for group in _POI_RESULT_LIMITS):
            for group, limit in _POI_RESULT_LIMITS.items():
                details = selected_poi_details.get(group) or []
                if details:
                    mcp_data["pois"][group] = details[:limit]
                    continue
                mcp_data["pois"][group] = [
                    self._build_basic_poi_info(name, city, group)
                    for name in selected_pois.get(group, [])[:limit]
                ]

            if api_key:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    mcp_data["weather"] = await self._fetch_amap_weather(client, api_key, request)
            else:
                mcp_data["weather"] = [
                    item.model_dump() for item in self._build_weather(request.start_date, request.travel_days)
                ]
                mcp_data["warnings"].append("AMAP_API_KEY 未配置，天气已使用本地兜底信息。")
            return {"mcp_data": mcp_data}

        if not api_key:
            for group, names in selected_pois.items():
                mcp_data["pois"][group] = [
                    self._build_basic_poi_info(name, city, group)
                    for name in names
                ]
            mcp_data["weather"] = [
                item.model_dump() for item in self._build_weather(request.start_date, request.travel_days)
            ]
            mcp_data["warnings"].append("AMAP_API_KEY 未配置，已使用本地兜底信息。")")
            return {"mcp_data": mcp_data}

        async with httpx.AsyncClient(timeout=6.0) as client:
            poi_tasks = []
            poi_keys = []
            for group, terms in search_terms.items():
                per_term_limit = 3 if group != "hotels" else 2
                for name in terms:
                    poi_keys.append(group)
                    poi_tasks.append(
                        self._search_amap_pois(client, api_key, city, name, group, per_term_limit)
                    )

            poi_batches = await asyncio.gather(*poi_tasks) if poi_tasks else []
            seen_by_group: Dict[str, set] = {
                "attractions": set(),
                "restaurants": set(),
                "hotels": set(),
            }
            for group, batch in zip(poi_keys, poi_batches):
                if batch.get("warning"):
                    mcp_data["warnings"].append(batch["warning"])
                for poi_info in batch.get("items", []):
                    dedupe_key = poi_info.get("id") or poi_info.get("name")
                    if dedupe_key in seen_by_group.setdefault(group, set()):
                        continue
                    if len(mcp_data["pois"].setdefault(group, [])) >= _POI_RESULT_LIMITS.get(group, 5):
                        continue
                    mcp_data["pois"][group].append(poi_info)
                    seen_by_group[group].add(dedupe_key)

            for group, names in selected_pois.items():
                if not mcp_data["pois"].get(group):
                    mcp_data["pois"][group] = [
                        self._build_basic_poi_info(name, city, group)
                        for name in names[: _POI_RESULT_LIMITS.get(group, 5)]
                    ]
                    mcp_data["warnings"].append(f"{_POI_CATEGORY_LABELS.get(group, group)}未查到高德结果，已使用本地兜底。")

            mcp_data["weather"] = await self._fetch_amap_weather(client, api_key, request)

        return {"mcp_data": mcp_data}

    async def _node_planner(self, state: Dict[str, Any]):
        request = state["request"]
        mcp_data = state.get("mcp_data") or {}
        profile_context = state.get("user_profile_context", "")
        plan = self._create_plan_from_mcp_data(request, mcp_data)
        if plan is None:
            plan = self._create_demo_plan(request)
        plan = await self._refine_plan_structure_with_llm(request, plan, mcp_data, profile_context)
        plan = await self._optimize_plan_text_with_llm(request, plan, mcp_data, profile_context)
        return {"final_plan": plan.model_dump()}

    def _build_rule_based_critic_scores(
        self,
        request: TripRequest,
        plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        geo_score = 10
        budget_score = 10
        preference_score = 10
        issues: List[str] = []

        days = plan.get("days") or []
        for day in days:
            attractions = day.get("attractions") or []
            if len(attractions) > 3:
                geo_score -= len(attractions) - 3
                issues.append(f"第{day.get('day_index', 0) + 1}天景点数量偏多")

            locations = [
                self._to_location(attraction.get("location"))
                for attraction in attractions
                if isinstance(attraction, dict)
            ]
            for first, second in zip(locations, locations[1:]):
                distance = self._distance_km_between_locations(first, second)
                if distance is None:
                    geo_score -= 1
                    issues.append("部分景点缺少有效经纬度，路线合理性待确认")
                elif distance > 30:
                    geo_score -= 3
                    issues.append(f"同日景点间距离约{distance:.1f}公里，跨度过大")
                elif distance > 15:
                    geo_score -= 2
                    issues.append(f"同日景点间距离约{distance:.1f}公里，建议优化顺序")
                elif distance > 8:
                    geo_score -= 1

        budget = plan.get("budget") or {}
        total_budget = budget.get("total")
        if request.budget and isinstance(total_budget, (int, float)):
            if total_budget > request.budget * 1.2:
                budget_score -= 4
                issues.append(f"预估费用{int(total_budget)}元明显超过预算{request.budget}元")
            elif total_budget > request.budget:
                budget_score -= 2
                issues.append(f"预估费用{int(total_budget)}元略高于预算{request.budget}元")
        elif request.budget and total_budget is None:
            budget_score -= 1
            issues.append("缺少总预算估算，预算匹配度待确认")

        if "经济" in request.spending_tier:
            hotel_costs = [
                day.get("hotel", {}).get("estimated_cost")
                for day in days
                if isinstance(day.get("hotel"), dict)
            ]
            if any(isinstance(cost, (int, float)) and cost > 600 for cost in hotel_costs):
                budget_score -= 2
                issues.append("经济型行程中存在偏高价酒店")

        preference_text = " ".join(getattr(request, "preferences", []) or [])
        free_text = getattr(request, "free_text_input", "") or ""
        user_keywords = [
            keyword
            for keyword in (preference_text + " " + free_text).replace("，", " ").split()
            if len(keyword) >= 2
        ]
        if user_keywords:
            plan_text = json.dumps(plan, ensure_ascii=False)
            matched = sum(1 for keyword in user_keywords if keyword in plan_text)
            if matched == 0:
                preference_score -= 3
                issues.append("行程内容未明显体现用户偏好")
            elif matched < max(1, len(user_keywords) // 2):
                preference_score -= 1

        geo_score = max(0, min(10, geo_score))
        budget_score = max(0, min(10, budget_score))
        preference_score = max(0, min(10, preference_score))
        should_revise = min(geo_score, budget_score, preference_score) < 6
        revision_focus = None
        if should_revise:
            score_map = {
                "地理路线": geo_score,
                "预算匹配": budget_score,
                "偏好满足": preference_score,
            }
            revision_focus = min(score_map, key=score_map.get)

        return {
            "geo_score": geo_score,
            "budget_score": budget_score,
            "preference_score": preference_score,
            "critique": "；".join(dict.fromkeys(issues)) or "行程整体可用，可交给用户继续精修。",
            "should_revise": should_revise,
            "revision_focus": revision_focus,
        }

    async def _audit_plan_with_llm(
        self,
        request: TripRequest,
        plan: Dict[str, Any],
        fallback_scores: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt = f"""
你是旅行行程质量审核员。请基于地理路线、预算匹配、偏好满足三个维度审核行程。

要求：
1. 只输出 JSON，不要输出解释、Markdown 或代码块。
2. geo_score、budget_score、preference_score 必须是 0-10 的整数。
3. should_revise 为 true 时，revision_focus 必须说明需要重跑的重点。
4. 只有存在明显问题时才 should_revise=true；轻微文案问题不需要重跑。

用户请求：
{json.dumps(request.model_dump(), ensure_ascii=False)}

当前行程：
{json.dumps(plan, ensure_ascii=False)}

规则兜底初评：
{json.dumps(fallback_scores, ensure_ascii=False)}

输出示例：
{{"geo_score":8,"budget_score":7,"preference_score":9,"critique":"路线整体合理","should_revise":false,"revision_focus":null}}
""".strip()

        raw_scores = await self._request_llm_json(
            system_prompt="你只负责审核旅行行程质量，并且只返回合法 JSON。",
            user_prompt=prompt,
            max_tokens=700,
        )
        if not raw_scores:
            return fallback_scores

        normalized = dict(fallback_scores)
        for key in ("geo_score", "budget_score", "preference_score"):
            try:
                normalized[key] = max(0, min(10, int(raw_scores.get(key, normalized[key]))))
            except (TypeError, ValueError):
                pass

        critique = raw_scores.get("critique")
        if isinstance(critique, str) and critique.strip():
            normalized["critique"] = critique.strip()[:500]

        normalized["should_revise"] = bool(
            raw_scores.get("should_revise")
            or min(
                normalized["geo_score"],
                normalized["budget_score"],
                normalized["preference_score"],
            )
            < 6
        )
        revision_focus = raw_scores.get("revision_focus")
        normalized["revision_focus"] = (
            str(revision_focus).strip()[:200]
            if revision_focus not in (None, "")
            else fallback_scores.get("revision_focus")
        )
        return normalized

    async def _node_qa_auditor(self, state: Dict[str, Any]):
        request = state["request"]
        plan = state.get("final_plan") or {}
        fallback_scores = self._build_rule_based_critic_scores(request, plan)
        critic_scores = await self._audit_plan_with_llm(request, plan, fallback_scores)
        critic_feedback = (
            f"{critic_scores.get('critique', '')} "
            f"修订重点：{critic_scores.get('revision_focus') or '无需重跑'}"
        ).strip()

        return {
            "critic_feedback": critic_feedback,
            "critic_scores": critic_scores,
            "revision_count": state.get("revision_count", 0) + 1,
        }

    async def _node_image_enricher(self, state: Dict[str, Any]):
        return {"final_plan": state.get("final_plan", {})}

    async def _node_await_refinement(self, state: Dict[str, Any]):
        user_message = interrupt(state.get("final_plan", {}))
        chat_history = list(state.get("chat_history") or [])
        chat_history.append({"role": "human", "content": str(user_message)})
        return {"chat_history": chat_history}

    def _fallback_refinement_intent(self, message: str) -> Dict[str, Any]:
        lowered = message.lower()
        if any(keyword in lowered for keyword in ("满意", "结束", "不用改", "确定", "就这样")):
            return {"intent": "end"}
        if any(keyword in lowered for keyword in ("经济", "便宜", "省钱", "穷游", "太贵")):
            return {"intent": "adjust_tier", "spending_tier": "经济型"}
        if any(keyword in lowered for keyword in ("奢侈", "豪华", "高端")):
            return {"intent": "adjust_tier", "spending_tier": "奢侈型"}
        if any(keyword in lowered for keyword in ("舒适", "标准")):
            return {"intent": "adjust_tier", "spending_tier": "舒适型"}
        if any(keyword in lowered for keyword in ("重做", "重来", "重新", "再来", "换一版", "换个方案")):
            return {"intent": "regenerate"}
        if any(keyword in lowered for keyword in ("加", "增加", "删", "删除", "换掉", "替换")):
            return {"intent": "change_poi"}
        if any(keyword in lowered for keyword in ("少一天", "减少", "更轻松", "放松", "慢一点", "太累", "第")):
            return {"intent": "modify_schedule"}
        return {"intent": "modify_schedule"}

    def _normalize_refinement_intent(
        self,
        raw_intent: Dict[str, Any],
        fallback_intent: Dict[str, Any],
    ) -> Dict[str, Any]:
        allowed_intents = {
            "end",
            "adjust_tier",
            "regenerate",
            "change_poi",
            "modify_schedule",
        }
        intent = raw_intent.get("intent") if isinstance(raw_intent, dict) else None
        normalized = dict(fallback_intent)
        if intent in allowed_intents:
            normalized["intent"] = intent

        spending_tier = raw_intent.get("spending_tier") if isinstance(raw_intent, dict) else None
        if spending_tier in {"经济型", "舒适型", "奢侈型"}:
            normalized["spending_tier"] = spending_tier

        refined_instruction = raw_intent.get("refined_instruction") if isinstance(raw_intent, dict) else None
        if isinstance(refined_instruction, str) and refined_instruction.strip():
            normalized["refined_instruction"] = refined_instruction.strip()[:500]

        target_day = raw_intent.get("target_day") if isinstance(raw_intent, dict) else None
        if target_day not in (None, ""):
            normalized["target_day"] = target_day

        reason = raw_intent.get("reason") if isinstance(raw_intent, dict) else None
        if isinstance(reason, str) and reason.strip():
            normalized["reason"] = reason.strip()[:300]

        if normalized.get("intent") == "adjust_tier" and not normalized.get("spending_tier"):
            normalized.update(fallback_intent)
        return normalized

    async def _classify_refinement_intent(
        self,
        state: Dict[str, Any],
        message: str,
    ) -> Dict[str, Any]:
        fallback_intent = self._fallback_refinement_intent(message)
        request = state["request"]
        compact_plan = {
            "city": (state.get("final_plan") or {}).get("city"),
            "days": [
                {
                    "day_index": day.get("day_index"),
                    "date": day.get("date"),
                    "description": day.get("description"),
                    "attractions": [
                        attraction.get("name")
                        for attraction in day.get("attractions", [])
                        if isinstance(attraction, dict)
                    ],
                }
                for day in (state.get("final_plan") or {}).get("days", [])
                if isinstance(day, dict)
            ],
        }
        prompt = f"""
你是 LangGraph 旅行助手的精修意图路由 Agent。请判断用户最新消息应该让图从哪个方向继续。

只输出 JSON，不要输出解释、Markdown 或代码块。

intent 只能是以下之一：
- end：用户满意或明确表示不用修改，图结束。
- adjust_tier：用户要求改预算/消费档次。
- regenerate：用户要求完全重做、换一版、重新规划。
- change_poi：用户要求增加、删除、替换景点/餐厅/酒店。
- modify_schedule：用户要求调整某天节奏、顺序、天数、轻松程度或其他局部行程。

spending_tier 只能在 adjust_tier 时填写：经济型 / 舒适型 / 奢侈型。
refined_instruction 用一句话保留用户的具体修改要求，供后续节点重跑时作为硬约束。

用户原始请求：
{json.dumps(request.model_dump(), ensure_ascii=False)}

当前简化行程：
{json.dumps(compact_plan, ensure_ascii=False)}

用户最新消息：
{message}

输出示例：
{{"intent":"modify_schedule","spending_tier":null,"target_day":2,"refined_instruction":"第二天减少一个景点并放慢节奏","reason":"用户明确要求调整第二天强度"}}
""".strip()

        raw_intent = await self._request_llm_json(
            system_prompt="你只负责识别旅行行程精修意图，并且只返回合法 JSON。",
            user_prompt=prompt,
            max_tokens=500,
        )
        return self._normalize_refinement_intent(raw_intent, fallback_intent)

    def _append_refinement_instruction(self, request: TripRequest, message: str) -> TripRequest:
        previous_free_text = getattr(request, "free_text_input", "") or ""
        refined_free_text = "\n".join(
            part
            for part in (
                previous_free_text,
                f"用户精修要求：{message}",
            )
            if part
        )
        return request.model_copy(update={"free_text_input": refined_free_text})

    async def _node_refine_agent(self, state: Dict[str, Any]):
        chat_history = state.get("chat_history") or []
        last_message = chat_history[-1] if chat_history else {}
        if isinstance(last_message, dict):
            message = last_message.get("content", "")
        else:
            message = getattr(last_message, "content", str(last_message))
        updates: Dict[str, Any] = {"revision_count": 0, "critic_feedback": ""}
        intent = await self._classify_refinement_intent(state, str(message))
        intent_name = intent.get("intent")
        refined_instruction = intent.get("refined_instruction") or str(message)

        if intent_name == "end":
            return {**updates, "resume_from": END}

        if intent_name == "adjust_tier":
            spending_tier = intent.get("spending_tier") or state["request"].spending_tier
            request = self._append_refinement_instruction(
                state["request"].model_copy(update={"spending_tier": spending_tier}),
                refined_instruction,
            )
            return {
                **updates,
                "request": request,
                "spending_tier": spending_tier,
                "resume_from": "poi_selector",
            }

        if intent_name == "regenerate":
            request = self._append_refinement_instruction(state["request"], refined_instruction)
            return {
                **updates,
                "request": request,
                "resume_from": "knowledge_retrieval",
            }

        if intent_name == "change_poi":
            request = self._append_refinement_instruction(state["request"], refined_instruction)
            return {
                **updates,
                "request": request,
                "resume_from": "poi_selector",
            }

        if intent_name == "modify_schedule":
            request = self._append_refinement_instruction(state["request"], refined_instruction)
            return {
                **updates,
                "request": request,
                "resume_from": "planner",
            }

        plan = json.loads(json.dumps(state.get("final_plan") or {}, ensure_ascii=False))
        plan["overall_suggestions"] = "已根据用户反馈完成基础精修。"
        return {**updates, "final_plan": plan, "resume_from": "await_refinement"}

    async def plan_trip(self, request: TripRequest, session_id: str) -> dict:
        state = {
            "request": request,
            "user_id": getattr(request, "user_id", "default_guest"),
            "chat_history": [{"role": "human", "content": f"规划{request.city}旅行"}],
            "spending_tier": request.spending_tier,
        }

        state = await self._run_graph(state, thread_id=session_id)
        plan = state["final_plan"]

        self.sessions[session_id] = {
            "request": request,
            "chat_history": state.get("chat_history", [])
            + [{"role": "ai", "content": "已生成基础行程"}],
            "final_plan": plan,
            "critic_scores": state.get("critic_scores"),
            "spending_tier": request.spending_tier,
            "revision_count": state.get("revision_count", 0),
            "user_profile": state.get("user_profile"),
            "user_profile_context": state.get("user_profile_context"),
        }

        return {
            "session_id": session_id,
            "user_id": getattr(request, "user_id", "default_guest"),
            "plan": plan,
            "critic_scores": state.get("critic_scores"),
            "consumption_tier": request.spending_tier,
        }

    async def _legacy_refine_trip(self, session_id: str, user_message: str) -> dict:
        session = self.sessions.get(session_id)
        if not session:
            return {
                "session_id": session_id,
                "plan": None,
                "critic_scores": None,
                "consumption_tier": None,
            }

        session["chat_history"].append({"role": "human", "content": user_message})
        plan = session["final_plan"]
        lowered = user_message.lower()

        if any(keyword in lowered for keyword in ("少一天", "减少", "删掉", "删")) and len(plan["days"]) > 1:
            plan["days"] = plan["days"][:-1]
            plan["end_date"] = plan["days"][-1]["date"]
            plan["overall_suggestions"] = "已按要求缩短行程。"
        elif any(keyword in lowered for keyword in ("更轻松", "放松", "慢一点")):
            plan["overall_suggestions"] = "已将行程调整为更轻松的节奏。"
        else:
            plan["overall_suggestions"] = "已根据用户反馈完成基础精修。"

        session["final_plan"] = plan
        session["chat_history"].append({"role": "ai", "content": plan["overall_suggestions"]})

        return {
            "session_id": session_id,
            "user_id": getattr(session.get("request"), "user_id", "default_guest"),
            "plan": plan,
            "critic_scores": session.get("critic_scores"),
            "consumption_tier": session.get("spending_tier"),
        }

    async def refine_trip(self, session_id: str, user_message: str) -> dict:
        session = self._get_or_restore_session(session_id)
        if not session:
            return {
                "session_id": session_id,
                "plan": None,
                "critic_scores": None,
                "consumption_tier": None,
            }

        state = await self._resume_graph(session_id, user_message)
        plan = state.get("final_plan") or session.get("final_plan")
        if not plan:
            return {
                "session_id": session_id,
                "plan": None,
                "critic_scores": None,
                "consumption_tier": None,
            }

        chat_history = list(state.get("chat_history") or session.get("chat_history", []))
        chat_history.append(
            {"role": "ai", "content": plan.get("overall_suggestions", "已更新行程。")}
        )

        session["request"] = state.get("request", session.get("request"))
        session["final_plan"] = plan
        session["critic_scores"] = state.get("critic_scores", session.get("critic_scores"))
        session["spending_tier"] = state.get("spending_tier", session.get("spending_tier"))
        session["revision_count"] = state.get("revision_count", 0)
        session["chat_history"] = chat_history
        session["user_profile"] = state.get("user_profile", session.get("user_profile"))
        session["user_profile_context"] = state.get(
            "user_profile_context",
            session.get("user_profile_context"),
        )

        return {
            "session_id": session_id,
            "user_id": self._user_id_from_request(session.get("request")),
            "plan": plan,
            "critic_scores": session.get("critic_scores"),
            "consumption_tier": session.get("spending_tier"),
        }

    async def get_chat_history(self, session_id: str) -> list:
        session = self._get_or_restore_session(session_id) or {}
        return session.get("chat_history", [])

    def _create_fallback_plan(self, request: TripRequest) -> TripPlan:
        return self._create_basic_plan(request)


async def get_trip_planner_agent() -> GraphTripPlanner:
    global _graph_planner
    if _graph_planner is None:
        _graph_planner = GraphTripPlanner()
    else:
    return _graph_planner
