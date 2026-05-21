"""Tests for restoring planner sessions from persisted LangGraph checkpoints."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from backend.app.agents.test import GraphTripPlanner
from backend.app.models.schemas import TripRequest


def _request_payload() -> dict:
    return {
        "user_id": "user-1",
        "city": "上海",
        "start_date": "2026-06-01",
        "end_date": "2026-06-03",
        "travel_days": 3,
        "transportation": "公共交通",
        "accommodation": "舒适型酒店",
        "preferences": ["历史文化"],
        "free_text_input": "节奏轻松一点",
        "spending_tier": "舒适型",
        "budget": 5000,
    }


class FakeCheckpointSaver:
    def __init__(self, channel_values: dict | None):
        self.channel_values = channel_values

    def get_tuple(self, config):
        if self.channel_values is None:
            return None
        return SimpleNamespace(checkpoint={"channel_values": self.channel_values})


class SessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _planner_with_checkpoint(self) -> GraphTripPlanner:
        planner = GraphTripPlanner.__new__(GraphTripPlanner)
        planner.sessions = {}
        planner.checkpointer = FakeCheckpointSaver(
            {
                "request": _request_payload(),
                "chat_history": [{"role": "human", "content": "规划上海旅行"}],
                "final_plan": {"city": "上海", "overall_suggestions": "原始方案"},
                "critic_scores": {"should_revise": False},
                "spending_tier": "舒适型",
                "revision_count": 1,
                "user_profile": {"likes": ["博物馆"]},
                "user_profile_context": "用户长期画像：喜欢博物馆",
            }
        )
        return planner

    async def test_restore_session_from_checkpoint_rehydrates_request(self):
        planner = self._planner_with_checkpoint()

        session = planner._restore_session_from_checkpoint("session-1")

        self.assertIsNotNone(session)
        self.assertIsInstance(session["request"], TripRequest)
        self.assertEqual(session["request"].city, "上海")
        self.assertEqual(planner.sessions["session-1"]["final_plan"]["city"], "上海")

    async def test_refine_trip_recovers_when_memory_cache_is_empty(self):
        planner = self._planner_with_checkpoint()
        resumed_request = TripRequest(**_request_payload())
        planner._resume_graph = AsyncMock(
            return_value={
                "request": resumed_request,
                "chat_history": [{"role": "human", "content": "第二天轻松一点"}],
                "final_plan": {"city": "上海", "overall_suggestions": "已调整"},
                "critic_scores": {"should_revise": False},
                "spending_tier": "舒适型",
                "revision_count": 0,
                "user_profile": {"likes": ["博物馆"]},
                "user_profile_context": "用户长期画像：喜欢博物馆",
            }
        )

        result = await planner.refine_trip("session-1", "第二天轻松一点")

        self.assertEqual(result["session_id"], "session-1")
        self.assertEqual(result["plan"]["overall_suggestions"], "已调整")
        self.assertEqual(result["user_id"], "user-1")
        planner._resume_graph.assert_awaited_once_with("session-1", "第二天轻松一点")

    async def test_get_chat_history_recovers_from_checkpoint(self):
        planner = self._planner_with_checkpoint()

        history = await planner.get_chat_history("session-1")

        self.assertEqual(history, [{"role": "human", "content": "规划上海旅行"}])


if __name__ == "__main__":
    unittest.main()
