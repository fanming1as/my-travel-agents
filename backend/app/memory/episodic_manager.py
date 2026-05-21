import sqlite3
import uuid
from datetime import datetime

from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from qdrant_client.models import Distance, VectorParams, PayloadSchemaType
import os

from .qdrant_manager import _configure_huggingface_retries

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:  # pragma: no cover
    from langchain_community.embeddings import HuggingFaceEmbeddings

load_dotenv()

# 1. 获取当前 episodic_manager.py 文件的绝对路径
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
# 2. 向上退两层，到达 backend 目录
BACKEND_DIR = os.path.dirname(CURRENT_DIR)
# 3. 拼接出绝对路径：backend/data/trips.db
DEFAULT_DB_PATH = os.path.join(BACKEND_DIR, "data", "trips.db")


class EpisodicMemoryManager:
    def __init__(self, sqlite_path=DEFAULT_DB_PATH, qdrant_collection="user_feedback_v2"):
        self.sqlite_path = sqlite_path
        print("[EpisodicMemoryManager] 开始初始化 SQLite", flush=True)
        self._init_sqlite()
        print("[EpisodicMemoryManager] SQLite 初始化完成", flush=True)
        _configure_huggingface_retries()

        self.qdrant_collection = qdrant_collection
        print(
            f"[EpisodicMemoryManager] 开始创建 Qdrant client，collection={qdrant_collection}",
            flush=True,
        )
        self.qdrant_client = QdrantClient(
            url=os.getenv("QDRANT_URL"),
            api_key=os.getenv("QDRANT_API_KEY") or None,
            check_compatibility=False,
        )
        print("[EpisodicMemoryManager] Qdrant client 创建完成", flush=True)
        print("[EpisodicMemoryManager] 开始创建 embeddings", flush=True)
        self.embeddings = self._create_embeddings()
        print("[EpisodicMemoryManager] embeddings 创建完成", flush=True)
        print("[EpisodicMemoryManager] 开始检查 collection", flush=True)
        self._init_qdrant()
        print("[EpisodicMemoryManager] collection 检查完成", flush=True)

        self.vector_store = QdrantVectorStore(
            client=self.qdrant_client,
            collection_name=self.qdrant_collection,
            embedding=self.embeddings
        )
        print("[EpisodicMemoryManager] 初始化完成", flush=True)

    def _create_embeddings(self):
        provider = (os.getenv("EMBED_PROVIDER") or "auto").strip().lower()
        local_model_name = (
            os.getenv("LOCAL_EMBED_MODEL_NAME")
            or "all-MiniLM-L6-v2"
        )
        openai_model_name = (
            os.getenv("OPENAI_EMBED_MODEL_NAME")
            or os.getenv("EMBED_MODEL_NAME")
            or "text-embedding-3-small"
        )

        if provider == "local":
            return HuggingFaceEmbeddings(model_name=local_model_name)

        if provider == "openai":
            return OpenAIEmbeddings(
                model=openai_model_name,
                api_key=os.getenv("EMBED_API_KEY") or os.getenv("LLM_API_KEY"),
                base_url=os.getenv("EMBED_BASE_URL") or os.getenv("LLM_BASE_URL"),
                check_embedding_ctx_length=False,
            )

        if provider == "auto":
            try:
                return HuggingFaceEmbeddings(model_name=local_model_name)
            except Exception:
                return OpenAIEmbeddings(
                    model=openai_model_name,
                    api_key=os.getenv("EMBED_API_KEY") or os.getenv("LLM_API_KEY"),
                    base_url=os.getenv("EMBED_BASE_URL") or os.getenv("LLM_BASE_URL"),
                    check_embedding_ctx_length=False,
                )

        raise RuntimeError(f"Unsupported EMBED_PROVIDER={provider!r}. Use auto, local, or openai.")

    def _init_sqlite(self):
        """建表：只存结构化的硬指标"""
        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute('''
                       CREATE TABLE IF NOT EXISTS trip_history
                       (
                           trip_id
                           TEXT
                           PRIMARY
                           KEY,
                           user_id
                           TEXT,
                           city
                           TEXT,
                           rating
                           INTEGER,
                           feedback_text
                           TEXT,
                           created_at
                           TIMESTAMP
                       )
                       ''')
        conn.commit()
        conn.close()

    def _init_qdrant(self):
        """建集合：准备存放向量化的评价，并建立高频过滤索引"""
        if not self.qdrant_client.collection_exists(self.qdrant_collection):
            print(f"🔡 正在创建情景记忆向量库 {self.qdrant_collection}")
            print("[EpisodicMemoryManager] 开始 probe embedding 维度", flush=True)
            sample_vector = self.embeddings.embed_query("test")
            print("[EpisodicMemoryManager] probe embedding 维度完成", flush=True)
            actual_dim = len(sample_vector)

            self.qdrant_client.create_collection(
                collection_name=self.qdrant_collection,
                vectors_config=VectorParams(size=actual_dim, distance=Distance.COSINE),
            )

            self.qdrant_client.create_payload_index(
                collection_name=self.qdrant_collection,
                field_name="metadata.user_id",
                field_schema=PayloadSchemaType.KEYWORD,
            )
            print("✅ 集合和User ID过滤索引创建成功")

    def save_experience(self, user_id: str, city: str, rating: int, feedback_text: str):
        trip_id = str(uuid.uuid4())

        conn = sqlite3.connect(self.sqlite_path)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT INTO trip_history VALUES (?, ?, ?, ?, ?, ?)',
            (trip_id, user_id, city, rating, feedback_text, datetime.now())
        )
        conn.commit()
        conn.close()

        from langchain_core.documents import Document
        doc = Document(
            page_content=feedback_text,
            metadata={
                "trip_id": trip_id,
                "user_id": user_id,
                "city": city,
                "rating": rating
            }
        )
        self.vector_store.add_documents([doc])
        print(f"✅ 情景记忆已归档: 用户 {user_id} 去 {city} 的经验(评分: {rating})")

    def recall_lessons(self, user_id: str, current_request_text: str, k: int = 2) -> str:
        """
        根据用户当前的新需求，捞取他历史上最相关的吐槽或经验。（向量检索）
        """
        from qdrant_client.http import models
        user_filter = models.Filter(
            must=[models.FieldCondition(key="metadata.user_id", match=models.MatchValue(value=user_id))]
        )

        # 进行向量检索，找到与当前请求最相关的历史经验
        docs = self.vector_store.similarity_search(
            current_request_text,
            k=k,  #只取最相关的前k条
            filter=user_filter
        )

        if not docs:
            return "该用户暂无相关历史经验反馈。"

        lessons = []
        for d in docs:
            city = d.metadata.get('city', '某地')
            rating = d.metadata.get('rating', '无评分')
            lessons.append(f"- 曾在[{city}]的经历(评分{rating}): {d.page_content}")

        return "\n".join(lessons)
