from __future__ import annotations

import os
import random
import sqlite3
from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    SerializerProtocol,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(CURRENT_DIR)
DEFAULT_CHECKPOINT_DB_PATH = os.path.join(APP_DIR, "data", "trips.db")


class SQLiteCheckpointSaver(BaseCheckpointSaver[str]):
    """SQLite-backed LangGraph checkpointer.

    LangGraph's bundled MemorySaver keeps checkpoints only in process memory.
    This saver stores the same checkpoint pieces in SQLite so graph interrupts
    can be resumed after the API process restarts.
    """

    def __init__(
        self,
        sqlite_path: str | None = None,
        *,
        serde: SerializerProtocol | None = None,
    ) -> None:
        super().__init__(serde=serde)
        self.sqlite_path = (
            sqlite_path
            or os.getenv("LANGGRAPH_CHECKPOINT_DB_PATH")
            or DEFAULT_CHECKPOINT_DB_PATH
        )
        self._init_sqlite()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.sqlite_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_sqlite(self) -> None:
        os.makedirs(os.path.dirname(self.sqlite_path), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS langgraph_checkpoints (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    checkpoint_type TEXT NOT NULL,
                    checkpoint_blob BLOB NOT NULL,
                    metadata_type TEXT NOT NULL,
                    metadata_blob BLOB NOT NULL,
                    parent_checkpoint_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
                );

                CREATE TABLE IF NOT EXISTS langgraph_checkpoint_blobs (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    channel TEXT NOT NULL,
                    version TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    value_blob BLOB NOT NULL,
                    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
                );

                CREATE TABLE IF NOT EXISTS langgraph_checkpoint_writes (
                    thread_id TEXT NOT NULL,
                    checkpoint_ns TEXT NOT NULL DEFAULT '',
                    checkpoint_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    write_idx INTEGER NOT NULL,
                    channel TEXT NOT NULL,
                    value_type TEXT NOT NULL,
                    value_blob BLOB NOT NULL,
                    task_path TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx)
                );

                CREATE INDEX IF NOT EXISTS idx_langgraph_checkpoints_latest
                    ON langgraph_checkpoints (thread_id, checkpoint_ns, checkpoint_id DESC);
                """
            )

    def _load_blobs(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        checkpoint_ns: str,
        versions: ChannelVersions,
    ) -> dict[str, Any]:
        channel_values: dict[str, Any] = {}
        for channel, version in versions.items():
            row = conn.execute(
                """
                SELECT value_type, value_blob
                FROM langgraph_checkpoint_blobs
                WHERE thread_id = ? AND checkpoint_ns = ? AND channel = ? AND version = ?
                """,
                (thread_id, checkpoint_ns, channel, str(version)),
            ).fetchone()
            if row and row[0] != "empty":
                channel_values[channel] = self.serde.loads_typed((row[0], row[1]))
        return channel_values

    def _load_writes(
        self,
        conn: sqlite3.Connection,
        thread_id: str,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> list[tuple[str, str, Any]]:
        rows = conn.execute(
            """
            SELECT task_id, channel, value_type, value_blob
            FROM langgraph_checkpoint_writes
            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
            ORDER BY task_id, write_idx
            """,
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchall()
        return [
            (task_id, channel, self.serde.loads_typed((value_type, value_blob)))
            for task_id, channel, value_type, value_blob in rows
        ]

    def _build_tuple(
        self,
        conn: sqlite3.Connection,
        row: sqlite3.Row | tuple[Any, ...],
        config: RunnableConfig | None = None,
    ) -> CheckpointTuple:
        (
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            checkpoint_type,
            checkpoint_blob,
            metadata_type,
            metadata_blob,
            parent_checkpoint_id,
        ) = row
        checkpoint: Checkpoint = self.serde.loads_typed((checkpoint_type, checkpoint_blob))
        metadata = self.serde.loads_typed((metadata_type, metadata_blob))
        checkpoint = {
            **checkpoint,
            "channel_values": self._load_blobs(
                conn,
                thread_id,
                checkpoint_ns,
                checkpoint["channel_versions"],
            ),
        }
        return CheckpointTuple(
            config=config
            or {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_checkpoint_id,
                    }
                }
                if parent_checkpoint_id
                else None
            ),
            pending_writes=self._load_writes(conn, thread_id, checkpoint_ns, checkpoint_id),
        )

    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id: str = config["configurable"]["thread_id"]
        checkpoint_ns: str = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = get_checkpoint_id(config)
        with self._connect() as conn:
            if checkpoint_id:
                row = conn.execute(
                    """
                    SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint_type,
                           checkpoint_blob, metadata_type, metadata_blob, parent_checkpoint_id
                    FROM langgraph_checkpoints
                    WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                    """,
                    (thread_id, checkpoint_ns, checkpoint_id),
                ).fetchone()
                return self._build_tuple(conn, row, config) if row else None

            row = conn.execute(
                """
                SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint_type,
                       checkpoint_blob, metadata_type, metadata_blob, parent_checkpoint_id
                FROM langgraph_checkpoints
                WHERE thread_id = ? AND checkpoint_ns = ?
                ORDER BY checkpoint_id DESC
                LIMIT 1
                """,
                (thread_id, checkpoint_ns),
            ).fetchone()
            return self._build_tuple(conn, row) if row else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        params: list[Any] = []
        where_clauses: list[str] = []
        if config:
            where_clauses.append("thread_id = ?")
            params.append(config["configurable"]["thread_id"])
            checkpoint_ns = config["configurable"].get("checkpoint_ns")
            if checkpoint_ns is not None:
                where_clauses.append("checkpoint_ns = ?")
                params.append(checkpoint_ns)
            checkpoint_id = get_checkpoint_id(config)
            if checkpoint_id:
                where_clauses.append("checkpoint_id = ?")
                params.append(checkpoint_id)
        if before and (before_checkpoint_id := get_checkpoint_id(before)):
            where_clauses.append("checkpoint_id < ?")
            params.append(before_checkpoint_id)

        sql = """
            SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint_type,
                   checkpoint_blob, metadata_type, metadata_blob, parent_checkpoint_id
            FROM langgraph_checkpoints
        """
        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)
        sql += " ORDER BY checkpoint_id DESC"

        yielded = 0
        with self._connect() as conn:
            for row in conn.execute(sql, params).fetchall():
                checkpoint_tuple = self._build_tuple(conn, row)
                if filter and not all(
                    query_value == checkpoint_tuple.metadata.get(query_key)
                    for query_key, query_value in filter.items()
                ):
                    continue
                if limit is not None and yielded >= limit:
                    break
                yielded += 1
                yield checkpoint_tuple

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        checkpoint_copy = checkpoint.copy()
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        values: dict[str, Any] = checkpoint_copy.pop("channel_values")  # type: ignore[misc]

        checkpoint_type, checkpoint_blob = self.serde.dumps_typed(checkpoint_copy)
        metadata_type, metadata_blob = self.serde.dumps_typed(
            get_checkpoint_metadata(config, metadata)
        )
        with self._connect() as conn:
            for channel, version in new_versions.items():
                if channel in values:
                    value_type, value_blob = self.serde.dumps_typed(values[channel])
                else:
                    value_type, value_blob = "empty", b""
                conn.execute(
                    """
                    INSERT OR REPLACE INTO langgraph_checkpoint_blobs
                        (thread_id, checkpoint_ns, channel, version, value_type, value_blob)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (thread_id, checkpoint_ns, channel, str(version), value_type, value_blob),
                )

            conn.execute(
                """
                INSERT OR REPLACE INTO langgraph_checkpoints
                    (thread_id, checkpoint_ns, checkpoint_id, checkpoint_type, checkpoint_blob,
                     metadata_type, metadata_blob, parent_checkpoint_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    checkpoint_type,
                    checkpoint_blob,
                    metadata_type,
                    metadata_blob,
                    config["configurable"].get("checkpoint_id"),
                ),
            )

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self._connect() as conn:
            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                value_type, value_blob = self.serde.dumps_typed(value)
                sql = (
                    """
                    INSERT OR IGNORE INTO langgraph_checkpoint_writes
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx,
                         channel, value_type, value_blob, task_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                    if write_idx >= 0
                    else """
                    INSERT OR REPLACE INTO langgraph_checkpoint_writes
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx,
                         channel, value_type, value_blob, task_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """
                )
                conn.execute(
                    sql,
                    (
                        thread_id,
                        checkpoint_ns,
                        checkpoint_id,
                        task_id,
                        write_idx,
                        channel,
                        value_type,
                        value_blob,
                        task_path,
                    ),
                )

    def delete_thread(self, thread_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM langgraph_checkpoints WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM langgraph_checkpoint_blobs WHERE thread_id = ?", (thread_id,))
            conn.execute("DELETE FROM langgraph_checkpoint_writes WHERE thread_id = ?", (thread_id,))

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        if not run_ids:
            return
        for checkpoint_tuple in list(self.list(None)):
            if checkpoint_tuple.metadata.get("run_id") in run_ids:
                config = checkpoint_tuple.config["configurable"]
                with self._connect() as conn:
                    params = (
                        config["thread_id"],
                        config.get("checkpoint_ns", ""),
                        config["checkpoint_id"],
                    )
                    conn.execute(
                        """
                        DELETE FROM langgraph_checkpoints
                        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                        """,
                        params,
                    )
                    conn.execute(
                        """
                        DELETE FROM langgraph_checkpoint_writes
                        WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                        """,
                        params,
                    )

    def prune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        if strategy not in {"keep_latest", "delete"}:
            raise ValueError("strategy must be 'keep_latest' or 'delete'")
        for thread_id in thread_ids:
            if strategy == "delete":
                self.delete_thread(thread_id)
                continue
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT checkpoint_ns, MAX(checkpoint_id)
                    FROM langgraph_checkpoints
                    WHERE thread_id = ?
                    GROUP BY checkpoint_ns
                    """,
                    (thread_id,),
                ).fetchall()
                latest = {(checkpoint_ns, checkpoint_id) for checkpoint_ns, checkpoint_id in rows}
                for checkpoint_tuple in list(self.list({"configurable": {"thread_id": thread_id}})):
                    config = checkpoint_tuple.config["configurable"]
                    key = (config.get("checkpoint_ns", ""), config["checkpoint_id"])
                    if key not in latest:
                        conn.execute(
                            """
                            DELETE FROM langgraph_checkpoints
                            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                            """,
                            (thread_id, key[0], key[1]),
                        )
                        conn.execute(
                            """
                            DELETE FROM langgraph_checkpoint_writes
                            WHERE thread_id = ? AND checkpoint_ns = ? AND checkpoint_id = ?
                            """,
                            (thread_id, key[0], key[1]),
                        )

    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for checkpoint_tuple in self.list(config, filter=filter, before=before, limit=limit):
            yield checkpoint_tuple

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)

    async def adelete_for_runs(self, run_ids: Sequence[str]) -> None:
        self.delete_for_runs(run_ids)

    async def aprune(
        self,
        thread_ids: Sequence[str],
        *,
        strategy: str = "keep_latest",
    ) -> None:
        self.prune(thread_ids, strategy=strategy)

    def get_next_version(self, current: str | None, channel: None) -> str:
        if current is None:
            current_v = 0
        elif isinstance(current, int):
            current_v = current
        else:
            current_v = int(str(current).split(".")[0])
        next_v = current_v + 1
        next_h = random.random()
        return f"{next_v:032}.{next_h:016}"
