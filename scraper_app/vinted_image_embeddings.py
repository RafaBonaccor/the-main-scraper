"""Build a durable, restartable image-embedding store from archived Vinted images.

The source scraper database and image archive are read-only inputs. Embeddings and
their provenance are written to a separate SQLite database so experiments cannot
damage marketplace history.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Protocol
from urllib.parse import urlsplit


DEFAULT_SOURCE_DB = Path("data/scraper.db")
DEFAULT_ARCHIVE_DIR = Path("data/deal_archive")
DEFAULT_STORE_DB = Path("data/vinted_image_embeddings.db")
DEFAULT_MODEL = "openai/clip-vit-base-patch32"
PREPROCESSING_VERSION = "clip-auto-processor-rgb-v1"
SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


class ImageEncoder(Protocol):
    model_name: str
    model_revision: str
    preprocessing_version: str
    device: str

    def encode(self, image_path: Path) -> list[float]: ...


@dataclass(frozen=True)
class ImageRecord:
    item_id: str
    item_link: str
    title: str
    status: str
    image_path: Path
    image_url: str
    first_seen_at: str
    sold_at: str
    sale_duration_seconds: int | None
    sale_duration_source: str
    price_value: float | None
    favorite_count: int | None
    seller_member_id: str
    category_path: str
    source_payload_json: str
    source_record_hash: str


def _json_dict(value: object) -> dict:
    try:
        payload = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _as_number(value: object, cast: Callable[[object], object]) -> object | None:
    if value is None or value == "":
        return None
    try:
        return cast(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _observed_sale_duration(first_seen_at: str, sold_at: str) -> int | None:
    first_seen = _parse_datetime(first_seen_at)
    sold = _parse_datetime(sold_at)
    if first_seen is None or sold is None:
        return None
    if first_seen.tzinfo is None and sold.tzinfo is not None:
        first_seen = first_seen.replace(tzinfo=sold.tzinfo)
    elif first_seen.tzinfo is not None and sold.tzinfo is None:
        sold = sold.replace(tzinfo=first_seen.tzinfo)
    seconds = int((sold - first_seen).total_seconds())
    return seconds if seconds >= 0 else None


def iter_archived_image_records(
    source_db_path: str | Path,
    archive_dir: str | Path,
) -> Iterator[ImageRecord]:
    """Stream eligible deal rows and resolve only files inside the archive root."""
    source_db = Path(source_db_path).expanduser().resolve()
    archive_root = Path(archive_dir).expanduser().resolve()
    if not source_db.is_file():
        raise FileNotFoundError(f"Database sorgente non trovato: {source_db}")
    if not archive_root.is_dir():
        raise FileNotFoundError(f"Archivio immagini non trovato: {archive_root}")

    uri = source_db.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            """
            SELECT item_link, item_id, item_name, first_seen_at, sold_at, status, payload_json
            FROM vinted_deal_sale_tracking
            WHERE status IN ('sold', 'active')
            ORDER BY CASE status WHEN 'sold' THEN 0 ELSE 1 END, first_seen_at ASC, item_id ASC
            """
        )
        for row in rows:
            payload = _json_dict(row["payload_json"])
            item_id = str(row["item_id"] or payload.get("item_id", "") or "").strip()
            item_link = str(row["item_link"] or payload.get("link", "") or "").strip()
            if not item_id and item_link:
                try:
                    item_id = Path(urlsplit(item_link).path).name.split("-", 1)[0]
                except ValueError:
                    item_id = ""
            raw_path = str(payload.get("archive_image_path", "") or "").strip()
            candidate = Path(raw_path).expanduser() if raw_path else Path()
            if not raw_path or not candidate.is_absolute():
                continue
            try:
                image_path = candidate.resolve(strict=True)
                if not image_path.is_relative_to(archive_root) or not image_path.is_file():
                    continue
            except (OSError, RuntimeError):
                continue
            if image_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                continue

            first_seen_at = str(row["first_seen_at"] or payload.get("deal_first_seen_at", "") or "")
            sold_at = str(row["sold_at"] or "")
            duration = _as_number(payload.get("deal_sold_after_seconds"), int)
            duration_source = str(payload.get("deal_sale_duration_source", "") or "")
            if duration is None and str(row["status"] or "") == "sold":
                duration = _observed_sale_duration(first_seen_at, sold_at)
                duration_source = "observed_first_seen_to_sold" if duration is not None else "unknown"
            elif duration is not None and not duration_source:
                duration_source = "payload"

            stable_payload = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            yield ImageRecord(
                item_id=item_id,
                item_link=item_link,
                title=str(row["item_name"] or payload.get("name", "") or ""),
                status=str(row["status"] or ""),
                image_path=image_path,
                image_url=str(payload.get("image_url", "") or ""),
                first_seen_at=first_seen_at,
                sold_at=sold_at,
                sale_duration_seconds=int(duration) if duration is not None else None,
                sale_duration_source=duration_source,
                price_value=_as_number(payload.get("price_value"), float),
                favorite_count=_as_number(payload.get("favorite_count", payload.get("favorites_count")), int),
                seller_member_id=str(payload.get("seller_member_id", "") or ""),
                category_path=str(payload.get("detail_category_path", payload.get("category_path", "")) or ""),
                source_payload_json=stable_payload,
                source_record_hash=hashlib.sha256(stable_payload.encode("utf-8")).hexdigest(),
            )


def _hash_image(path: Path) -> tuple[str, int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as stream:
        header = stream.read(16)
        if not header:
            raise ValueError("file immagine vuoto")
        stream.seek(0)
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
    image_format = _detect_image_format(header)
    if not image_format:
        raise ValueError("formato immagine non riconosciuto")
    return digest.hexdigest(), byte_count, image_format


def _detect_image_format(header: bytes) -> str:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if header.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "WEBP"
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "GIF"
    if header.startswith(b"BM"):
        return "BMP"
    return ""


def _ensure_store_schema(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS image_assets (
            image_sha256 TEXT PRIMARY KEY,
            original_path TEXT NOT NULL,
            byte_size INTEGER NOT NULL,
            image_format TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            first_indexed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS image_asset_locations (
            image_sha256 TEXT NOT NULL REFERENCES image_assets(image_sha256),
            image_path TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            PRIMARY KEY(image_sha256, image_path)
        );
        CREATE TABLE IF NOT EXISTS listing_image_observations (
            observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_db_path TEXT NOT NULL,
            item_id TEXT NOT NULL,
            item_link TEXT NOT NULL,
            image_sha256 TEXT NOT NULL REFERENCES image_assets(image_sha256),
            image_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            first_seen_at TEXT NOT NULL DEFAULT '',
            sold_at TEXT NOT NULL DEFAULT '',
            sale_duration_seconds INTEGER,
            sale_duration_source TEXT NOT NULL DEFAULT '',
            price_value REAL,
            favorite_count INTEGER,
            seller_member_id TEXT NOT NULL DEFAULT '',
            category_path TEXT NOT NULL DEFAULT '',
            source_record_hash TEXT NOT NULL,
            source_payload_json TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            UNIQUE(source_db_path, item_id, image_sha256, status, sold_at, source_record_hash),
            UNIQUE(source_db_path, item_link, image_sha256, status, sold_at, source_record_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_image_observations_item_id
            ON listing_image_observations(item_id, indexed_at);
        CREATE INDEX IF NOT EXISTS idx_image_observations_status
            ON listing_image_observations(status, sale_duration_seconds);
        CREATE TABLE IF NOT EXISTS image_embeddings (
            image_sha256 TEXT NOT NULL REFERENCES image_assets(image_sha256),
            model_name TEXT NOT NULL,
            model_revision TEXT NOT NULL,
            preprocessing_version TEXT NOT NULL,
            embedding_dim INTEGER NOT NULL,
            vector_dtype TEXT NOT NULL,
            vector_blob BLOB NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(image_sha256, model_name, model_revision, preprocessing_version)
        );
        CREATE TABLE IF NOT EXISTS embedding_jobs (
            image_sha256 TEXT NOT NULL REFERENCES image_assets(image_sha256),
            model_name TEXT NOT NULL,
            model_revision TEXT NOT NULL,
            preprocessing_version TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','running','complete','failed')),
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL DEFAULT '',
            completed_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(image_sha256, model_name, model_revision, preprocessing_version)
        );
        CREATE TABLE IF NOT EXISTS embedding_runs (
            run_id TEXT PRIMARY KEY,
            source_db_path TEXT NOT NULL,
            model_name TEXT NOT NULL,
            model_revision_requested TEXT NOT NULL,
            preprocessing_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            indexed_assets INTEGER NOT NULL DEFAULT 0,
            embedded_assets INTEGER NOT NULL DEFAULT 0,
            already_embedded INTEGER NOT NULL DEFAULT 0,
            failed_assets INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )


def _backup_store_if_present(store_path: Path, backup_dir: Path) -> Path | None:
    if not store_path.is_file() or store_path.stat().st_size <= 0:
        return None
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{store_path.stem}_{timestamp}_{uuid.uuid4().hex[:8]}{store_path.suffix}"
    with closing(sqlite3.connect(store_path)) as source, closing(sqlite3.connect(backup_path)) as destination:
        source.backup(destination)
        integrity = str(destination.execute("PRAGMA integrity_check").fetchone()[0] or "")
        if integrity.lower() != "ok":
            backup_path.unlink(missing_ok=True)
            raise sqlite3.DatabaseError(f"Backup integrity check fallito: {integrity}")
    return backup_path


def _encode_float32(values: list[float]) -> bytes:
    if not values:
        raise ValueError("embedding vuoto")
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("embedding contiene valori non finiti")
    norm = math.sqrt(sum(float(value) * float(value) for value in values))
    if norm <= 0:
        raise ValueError("norma embedding nulla")
    normalized = [float(value) / norm for value in values]
    return struct.pack(f"<{len(normalized)}f", *normalized)


class ClipImageEncoder:
    def __init__(self, model_name: str, revision: str, device: str = "auto") -> None:
        try:
            import torch
            import PIL
            from PIL import Image
            import transformers
            from huggingface_hub import snapshot_download
            from transformers import AutoProcessor, CLIPModel
        except ImportError as error:
            raise RuntimeError(
                "Dipendenze embeddings mancanti. Installa requirements-vinted-embeddings.txt "
                "nell'ambiente Python dello scraper."
            ) from error

        self._torch = torch
        self._image_module = Image
        self.model_name = model_name
        self.device = self._choose_device(torch, device)
        self.preprocessing_version = (
            f"{PREPROCESSING_VERSION}+torch-{torch.__version__}"
            f"+transformers-{transformers.__version__}+pillow-{PIL.__version__}"
        )
        snapshot_path = snapshot_download(
            repo_id=model_name,
            revision=revision,
            allow_patterns=["*.json", "*.txt", "pytorch_model.bin"],
        )
        self._processor = AutoProcessor.from_pretrained(snapshot_path, local_files_only=True)
        self._model = CLIPModel.from_pretrained(
            snapshot_path,
            use_safetensors=False,
            local_files_only=True,
        ).to(self.device).eval()
        self.model_revision = Path(snapshot_path).name

    @staticmethod
    def _choose_device(torch, requested: str) -> str:
        if requested != "auto":
            if requested == "cuda" and not torch.cuda.is_available():
                raise RuntimeError("Device CUDA richiesto ma non disponibile.")
            if requested == "mps" and not (hasattr(torch.backends, "mps") and torch.backends.mps.is_available()):
                raise RuntimeError("Device MPS richiesto ma non disponibile.")
            return requested
        if torch.cuda.is_available():
            return "cuda"
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps"
        return "cpu"

    def encode(self, image_path: Path) -> list[float]:
        with self._image_module.open(image_path) as image:
            rgb_image = image.convert("RGB")
            inputs = self._processor(images=rgb_image, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with self._torch.inference_mode():
            features = self._model.get_image_features(**inputs)
        if not isinstance(features, self._torch.Tensor):
            output = features
            features = getattr(output, "pooler_output", None)
            if features is None:
                features = getattr(output, "image_embeds", None)
        if features is None:
            raise RuntimeError("Il modello non ha restituito image features.")
        if features.ndim == 2:
            features = features[0]
        return [float(value) for value in features.detach().float().cpu().tolist()]


def run_embedding_batch(
    *,
    source_db_path: str | Path = DEFAULT_SOURCE_DB,
    archive_dir: str | Path = DEFAULT_ARCHIVE_DIR,
    store_db_path: str | Path = DEFAULT_STORE_DB,
    backup_dir: str | Path | None = None,
    limit: int = 0,
    index_only: bool = False,
    retry_failed: bool = False,
    model_name: str = DEFAULT_MODEL,
    model_revision: str = "main",
    device: str = "auto",
    encoder: ImageEncoder | None = None,
) -> dict[str, object]:
    source_db = Path(source_db_path).expanduser().resolve()
    archive_root = Path(archive_dir).expanduser().resolve()
    store_path = Path(store_db_path).expanduser().resolve()
    actual_backup_dir = Path(backup_dir).expanduser().resolve() if backup_dir else store_path.parent / "backups"
    if store_path == source_db:
        raise ValueError("Il DB embedding deve essere distinto dal DB sorgente.")
    if not index_only and encoder is None:
        encoder = ClipImageEncoder(model_name=model_name, revision=model_revision, device=device)
    effective_model_revision = str(getattr(encoder, "model_revision", model_revision) or model_revision)
    effective_model_name = str(getattr(encoder, "model_name", model_name) or model_name)
    effective_preprocessing_version = str(
        getattr(encoder, "preprocessing_version", PREPROCESSING_VERSION) or PREPROCESSING_VERSION
    )

    # Materialize the small metadata snapshot and close the source DB before potentially
    # long model inference, so no read transaction stays open while the scraper writes.
    records = list(iter_archived_image_records(source_db, archive_root))
    store_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = _backup_store_if_present(store_path, actual_backup_dir)
    timestamp = datetime.now().isoformat(timespec="seconds")
    run_id = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{uuid.uuid4().hex[:8]}"
    counters = {
        "source_records": len(records),
        "indexed_assets": 0,
        "embedded_assets": 0,
        "already_embedded": 0,
        "failed_assets": 0,
        "skipped_failed_assets": 0,
    }
    errors: list[dict[str, str]] = []

    with closing(sqlite3.connect(store_path, timeout=30)) as connection:
        connection.row_factory = sqlite3.Row
        _ensure_store_schema(connection)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            """
            INSERT INTO embedding_runs (
                run_id, source_db_path, model_name, model_revision_requested,
                preprocessing_version, started_at, status
            ) VALUES (?, ?, ?, ?, ?, ?, 'running')
            """,
            (run_id, str(source_db), effective_model_name, str(model_revision), effective_preprocessing_version, timestamp),
        )
        connection.commit()

        seen_hashes: set[str] = set()
        processed_embedding_hashes: set[str] = set()
        for record in records:
            image_sha256 = ""
            try:
                image_sha256, byte_size, image_format = _hash_image(record.image_path)
                if limit > 0 and image_sha256 not in seen_hashes and len(seen_hashes) >= limit:
                    break
                is_new_asset = image_sha256 not in seen_hashes
                seen_hashes.add(image_sha256)
                indexed_at = datetime.now().isoformat(timespec="seconds")
                with connection:
                    connection.execute(
                        """
                        INSERT INTO image_assets(image_sha256,original_path,byte_size,image_format,first_indexed_at)
                        VALUES(?,?,?,?,?)
                        ON CONFLICT(image_sha256) DO NOTHING
                        """,
                        (image_sha256, str(record.image_path), byte_size, image_format, indexed_at),
                    )
                    connection.execute(
                        """INSERT OR IGNORE INTO image_asset_locations(image_sha256,image_path,first_seen_at)
                           VALUES(?,?,?)""",
                        (image_sha256, str(record.image_path), indexed_at),
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO listing_image_observations(
                            source_db_path,item_id,item_link,image_sha256,image_url,title,status,first_seen_at,sold_at,
                            sale_duration_seconds,sale_duration_source,price_value,favorite_count,seller_member_id,
                            category_path,source_record_hash,source_payload_json,indexed_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            str(source_db), record.item_id, record.item_link, image_sha256, record.image_url, record.title,
                            record.status, record.first_seen_at, record.sold_at, record.sale_duration_seconds,
                            record.sale_duration_source, record.price_value, record.favorite_count,
                            record.seller_member_id, record.category_path, record.source_record_hash,
                            record.source_payload_json, indexed_at,
                        ),
                    )
                if is_new_asset:
                    counters["indexed_assets"] += 1

                if index_only:
                    continue
                if image_sha256 in processed_embedding_hashes:
                    continue
                processed_embedding_hashes.add(image_sha256)
                embedding_key = (image_sha256, effective_model_name, effective_model_revision, effective_preprocessing_version)
                exists = connection.execute(
                    """SELECT 1 FROM image_embeddings WHERE image_sha256=? AND model_name=?
                       AND model_revision=? AND preprocessing_version=?""",
                    embedding_key,
                ).fetchone()
                if exists:
                    counters["already_embedded"] += 1
                    continue
                prior_job = connection.execute(
                    """SELECT status,attempts FROM embedding_jobs WHERE image_sha256=? AND model_name=?
                       AND model_revision=? AND preprocessing_version=?""",
                    embedding_key,
                ).fetchone()
                if prior_job and prior_job["status"] == "failed" and not retry_failed:
                    counters["skipped_failed_assets"] += 1
                    errors.append({
                        "item_id": record.item_id,
                        "path": str(record.image_path),
                        "error": f"Tentativo precedente fallito: {prior_job['last_error']} (usa --retry-failed)",
                    })
                    continue
                attempts = int(prior_job["attempts"] if prior_job else 0) + 1
                with connection:
                    connection.execute(
                        """INSERT INTO embedding_jobs(
                           image_sha256,model_name,model_revision,preprocessing_version,status,attempts,
                           last_error,started_at,completed_at
                           ) VALUES(?,?,?,?, 'running', ?, '', ?, '')
                           ON CONFLICT(image_sha256,model_name,model_revision,preprocessing_version)
                           DO UPDATE SET status='running',attempts=excluded.attempts,last_error='',
                             started_at=excluded.started_at,completed_at=''""",
                        (*embedding_key, attempts, indexed_at),
                    )
                assert encoder is not None
                vector_values = encoder.encode(record.image_path)
                vector = _encode_float32(vector_values)
                with connection:
                    connection.execute(
                        """INSERT OR IGNORE INTO image_embeddings(
                           image_sha256,model_name,model_revision,preprocessing_version,
                           embedding_dim,vector_dtype,vector_blob,created_at
                           ) VALUES(?,?,?,?,?,?,?,?)""",
                        (*embedding_key, len(vector_values), "float32_le_l2_normalized", vector, indexed_at),
                    )
                    connection.execute(
                        """UPDATE embedding_jobs SET status='complete',last_error='',completed_at=?
                           WHERE image_sha256=? AND model_name=? AND model_revision=? AND preprocessing_version=?""",
                        (datetime.now().isoformat(timespec="seconds"), *embedding_key),
                    )
                counters["embedded_assets"] += 1
            except Exception as error:  # one invalid image must not abort a recoverable batch
                counters["failed_assets"] += 1
                message = f"{type(error).__name__}: {error}"[:1000]
                errors.append({"item_id": record.item_id, "path": str(record.image_path), "error": message})
                try:
                    if image_sha256 and not index_only:
                        with connection:
                            connection.execute(
                                """INSERT INTO embedding_jobs(
                                   image_sha256,model_name,model_revision,preprocessing_version,status,
                                   attempts,last_error,started_at,completed_at
                                   ) VALUES(?,?,?,?,'failed',1,?,?,?)
                                   ON CONFLICT(image_sha256,model_name,model_revision,preprocessing_version)
                                   DO UPDATE SET status='failed',last_error=excluded.last_error,completed_at=excluded.completed_at""",
                                (image_sha256, effective_model_name, effective_model_revision, effective_preprocessing_version,
                                 message, datetime.now().isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
                            )
                except sqlite3.Error:
                    pass

        run_status = "completed_with_errors" if errors else "completed"
        summary = {**counters, "errors": errors, "backup_path": str(backup_path or "")}
        with connection:
            connection.execute(
                """UPDATE embedding_runs SET finished_at=?,status=?,indexed_assets=?,embedded_assets=?,
                   already_embedded=?,failed_assets=?,summary_json=? WHERE run_id=?""",
                (datetime.now().isoformat(timespec="seconds"), run_status, counters["indexed_assets"],
                 counters["embedded_assets"], counters["already_embedded"], counters["failed_assets"],
                 json.dumps(summary, ensure_ascii=False), run_id),
            )
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    return {"ok": not errors, "run_id": run_id, "status": run_status, **counters, "errors": errors, "backup_path": str(backup_path or ""), "store_path": str(store_path)}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build Vinted image embeddings from archived images.")
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE_DB), help="Read-only source scraper database.")
    parser.add_argument("--archive-dir", default=str(DEFAULT_ARCHIVE_DIR), help="Root folder containing archived product images.")
    parser.add_argument("--store-db", default=str(DEFAULT_STORE_DB), help="Separate durable SQLite embedding store.")
    parser.add_argument("--backup-dir", default="", help="Backup folder for the embedding store; defaults beside the store DB.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum unique images to process; 0 means all eligible archived images.")
    parser.add_argument("--index-only", action="store_true", help="Index images and source labels without downloading or loading a model.")
    parser.add_argument("--retry-failed", action="store_true", help="Retry images whose prior embedding attempt failed.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Hugging Face CLIP model id.")
    parser.add_argument("--revision", default="main", help="Model revision/tag or commit hash to load.")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda", "mps"), default="auto")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.limit < 0:
        raise SystemExit("--limit deve essere >= 0")
    try:
        result = run_embedding_batch(
            source_db_path=args.source_db,
            archive_dir=args.archive_dir,
            store_db_path=args.store_db,
            backup_dir=args.backup_dir or None,
            limit=args.limit,
            index_only=args.index_only,
            retry_failed=args.retry_failed,
            model_name=args.model,
            model_revision=args.revision,
            device=args.device,
        )
    except Exception as error:
        result = {"ok": False, "error": f"{type(error).__name__}: {error}"}
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
