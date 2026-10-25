"""Six reading zones from editable lung masks and apex/dome landmarks, in image IJK."""
from __future__ import annotations

import copy
import math

import numpy as np

PARTITION_RULE = "GBZ70-2015-section3.7-apex-to-diaphragm-dome-thirds"
ZONE_LABELS = {1: "RU", 2: "RM", 3: "RL", 4: "LU", 5: "LM", 6: "LL"}


def validate_masks(right, left, image):
    expected = (int(image["rows"]), int(image["columns"]))
    for mask in (right, left):
        if mask.shape != expected or mask.dtype != np.bool_:
            raise ValueError("肺野掩膜尺寸或类型与原图不一致。")
        if not mask.any():
            raise ValueError("左右肺野均需保留有效区域，请修正掩膜。")
    if np.any(right & left):
        raise ValueError("左右肺野不能重叠，请用画笔或橡皮修正。")


def validate_landmarks(landmarks, image):
    if set(landmarks) != {"R", "L"}:
        raise ValueError("需要左右肺的肺尖和膈顶定位点。")
    output = {}
    for side in ("R", "L"):
        output[side] = {}
        for name in ("apex", "dome"):
            point = landmarks[side][name]
            if len(point) != 3 or not all(math.isfinite(float(v)) for v in point):
                raise ValueError("分区定位点坐标无效。")
            x, y, z = map(float, point)
            if not (0 <= x < image["columns"] and 0 <= y < image["rows"]) or abs(z) > 1e-3:
                raise ValueError("肺尖或膈顶定位点离开胸片，请拖回图内。")
            output[side][name] = [round(x, 6), round(y, 6), 0.0]
        if output[side]["dome"][1] - output[side]["apex"][1] < 3:
            raise ValueError("膈顶必须位于肺尖下方，请核对定位点。")
    return output


def estimate_landmarks(right, left):
    """Provisional estimates only. A clinician must verify the inferior contour's dome."""
    result = {}
    for side, mask in (("R", right), ("L", left)):
        yy, xx = np.nonzero(mask)
        if not len(yy):
            raise ValueError("模型没有找到完整的左右肺野。")
        top, bottom, x0, x1 = int(yy.min()), int(yy.max()), int(xx.min()), int(xx.max())
        apex_x = float(np.median(xx[yy <= top + 2]))
        # The dome is not the lowest costophrenic point. Use the central inferior
        # contour, excluding narrow apical/lateral columns, as an editable estimate.
        candidates = []
        for x in range(int(x0 + (x1 - x0) * .25), int(x0 + (x1 - x0) * .75) + 1):
            rows = np.flatnonzero(mask[:, x])
            if len(rows) and rows[-1] > top + .60 * (bottom - top) and rows[-1] - rows[0] > .50 * (bottom - top):
                candidates.append((int(rows[-1]), x))
        if not candidates:
            raise ValueError("不能从肺野可靠初始化膈顶，请重新生成或修正肺野。")
        candidates.sort()
        dome_y, dome_x = candidates[max(0, len(candidates) // 10)]
        result[side] = {"apex": [apex_x, float(top), 0.0], "dome": [float(dome_x), float(dome_y), 0.0]}
    return result


def cuts(landmarks, side):
    top, dome = landmarks[side]["apex"][1], landmarks[side]["dome"][1]
    return top + (dome - top) / 3.0, top + 2 * (dome - top) / 3.0


def zone_map(right, left, landmarks):
    image = {"rows": right.shape[0], "columns": right.shape[1]}
    validate_masks(right, left, image)
    landmarks = validate_landmarks(landmarks, image)
    result = np.zeros(right.shape, dtype=np.uint8)
    rows = np.arange(right.shape[0])[:, None]
    for side, mask, offset in (("R", right, 0), ("L", left, 3)):
        first, second = cuts(landmarks, side)
        labels = np.where(rows < first, 1 + offset, np.where(rows < second, 2 + offset, 3 + offset))
        result[mask] = np.broadcast_to(labels, right.shape)[mask]
    if set(np.unique(result)) - {0} != set(ZONE_LABELS):
        raise ValueError("六区中有空区域，请核对肺野、肺尖和膈顶。")
    return result


def validate_partition(partition, image, assets, complete=False):
    if partition is None:
        return None
    value = copy.deepcopy(partition)
    if value.get("rule") != PARTITION_RULE:
        raise ValueError("分区规则无效。")
    if value.get("state") not in ("pending_review", "edited", "confirmed"):
        raise ValueError("分区确认状态无效。")
    value["landmarks"] = validate_landmarks(value["landmarks"], image)
    value["baseline_landmarks"] = validate_landmarks(value["baseline_landmarks"], image)
    if not value.get("model", {}).get("weights_sha256"):
        raise ValueError("分区缺少模型权重来源记录。")
    # Verify both the retained machine result and the current editable masks.
    for reference in (value["baseline"], value["masks"]):
        validate_masks(*assets.lungs(reference), image)
    labels = zone_map(*assets.lungs(value["masks"]), value["landmarks"])
    counts = np.bincount(labels.ravel(), minlength=7)
    value["pixel_counts"] = {str(i): int(counts[i]) for i in range(1, 7)}
    if value["state"] == "confirmed" and not value.get("confirmed_at"):
        raise ValueError("分区确认时间缺失。")
    if complete and value["state"] != "confirmed":
        raise ValueError("请先核对左右肺野和肺尖 / 膈顶，再点击“确认分区”。")
    return value
