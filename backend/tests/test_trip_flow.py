"""Backend route tests for the travel planner flow.

These tests exercise the FastAPI routes end-to-end while mocking external
dependencies so the suite can run without network access or live model
services.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from unittest.mock import MagicMock, patch
import unittest

from fastapi.testclient import TestClient

os.environ["DEBUG"] = "false"

import backend.app.api.main as api_main
import backend.app.api.routes.trip as trip_routes


class FakeTripPlanner:
    async def plan_trip(self, request, session_id: str) -> dict:
        return {
            "session_id": session_id,
            "plan": {
                "city": request.city,
                "start_date": request.start_date,
                "end_date": request.end_date,
                "days": [],
                "overall_suggestions": "Mocked plan",
            },
            "critic_scores": {
                "geo_score": 10,
                "budget_score": 9,
                "preference_score": 8,
                "critique": "Looks good",
                "should_revise": False,
                "revision_focus": None,
            },
            "consumption_tier": request.spending_tier,
        }

    async def refine_trip(self, session_id: str, user_message: str) -> dict:
        return {
            "session_id": session_id,
            "plan": {
                "city": "北京",
                "overall_suggestions": f"Refined because: {user_message}",
            },
            "critic_scores": {
                "geo_score": 10,
                "budget_score": 9,
                "preference_score": 9,
                "critique": "Refined",
                "should_revise": False,
                "revision_focus": None,
            },
            "consumption_tier": "舒适型",
        }

    async def get_chat_history(self, session_id: str) -> list[dict]:
        return [
            {"role": "human", "content": "Please plan a trip to Beijing."},
            {"role": "ai", "content": "Mocked answer"},
        ]


class TripFlowTests(unittest.TestCase):
    def setUp(self):
        self.validate_patch = patch.object(api_main, "validate_config", return_value=True)
        self.print_patch = patch.object(api_main, "print_config", return_value=None)
        self.api_print_patch = patch.object(api_main, "print", side_effect=lambda *args, **kwargs: None)
        self.trip_print_patch = patch.object(trip_routes, "print", side_effect=lambda *args, **kwargs: None)
        self.validate_patch.start()
        self.print_patch.start()
        self.api_print_patch.start()
        self.trip_print_patch.start()
        self.addCleanup(self.validate_patch.stop)
        self.addCleanup(self.print_patch.stop)
        self.addCleanup(self.api_print_patch.stop)
        self.addCleanup(self.trip_print_patch.stop)

    @contextmanager
    def client(self):
        with TestClient(api_main.app) as test_client:
            yield test_client

    def test_health_endpoint(self):
        with self.client() as client:
            response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")

    def test_plan_trip_endpoint(self):
        fake_planner = FakeTripPlanner()
        payload = {
            "city": "北京",
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
            "travel_days": 3,
            "transportation": "公共交通",
            "accommodation": "舒适型酒店",
            "preferences": ["历史文化", "美食"],
            "free_text_input": "希望节奏不要太紧",
            "spending_tier": "舒适型",
            "budget": 5000,
        }

        with self.client() as client, patch.object(
            trip_routes, "get_trip_planner_agent", return_value=fake_planner
        ):
            response = client.post("/api/trip/plan", json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["session_id"])
        self.assertEqual(body["data"]["city"], "北京")
        self.assertEqual(body["data"]["overall_suggestions"], "Mocked plan")
        self.assertEqual(body["consumption_tier"], "舒适型")

    def test_refine_trip_endpoint(self):
        fake_planner = FakeTripPlanner()
        payload = {
            "session_id": "session-123",
            "message": "第二天别太累，换成更轻松的安排",
        }

        with self.client() as client, patch.object(
            trip_routes, "get_trip_planner_agent", return_value=fake_planner
        ):
            response = client.post("/api/trip/refine", json=payload)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["session_id"], "session-123")
        self.assertIn("Refined because", body["data"]["overall_suggestions"])
        self.assertEqual(body["consumption_tier"], "舒适型")

    def test_history_endpoint(self):
        fake_planner = FakeTripPlanner()

        with self.client() as client, patch.object(
            trip_routes, "get_trip_planner_agent", return_value=fake_planner
        ):
            response = client.get("/api/trip/history/session-abc")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["session_id"], "session-abc")
        self.assertEqual(len(body["history"]), 2)
        self.assertEqual(body["history"][0]["role"], "human")

    def test_feedback_endpoint(self):
        fake_memory_manager = MagicMock()
        fake_memory_manager.save_experience = MagicMock()

        with self.client() as client, patch.object(
            trip_routes, "EpisodicMemoryManager", return_value=fake_memory_manager
        ):
            response = client.post(
                "/api/trip/feedback",
                json={
                    "user_id": "user-1",
                    "city": "北京",
                    "rating": 5,
                    "feedback_text": "很不错",
                },
            )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "success")
        fake_memory_manager.save_experience.assert_called_once_with(
            user_id="user-1",
            city="北京",
            rating=5,
            feedback_text="很不错",
        )


if __name__ == "__main__":
    unittest.main()
