import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse


def is_s3_uri(value):
    return isinstance(value, str) and value.startswith("s3://")


def _s3_client(settings=None):
    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError("boto3 is required for s3:// inputs/outputs") from exc
    settings = settings or {}
    endpoint_url = (settings.get("endpoint_url") or os.getenv("AWS_ENDPOINT_URL")
                    or os.getenv("S3_ENDPOINT_URL"))
    options = {"region_name": settings.get("region") or os.getenv("AWS_REGION")
               or os.getenv("AWS_DEFAULT_REGION")}
    access_key = settings.get("access_key_id") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = settings.get("secret_access_key") or os.getenv("AWS_SECRET_ACCESS_KEY")
    session_token = settings.get("session_token") or os.getenv("AWS_SESSION_TOKEN")
    if access_key and secret_key:
        options["aws_access_key_id"] = access_key
        options["aws_secret_access_key"] = secret_key
    if session_token:
        options["aws_session_token"] = session_token
    if endpoint_url:
        from botocore.config import Config
        options["endpoint_url"] = endpoint_url.rstrip("/")
        options["config"] = Config(s3={"addressing_style": "path"})
    return boto3.client("s3", **options)


def _split_s3(uri):
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"invalid S3 URI: {uri}")
    return parsed.netloc, unquote(parsed.path.lstrip("/"))


def download_dataset(uri, destination=None, s3_settings=None):
    """Download normal.txt and attack.txt from an S3 file or prefix."""
    bucket, key = _split_s3(uri)
    root = Path(destination or tempfile.mkdtemp(prefix="auto-ml-s3-"))
    root.mkdir(parents=True, exist_ok=True)
    client = _s3_client(s3_settings)
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


def resolve_input_file(value, s3_settings=None):
    """Return a local path for one input file, downloading a single S3 object if needed."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("input file path must be a non-empty string")
    value = value.strip()
    if not is_s3_uri(value):
        path = Path(value).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"input file not found: {path}")
        return path

    bucket, key = _split_s3(value)
    if not key:
        raise ValueError(f"S3 input must point to an object: {value}")
    suffix = Path(key).suffix or ".txt"
    fd, local_path = tempfile.mkstemp(prefix="auto-ml-input-", suffix=suffix)
    os.close(fd)
    try:
        _s3_client(s3_settings).download_file(bucket, key, local_path)
    except Exception:
        Path(local_path).unlink(missing_ok=True)
        raise
    return Path(local_path)


def upload_directory(local_dir, destination_uri, s3_settings=None):
    """Upload all files under local_dir; returns uploaded count."""
    bucket, prefix = _split_s3(destination_uri)
    client = _s3_client(s3_settings)
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
