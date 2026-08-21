import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class EmbeddingProviderError(RuntimeError):
    pass


class BaseEmbeddingProvider:
    provider_type = "base"

    @property
    def key(self):
        raise NotImplementedError

    def embed(self, texts):
        raise NotImplementedError

    def status(self):
        return {
            "ready": True,
            "provider": self.provider_type,
            "provider_key": self.key,
        }


class SentenceTransformerEmbeddingProvider(BaseEmbeddingProvider):
    provider_type = "sentence_transformers"

    def __init__(self, model_name, device="cpu", batch_size=16):
        self.model_name = str(model_name or "BAAI/bge-m3").strip()
        self.device = str(device or "cpu").strip()
        self.batch_size = max(1, min(256, int(batch_size or 16)))
        self._model = None
        self.last_error = ""

    @property
    def key(self):
        return f"sentence_transformers:{self.model_name}"

    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingProviderError(
                "sentence-transformers is not installed. Install the optional embedding dependency first."
            ) from exc
        try:
            self._model = SentenceTransformer(self.model_name, device=self.device)
            self.last_error = ""
            return self._model
        except Exception as exc:
            self.last_error = str(exc)
            raise EmbeddingProviderError(f"Unable to load embedding model: {exc}") from exc

    def embed(self, texts):
        texts = [str(text or "") for text in texts]
        if not texts:
            return []
        model = self._load()
        try:
            vectors = model.encode(
                texts,
                batch_size=self.batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
            return [[float(value) for value in vector] for vector in vectors]
        except Exception as exc:
            self.last_error = str(exc)
            raise EmbeddingProviderError(f"Embedding inference failed: {exc}") from exc

    def status(self):
        try:
            import sentence_transformers  # noqa: F401
            dependency_available = True
        except ImportError:
            dependency_available = False
        return {
            "ready": dependency_available,
            "provider": self.provider_type,
            "provider_key": self.key,
            "model": self.model_name,
            "device": self.device,
            "dependency_available": dependency_available,
            "model_loaded": self._model is not None,
            "last_error": self.last_error,
        }


class OpenAICompatibleEmbeddingProvider(BaseEmbeddingProvider):
    provider_type = "openai_compatible"

    def __init__(self, api_url, model_name, api_key_env="EMBEDDING_API_KEY", batch_size=32, timeout=120):
        self.api_url = self._normalize_url(api_url)
        self.model_name = str(model_name or "").strip()
        self.api_key_env = str(api_key_env or "EMBEDDING_API_KEY").strip()
        self.batch_size = max(1, min(256, int(batch_size or 32)))
        self.timeout = max(10, min(600, int(timeout or 120)))
        self.last_error = ""
        if not self.model_name:
            raise EmbeddingProviderError("An embedding model name is required.")

    def _normalize_url(self, value):
        raw = str(value or "").strip().rstrip("/")
        if not raw:
            raise EmbeddingProviderError("An embedding API URL is required.")
        parsed = urlparse(raw)
        if parsed.scheme not in {"http", "https"}:
            raise EmbeddingProviderError("Embedding API URL must use http or https.")
        if raw.endswith("/embeddings"):
            return raw
        if raw.endswith("/v1"):
            return raw + "/embeddings"
        return raw + "/v1/embeddings"

    @property
    def key(self):
        return f"openai_compatible:{self.api_url}:{self.model_name}"

    def _api_key(self):
        return str(os.environ.get(self.api_key_env, "") or "").strip()

    def embed(self, texts):
        texts = [str(text or "") for text in texts]
        if not texts:
            return []
        api_key = self._api_key()
        if not api_key:
            raise EmbeddingProviderError(
                f"Embedding API key is missing from environment variable {self.api_key_env}."
            )
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        vectors = []
        try:
            for start in range(0, len(texts), self.batch_size):
                batch = texts[start:start + self.batch_size]
                request = Request(
                    self.api_url,
                    data=json.dumps({"model": self.model_name, "input": batch}).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, list) or len(data) != len(batch):
                    raise EmbeddingProviderError("Embedding API returned an unexpected number of vectors.")
                data.sort(key=lambda item: int(item.get("index", 0)))
                for item in data:
                    vector = item.get("embedding")
                    if not isinstance(vector, list) or not vector:
                        raise EmbeddingProviderError("Embedding API returned an invalid vector.")
                    vectors.append([float(value) for value in vector])
            self.last_error = ""
            return vectors
        except EmbeddingProviderError:
            raise
        except HTTPError as exc:
            self.last_error = f"HTTP {exc.code}"
            raise EmbeddingProviderError(f"Embedding API request failed with HTTP {exc.code}.") from exc
        except URLError as exc:
            self.last_error = str(exc.reason)
            raise EmbeddingProviderError(f"Embedding API connection failed: {exc.reason}") from exc
        except Exception as exc:
            self.last_error = str(exc)
            raise EmbeddingProviderError(f"Embedding API request failed: {exc}") from exc

    def status(self):
        return {
            "ready": bool(self._api_key() and self.model_name and self.api_url),
            "provider": self.provider_type,
            "provider_key": self.key,
            "model": self.model_name,
            "api_url": self.api_url,
            "api_key_env": self.api_key_env,
            "api_key_available": bool(self._api_key()),
            "last_error": self.last_error,
        }


def create_embedding_provider(settings):
    provider_type = str(settings.get("embedding_provider") or "sentence_transformers").strip().lower()
    batch_size = settings.get("embedding_batch_size", 16)
    if provider_type == "sentence_transformers":
        return SentenceTransformerEmbeddingProvider(
            model_name=settings.get("embedding_model", "BAAI/bge-m3"),
            device=settings.get("embedding_device", "cpu"),
            batch_size=batch_size,
        )
    if provider_type == "openai_compatible":
        return OpenAICompatibleEmbeddingProvider(
            api_url=settings.get("embedding_api_url", ""),
            model_name=settings.get("embedding_model", ""),
            api_key_env=settings.get("embedding_api_key_env", "EMBEDDING_API_KEY"),
            batch_size=batch_size,
            timeout=settings.get("embedding_api_timeout_seconds", 120),
        )
    raise EmbeddingProviderError(f"Unsupported embedding provider: {provider_type}")
