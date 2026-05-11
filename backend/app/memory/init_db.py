# init_db.py
import os
from qdrant_manager import QdrantSemanticMemory


def run_ingestion():
    # 1. 实例化你的语义记忆模块
    memory = QdrantSemanticMemory(collection_name="travel_guide")

    # 2. 指定你的数据目录
    data_dir = r"F:\travel-agent\travel_agent-main\backend\app\data"

    # 3. 遍历目录下的所有文本文件
    if not os.path.exists(data_dir):
        print(f"❌ 错误：目录 {data_dir} 不存在")
        return

    for filename in os.listdir(data_dir):
        if filename.endswith(".txt") or filename.endswith(".md"):
            file_path = os.path.join(data_dir, filename)
            print(f"正在录入: {filename}...")
            try:
                memory.ingest_local_docs(file_path)
            except Exception as e:
                print(f"❌ 录入 {filename} 失败: {e}")

    print("✨ 所有本地文档已成功同步至 Qdrant 向量数据库！")


if __name__ == "__main__":
    run_ingestion()