import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse


def is_s3_uri(value):
    return isinstance(value, str) and value.startswith("s3://")


def _s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for s3:// inputs/outputs") from exc
    return boto3.client("s3", region_name=os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION"))


def _split_s3(uri):
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"invalid S3 URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def download_dataset(uri, destination=None):
    """Download normal.txt and attack.txt from an S3 file or prefix."""
    bucket, key = _split_s3(uri)
    root = Path(destination or tempfile.mkdtemp(prefix="auto-ml-s3-"))
    root.mkdir(parents=True, exist_ok=True)
    client = _s3_client()
    candidates = []
    if key.endswith(".txt"):
        candidates.append((key, Path(key).name))
    else:
        prefix = key.rstrip("/")
        candidates.extend(((f"{prefix}/normal.txt", "normal.txt"),
                           (f"{prefix}/attack.txt", "attack.txt")))
    downloaded = set()
    for object_key, filename in candidates:
        target = root / filename
        try:
            client.download_file(bucket, object_key, str(target))
            downloaded.add(filename)
        except Exception:
            continue
    if not {"normal.txt", "attack.txt"}.issubset(downloaded):
        raise FileNotFoundError(f"S3 dataset must contain normal.txt and attack.txt under {uri}")
    return root


def upload_directory(local_dir, destination_uri):
    """Upload all files under local_dir; returns uploaded count."""
    bucket, prefix = _split_s3(destination_uri)
    client = _s3_client()
    local_dir = Path(local_dir)
    count = 0
    for path in local_dir.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(local_dir).as_posix()
        key = "/".join(part for part in (prefix.rstrip("/"), relative) if part)
        client.upload_file(str(path), bucket, key)
        count += 1
    return {"status": "uploaded", "uri": destination_uri.rstrip("/") + "/", "files": count}

