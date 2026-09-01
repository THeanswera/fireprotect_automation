from __future__ import annotations

import os
from pathlib import Path
import shutil


class ExclusiveInstallError(FileExistsError):
    """A destination already existed during no-overwrite finalization."""


def install_file_no_overwrite(
    temporary_path: str | Path, destination_path: str | Path
) -> None:
    """Install a completed file without ever replacing an existing destination.

    A hard link provides atomic publication where supported.  Filesystems that
    reject hard links (common for network and sync-backed folders) fall back to
    an exclusive-create copy.  The fallback may expose the new file while it is
    being copied, but it remains race-safe with respect to no-overwrite: the
    destination is opened with ``O_EXCL`` and is removed if copying fails.
    """

    source = Path(temporary_path)
    destination = Path(destination_path)
    try:
        os.link(source, destination)
        return
    except FileExistsError as exc:
        raise ExclusiveInstallError(
            f"Destination already exists: {destination}"
        ) from exc
    except OSError:
        pass

    created = False
    try:
        with source.open("rb") as input_stream:
            try:
                output_stream = destination.open("xb")
            except FileExistsError as exc:
                raise ExclusiveInstallError(
                    f"Destination already exists: {destination}"
                ) from exc
            created = True
            with output_stream:
                shutil.copyfileobj(input_stream, output_stream, 1024 * 1024)
                output_stream.flush()
                os.fsync(output_stream.fileno())
    except Exception:
        if created:
            try:
                destination.unlink()
            except FileNotFoundError:
                pass
        raise
