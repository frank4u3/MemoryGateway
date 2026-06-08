import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from gateway.memory.schemas import MemoryPack, MemoryPackFile

_MEMORY_DIR = Path("memory")
_CURRENT_LINK = _MEMORY_DIR / "current"


class MemoryStore:
    """Versioned file-based memory pack store with Redis caching."""

    def __init__(self, base_dir: Optional[str] = None, redis_client=None):
        self._base = Path(base_dir) if base_dir else _MEMORY_DIR
        self._base.mkdir(parents=True, exist_ok=True)
        self._redis = redis_client
        self._current_version: Optional[str] = None

    # ---- Write ----

    def save(self, pack: MemoryPack) -> None:
        version_dir = self._base / pack.version
        version_dir.mkdir(parents=True, exist_ok=True)

        for f in pack.files:
            path = version_dir / f.filename
            path.write_text(f.content, encoding="utf-8")

        meta = {
            "version": pack.version,
            "created_at": pack.created_at,
            "checksum": pack.checksum,
            "files": [f.filename for f in pack.files],
        }
        (version_dir / "metadata.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8"
        )

        self._current_version = pack.version

        cursor = self._base / "current"
        if cursor.exists():
            cursor.unlink()
        cursor.write_text(pack.version, encoding="utf-8")

        if self._redis:
            try:
                self._redis.set(
                    "memory:current",
                    json.dumps(
                        {
                            "version": pack.version,
                            "created_at": pack.created_at,
                            "checksum": pack.checksum,
                            "files": {
                                f.filename: f.content for f in pack.files
                            },
                        }
                    ),
                )
            except Exception:
                pass

    # ---- Read ----

    def load(self, version: str) -> Optional[MemoryPack]:
        if self._redis and version == "current":
            cached = self._load_from_redis()
            if cached:
                return cached

        version_dir = self._base / version
        if not version_dir.is_dir():
            return None

        meta_path = version_dir / "metadata.json"
        if not meta_path.exists():
            return None

        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        files = []
        for filename in meta.get("files", []):
            path = version_dir / filename
            if path.exists():
                files.append(
                    MemoryPackFile(
                        filename=filename,
                        content=path.read_text(encoding="utf-8"),
                    )
                )

        return MemoryPack(
            version=meta.get("version", version),
            created_at=meta.get("created_at", ""),
            checksum=meta.get("checksum", ""),
            files=files,
        )

    def _load_from_redis(self) -> Optional[MemoryPack]:
        try:
            data = self._redis.get("memory:current")
            if data:
                parsed = json.loads(data)
                return MemoryPack(
                    version=parsed["version"],
                    created_at=parsed["created_at"],
                    checksum=parsed["checksum"],
                    files=[
                        MemoryPackFile(filename=k, content=v)
                        for k, v in parsed["files"].items()
                    ],
                )
        except Exception:
            pass
        return None

    def current(self) -> Optional[MemoryPack]:
        if self._redis:
            cached = self._load_from_redis()
            if cached:
                return cached

        cursor = self._base / "current"
        if not cursor.exists():
            return None
        version = cursor.read_text(encoding="utf-8").strip()
        return self.load(version)

    def current_version(self) -> Optional[str]:
        if self._current_version:
            return self._current_version
        cursor = self._base / "current"
        if cursor.exists():
            return cursor.read_text(encoding="utf-8").strip()
        return None

    def list_versions(self) -> list[str]:
        versions = []
        for entry in self._base.iterdir():
            if entry.is_dir() and (entry / "metadata.json").exists():
                versions.append(entry.name)
        return sorted(versions)


def build_pack(
    files: dict[str, str],
    version: Optional[str] = None,
) -> MemoryPack:
    if version is None:
        version = f"v{int(time.time())}"
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    sorted_keys = sorted(files.keys())
    raw = "".join(files[k] for k in sorted_keys)
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    return MemoryPack(
        version=version,
        created_at=created_at,
        checksum=checksum,
        files=[
            MemoryPackFile(filename=k, content=v)
            for k, v in sorted(files.items())
        ],
    )
