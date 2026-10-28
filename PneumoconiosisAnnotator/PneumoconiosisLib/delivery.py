"""Describe and inventory a self-contained annotation delivery snapshot."""
from pathlib import Path
import json

from .core import file_sha256


def write_delivery_index(directory, snapshot):
    directory = Path(directory)
    readers = []
    for package in snapshot["packages"]:
        counts = {"complete": 0, "draft": 0, "unread": 0}
        for record in package["records"]:
            counts[record["reading_state"]] += 1
        readers.append({"reader_id": package["reader_id"], "case_count": len(package["records"]),
                        "status_counts": counts, "records": f"{package['reader_id']}/{package['reader_id']}.json"})
    description = """医院 → AI 研究者：胸片标注交付包

本目录是导出时的标注快照。原始 DICOM 胸片另行交付，未包含在本包中。
继续阅片请在软件中选择原片文件夹，再打开原有标注项目；本包不是标注项目备份。
导出不代表所有病例已完成。各读者的 complete（完成）、draft（草稿）、unread（未评）数量见 manifest.json；JSON 当前记录的 reading_state 与之对应，分析时应按此状态筛选。

文件说明（reader-01 等每个文件夹对应一位实际参与阅片的读者）：
  reader-01.csv          六肺区评分、整片质量与完成状态；未评不等于 0/0。
  reader-01-images.csv   case_id → 原片相对路径及 SHA-256，供另行交付的胸片核对。
  reader-01-spatial.csv  圆形、多边形的坐标与尺寸；坐标为原图 [列, 行, 切片]。
  reader-01.json         完整当前记录、修改历史、附件引用、坐标与评分定义。
  assets/masks/         肺野掩膜 NPZ；right/left 为 0/1 数组。文件名是内容 SHA-256，
                        由 JSON 中 partition.masks（当前）和 partition.baseline（初始）关联病例。
  assets/audio/         按病例和读者组织的原始录音，JSON 中 audio 引用。
  zone-maps/            按 case_id 命名的六区 NPZ，zones 值 0=区外，1–6=RU/RM/RL/LU/LM/LL。
  manifest.json         导出时间、读者和状态统计、包内文件路径与 SHA-256 校验值。
没有相应标注时，不生成对应附件文件夹。LabelMe PNG/JSON 由「导出 LabelMe」另行生成。

与胸片对应：以各 reader-XX-images.csv 的 source_relative_path 为原片文件夹内的相对路径，
校验原片 SHA-256 后，用 case_id 关联评分、空间标注和 JSON。对照表的 ./ 是路径前缀。
图像移动或更名后仍可通过 source_sha256 校验匹配；不要按文件排列顺序匹配。
尺寸的单位和校准来源见记录的 image.measurement_scale；没有可靠物理尺度时使用 px。
原片路径/文件名及录音可能包含身份信息，本包不自动脱敏。
"""
    (directory / "交付说明.txt").write_text(description, encoding="utf-8")
    files = [{"path": path.relative_to(directory).as_posix(), "size_bytes": path.stat().st_size,
              "sha256": file_sha256(path)} for path in sorted(directory.rglob("*")) if path.is_file()]
    manifest = {"format": "pneumoconiosis-research-delivery", "version": 1,
                "exported_at": snapshot["packages"][0]["exported_at"], "includes_source_images": False,
                "case_count": len(snapshot["images"]), "readers": readers, "files": files}
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
