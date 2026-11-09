"""Локальные multilingual embeddings: статус не загружает ONNX и не обращается к сети."""
from __future__ import annotations

import hashlib
from importlib.metadata import version
from pathlib import Path
from threading import RLock


class SemanticIndexError(RuntimeError):
    """Ожидаемая ошибка локального индекса с HTTP-статусом для вызывающего сервиса."""

    def __init__(self, message: str, status: int = 503):
        super().__init__(message)
        self.status = status


class LocalEmbedding:
    """Лениво использует уже установленную модель; загрузка из интернета запрещена."""

    NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    DIMENSIONS = 384
    PAYLOAD_TOKENS = 126
    CACHE_NAME = "models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
    FILES = ("model_optimized.onnx", "config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json")

    def __init__(self, cache_dir: Path):
        """Сохраняет путь к глобальному cache; никаких импортов ONNX или чтения весов."""
        self.cache_dir = Path(cache_dir)
        self._lock = RLock()
        self._identity = None
        self._tokenizer = None
        self._model = None

    def _model_dir(self) -> Path | None:
        """Находит локальный snapshot через refs/main либо явно распакованный каталог."""
        for direct in (self.cache_dir, self.cache_dir / self.CACHE_NAME):
            if all((direct / name).is_file() for name in self.FILES):
                return direct
        root = self.cache_dir / self.CACHE_NAME
        try:
            revision = (root / "refs" / "main").read_text(encoding="utf-8").strip()
        except OSError:
            return None
        # refs — данные cache, а не разрешение читать произвольный путь.
        if not revision or any(char not in "0123456789abcdef" for char in revision.lower()):
            return None
        snapshot = root / "snapshots" / revision
        return snapshot if all((snapshot / name).is_file() for name in self.FILES) else None

    def available(self) -> bool:
        """Проверяет только наличие локальных файлов, без хеширования и инициализации модели."""
        return self._model_dir() is not None

    def metadata(self) -> dict:
        """Дешёвые сведения для status UI; не являются fingerprint конкретных весов."""
        return {"name": self.NAME, "dimensions": self.DIMENSIONS, "available": self.available(), "cacheDir": str(self.cache_dir), "payloadTokens": self.PAYLOAD_TOKENS, "offline": True}

    def _require_dir(self) -> Path:
        """Явно сообщает об отсутствующей установке, не пробуя скачать модель."""
        directory = self._model_dir()
        if directory is None:
            raise SemanticIndexError("Локальная embedding-модель не установлена. Установите модель отдельно перед индексацией.")
        return directory

    def identity(self) -> dict:
        """Один раз хеширует веса, tokenizer и config; fingerprint включает алгоритм и версию engine."""
        with self._lock:
            if self._identity is None:
                directory = self._require_dir()
                engine = version("fastembed")
                digest = hashlib.sha256(f"{self.NAME}|{self.DIMENSIONS}|{engine}|mean-normalized|payload126-v1".encode())
                for name in self.FILES:
                    digest.update(name.encode())
                    with (directory / name).open("rb") as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b""):
                            digest.update(block)
                self._identity = {"name": self.NAME, "dimensions": self.DIMENSIONS, "fingerprint": digest.hexdigest(), "engine": f"fastembed/{engine}", "payloadTokens": self.PAYLOAD_TOKENS, "offline": True}
            return dict(self._identity)

    def _get_tokenizer(self):
        """Загружает только локальный tokenizer без padding/truncation для точного бюджета."""
        with self._lock:
            if self._tokenizer is None:
                from tokenizers import Tokenizer
                self._tokenizer = Tokenizer.from_file(str(self._require_dir() / "tokenizer.json"))
                self._tokenizer.no_truncation()
                self._tokenizer.no_padding()
            return self._tokenizer

    def split_text(self, text: str, max_tokens: int = PAYLOAD_TOKENS) -> list[dict]:
        """Делит текст без потери символов; каждый slice повторно проверен локальным tokenizer."""
        if not isinstance(text, str):
            raise SemanticIndexError("Текст embedding должен быть строкой.", 400)
        if not 1 <= max_tokens <= self.PAYLOAD_TOKENS:
            raise SemanticIndexError("Некорректный token budget.", 400)
        if not text.strip():
            return []
        tokenizer = self._get_tokenizer()
        parts, start = [], 0
        while start < len(text):
            remaining = text[start:]
            encoding = tokenizer.encode(remaining, add_special_tokens=False)
            if not encoding.ids and parts:
                # Хвост из пробелов относится к последнему slice, а не к пустому embedding.
                parts[-1]["text"] += remaining
                parts[-1]["end"] = len(text)
                break
            if len(encoding.ids) <= max_tokens:
                parts.append({"text": remaining, "start": start, "end": len(text), "tokenCount": len(encoding.ids)})
                break
            token_end = max_tokens
            end = encoding.offsets[token_end - 1][1]
            # Срез внутри слова может заново токенизироваться иначе. Проверяем именно embedding-вход.
            count = len(tokenizer.encode(remaining[:end], add_special_tokens=False).ids)
            while count > max_tokens and token_end > 1:
                token_end -= 1
                end = encoding.offsets[token_end - 1][1]
                count = len(tokenizer.encode(remaining[:end], add_special_tokens=False).ids)
            if end <= 0 or count > max_tokens:
                raise SemanticIndexError("Tokenizer не смог разделить текст в пределах token budget.")
            parts.append({"text": remaining[:end], "start": start, "end": start + end, "tokenCount": count})
            start += end
        return parts

    def _get_model(self):
        """FastEmbed получает конкретный локальный каталог, CPU provider и два потока."""
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                directory = self._require_dir()
                try:
                    self._model = TextEmbedding(model_name=self.NAME, cache_dir=str(self.cache_dir), specific_model_path=str(directory), local_files_only=True, providers=["CPUExecutionProvider"], threads=2)
                except Exception as error:
                    raise SemanticIndexError("Не удалось загрузить локальную embedding-модель.") from error
            return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Кодирует проверенные короткие тексты; слишком длинный вход отклоняется явно."""
        if not texts:
            return []
        tokenizer = self._get_tokenizer()
        for text in texts:
            if not isinstance(text, str) or not text.strip():
                raise SemanticIndexError("Пустой или некорректный embedding-текст.", 400)
            if len(tokenizer.encode(text, add_special_tokens=False).ids) > self.PAYLOAD_TOKENS:
                raise SemanticIndexError("Текст превышает 126 токенов; требуется разделение.", 400)
        return [vector.tolist() for vector in self._get_model().embed(texts, batch_size=32, parallel=None)]

    def query(self, text: str) -> list[float]:
        """Кодирует один запрос без скрытого усечения и без чтения исходного проекта."""
        return self.embed([text])[0]
