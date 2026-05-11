"""Unit tests for planner node behavior."""

from __future__ import annotations

import unittest

from backend.app.agents.test import GraphTripPlanner
from backend.app.models.schemas import TripRequest


class PlannerNodeTests(unittest.IsolatedAsyncioTestCase):
    async def test_node_planner_prefers_mcp_data_pois(self):
        planner = GraphTripPlanner.__new__(GraphTripPlanner)
        request = TripRequest(
            city="上海",
            start_date="2025-06-01",
            end_date="2025-06-03",
            travel_days=3,
            transportation="公共交通",
            accommodation="舒适型酒店",
            preferences=["历史文化", "美食"],
            free_text_input="希望节奏轻松一点",
            spending_tier="舒适型",
            budget=5000,
        )
        state = {
            "request": request,
            "mcp_data": {
                "pois": {
                    "attractions": [
                        {
                            "id": "poi-1",
                            "name": "上海博物馆",
                            "category": "景点",
                            "type": "博物馆",
                            "address": "上海市黄浦区人民大道201号",
                            "location": {"longitude": 121.475663, "latitude": 31.229037},
                            "rating": 4.8,
                            "business_area": "人民广场",
                        }
                    ],
                    "restaurants": [
                        {
                            "id": "food-1",
                            "name": "本帮菜馆",
                            "type": "餐饮服务",
                            "address": "上海市黄浦区示例路1号",
                            "location": {"longitude": 121.48, "latitude": 31.23},
                            "rating": 4.5,
                            "business_area": "人民广场",
                        }
                    ],
                    "hotels": [
                        {
                            "id": "hotel-1",
                            "name": "人民广场酒店",
                            "type": "住宿服务",
                            "address": "上海市黄浦区示例路2号",
                            "location": {"longitude": 121.479, "latitude": 31.231},
                            "rating": 4.6,
                            "business_area": "人民广场",
                        }
                    ],
                },
                "weather": [
                    {
                        "date": "2025-06-01",
                        "day_weather": "晴",
                        "night_weather": "多云",
                        "day_temp": 28,
                        "night_temp": 21,
                        "wind_direction": "东风",
                        "wind_power": "3级",
                    }
                ],
                "warnings": [],
            },
        }

        result = await GraphTripPlanner._node_planner(planner, state)
        final_plan = result["final_plan"]

        self.assertEqual(final_plan["days"][0]["attractions"][0]["name"], "上海博物馆")
        self.assertEqual(final_plan["days"][0]["hotel"]["name"], "人民广场酒店")
        self.assertEqual(final_plan["days"][0]["meals"][1]["name"], "本帮菜馆")


if __name__ == "__main__":
    unittest.main()
