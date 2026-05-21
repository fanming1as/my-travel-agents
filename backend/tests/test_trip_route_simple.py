"""Simple route tests for the trip API."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ["DEBUG"] = "false"

import backend.app.api.main as api_main
import backend.app.api.routes.trip as trip_routes


class SimpleTripRouteTests(unittest.TestCase):
    def setUp(self):
        self.validate_patch = patch.object(api_main, "validate_config", return_value=True)
        self.print_config_patch = patch.object(api_main, "print_config", return_value=None)
        self.api_print_patch = patch.object(api_main, "print", side_effect=lambda *args, **kwargs: None)
        self.trip_print_patch = patch.object(trip_routes, "print", side_effect=lambda *args, **kwargs: None)

        self.validate_patch.start()
        self.print_config_patch.start()
        self.api_print_patch.start()
        self.trip_print_patch.start()

        self.addCleanup(self.validate_patch.stop)
        self.addCleanup(self.print_config_patch.stop)
        self.addCleanup(self.api_print_patch.stop)
        self.addCleanup(self.trip_print_patch.stop)

    @contextmanager
    def client(self):
        with TestClient(api_main.app) as test_client:
            yield test_client

    def test_trip_health_endpoint(self):
        with self.client() as client:
            response = client.get("/api/trip/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(response.json()["service"], "trip-planner")

    def _valid_plan_payload(self) -> dict:
        return {
            "city": "北京",
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
            "travel_days": 3,
            "transportation": "公共交通",
            "accommodation": "舒适型酒店",
            "preferences": ["历史文化", "美食"],
            "free_text_input": "希望节奏轻松一点",
            "spending_tier": "舒适型",
            "budget": 5000,
        }

    def test_plan_trip_rejects_end_date_before_start_date(self):
        payload = self._valid_plan_payload()
        payload["start_date"] = "2025-06-03"
        payload["end_date"] = "2025-06-01"

        with self.client() as client, patch.object(
            trip_routes, "get_trip_planner_agent"
        ) as get_agent:
            response = client.post("/api/trip/plan", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertIn("end_date 不能早于 start_date", response.text)
        get_agent.assert_not_called()

    def test_plan_trip_rejects_mismatched_travel_days(self):
        payload = self._valid_plan_payload()
        payload["travel_days"] = 2

        with self.client() as client, patch.object(
            trip_routes, "get_trip_planner_agent"
        ) as get_agent:
            response = client.post("/api/trip/plan", json=payload)

        self.assertEqual(response.status_code, 422)
        self.assertIn("travel_days 应该与日期范围一致", response.text)
        get_agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
