"""Case-bound quality, audio and clinician-reviewed lung partition controls."""
from __future__ import annotations

import copy
from pathlib import Path
import time
import uuid

import numpy as np
import qt
import slicer

from .assets import resolve_asset
from .core import QUALITY_GRADES, utc_now
from .partition import validate_partition
from .partition_view import ImagePartition
from .workers import Worker
from .radio_choices import RadioChoices


class CaseFeatures:
    def __init__(self, widget, root):
        self.w, self.root = widget, Path(root)
        self.restoring = False
        self.partition_state = None
        self.audio = []
        self.model_worker = self.audio_worker = self.player = None
        self.jobs = {}
        self.audio_job = None
        self.recording_started = None
        self.last_audio_error = None
        self.qualityWidget = qt.QGroupBox("整片质量")
        layout = qt.QVBoxLayout(self.qualityWidget)
        layout.setSpacing(3)
        options = [(None, "未评"), *((grade, f"{grade} {name}") for grade, name in QUALITY_GRADES.items())]
        self.qualityChoices = RadioChoices(self.qualityWidget, options, self.changed)
        row = qt.QHBoxLayout()
        row.setSpacing(4)
        for value, label in options:
            button = self.qualityChoices.buttons[value]
            button.setAccessibleName("整片质量 · " + label)
            row.addWidget(button)
        layout.addLayout(row)
        self.qualityReason = qt.QLineEdit()
        self.qualityReason.setPlaceholderText("质量缺陷原因（差片 / 废片必填）")
        self.qualityReason.setMaxLength(2000)
        self.qualityReason.connect("textChanged(QString)", self.changed)
        layout.addWidget(self.qualityReason)

        self.partitionWidget = qt.QGroupBox("肺野与六区")
        layout = qt.QVBoxLayout(self.partitionWidget)
        layout.setSpacing(3)
        row = qt.QHBoxLayout()
        self.autoButton = qt.QPushButton("自动肺野 / 六区")
        self.autoButton.connect("clicked()", self.segment)
        self.editLungsButton = qt.QPushButton("修正肺野")
        self.editLungsButton.connect("clicked()", self.edit_lungs)
        self.editPointsButton = qt.QPushButton("调整分界")
        self.editPointsButton.setCheckable(True)
        self.editPointsButton.connect("toggled(bool)", self.edit_landmarks)
        for button in (self.autoButton, self.editLungsButton, self.editPointsButton):
            row.addWidget(button)
        layout.addLayout(row)
        row = qt.QHBoxLayout()
        self.visibleCheck = qt.QCheckBox("显示分区")
        self.visibleCheck.setChecked(True)
        self.visibleCheck.connect("toggled(bool)", lambda value: self.partition.set_visible(value))
        row.addWidget(self.visibleCheck)
        self.confirmButton = qt.QPushButton("确认分区")
        self.confirmButton.connect("clicked()", self.confirm_partition)
        row.addWidget(self.confirmButton)
        self.clearButton = qt.QPushButton("清除分区")
        self.clearButton.connect("clicked()", self.clear_partition)
        row.addWidget(self.clearButton)
        layout.addLayout(row)
        self.partitionLabel = qt.QLabel("自动结果需核对左右肺野、肺尖和膈顶后确认。")
        self.partitionLabel.setWordWrap(True)
        self.partitionLabel.setStyleSheet("color:#7d8899; font-size:11px;")
        layout.addWidget(self.partitionLabel)
        self.partition = ImagePartition(self.partition_edited, self.w.showError)

        self.audioWidget = qt.QGroupBox("语音备注")
        layout = qt.QVBoxLayout(self.audioWidget)
        layout.setSpacing(3)
        row = qt.QHBoxLayout()
        self.recordButton = qt.QPushButton("● 开始录音")
        self.recordButton.connect("clicked()", self.toggle_recording)
        row.addWidget(self.recordButton)
        self.audioCombo = qt.QComboBox()
        self.audioCombo.setMinimumContentsLength(8)
        row.addWidget(self.audioCombo, 1)
        self.playButton = qt.QPushButton("回放")
        self.playButton.connect("clicked()", self.play_audio)
        row.addWidget(self.playButton)
        self.removeAudioButton = qt.QPushButton("移除")
        self.removeAudioButton.setToolTip("从当前记录移除；历史版本仍保留原始录音。")
        self.removeAudioButton.connect("clicked()", self.remove_audio)
        row.addWidget(self.removeAudioButton)
        layout.addLayout(row)
        self.audioLabel = qt.QLabel("本地录音 · 随当前胸片和读者保存")
        self.audioLabel.setStyleSheet("color:#7d8899; font-size:11px;")
        layout.addWidget(self.audioLabel)
        self.audioTimer = qt.QTimer()
        self.audioTimer.setInterval(250)
        self.audioTimer.connect("timeout()", self.recording_time)

    def changed(self, *_):
        if self.restoring or self.w.updating or self.w.currentRecord is None:
            return
        self.w.currentRecord["status"] = "draft"
        self.w.dirty = True
        self.w.setSaveStatus("正在保存…")
        self.w.saveTimer.start()
        self.update_controls()

    def restore(self):
        self.restoring = True
        record = self.w.currentRecord
        self.partition_state = copy.deepcopy(record["partition"])
        cached = None
        if self.partition_state is None:
            cached = self.w.store.preference("model:" + record["case_id"])
            if cached and cached["source_sha256"] == self.w.cases[self.w.currentIndex]["source_sha256"]:
                self.partition_state = copy.deepcopy(cached)
        try:
            self.qualityChoices.set_value(record["quality"]["grade"])
            self.qualityReason.setText(record["quality"]["reason"])
            self.audio = copy.deepcopy(record["audio"])
            self.editPointsButton.setChecked(False)
            self.partition.restore(self.w.volume, self.partition_state, self.w.store.assets)
            self.partition.set_visible(self.visibleCheck.isChecked())
        except Exception as exc:
            self.partition.clear()
            self.w.showError("分区恢复失败：" + str(exc) + "；可清除损坏分区后重新生成。")
        finally:
            self.restoring = False
        self.update_audio_list()
        self.audioLabel.setText(f"本例已保存 {len(self.audio)} 段录音" if self.audio else "本地录音 · 随当前胸片和读者保存")
        if cached is not None and self.partition.state is not None:
            self.changed()
        self.update_controls()

    def capture(self):
        if self.partition.state is not None:
            self.partition_state = self.partition.capture()
        return {"quality": {"grade": self.qualityChoices.value, "reason": self.qualityReason.text},
                "partition": self.partition_state, "audio": self.audio}

    def prepare_leave(self):
        if not self.stop_recording():
            return False
        if self.player:
            self.player.close()
            self.player = None
        self.partition.close_editor()
        self.partition.timer.stop()
        self.editPointsButton.setChecked(False)
        return True

    def clear(self, preserve=False):
        if preserve and self.partition.state is not None:
            self.partition_state = self.partition.capture()
        self.partition.clear()
        if not preserve:
            self.partition_state = None
            self.audio = []
        self.update_audio_list()

    def close(self):
        self.prepare_leave()
        self.partition.clear()
        for worker in (self.model_worker, self.audio_worker, self.player):
            if worker:
                worker.close()
        self.audioTimer.stop()

    def segment(self):
        if self.w.currentRecord is None or self.partition_state is not None or self.jobs:
            return False
        try:
            self.w.markups.finish()
            if self.model_worker is None or self.model_worker.process.state() == qt.QProcess.NotRunning:
                self.model_worker = Worker(self.root, "model", self.model_event, self.model_ended)
            case = self.w.cases[self.w.currentIndex]
            key = uuid.uuid4().hex
            directory = self.w.store.directory / "model-inputs"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / (case["case_id"] + ".npy")
            image = slicer.util.arrayFromVolume(self.w.volume)[0].astype(np.float32)
            if self.w.defaultDisplay["color"] == "vtkMRMLColorTableNodeInvertedGrey":
                image = image.min() + image.max() - image
            np.save(path, image)
            job = {"id": key, "case_id": case["case_id"], "reader_id": self.w.currentReader,
                   "source_sha256": case["source_sha256"], "project": str(self.w.store.directory), "input": str(path)}
            self.jobs[key] = job
            self.model_worker.send(job)
            self.partitionLabel.setText("正在自动分割肺野…可以继续查看其他病例。")
            self.update_controls()
            return True
        except Exception as exc:
            self.jobs.clear()
            self.w.showError("自动分割未启动：" + str(exc))
            self.update_controls()
            return False

    def model_event(self, value):
        job = self.jobs.get(value.get("job"))
        if not job:
            return
        if value["event"] == "progress":
            self.partitionLabel.setText(value["text"])
            return
        self.jobs.pop(value["job"], None)
        if value["event"] == "error":
            self.w.showError("自动分割失败：" + value["message"])
            self.update_controls()
            return
        if self.w.store is None or str(self.w.store.directory) != job["project"]:
            self.update_controls()
            return
        try:
            partition = value["partition"]
            case = next(c for c in self.w.cases if c["case_id"] == job["case_id"])
            validate_partition(partition, case, self.w.store.assets)
            if partition["source_sha256"] != case["source_sha256"]:
                raise ValueError("模型结果不对应原始胸片。")
            self.w.store.set_preference("model:" + job["case_id"], partition)
            if self.w.currentRecord and self.w.currentRecord["case_id"] == job["case_id"] and self.w.currentReader == job["reader_id"] and self.partition_state is None:
                self.partition_state = partition
                self.partition.restore(self.w.volume, partition, self.w.store.assets)
                self.partition.set_visible(self.visibleCheck.isChecked())
                self.changed()
                self.w.flushSave()
        except Exception as exc:
            self.w.showError("分割结果未应用：" + str(exc))
        self.update_controls()

    def model_ended(self, code, _errors):
        if self.jobs:
            self.jobs.clear()
            self.w.showError(f"分割进程已停止（{code}），当前评分仍保留，可重试。")
            self.update_controls()

    def partition_edited(self):
        self.changed()

    def edit_lungs(self):
        self.w.markups.finish()
        self.editPointsButton.setChecked(False)
        self.partition.open_editor()

    def edit_landmarks(self, enabled):
        if not self.restoring:
            self.w.markups.finish()
        self.partition.edit_landmarks(enabled)
        if enabled:
            self.partitionLabel.setText("拖动 R-A / L-A 肺尖、R-D / L-D 膈顶，六区会重新计算。")

    def confirm_partition(self):
        try:
            self.partition.close_editor()
            value = self.partition.capture()
            if value is None:
                return False
            value["state"], value["confirmed_at"] = "confirmed", utc_now()
            value["confirmed_by"] = self.w.currentReader
            validate_partition(value, self.w.cases[self.w.currentIndex], self.w.store.assets, complete=True)
            self.partition.state = value
            self.partition_state = value
            self.editPointsButton.setChecked(False)
            self.changed()
            return self.w.flushSave()
        except Exception as exc:
            self.w.showError("分区尚不能确认：" + str(exc))
            return False

    def clear_partition(self):
        self.partition.clear()
        self.partition_state = None
        self.editPointsButton.setChecked(False)
        if self.w.store and self.w.currentRecord:
            self.w.store.set_preference("model:" + self.w.currentRecord["case_id"], None)
        self.changed()
        self.w.flushSave()

    def toggle_recording(self):
        if self.audio_worker and self.audio_worker.process.state() != qt.QProcess.NotRunning:
            return self.stop_recording()
        try:
            if self.player:
                self.player.close()
                self.player = None
            job = {"id": uuid.uuid4().hex, "project": str(self.w.store.directory),
                   "case_id": self.w.currentRecord["case_id"], "reader_id": self.w.currentReader}
            self.audio_job = job
            self.last_audio_error = None
            self.audio_worker = Worker(self.root, "record", self.audio_event, self.audio_ended, job)
            self.audioLabel.setText("正在启动麦克风…")
            self.update_controls()
            return True
        except Exception as exc:
            self.w.showError("录音未启动：" + str(exc))
            return False

    def audio_event(self, value):
        if value["event"] == "recording":
            self.recording_started = time.monotonic()
            self.audioTimer.start()
        elif value["event"] == "recorded":
            self.audioTimer.stop()
            recording = value.get("recording")
            job = self.audio_job
            if recording and self.w.currentRecord and self.w.currentRecord["case_id"] == job["case_id"] and self.w.currentReader == job["reader_id"] and str(self.w.store.directory) == job["project"]:
                if not any(a["id"] == recording["id"] for a in self.audio):
                    self.audio.append(recording)
                    self.changed()
            self.update_audio_list()
            self.audioLabel.setText("录音已保存" if recording else "没有录入音频，未添加空录音。")
            if value.get("warning"):
                self.w.showError(value["warning"] + " 已录入的部分已保存。")
        elif value["event"] == "error":
            self.last_audio_error = value["message"]
            self.audioTimer.stop()
            self.audioLabel.setText("录音失败，请检查麦克风权限和设备。")
            self.w.showError("录音失败：" + value["message"])

    def audio_ended(self, code, _errors):
        self.audioTimer.stop()
        self.recording_started = None
        if code and self.last_audio_error is None:
            self.last_audio_error = "录音进程意外结束。"
            self.audioLabel.setText("录音意外停止，请检查设备或重试。")
            self.w.showError("录音进程意外结束；已写入文件会在重开项目时检查恢复。")
        self.update_controls()

    def recording_time(self):
        if self.recording_started is not None:
            seconds = int(time.monotonic() - self.recording_started)
            self.audioLabel.setText(f"● 正在录音　{seconds // 60:02d}:{seconds % 60:02d}")

    def stop_recording(self):
        if self.audio_worker:
            if not self.audio_worker.stop_recording():
                self.w.showError("录音仍在保存，请稍后重试；暂不能切换病例。")
                return False
            self.audioTimer.stop()
            self.recording_started = None
            self.update_controls()
        return True

    def update_audio_list(self):
        selected = self.audioCombo.currentData
        self.audioCombo.clear()
        for index, recording in enumerate(self.audio):
            seconds = round(recording["duration_seconds"])
            suffix = " · 中断恢复" if recording.get("interrupted") else ""
            self.audioCombo.addItem(f"录音 {index + 1} · {seconds // 60:02d}:{seconds % 60:02d}{suffix}", recording["id"])
        found = self.audioCombo.findData(selected)
        self.audioCombo.setCurrentIndex(found if found >= 0 else self.audioCombo.count - 1)

    def play_audio(self):
        if self.player and self.player.process.state() != qt.QProcess.NotRunning:
            self.player.close()
            self.player = None
            self.update_controls()
            return
        selected = next((a for a in self.audio if a["id"] == self.audioCombo.currentData), None)
        if selected:
            try:
                path = resolve_asset(self.w.store.directory, selected["path"])
                self.w.store.assets.read(selected)
                self.player = Worker(self.root, "play", self.play_event, lambda *_: self.update_controls(), {"path": str(path)})
                self.update_controls()
            except Exception as exc:
                self.w.showError("无法回放：" + str(exc))

    def play_event(self, value):
        if value["event"] == "error":
            self.w.showError("回放失败：" + value["message"])

    def remove_audio(self):
        selected = self.audioCombo.currentData
        self.audio = [a for a in self.audio if a["id"] != selected]
        self.update_audio_list()
        self.changed()
        self.w.flushSave()

    def update_controls(self):
        active = self.w.currentRecord is not None and self.w.volume is not None
        present = self.partition_state is not None
        editable = self.partition.state is not None
        recording = self.audio_worker is not None and self.audio_worker.process.state() != qt.QProcess.NotRunning
        playing = self.player is not None and self.player.process.state() != qt.QProcess.NotRunning
        for control in (self.qualityWidget, self.recordButton):
            control.setEnabled(active)
        self.autoButton.setEnabled(active and not present and not self.jobs)
        self.editLungsButton.setEnabled(active and editable)
        self.editPointsButton.setEnabled(active and editable)
        self.confirmButton.setEnabled(active and editable)
        self.visibleCheck.setEnabled(active and editable)
        self.clearButton.setEnabled(active and present)
        self.audioCombo.setEnabled(active and bool(self.audio) and not recording)
        self.playButton.setEnabled(active and bool(self.audio) and not recording)
        self.removeAudioButton.setEnabled(active and bool(self.audio) and not recording and not playing)
        self.recordButton.setText("■ 停止录音" if recording else "● 开始录音")
        self.playButton.setText("停止" if playing else "回放")
        self.recordButton.setStyleSheet("color:#d96767; font-weight:600;" if recording else "")
        if self.jobs:
            self.autoButton.setText("分割中…")
        else:
            self.autoButton.setText("已生成分区" if present else "自动肺野 / 六区")
        if editable and not self.editPointsButton.isChecked():
            state = self.partition.state["state"]
            labels = {"pending_review": "自动结果 · 待医生确认", "edited": "已手动修正 · 待重新确认", "confirmed": "分区已确认"}
            self.partitionLabel.setText(labels[state] + "；可修正肺野及肺尖 / 膈顶。")
        elif not present and not self.jobs:
            self.partitionLabel.setText("自动结果需核对左右肺野、肺尖和膈顶后确认。")
