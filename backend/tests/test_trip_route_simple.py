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


if __name__ == "__main__":
    unittest.main()
