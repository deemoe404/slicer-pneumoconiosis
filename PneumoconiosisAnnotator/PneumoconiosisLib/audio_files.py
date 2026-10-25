"""Recoverable PCM recordings and verified attachment metadata, independent of the GUI."""
from __future__ import annotations

import json
import os
import time
import uuid
import wave
from pathlib import Path

from .assets import atomic_bytes, resolve_asset
from .core import file_sha256, utc_now, validate_reader


def validate_audio(records, directory):
    normalized, ids = [], set()
    for record in records:
        value = dict(record)
        uuid.UUID(value["id"])
        if value["id"] in ids:
            raise ValueError("录音编号重复。")
        ids.add(value["id"])
        path = resolve_asset(directory, value["path"])
        if file_sha256(path) != value["sha256"]:
            raise ValueError("录音文件校验失败，请核对备份。")
        with wave.open(str(path), "rb") as stream:
            if stream.getnchannels() != 1 or stream.getsampwidth() != 2 or stream.getnframes() <= 0:
                raise ValueError("录音必须是有内容的单声道 PCM WAV。")
            if stream.getnframes() != value["frames"] or stream.getframerate() != value["sample_rate"]:
                raise ValueError("录音时长与记录不一致。")
            duration = stream.getnframes() / stream.getframerate()
        value["duration_seconds"] = round(duration, 3)
        normalized.append(value)
    return normalized


class WaveRecording:
    """Each block patches and flushes its WAV header, so interrupted clips remain readable."""
    def __init__(self, directory, case_id, reader, rate, identifier=None):
        self.directory = Path(directory).resolve()
        self.reader = validate_reader(reader)
        self.identifier = identifier or uuid.uuid4().hex
        uuid.UUID(self.identifier)
        self.relative = f"assets/audio/{case_id}/{self.reader}/{self.identifier}.wav"
        self.path = resolve_asset(self.directory, self.relative)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.part = self.path.with_suffix(".part.wav")
        self.manifest = self.path.with_suffix(".json")
        self.session = {"id": self.identifier, "case_id": case_id, "reader_id": self.reader,
                        "path": self.relative, "created_at": utc_now(), "state": "recording"}
        atomic_bytes(self.manifest, json.dumps(self.session, ensure_ascii=False).encode())
        self.file = self.part.open("w+b")
        self.wave = wave.open(self.file, "wb")
        self.wave.setnchannels(1)
        self.wave.setsampwidth(2)
        self.wave.setframerate(int(rate))
        self.frames = 0

    def write(self, pcm):
        self.wave.writeframes(pcm)
        self.frames += len(pcm) // 2
        self.file.flush()
        os.fsync(self.file.fileno())

    def finish(self, interrupted=False):
        self.wave.close()
        self.file.flush()
        os.fsync(self.file.fileno())
        self.file.close()
        if not self.frames:
            self.session["state"] = "empty"
            atomic_bytes(self.manifest, json.dumps(self.session).encode())
            return None
        os.replace(self.part, self.path)
        return finalize_recording(self.directory, self.manifest, interrupted)


def finalize_recording(directory, manifest, interrupted=False):
    session = json.loads(Path(manifest).read_text())
    path = resolve_asset(directory, session["path"])
    part = path.with_suffix(".part.wav")
    if not path.exists() and part.exists():
        # Only complete flushed PCM blocks are recovered. Invalid/empty files remain
        # available for inspection instead of being reported as successful recordings.
        with wave.open(str(part), "rb") as stream:
            if stream.getnframes() <= 0:
                return None
        os.replace(part, path)
    if not path.exists():
        return None
    with wave.open(str(path), "rb") as stream:
        if not stream.getnframes():
            return None
        value = {"id": session["id"], "path": session["path"], "sha256": file_sha256(path),
                 "frames": stream.getnframes(), "sample_rate": stream.getframerate(),
                 "duration_seconds": round(stream.getnframes() / stream.getframerate(), 3),
                 "created_at": session["created_at"], "interrupted": bool(interrupted)}
    session.update(state="ready", recording=value)
    atomic_bytes(manifest, json.dumps(session, ensure_ascii=False).encode())
    return value


def pending_recordings(directory):
    for manifest in (Path(directory) / "assets" / "audio").glob("*/*/*.json"):
        try:
            session = json.loads(manifest.read_text())
            if session.get("state") == "recording":
                # A worker still shutting down after its parent exited gets time to
                # finish normally; otherwise recover the last flushed blocks.
                if time.time() - manifest.stat().st_mtime < 3:
                    continue
                recording = finalize_recording(directory, manifest, interrupted=True)
            else:
                recording = session.get("recording")
            if recording:
                validate_audio([recording], directory)
                yield session["case_id"], validate_reader(session["reader_id"]), recording
        except (OSError, ValueError, KeyError, wave.Error):
            # Corrupt attachments are not linked into an annotation. Keep the files.
            continue
