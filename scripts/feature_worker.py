"""Isolated model/audio/export processes with newline-delimited JSON results."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PneumoconiosisAnnotator"))


def emit(**value):
    print(json.dumps(value, ensure_ascii=True, allow_nan=False), flush=True)


class LungModel:
    def __init__(self):
        import torch
        import torchxrayvision as xrv
        from PneumoconiosisLib.model_config import WEIGHTS_FILE, WEIGHTS_URL, WEIGHTS_SHA256
        from PneumoconiosisLib.runtime import model_directory
        cache = model_directory(ROOT)
        path = cache / WEIGHTS_FILE
        if not path.exists():
            raise RuntimeError("肺野模型尚未准备，请先运行模型准备脚本。")
        if hashlib.sha256(path.read_bytes()).hexdigest() != WEIGHTS_SHA256:
            raise RuntimeError("肺野模型权重校验失败，请重新运行准备脚本。")
        torch.set_num_threads(min(8, os.cpu_count() or 1))
        self.torch = torch
        with contextlib.redirect_stdout(sys.stderr):
            self.model = xrv.baseline_models.chestx_det.PSPNet(cache_dir=str(cache)).eval()
        self.metadata = {"name": "ChestX-Det PSPNet", "library": "torchxrayvision",
                         "library_version": importlib.metadata.version("torchxrayvision"),
                         "weights_source": WEIGHTS_URL, "weights_sha256": WEIGHTS_SHA256,
                         "torch_version": importlib.metadata.version("torch"),
                         "numpy_version": importlib.metadata.version("numpy"),
                         "scipy_version": importlib.metadata.version("scipy"),
                         "preprocessing": "full-image-percentile-0.5-99.5-letterbox512-v1",
                         "threshold": 0.5, "postprocessing": "largest-component-fill-holes-exclusive-probability",
                         "landmark_initialization": "apex-and-central-inferior-contour-estimate-v1"}

    def predict(self, job):
        import numpy as np
        from scipy import ndimage
        from PneumoconiosisLib.assets import AssetStore
        from PneumoconiosisLib.partition import estimate_landmarks, zone_map
        from PneumoconiosisLib.core import utc_now
        torch = self.torch
        image = np.load(job["input"], allow_pickle=False).astype(np.float32)
        if image.ndim != 2 or not np.isfinite(image).all():
            raise ValueError("模型输入不是有效的二维胸片。")
        h, w = image.shape
        low, high = np.percentile(image, [.5, 99.5])
        if high <= low:
            raise ValueError("胸片灰度范围不足，不能自动分割。")
        image = np.clip((image - low) / (high - low), 0, 1).astype(np.float32)
        scale = 512 / max(h, w)
        nh, nw = round(h * scale), round(w * scale)
        top, left = (512 - nh) // 2, (512 - nw) // 2
        tensor = torch.from_numpy(image)[None, None]
        tensor = torch.nn.functional.interpolate(tensor, size=(nh, nw), mode="bilinear", align_corners=False)
        tensor = torch.nn.functional.pad(tensor, (left, 512 - nw - left, top, 512 - nh - top), value=0)
        with torch.inference_mode(), contextlib.redirect_stdout(sys.stderr):
            prediction = self.model(tensor * 2048 - 1024).sigmoid()
            selected = prediction[:, [self.model.targets.index("Right Lung"), self.model.targets.index("Left Lung")]]
            selected = selected[:, :, top:top + nh, left:left + nw]
            probabilities = torch.nn.functional.interpolate(selected, size=(h, w), mode="bilinear", align_corners=False)[0].numpy()
        masks = []
        for probability in probabilities:
            labeled, count = ndimage.label(probability >= .5)
            if not count:
                raise ValueError("模型没有识别出左右肺野，请检查胸片方向和质量。")
            counts = np.bincount(labeled.ravel())
            counts[0] = 0
            masks.append(ndimage.binary_fill_holes(labeled == counts.argmax()))
        right, left_mask = masks
        overlap = right & left_mask
        right[overlap & (probabilities[0] < probabilities[1])] = False
        left_mask[overlap & (probabilities[0] >= probabilities[1])] = False
        if any(mask.sum() < h * w * .01 or mask.sum() > h * w * .65 for mask in masks):
            raise ValueError("自动肺野范围异常，请核对图像后再试。")
        landmarks = estimate_landmarks(right, left_mask)
        labels = zone_map(right, left_mask, landmarks)
        reference = AssetStore(job["project"]).put_lungs(right, left_mask)
        return {"baseline": reference, "masks": reference, "baseline_landmarks": landmarks,
                "landmarks": landmarks, "rule": "GBZ70-2015-section3.7-apex-to-diaphragm-dome-thirds",
                "state": "pending_review", "model": self.metadata, "created_at": utc_now(),
                "confirmed_at": None, "source_sha256": job["source_sha256"],
                "pixel_counts": {str(label): int((labels == label).sum()) for label in range(1, 7)}}


def model_loop():
    model = None
    for line in sys.stdin:
        job = {}
        try:
            job = json.loads(line)
            emit(event="progress", job=job["id"], text="正在载入模型…" if model is None else "正在分割肺野…")
            model = model or LungModel()
            started = time.monotonic()
            partition = model.predict(job)
            emit(event="result", job=job["id"], partition=partition, seconds=round(time.monotonic() - started, 3))
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            emit(event="error", job=job.get("id"), message=str(exc))


def record(job):
    import sounddevice as sd
    from PneumoconiosisLib.audio_files import WaveRecording
    blocks = queue.Queue(maxsize=128)
    stopped = threading.Event()
    fault = []

    def stop_when_input_closes():
        sys.stdin.readline()
        stopped.set()

    def callback(data, _frames, _timing, status):
        if status:
            fault.append(str(status))
            stopped.set()
        try:
            blocks.put_nowait(bytes(data))
        except queue.Full:
            fault.append("录音写入过慢，已停止，已有内容仍会保存。")
            stopped.set()

    device = sd.query_devices(kind="input")
    rate = int(device["default_samplerate"])
    writer = None
    try:
        with sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=max(1, rate // 5), callback=callback):
            writer = WaveRecording(job["project"], job["case_id"], job["reader_id"], rate, job["id"])
            threading.Thread(target=stop_when_input_closes, daemon=True).start()
            emit(event="recording", rate=rate)
            while not stopped.is_set():
                try:
                    writer.write(blocks.get(timeout=.25))
                except queue.Empty:
                    pass
        while not blocks.empty():
            writer.write(blocks.get_nowait())
        value = writer.finish(interrupted=bool(fault))
        emit(event="recorded", recording=value, warning="; ".join(fault))
    except Exception as exc:
        if writer and not writer.file.closed:
            partial = writer.finish(interrupted=True)
            if partial:
                emit(event="recorded", recording=partial, warning=str(exc))
        raise


def play(job):
    import sounddevice as sd
    import wave
    with wave.open(job["path"], "rb") as stream, sd.RawOutputStream(
            samplerate=stream.getframerate(), channels=1, dtype="int16") as output:
        emit(event="playing")
        while block := stream.readframes(4096):
            output.write(block)
    emit(event="played")


def labelme(job):
    from PneumoconiosisLib.labelme_export import export_labelme
    snapshot = json.loads(Path(job["snapshot"]).read_text(encoding="utf-8"))
    result = export_labelme(job["destination"], snapshot,
                           progress=lambda current, total: emit(event="progress", current=current, total=total))
    emit(event="exported", **result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("model", "record", "play", "labelme"))
    parser.add_argument("job", nargs="?")
    args = parser.parse_args()
    try:
        if args.mode == "model":
            model_loop()
        else:
            {"record": record, "play": play, "labelme": labelme}[args.mode](json.loads(args.job))
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit(event="error", message=str(exc))
        raise SystemExit(1)
