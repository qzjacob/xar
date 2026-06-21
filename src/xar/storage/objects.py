"""Object storage for raw artifacts. Default = local filesystem (turnkey, zero
extra services). `s3://` URLs route to MinIO/S3 when boto3 is installed."""
from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlparse

from ..config import get_settings


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:24]


def put(data: bytes, suffix: str = "") -> str:
    """Store bytes, return an object key (used as documents.object_key)."""
    s = get_settings()
    key = f"{_hash(data)}{suffix}"
    uri = urlparse(s.object_store)
    if uri.scheme in ("", "file"):
        base = Path((uri.netloc + uri.path) or "./data/objects")
        base.mkdir(parents=True, exist_ok=True)
        (base / key).write_bytes(data)
        return key
    if uri.scheme == "s3":
        import boto3  # optional dependency

        client = boto3.client("s3")
        client.put_object(Bucket=uri.netloc, Key=f"{uri.path.strip('/')}/{key}", Body=data)
        return key
    raise ValueError(f"unsupported object store: {s.object_store}")


def get(key: str) -> bytes:
    s = get_settings()
    uri = urlparse(s.object_store)
    if uri.scheme in ("", "file"):
        base = Path((uri.netloc + uri.path) or "./data/objects")
        return (base / key).read_bytes()
    if uri.scheme == "s3":
        import boto3

        client = boto3.client("s3")
        obj = client.get_object(Bucket=uri.netloc, Key=f"{uri.path.strip('/')}/{key}")
        return obj["Body"].read()
    raise ValueError(f"unsupported object store: {s.object_store}")
