import sqlite3


DB_FILE = "backend/app/data/trips.db"
TABLE_NAME = "langgraph_checkpoints"  # 可改成你想查看的表名


def print_tables(cursor: sqlite3.Cursor) -> None:
    print("=" * 40)
    print("数据库里的所有表：")
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
    for (table_name,) in cursor.fetchall():
        print("-", table_name)


def print_table_schema(cursor: sqlite3.Cursor, table_name: str) -> None:
    print("\n" + "=" * 40)
    print(f"表 {table_name} 的字段结构：")
    cursor.execute(f"PRAGMA table_info({table_name});")
    for row in cursor.fetchall():
        print(row)


def print_table_preview(cursor: sqlite3.Cursor, table_name: str) -> None:
    print("\n" + "=" * 40)
    print(f"表 {table_name} 的前 10 条数据：")

    if table_name == "langgraph_checkpoints":
        cursor.execute(
            """
            SELECT
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                parent_checkpoint_id,
                created_at
            FROM langgraph_checkpoints
            ORDER BY created_at DESC
            LIMIT 10;
            """
        )
    elif table_name == "langgraph_checkpoint_blobs":
        cursor.execute(
            """
            SELECT
                thread_id,
                checkpoint_ns,
                channel,
                version,
                value_type
            FROM langgraph_checkpoint_blobs
            LIMIT 10;
            """
        )
    elif table_name == "langgraph_checkpoint_writes":
        cursor.execute(
            """
            SELECT
                thread_id,
                checkpoint_ns,
                checkpoint_id,
                task_id,
                channel,
                value_type
            FROM langgraph_checkpoint_writes
            LIMIT 10;
            """
        )
    else:
        cursor.execute(f"SELECT * FROM {table_name} LIMIT 10;")

    for row in cursor.fetchall():
        print(row)


def main() -> None:
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()

    try:
        print_tables(cursor)
        print_table_schema(cursor, TABLE_NAME)
        print_table_preview(cursor, TABLE_NAME)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
