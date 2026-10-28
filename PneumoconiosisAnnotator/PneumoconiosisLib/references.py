"""Read an external library of single-frame grayscale DICOM references."""
from concurrent.futures import ThreadPoolExecutor
import math
import os
from pathlib import Path

import numpy as np
import pydicom
from pydicom.pixels import apply_modality_lut, apply_voi_lut


def _is_image(ds):
    return (int(ds.get("NumberOfFrames", 1)) == 1
            and int(ds.get("SamplesPerPixel", 1)) == 1
            and str(ds.get("PhotometricInterpretation", "")) in ("MONOCHROME1", "MONOCHROME2")
            and int(ds.get("Rows", 0)) > 0 and int(ds.get("Columns", 0)) > 0)


def scan_references(directory):
    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("请选择存在的标准片文件夹。")
    if any(part == "__MACOSX" or part.startswith("._") for part in root.parts):
        raise ValueError("这是 Mac 压缩包的辅助目录，请选择实际存放标准片的文件夹。")
    paths = []
    for parent, folders, names in os.walk(root):
        folders[:] = sorted(name for name in folders if name != "__MACOSX" and not name.startswith("."))
        paths.extend(Path(parent) / name for name in names
                     if not name.startswith(".") and Path(name).suffix.lower() in (".dcm", ".dicom", ""))

    def read(path):
        try:
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            if not _is_image(ds):
                return None
        except (OSError, ValueError, TypeError, pydicom.errors.InvalidDicomError):
            return None
        relative = path.relative_to(root)
        return {"path": str(path), "relative_path": relative.as_posix(),
                "label": relative.with_suffix("").as_posix()}

    with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        images = [image for image in pool.map(read, sorted(paths)) if image is not None]
    if not images:
        raise ValueError("没有找到可用的单帧灰度 DICOM 标准片。")
    return images


def read_reference(path):
    """Keep stored pixel order and use DICOM's modality/VOI transforms for display."""
    ds = pydicom.dcmread(path)
    if not _is_image(ds):
        raise ValueError("标准片需要是单帧灰度 DICOM。")
    pixels = apply_modality_lut(ds.pixel_array, ds)
    if pixels.shape != (int(ds.Rows), int(ds.Columns)):
        raise ValueError("标准片像素尺寸与 DICOM 描述不一致。")

    # Applying VOI through pydicom also handles LUTs and nonlinear window functions.
    # The reference volume contains presentation values; it is never an input to inference.
    has_voi = bool(ds.get("VOILUTSequence")) or ("WindowCenter" in ds and "WindowWidth" in ds)
    if has_voi:
        pixels = apply_voi_lut(pixels, ds)
        if ds.get("VOILUTSequence"):
            lower, upper = 0.0, float(2 ** int(ds.VOILUTSequence[0].LUTDescriptor[2]) - 1)
        else:
            bits = int(ds.BitsStored)
            lower, upper = ((-(2 ** (bits - 1)), 2 ** (bits - 1) - 1)
                            if int(ds.get("PixelRepresentation", 0)) else (0, 2 ** bits - 1))
            if ds.get("ModalityLUTSequence"):
                lower, upper = 0, 2 ** int(ds.ModalityLUTSequence[0].LUTDescriptor[2]) - 1
            elif "RescaleSlope" in ds and "RescaleIntercept" in ds:
                lower, upper = (lower * float(ds.RescaleSlope) + float(ds.RescaleIntercept),
                                upper * float(ds.RescaleSlope) + float(ds.RescaleIntercept))
    else:
        lower, upper = float(np.min(pixels)), float(np.max(pixels))
    lower, upper = sorted((float(lower), float(upper)))
    if not math.isfinite(lower) or not math.isfinite(upper) or not np.isfinite(pixels).all():
        raise ValueError("标准片包含无效灰度值。")

    spacing = ds.get("PixelSpacing", ds.get("ImagerPixelSpacing", ds.get("PixelAspectRatio", [1, 1])))
    row_spacing, column_spacing = (float(value) for value in spacing)
    if not all(math.isfinite(value) and value > 0 for value in (row_spacing, column_spacing)):
        raise ValueError("标准片像素间距无效。")
    return {"pixels": np.ascontiguousarray(pixels, dtype=np.float32),
            "spacing": (column_spacing, row_spacing, 1.0),
            "window": max(upper - lower, 1.0), "level": (lower + upper) / 2.0,
            "inverted": str(ds.PhotometricInterpretation) == "MONOCHROME1"}
