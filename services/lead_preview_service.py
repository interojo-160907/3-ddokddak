from __future__ import annotations

import os
import re
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

from config import DATA_CENTER_DIR


IMAGE_FOLDER_NAME = "리드지 미리보기 이미지"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
CLONE_SOURCE_FOLDER_NAME = "리드지 PDF 백업"
MANUAL_SOURCE_FOLDER_NAME = "리드지 수동 등록"
SOURCE_EXTENSIONS = (".pdf",) + IMAGE_EXTENSIONS
FIXED_PREVIEW_FOLDER_NAME = "리드지 미리보기 캐시"
VECTOR_PREVIEW_FOLDER_NAME = "리드지 벡터 미리보기 캐시"
VECTOR_PREVIEW_VERSION = "vector-crop-v1"
FIXED_PREVIEW_VERSION = "vector-render-v1-pyramid"


def _normalize(value: object) -> str:
    return re.sub(r"[^0-9A-Z가-힣]", "", str(value or "").upper())


def resolve_preview_folder(
    pdf_path: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    if data_root is not None:
        root = Path(data_root)
    elif pdf_path is not None:
        source = Path(pdf_path)
        root = source.parent.parent
    else:
        root = DATA_CENTER_DIR
    folder = root / IMAGE_FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resolve_manual_source_folder(
    pdf_path: Path | None = None,
    data_root: Path | None = None,
) -> Path:
    if data_root is not None:
        root = Path(data_root)
    elif pdf_path is not None:
        root = Path(pdf_path).parent.parent
    else:
        root = DATA_CENTER_DIR
    folder = root / MANUAL_SOURCE_FOLDER_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def resolve_clone_source_folder(data_root: Path | None = None) -> Path:
    if data_root is not None:
        root = Path(data_root)
    else:
        root = DATA_CENTER_DIR
    return root / CLONE_SOURCE_FOLDER_NAME


def find_clone_lead_source(
    lead_spec: object,
    lead_code: object,
    data_root: Path | None = None,
) -> Path | None:
    folder = resolve_clone_source_folder(data_root=data_root)
    keys = [key for key in (_normalize(lead_spec), _normalize(lead_code)) if key]
    if not folder.is_dir() or not keys:
        return None
    candidates: list[tuple[int, float, Path]] = []
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        normalized_stem = _normalize(path.stem)
        rank = 0
        for key in keys:
            if normalized_stem == key:
                rank = max(rank, 3)
            elif normalized_stem.startswith(key):
                rank = max(rank, 2)
        if rank:
            candidates.append((rank, path.stat().st_mtime, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def find_lead_preview_image(
    lead_spec: object,
    lead_code: object,
    pdf_path: Path | None = None,
    data_root: Path | None = None,
) -> Path | None:
    folder = resolve_preview_folder(pdf_path=pdf_path, data_root=data_root)
    keys = [key for key in (_normalize(lead_spec), _normalize(lead_code)) if key]
    if not keys:
        return None

    image_paths = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    normalized_paths = [(_normalize(path.stem), path) for path in image_paths]
    for key in keys:
        for normalized_stem, path in normalized_paths:
            if normalized_stem == key:
                return path
    for key in keys:
        for normalized_stem, path in normalized_paths:
            if normalized_stem.startswith(key):
                return path
    return None


def find_manual_lead_source(
    lead_spec: object,
    lead_code: object,
    data_root: Path | None = None,
) -> Path | None:
    keys = [key for key in (_normalize(lead_spec), _normalize(lead_code)) if key]
    if not keys:
        return None
    folders = [
        resolve_manual_source_folder(data_root=data_root),
        resolve_preview_folder(data_root=data_root),
    ]
    candidates: list[tuple[int, float, Path]] = []
    for folder in folders:
        for path in folder.iterdir():
            if not path.is_file() or path.suffix.lower() not in SOURCE_EXTENSIONS:
                continue
            normalized_stem = _normalize(path.stem)
            match_rank = 0
            for key in keys:
                if normalized_stem == key:
                    match_rank = max(match_rank, 3)
                elif normalized_stem.startswith(key):
                    match_rank = max(match_rank, 2)
            if match_rank:
                pdf_bonus = 1 if path.suffix.lower() == ".pdf" else 0
                candidates.append((match_rank * 10 + pdf_bonus, path.stat().st_mtime, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def register_manual_lead_source(
    source_path: Path | str,
    lead_spec: object,
    lead_code: object,
    data_root: Path | None = None,
) -> Path:
    source = Path(source_path)
    if not source.is_file() or source.suffix.lower() not in SOURCE_EXTENSIONS:
        raise ValueError("PDF 또는 이미지 파일만 등록할 수 있습니다.")
    raw_prefix = str(lead_spec or lead_code or "리드지").strip()
    safe_prefix = re.sub(r'[<>:"/\\|?*]', "_", raw_prefix).strip(" .") or "리드지"
    folder = resolve_manual_source_folder(data_root=data_root)
    target = folder / f"{safe_prefix}__{source.name}"
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = folder / f"{safe_prefix}__{stamp}__{source.name}"
    shutil.copy2(source, target)
    return target


def _file_fingerprint(path: Path) -> str:
    stat = path.stat()
    digest = hashlib.sha1()
    digest.update(f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8"))
    sample_size = 64 * 1024
    with path.open("rb") as source:
        offsets = (
            0,
            max(0, stat.st_size // 2 - sample_size // 2),
            max(0, stat.st_size - sample_size),
        )
        for offset in offsets:
            source.seek(offset)
            digest.update(source.read(sample_size))
    return digest.hexdigest()[:20]


def cropped_pdf_preview_path(pdf_path: Path | str) -> Path | None:
    source_path = Path(pdf_path)
    if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
        return None
    cache_folder = source_path.parent.parent / VECTOR_PREVIEW_FOLDER_NAME
    cache_folder.mkdir(parents=True, exist_ok=True)
    fingerprint = _file_fingerprint(source_path)
    cache_key = hashlib.sha1(
        f"{VECTOR_PREVIEW_VERSION}|{source_path.resolve()}|{fingerprint}".encode("utf-8")
    ).hexdigest()[:24]
    return cache_folder / f"{cache_key}.pdf"


def ensure_cropped_pdf_preview(pdf_path: Path | str) -> Path | None:
    source_path = Path(pdf_path)
    preview_path = cropped_pdf_preview_path(source_path)
    if preview_path is None:
        return None
    if preview_path.is_file():
        return preview_path

    temp_path = preview_path.with_suffix(".tmp.pdf")
    try:
        from pypdf import PdfReader, PdfWriter, Transformation
        from pypdf.generic import RectangleObject

        reader = PdfReader(str(source_path))
        page = reader.pages[0]
        if page.rotation:
            page.transfer_rotation_to_content()
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        left = page_width * 0.68
        bottom = 0.0
        right = page_width
        top = page_height * 0.50
        page.add_transformation(
            Transformation().translate(tx=-left, ty=-bottom),
            expand=False,
        )
        cropped_box = RectangleObject((0, 0, right - left, top - bottom))
        page.mediabox = cropped_box
        page.cropbox = cropped_box
        page.trimbox = cropped_box
        page.bleedbox = cropped_box
        page.artbox = cropped_box
        writer = PdfWriter()
        writer.add_page(page)
        with temp_path.open("wb") as output:
            writer.write(output)
        os.replace(temp_path, preview_path)
        return preview_path
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def fixed_pdf_preview_path(pdf_path: Path | str, level: int = 1) -> Path | None:
    source_path = Path(pdf_path)
    if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
        return None
    level = level if level in (1, 2, 4) else 1
    cache_folder = source_path.parent.parent / FIXED_PREVIEW_FOLDER_NAME
    cache_folder.mkdir(parents=True, exist_ok=True)
    fingerprint = _file_fingerprint(source_path)
    cache_key = hashlib.sha1(
        f"{FIXED_PREVIEW_VERSION}|{level}x|{source_path.resolve()}|{fingerprint}".encode("utf-8")
    ).hexdigest()[:24]
    return cache_folder / f"{cache_key}_{level}x.png"


def ensure_fixed_pdf_preview(pdf_path: Path | str, level: int = 1) -> Path | None:
    """Render a fixed lower-right area without modifying the synchronized PDF."""
    source_path = Path(pdf_path)
    if not source_path.is_file() or source_path.suffix.lower() != ".pdf":
        return None

    level = level if level in (1, 2, 4) else 1
    preview_path = fixed_pdf_preview_path(source_path, level)
    if preview_path is None:
        return None
    if preview_path.is_file():
        return preview_path

    try:
        import pypdfium2 as pdfium

        cropped_pdf_path = ensure_cropped_pdf_preview(source_path)
        if cropped_pdf_path is None:
            return None
        document = pdfium.PdfDocument(str(cropped_pdf_path))
        page = document[0]
        bitmap = page.render(
            scale=4.0 * level,
            rotation=0,
            fill_color=(255, 255, 255, 255),
            rev_byteorder=True,
            optimize_mode="print",
        )
        image = bitmap.to_pil().convert("RGB")
        image.save(preview_path, format="PNG", compress_level=2)
        page.close()
        document.close()
        return preview_path
    except Exception:
        try:
            preview_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
