"""Thin bridge between the Kotlin app and flip_exe_to_pdf (called through Chaquopy)."""
import json

from flip_exe_to_pdf import convert_exe_to_pdf


def run(src, dst, level, cb):
    """cb is a Kotlin object with progress(done, total) and cancelled() -> bool."""
    info = convert_exe_to_pdf(
        src, dst, level,
        progress=lambda i, n: cb.progress(i, n),
        cancelled=lambda: cb.cancelled(),
    )
    return json.dumps(info, ensure_ascii=False)
