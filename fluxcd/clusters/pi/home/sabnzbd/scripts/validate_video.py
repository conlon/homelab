#!/usr/bin/env python3
"""SABnzbd post-processing script: fail jobs whose video files are not real video.

Why this exists
---------------
Some indexer releases are truncated NZBs (e.g. ~700 KB total) wrapping a 7z
archive whose header declares a full-size .mp4/.mkv. Every SABnzbd check passes:
par2 only covers what is in the NZB ("Quick Check OK") and 7z "unpacks" the
archive, producing a sparse or zero-filled video file. SABnzbd then reports the
job as Completed, so Sonarr/Radarr never get a failure signal; they retry the
import forever and log a stack trace per attempt (this filled sonarr's config
volume via logs.db in Nov 2026).

Exiting non-zero here, with `script_can_fail = 1`, makes SABnzbd mark the job
Failed. Sonarr/Radarr failed-download handling then blocklists the release and
searches for another one automatically.

Checks (per video file; samples/extras are included on purpose: a fake
"sample" fails too, and real samples pass these checks):
  1. size > 0
  2. a valid container header for the extension (Matroska/MP4/AVI/TS/...)
  3. not hollow: SAMPLE_COUNT evenly spaced SAMPLE_BYTES chunks are read and
     at most MAX_ZERO_FRACTION of them may be entirely zero bytes. Sparse holes
     and zero-filled regions read back as zeros; compressed video essentially
     never contains 64 KiB of zeros.

The sparse check is deliberately content-based, not st_blocks-based: the media
share is ZFS over NFS, and ZFS only reports allocated blocks after the txg
commits (~5 s), so a just-unpacked real file looks 0% allocated.

Jobs with no video files are left alone (other categories, or season packs of
archives SAB could not unpack, which SAB already fails on its own).

SABnzbd configuration (lives in sabnzbd.ini on the config PVC, NOT in git):
  [misc] script_dir = /scripts
  [misc] script_can_fail = 1
  [categories] tv, movies and * -> script = validate_video.py
"""
import os
import sys

VIDEO_EXTS = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts", ".wmv", ".webm", ".mpg", ".mpeg"}

SAMPLE_COUNT = 32
SAMPLE_BYTES = 64 * 1024
# Fakes seen in the wild were 40-100% hollow. Real files: 0%.
MAX_ZERO_FRACTION = 0.25


def header_ok(path, ext, head):
    """Return True if the first bytes look like the container the extension claims."""
    if ext in (".mkv", ".webm"):
        return head[:4] == b"\x1a\x45\xdf\xa3"  # EBML
    if ext in (".mp4", ".m4v", ".mov"):
        # ISO BMFF: 4-byte box size, then a box type. ftyp is normal; some
        # muxers start with other top-level boxes.
        return head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide", b"skip", b"pnot")
    if ext == ".avi":
        return head[:4] == b"RIFF" and head[8:12] == b"AVI "
    if ext == ".ts":
        return head[:1] == b"\x47"  # MPEG-TS sync byte
    if ext == ".m2ts":
        return head[4:5] == b"\x47"  # 4-byte timestamp prefix, then sync byte
    if ext == ".wmv":
        return head[:4] == b"\x30\x26\xb2\x75"  # ASF GUID prefix
    if ext in (".mpg", ".mpeg"):
        return head[:3] == b"\x00\x00\x01"  # MPEG-PS start code
    return True


def check_file(path):
    """Return a failure reason string, or None if the file looks like real video."""
    ext = os.path.splitext(path)[1].lower()
    st = os.stat(path)
    size = st.st_size
    if size == 0:
        return "empty file"
    with open(path, "rb") as fh:
        head = fh.read(16)
        if not header_ok(path, ext, head):
            return f"not a valid {ext} container (starts with {head[:8].hex()})"
        if size < SAMPLE_COUNT * SAMPLE_BYTES:
            return None
        zero = bytes(SAMPLE_BYTES)
        step = (size - SAMPLE_BYTES) // (SAMPLE_COUNT - 1)
        zeros = 0
        for i in range(SAMPLE_COUNT):
            fh.seek(i * step)
            chunk = fh.read(SAMPLE_BYTES)
            if len(chunk) < SAMPLE_BYTES or chunk == zero[: len(chunk)]:
                zeros += 1
    frac = zeros / SAMPLE_COUNT
    if frac > MAX_ZERO_FRACTION:
        return f"hollow file: {frac:.0%} of sampled regions are empty ({size // 1_000_000} MB declared)"
    return None


def main():
    # SABnzbd passes the final job directory as argv[1] (and SAB_COMPLETE_DIR).
    job_dir = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SAB_COMPLETE_DIR", "")
    if not job_dir or not os.path.isdir(job_dir):
        print(f"validate_video: no job directory ({job_dir!r}); skipping")
        return 0
    # If SABnzbd already failed the job (SAB_PP_STATUS != 0), don't pile on.
    if os.environ.get("SAB_PP_STATUS", "0") not in ("0", ""):
        print("validate_video: job already failed in SABnzbd; skipping")
        return 0

    checked, bad = 0, []
    for root, _dirs, files in os.walk(job_dir):
        for name in files:
            if os.path.splitext(name)[1].lower() not in VIDEO_EXTS:
                continue
            path = os.path.join(root, name)
            checked += 1
            try:
                reason = check_file(path)
            except OSError as exc:
                reason = f"unreadable: {exc}"
            if reason:
                bad.append((os.path.relpath(path, job_dir), reason))

    for rel, reason in bad:
        print(f"BAD {rel}: {reason}")
    if bad:
        # SABnzbd shows the LAST line of output as the job's failure message.
        print(f"Fake/broken video: {len(bad)} of {checked} video file(s) failed validation ({bad[0][1]})")
        return 1
    print(f"validate_video: {checked} video file(s) OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
