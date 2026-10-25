"""Download an immutable official weight file, verify it, then test model loading."""
import hashlib
import os
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "PneumoconiosisAnnotator"))
from PneumoconiosisLib.model_config import WEIGHTS_FILE, WEIGHTS_URL, WEIGHTS_SHA256

directory = ROOT / "local-data" / "models"
directory.mkdir(parents=True, exist_ok=True)
path = directory / WEIGHTS_FILE
if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != WEIGHTS_SHA256:
    temporary = path.with_suffix(".download")
    digest = hashlib.sha256()
    print("正在从作者官方发布下载肺野模型（约 273 MB）…", flush=True)
    with requests.get(WEIGHTS_URL, stream=True, timeout=(20, 120)) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for block in response.iter_content(1024 * 1024):
                stream.write(block)
                digest.update(block)
            stream.flush()
            os.fsync(stream.fileno())
    if digest.hexdigest() != WEIGHTS_SHA256:
        raise SystemExit("模型权重校验失败；未替换已有模型。请重试。")
    os.replace(temporary, path)
from feature_worker import LungModel
model = LungModel()
print("模型已准备，权重 SHA256：" + model.metadata["weights_sha256"], flush=True)
