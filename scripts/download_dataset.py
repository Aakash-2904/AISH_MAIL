"""Download the Enron maildir tarball and extract only the selected mailboxes.

Stdlib only (no wget/tar), so it works on Windows, macOS and Linux.

* ``download(url, dest)``: streaming download with HTTP Range resume into
  ``<dest>.part`` and a progress bar; skipped when ``dest`` is already complete.
* ``extract(tarball, dest_root, mailboxes)``: single streaming pass over the
  gzip tarball (``r|gz``) that writes only ``maildir/<mailbox>/...`` regular
  files. Enron file names end in '.' (``inbox/45.``), which Windows cannot
  store, so a trailing '.' (or space) is written as '_' on every OS
  (``inbox/45_``); ``discover.relative_source`` maps it back.

CLI::

    python scripts/download_dataset.py [--mailboxes a,b] [--data-dir data] [--force-extract]
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.request
from collections.abc import Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from enron_pipeline import config  # noqa: E402

CHUNK = 1 << 20
# Size of enron_mail_20150507.tar.gz as served by CMU; lets a complete local
# copy be recognised without any network request.
KNOWN_SIZE = 443_254_787
MAX_ATTEMPTS = 8
_UA = {"User-Agent": "enron-dedup-pipeline/1.0 (python-urllib)"}


class UnsafeMemberError(RuntimeError):
    """A tar member would be written outside the target directory."""


# --------------------------------------------------------------------------- download


def remote_size(url: str, timeout: float = 30.0) -> int | None:
    """Content-Length from a HEAD request, or None if unavailable."""
    try:
        req = urllib.request.Request(url, method="HEAD", headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            value = resp.headers.get("Content-Length")
            return int(value) if value else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def is_complete(dest: Path, expected: int | None) -> bool:
    """True when ``dest`` exists with the expected (or the known CMU) size."""
    if not dest.is_file():
        return False
    size = dest.stat().st_size
    return size == KNOWN_SIZE or (expected is not None and size == expected)


def _progress(have: int, total: int | None, started: float) -> None:
    """Single-line progress bar on stderr."""
    mb = have / 1e6
    rate = mb / max(time.time() - started, 1e-6)
    if total:
        pct = 100.0 * have / total
        bar = "#" * int(pct / 4)
        msg = f"\r[{bar:<25}] {pct:5.1f}% {mb:8.1f}/{total / 1e6:.1f} MB {rate:5.1f} MB/s"
    else:
        msg = f"\r{mb:8.1f} MB {rate:5.1f} MB/s"
    sys.stderr.write(msg)
    sys.stderr.flush()


def _fetch_once(url: str, part: Path, total: int | None) -> None:
    """One streaming request, resuming from the current ``.part`` size."""
    have = part.stat().st_size if part.exists() else 0
    headers = dict(_UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        if have and resp.status != 206:
            print("\nserver ignored Range; restarting download", file=sys.stderr)
            have = 0
        mode = "ab" if have else "wb"
        started = time.time()
        last = 0.0
        with part.open(mode) as fh:
            while chunk := resp.read(CHUNK):
                fh.write(chunk)
                have += len(chunk)
                if time.time() - last > 0.5:
                    _progress(have, total, started)
                    last = time.time()
        _progress(have, total, started)
        sys.stderr.write("\n")


def download(url: str, dest: Path) -> Path:
    """Download ``url`` to ``dest`` (resumable, retried); skip if complete.

    Returns ``dest``. Raises RuntimeError after repeated failures or a size
    mismatch.
    """
    dest = Path(dest)
    if dest.is_file() and dest.stat().st_size == KNOWN_SIZE:
        print(f"tarball already complete: {dest} ({KNOWN_SIZE} bytes), skipping download")
        return dest
    total = remote_size(url)
    if is_complete(dest, total):
        print(f"tarball already complete: {dest}, skipping download")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if total is not None and part.exists() and part.stat().st_size >= total:
            break
        try:
            _fetch_once(url, part, total)
            if total is None or part.stat().st_size >= total:
                break
        except (urllib.error.URLError, OSError, ConnectionError) as exc:
            print(f"\ndownload error (attempt {attempt}/{MAX_ATTEMPTS}): {exc!r}", file=sys.stderr)
            if attempt == MAX_ATTEMPTS:
                raise RuntimeError(f"download failed after {MAX_ATTEMPTS} attempts: {exc}") from exc
            time.sleep(5)
    size = part.stat().st_size
    if total is not None and size != total:
        raise RuntimeError(f"size mismatch: got {size} bytes, expected {total}")
    os.replace(part, dest)
    print(f"download done: {dest} ({size} bytes)")
    return dest


# --------------------------------------------------------------------------- extract


def windows_safe_component(component: str) -> str:
    """Replace trailing '.'/' ' characters with '_' (one '_' per character).

    ``"45."`` -> ``"45_"``. Applied on every OS so paths are identical everywhere.
    """
    stripped = component.rstrip(". ")
    return stripped + "_" * (len(component) - len(stripped))


def member_relpath(name: str) -> list[str]:
    """Validate a tar member name and return its sanitised path components.

    Rejects absolute paths, drive letters, backslashes, empty and ``..``
    components (raises UnsafeMemberError). A leading ``./`` is tolerated.
    """
    if name.startswith("./"):
        name = name[2:]
    if not name or name.startswith("/") or os.path.isabs(name) or ":" in name or "\\" in name:
        raise UnsafeMemberError(f"unsafe member name {name!r}")
    parts = name.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise UnsafeMemberError(f"bad member name {name!r}")
    return [windows_safe_component(p) for p in parts]


def safe_target(dest_root: Path, parts: Sequence[str]) -> Path:
    """Resolve the output path and make sure it stays inside ``dest_root``."""
    root = Path(os.path.abspath(dest_root))
    target = Path(os.path.abspath(root.joinpath(*parts)))
    if os.path.commonpath([str(root), str(target)]) != str(root) or target == root:
        raise UnsafeMemberError(f"path escapes target dir: {target}")
    return target


def _selected(name: str, prefixes: tuple[str, ...]) -> bool:
    """True when the member belongs to one of the selected mailboxes."""
    if name.startswith("./"):
        name = name[2:]
    return name.startswith(prefixes)


def extract(tarball: Path, dest_root: Path, mailboxes: Sequence[str]) -> tuple[int, int]:
    """Stream-extract regular files of ``maildir/<mailbox>/`` for ``mailboxes``.

    Writes under ``dest_root`` (so files land in ``dest_root/maildir/...``).
    Non-regular members (dirs, links, devices) are skipped; directories are
    created as needed. Returns ``(files_extracted, files_renamed)``.
    """
    prefixes = tuple(f"maildir/{m}/" for m in mailboxes)
    extracted = renamed = 0
    started = time.time()
    with tarfile.open(tarball, "r|gz") as tf:
        for member in tf:
            if not member.isreg() or not _selected(member.name, prefixes):
                continue
            raw = member.name[2:] if member.name.startswith("./") else member.name
            parts = member_relpath(raw)
            if parts != raw.split("/"):
                renamed += 1
            target = safe_target(dest_root, parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            src = tf.extractfile(member)
            if src is None:
                continue
            with target.open("wb") as fh:
                shutil.copyfileobj(src, fh, CHUNK)
            extracted += 1
            if extracted % 5000 == 0:
                print(f"  extracted {extracted} files ({time.time() - started:.0f}s)")
    print(f"extracted {extracted} files ({renamed} renamed for Windows) in {time.time() - started:.0f}s")
    return extracted, renamed


def missing_mailboxes(maildir: Path, mailboxes: Sequence[str]) -> list[str]:
    """Mailboxes whose ``maildir/<name>`` folder is absent or empty."""
    missing = []
    for name in mailboxes:
        folder = Path(maildir) / name
        if not folder.is_dir() or not any(folder.iterdir()):
            missing.append(name)
    return missing


def ensure_dataset(
    mailboxes: Sequence[str],
    data_dir: Path = config.DATA_DIR,
    url: str = config.DATASET_URL,
    force_extract: bool = False,
) -> list[str]:
    """Download (if needed) and extract any missing mailboxes. Returns the
    mailboxes that were extracted (empty list when everything was present)."""
    data_dir = Path(data_dir)
    todo = list(mailboxes) if force_extract else missing_mailboxes(data_dir / "maildir", mailboxes)
    if not todo:
        print(f"dataset present for {len(mailboxes)} mailbox(es); nothing to do")
        return []
    tarball = download(url, data_dir / config.TARBALL.name)
    print(f"extracting mailboxes: {', '.join(todo)}")
    extract(tarball, data_dir, todo)
    still = missing_mailboxes(data_dir / "maildir", todo)
    if still:
        raise RuntimeError(f"mailbox(es) not found in tarball: {', '.join(still)}")
    return todo


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--mailboxes", default=",".join(config.DEFAULT_MAILBOXES),
                   help="comma-separated mailbox names (default: config.DEFAULT_MAILBOXES)")
    p.add_argument("--data-dir", type=Path, default=config.DATA_DIR)
    p.add_argument("--url", default=config.DATASET_URL)
    p.add_argument("--force-extract", action="store_true",
                   help="re-extract even if the mailbox folders already exist")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = _parse_args(argv)
    mailboxes = [m.strip() for m in args.mailboxes.split(",") if m.strip()]
    try:
        ensure_dataset(mailboxes, args.data_dir, args.url, args.force_extract)
    except (RuntimeError, OSError, tarfile.TarError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
