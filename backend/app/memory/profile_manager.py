import json
import os
import sqlite3
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List

from .episodic_manager import DEFAULT_DB_PATH


DEFAULT_PROFILE: Dict[str, Any] = {
    "diet_avoid": [],
    "travel_with": [],
    "pace_preference": None,
    "avoid": [],
    "likes": [],
    "hotel_preference": None,
    "budget_preference": None,
}

LIST_FIELDS = {"diet_avoid", "travel_with", "avoid", "likes"}
SCALAR_FIELDS = {"pace_preference", "hotel_preference", "budget_preference"}


class ProfileMemoryManager:
    def __init__(self, sqlite_path: str = DEFAULT_DB_PATH):
        self.sqlite_path = sqlite_path
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        os.makedirs(os.path.dirname(self.sqlite_path), exist_ok=True)
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS user_profiles (
                user_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TIMESTAMP
            )
            """
        )
        conn.commit()
        conn.close()

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT profile_json FROM user_profiles WHERE user_id = ?",
            (user_id,),
        )
        row = cursor.fetchone()
        conn.close()

        profile = deepcopy(DEFAULT_PROFILE)
        if not row:
            return profile

        try:
            stored = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return profile

        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in profile:
                    profile[key] = value
        return self._normalize_profile(profile)

    def update_profile(self, user_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
        profile = self.get_profile(user_id)
        patch = self._normalize_profile_patch(patch)
        if not self.has_profile_data(patch):
            return profile

        for key in LIST_FIELDS:
            profile[key] = self._merge_unique(profile.get(key, []), patch.get(key, []))

        for key in SCALAR_FIELDS:
            value = patch.get(key)
            if isinstance(value, str) and value.strip():
                profile[key] = value.strip()

        profile = self._normalize_profile(profile)
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO user_profiles (user_id, profile_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                profile_json = excluded.profile_json,
                updated_at = excluded.updated_at
            """,
            (user_id, json.dumps(profile, ensure_ascii=False), datetime.now()),
        )
        conn.commit()
        conn.close()
        return profile

    def format_profile(self, profile: Dict[str, Any]) -> str:
        profile = self._normalize_profile(profile)
        lines: List[str] = []
        if profile["diet_avoid"]:
            lines.append(f"- 饮食避雷：{', '.join(profile['diet_avoid'])}")
        if profile["travel_with"]:
            lines.append(f"- 同行人：{', '.join(profile['travel_with'])}")
        if profile["pace_preference"]:
            lines.append(f"- 行程节奏：{profile['pace_preference']}")
        if profile["avoid"]:
            lines.append(f"- 需要避免：{', '.join(profile['avoid'])}")
        if profile["likes"]:
            lines.append(f"- 偏好内容：{', '.join(profile['likes'])}")
        if profile["hotel_preference"]:
            lines.append(f"- 住宿偏好：{profile['hotel_preference']}")
        if profile["budget_preference"]:
            lines.append(f"- 消费偏好：{profile['budget_preference']}")

        if not lines:
            return "该用户暂无长期画像。"
        return "用户长期画像：\n" + "\n".join(lines)

    def has_profile_data(self, profile: Dict[str, Any]) -> bool:
        normalized = self._normalize_profile_patch(profile)
        return any(normalized.get(key) for key in LIST_FIELDS | SCALAR_FIELDS)

    def _normalize_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        normalized = deepcopy(DEFAULT_PROFILE)
        normalized.update(self._normalize_profile_patch(profile))
        return normalized

    def _normalize_profile_patch(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        if not isinstance(patch, dict):
            return normalized

        for key in LIST_FIELDS:
            values = patch.get(key, [])
            if isinstance(values, str):
                values = [values]
            if not isinstance(values, list):
                values = []
            normalized[key] = [
                str(value).strip()
                for value in values
                if value is not None and str(value).strip()
            ]

        for key in SCALAR_FIELDS:
            value = patch.get(key)
            normalized[key] = str(value).strip() if value not in (None, "") else None

        return normalized

    def _merge_unique(self, current: Any, incoming: Any) -> List[str]:
        result: List[str] = []
        for value in list(current or []) + list(incoming or []):
            text = str(value).strip()
            if text and text not in result:
                result.append(text)
        return result
