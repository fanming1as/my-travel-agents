import os
from functools import wraps
from typing import Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams
from langchain_openai import OpenAIEmbeddings

try:
    from langchain_huggingface import HuggingFaceEmbeddings
except ImportError:  # pragma: no cover
    from langchain_community.embeddings import HuggingFaceEmbeddings

from langchain_qdrant import QdrantVectorStore


def _configure_huggingface_retries() -> None:
    max_retries = int(os.getenv("HF_HUB_MAX_RETRIES", "0"))

    from huggingface_hub.utils import _http as hf_http

    if not getattr(hf_http.http_backoff, "_travel_agent_retry_limited", False):
        original_http_backoff = hf_http.http_backoff

        @wraps(original_http_backoff)
        def limited_http_backoff(*args, **kwargs):
            kwargs.setdefault("max_retries", max_retries)
            return original_http_backoff(*args, **kwargs)

        limited_http_backoff._travel_agent_retry_limited = True
        hf_http.http_backoff = limited_http_backoff

    if not getattr(hf_http.http_stream_backoff, "_travel_agent_retry_limited", False):
        original_http_stream_backoff = hf_http.http_stream_backoff

        @wraps(original_http_stream_backoff)
        def limited_http_stream_backoff(*args, **kwargs):
            kwargs.setdefault("max_retries", max_retries)
            return original_http_stream_backoff(*args, **kwargs)

        limited_http_stream_backoff._travel_agent_retry_limited = True
        hf_http.http_stream_backoff = limited_http_stream_backoff

        try:
            import huggingface_hub.file_download as hf_file_download

            hf_file_download.http_stream_backoff = limited_http_stream_backoff
        except Exception:
            pass


class QdrantSemanticMemory:
    """
    Qdrant-backed semantic memory with embedding fallback.

    Supported providers:
    - auto: try local HuggingFace first, then OpenAI-compatible embeddings
    - local: force HuggingFace only
    - openai: force OpenAI-compatible embeddings only
    """

    def __init__(self, collection_name: str = "travel_guides_v2"):
        load_dotenv()
        _configure_huggingface_retries()
        self.collection_name = collection_name
        self.embedding_backend = None
        self.embedding_model_name = None
        self.embedding_dimension = None

        self.client = self._create_qdrant_client()
        self.embeddings = self._create_embeddings()
        self._ensure_collection_ready()

        self.vector_store = QdrantVectorStore(
            client=self.client,
            collection_name=self.collection_name,
            embedding=self.embeddings,
        )

    def _create_qdrant_client(self) -> QdrantClient:
        qdrant_url = (os.getenv("QDRANT_URL") or "").strip()
        if not qdrant_url:
            raise RuntimeError(
                "QDRANT_URL is not configured. Example: http://localhost:6333"
            )

        parsed = urlparse(qdrant_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError(
                f"Invalid QDRANT_URL={qdrant_url!r}. Use a full URL like "
                "'http://localhost:6333' or your Qdrant Cloud HTTPS endpoint."
            )

        return QdrantClient(
            url=qdrant_url,
            api_key=os.getenv("QDRANT_API_KEY") or None,
            check_compatibility=False,
        )

    def _create_embeddings(self):
        provider = (os.getenv("EMBED_PROVIDER") or "auto").strip().lower()
        local_model_name = (
            os.getenv("LOCAL_EMBED_MODEL_NAME")
            #or "BAAI/bge-m3"
            or "all-MiniLM-L6-v2"
        )
        openai_model_name = (
            os.getenv("OPENAI_EMBED_MODEL_NAME")
            or os.getenv("EMBED_MODEL_NAME")
            or "text-embedding-3-small"
        )

        local_error = None
        openai_error = None

        if provider in {"auto", "local"}:
            try:
                embeddings = HuggingFaceEmbeddings(model_name=local_model_name)
                self._set_embedding_runtime(embeddings, "local", local_model_name)
                return embeddings
            except Exception as exc:
                local_error = exc
                if provider == "local":
                    raise RuntimeError(
                        self._format_embedding_error(
                            preferred="local HuggingFace",
                            model_name=local_model_name,
                            primary_error=local_error,
                            fallback_error=None,
                            fallback_used=False,
                        )
                    ) from exc

        if provider in {"auto", "openai"}:
            try:
                api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
                base_url = os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
                if not api_key:
                    raise RuntimeError(
                        "LLM_API_KEY or OPENAI_API_KEY is required for OpenAI-compatible embeddings."
                    )

                embeddings = OpenAIEmbeddings(
                    model=openai_model_name,
                    api_key=api_key,
                    base_url=base_url,
                    check_embedding_ctx_length=False,
                )
                self._set_embedding_runtime(embeddings, "openai", openai_model_name)
                return embeddings
            except Exception as exc:
                openai_error = exc
                if provider == "openai":
                    raise RuntimeError(
                        self._format_embedding_error(
                            preferred="OpenAI-compatible",
                            model_name=openai_model_name,
                            primary_error=openai_error,
                            fallback_error=None,
                            fallback_used=False,
                        )
                    ) from exc

        if provider == "auto":
            raise RuntimeError(
                self._format_embedding_error(
                    preferred="local HuggingFace",
                    model_name=local_model_name,
                    primary_error=local_error,
                    fallback_error=openai_error,
                    fallback_used=True,
                )
            )

        raise RuntimeError(f"Unsupported EMBED_PROVIDER={provider!r}. Use auto, local, or openai.")

    def _set_embedding_runtime(self, embeddings, backend: str, model_name: str) -> None:
        self.embedding_backend = backend
        self.embedding_model_name = model_name
        self.embedding_dimension = self._probe_embedding_dimension(
            embeddings=embeddings,
            backend=backend,
            model_name=model_name,
        )

    def _probe_embedding_dimension(self, embeddings, backend: str, model_name: str) -> int:
        try:
            vector = embeddings.embed_query("dimension probe")
            return len(vector)
        except Exception as exc:
            raise RuntimeError(
                f"Embedding model '{model_name}' ({backend}) cannot generate a probe vector."
            ) from exc

    def _format_embedding_error(
        self,
        preferred: str,
        model_name: str,
        primary_error: Optional[Exception],
        fallback_error: Optional[Exception],
        fallback_used: bool,
    ) -> str:
        lines = [
            "Failed to initialize embedding model.",
            f"Preferred backend: {preferred}.",
            f"Model: {model_name}.",
        ]
        if primary_error is not None:
            lines.append(f"Primary error: {primary_error}")
        if fallback_used:
            if fallback_error is not None:
                lines.append(f"Fallback error: {fallback_error}")
            lines.append("Tried local HuggingFace first, then OpenAI-compatible embeddings, but both failed.")
        else:
            lines.append("No fallback backend was used.")
        lines.extend(
            [
                "",
                "Suggested fixes:",
                "- If you want offline/local embeddings, set EMBED_PROVIDER=local and use a cached local model.",
                "- If you want API-based embeddings, set EMBED_PROVIDER=openai and configure LLM_API_KEY/LLM_BASE_URL.",
                "- Do not set EMBED_MODEL_NAME to an OpenAI model name when EMBED_PROVIDER is local.",
            ]
        )
        return "\n".join(lines)

    def _ensure_collection_ready(self) -> None:
        if self.embedding_dimension is None:
            raise RuntimeError("Embedding dimension is unavailable after initialization.")

        if not self.client.collection_exists(self.collection_name):
            print(
                f"Detected missing Qdrant collection '{self.collection_name}'. Creating it with dimension {self.embedding_dimension}..."
            )
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(
                    size=self.embedding_dimension,
                    distance=Distance.COSINE,
                ),
            )
            print("Qdrant collection created successfully.")
            return

        try:
            collection_info = self.client.get_collection(self.collection_name)
            vectors_config = collection_info.config.params.vectors
            existing_dim = getattr(vectors_config, "size", None)
            if existing_dim is not None and existing_dim != self.embedding_dimension:
                raise RuntimeError(
                    f"Qdrant collection '{self.collection_name}' already exists with vector size {existing_dim}, "
                    f"but the current embedding model '{self.embedding_model_name}' produces dimension {self.embedding_dimension}. "
                    "Delete/recreate the collection or switch to a matching embedding model."
                )
        except RuntimeError:
            raise
        except Exception:
            pass

    def ingest_local_docs(self, file_path: str):
        from langchain_community.document_loaders import TextLoader
        from langchain_text_splitters import RecursiveCharacterTextSplitter

        loader = TextLoader(file_path, encoding="utf-8")
        documents = loader.load()

        splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=60)
        texts = splitter.split_documents(documents)

        self.vector_store.add_documents(texts)
        print(f"Successfully ingested {file_path} into Qdrant.")

    def search_knowledge(self, query: str, k: int = 3):
        docs = self.vector_store.similarity_search(query, k=k)
        return "\n\n".join(doc.page_content for doc in docs)

    #根据一段查询文本 query，异步地从 Qdrant 语义知识库里找出最相似的几条文档内容
    async def search_knowledge_async(self, query: str, k: int = 3):
        docs = await self.vector_store.asimilarity_search(query, k=k)
        return [(doc.metadata.get("_id", doc.page_content), doc.page_content) for doc in docs]
