"""Download EasyOCR model files during image build.

This script is intentionally tolerant of network failures.
If download fails, runtime can still attempt lazy download.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        import easyocr
    except Exception as exc:
        print(f"[easyocr-build] easyocr import failed: {exc}")
        return 1

    model_dir = os.getenv("EASY_OCR_MODEL_DIR", "/app/models/easyocr")
    langs_env = os.getenv("EASYOCR_LANGS", "en,es")
    langs = [lang.strip() for lang in langs_env.split(",") if lang.strip()]
    if not langs:
        langs = ["en"]

    print(f"[easyocr-build] model_dir={model_dir}")
    print(f"[easyocr-build] langs={langs}")

    os.makedirs(model_dir, exist_ok=True)

    try:
        # Reader initialization triggers model download if missing.
        easyocr.Reader(langs, gpu=False, model_storage_directory=model_dir, download_enabled=True)
        print("[easyocr-build] models ready")
        return 0
    except Exception as exc:
        print(f"[easyocr-build] model download failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
