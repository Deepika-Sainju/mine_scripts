"""Direct S3 reads for the member-file-driven flow (file bucket, READ-ONLY).

The member CSV is the entry point. Given its s3:// URI we derive the sibling
`Chart/` prefix, list it ONCE into a `{filename -> key}` index (charts are nested
under batch-GUID folders, so a flat lookup by filename is what we need), and find
the claims CSVs (every `.csv` sitting under `Chart/`, when present).

    s3://<bucket>/processed/<client>/<project>/Member/<file>.csv   (input)
    s3://<bucket>/processed/<client>/<project>/Chart/<GUID>/…       (charts + claims)

Nothing is ever written back to S3.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple
from urllib.parse import urlparse

import boto3
from dotenv import load_dotenv

log = logging.getLogger(__name__)
load_dotenv()

_MEMBER_SEG = "/Member/"
_CHART_SEG = "Chart/"


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """s3://bucket/key -> (bucket, key)."""
    p = urlparse(uri)
    if p.scheme != "s3" or not p.netloc:
        raise ValueError(f"not an s3:// URI: {uri!r}")
    return p.netloc, p.path.lstrip("/")


def project_root_for(member_key: str) -> str:
    """Project prefix (…/<project>/) for a `.../Member/<file>.csv` key.

    Outputs mirror the source layout relative to this root, so `Member/` and
    `Chart/` files land in matching subfolders and never collide on basename.
    """
    i = member_key.find(_MEMBER_SEG)
    if i == -1:
        raise ValueError(f"member key not under a Member/ folder: {member_key!r}")
    return member_key[: i + 1]  # <project>/


def chart_prefix_for(member_key: str) -> str:
    """Sibling `Chart/` prefix for a `.../Member/<file>.csv` key."""
    return project_root_for(member_key) + _CHART_SEG  # <project>/Chart/


def s3_client():
    return boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))


def list_charts(bucket: str, chart_prefix: str, s3=None
                ) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """One recursive listing of `Chart/` -> (index, children).

    * ``index``   ``{basename filename: full S3 key}`` — used to resolve the
      **main** chart a member row names (later keys win on a basename collision,
      which is logged).
    * ``children`` ``{top-level folder name: [child keys, sorted]}`` — every file
      nested in a subfolder directly under `Chart/`. A main file `Chart/abc.pdf`
      and its attachments `Chart/abc/xyz.xml`, `Chart/abc/sub/j.pdf` share the
      folder/stem ``abc`` (``children['abc']`` holds the attachment keys, however
      deeply nested), so a member's whole document set can be de-identified
      together with the same identity.
    """
    s3 = s3 or s3_client()
    index: Dict[str, str] = {}
    children: Dict[str, List[str]] = {}
    plen = len(chart_prefix)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=chart_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue  # folder marker
            name = key.rsplit("/", 1)[-1]
            rel = key[plen:]  # path relative to Chart/
            if "/" in rel:  # nested -> an attachment of its top-level folder
                folder = rel.split("/", 1)[0]
                children.setdefault(folder, []).append(key)
            if name in index and index[name] != key:
                log.warning("duplicate chart filename %r: %s shadows %s",
                            name, key, index[name])
            index[name] = key
    for keys in children.values():
        keys.sort()
    return index, children


def list_chart_index(bucket: str, chart_prefix: str, s3=None) -> Dict[str, str]:
    """Back-compat: the basename `{filename -> key}` index only (see `list_charts`)."""
    return list_charts(bucket, chart_prefix, s3)[0]


def list_csv_keys(bucket: str, prefix: str, s3=None) -> List[str]:
    """Every `*.csv` object under `prefix` (e.g. a Member/ folder holding many
    member CSVs), sorted. Read-only."""
    s3 = s3 or s3_client()
    out = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.lower().endswith(".csv") and not k.endswith("/"):
                out.append(k)
    return sorted(out)


def find_claims_keys(index: Dict[str, str]) -> List[str]:
    """Every claims CSV inside Chart/ (all `.csv` objects in the index), sorted.

    A project can ship several claims files; all of them are de-identified.
    """
    return sorted(k for name, k in index.items() if name.lower().endswith(".csv"))


def resolve_chart(bucket: str, chart_prefix: str, chart_name: str, s3=None
                  ) -> Tuple[str, List[str]]:
    """Resolve ONE member's main chart + attachments by a SCOPED listing.

    Lists only `Chart/<stem>*` (a handful of keys) instead of the whole `Chart/`
    tree — so cost is O(one member), not O(all charts in the project). This is
    what lets the pipeline run against buckets with millions of charts: a member
    file with 200 rows does ~200 tiny listings, never a full enumeration.

    Returns (main_key or "", [attachment_keys]) where:
      * main_key      = `Chart/<chart_name>` (exact basename match), or a
                        top-level `Chart/<stem>.<ext>` if the member names a
                        different extension; "" if absent.
      * attachments   = every file nested under `Chart/<stem>/…` (sorted).
    """
    s3 = s3 or s3_client()
    stem = chart_name.rsplit(".", 1)[0] if "." in chart_name else chart_name
    if not stem:
        return "", []
    folder = chart_prefix + stem + "/"
    main_key, attach = "", []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=chart_prefix + stem):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue
            name = key.rsplit("/", 1)[-1]
            if key.startswith(folder):
                attach.append(key)                      # Chart/<stem>/…
            elif name == chart_name:
                main_key = key                          # exact Chart/<chart_name>
            elif not main_key and "/" not in key[len(chart_prefix):] \
                    and name.rsplit(".", 1)[0] == stem:
                main_key = key                          # Chart/<stem>.<other-ext>
    attach.sort()
    return main_key, attach


def find_claims_keys_toplevel(bucket: str, chart_prefix: str, s3=None) -> List[str]:
    """Top-level claims CSVs (`Chart/*.csv`) via a DELIMITED listing — cheap.

    Uses `Delimiter='/'` so S3 returns only the immediate children of `Chart/`
    (never recursing into millions of nested chart objects). Claims files sit at
    the top of `Chart/`, so this finds them without a full enumeration.
    """
    s3 = s3 or s3_client()
    out: List[str] = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=chart_prefix, Delimiter="/"):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if k.lower().endswith(".csv") and not k.endswith("/"):
                out.append(k)
    return sorted(out)


def download(bucket: str, key: str, dest: str, s3=None) -> str:
    """Download one object to `dest` (creating parent dirs). Returns dest."""
    s3 = s3 or s3_client()
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    s3.download_file(bucket, key, dest)
    return dest
