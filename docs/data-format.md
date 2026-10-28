# 数据格式规范

本规范对应 JSON 导出版本 **4**。操作方法见 [使用手册](usage.md)，实现入口是 [core.py](../PneumoconiosisAnnotator/PneumoconiosisLib/core.py)。

## 记录单位与版本

当前记录以 `(case_id, reader_id)` 为键：同一项目的所有胸片、所有读者记录保存在一个 SQLite 库中，录音和掩膜作为外部附件保存。`case_id` 为 `CXR-` 加 SOP Instance UID 的 SHA256 前 16 个十六进制字符，是图像标识，不是患者标识；`reader-01` 至 `reader-10` 对应界面 01–10。

一次导出只包含当前病例列表和当前读者。顶层 `schema_version` 表示导出封装格式，记录及历史载荷中的同名字段表示其写入格式；分析时分别检查版本。历史是原始保存载荷，可能来自其他格式版本，不保证与最新记录字段完全相同。

项目允许破坏性格式调整，不承诺跨版本兼容或自动迁移。应保留原导出、记录所用提交版本，按实际 `schema_version` 选择解析器；遇到未支持版本应明确报错。SQLite 的 `PRAGMA user_version` 属于数据库结构版本，与 JSON 版本独立。

## 导出目录与文件作用

目录名为 `pneumoconiosis-<reader_id>-YYYYMMDD-HHMMSS-ffffff`。文件名时间使用导出机器本地时间；JSON 的导出、保存及确认时间使用带时区的 ISO 8601 UTC 字符串。

| 路径 | 作用与粒度 |
| --- | --- |
| `<目录同名>.csv` | 当前评分表，每病例固定六行，包含草稿和未评病例 |
| `<目录同名>-images.csv` | 当前病例与原始胸片的对照表，每病例一行，包含未评病例 |
| `<目录同名>-spatial.csv` | 当前点 / 多边形顶点表；每个点一行，每个多边形顶点一行，无标注时仅有表头 |
| `<目录同名>.json` | 完整当前记录、修改历史及格式字典 |
| `assets/audio/<case_id>/<reader_id>/<clip_id>.wav` | 当前记录或历史引用的语音 |
| `assets/masks/<sha256>.npz` | 当前记录或历史引用的初始 / 修订肺野掩膜 |
| `zone-maps/<case_id>.npz` | 当前分区生成的六区标签图，不是历史各版标签图 |

附件目录按需生成。附件相对路径以导出目录为根；原片对照表中的路径以打开胸片时选择的 DICOM 目录为根。交付时须保留整套目录。原始 DICOM 不在包内，导出也不是界面支持的项目恢复格式。

导出读取同一个数据库快照，并复制当前记录和历史所引用的附件、核对哈希；全部生成后才将临时目录改为最终目录名。失败时可能留下隐藏的 `.partial-export-*` 目录，不应作为成功导出使用。

## 原片对照表

`<目录同名>-images.csv` 使用 UTF-8 BOM，可用 Excel 打开。列为：

| 字段 | 含义 |
| --- | --- |
| `case_id` | 与评分 CSV、空间 CSV 和 JSON 对应的图像编号 |
| `source_relative_path` | 相对于「打开 DICOM 目录」所选目录的路径，保留子目录和原文件名 |
| `source_sha256` | 导入时原始 DICOM 文件的 SHA256，用于核对是否为同一份文件 |

例如 `CXR-…` 对应 `./批次 A/胸片.dcm`，就到所选原片目录下的 `批次 A` 文件夹找 `胸片.dcm`。路径统一使用 `/`，以 `./` 开头，避免文件名被表格软件识别为公式，同时保持程序可直接拼接读取。相同文件名在不同子目录中不会混淆；整批原片移动但内部结构不变时，对照表仍然有效。扫描时已合并的相同图像副本只列出当前病例实际引用的文件。

对照表只包含当前病例列表，每病例一行，不随六区或修改历史重复，也不包含标准片。`source_sha256` 来自导入记录，导出时不重新读取原片；需要验证当前文件内容时，应重新计算其 SHA256 并比较。原片绝对目录和原始 DICOM UID 不导出。对照表保留原文件名和目录名，应与原始数据一起保管；JSON 中不加入这些路径。

## JSON 顶层

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 整数 `4` |
| `standard` | 实现采用的参考规范标识：`GBZ 70-2015 + amendment 1` |
| `exported_at` | 导出时间 |
| `reader_id` | 本次导出的读者 |
| `coordinate_system` | 像素坐标系统、单位、轴、零基索引及闭合约定 |
| `grade_codes` | 原始小级到 CSV 安全编码的映射 |
| `zone_names` | 六区代号到中文名的映射 |
| `quality_grades` | 质量等级到名称的映射，JSON 对象键为字符串 |
| `records` | 每病例一条当前记录，包括未评病例 |
| `history` | 对应读者和病例的已保存修订，按修订编号排列 |

`history` 每项为 `revision`、`at`、`annotation`：分别是库内修订编号、保存时间、当次原始载荷。`revision` 不是每病例从 1 开始的连续序号。`records` 是分析当前标注的入口，不能把历史里的每次保存当作独立胸片或独立读者评分。

## 当前记录

| 字段 | 含义 |
| --- | --- |
| `schema_version`, `standard` | 该载荷版本和参考规范 |
| `case_id`, `reader_id` | 图像与读者联合标识 |
| `status` | `draft` 或 `complete` |
| `image` | 原图 `columns`、`rows`，单位像素 |
| `zones` | 六区评价，见下表 |
| `quality` | 整片质量对象 |
| `spatial_annotations` | 点 / 多边形数组，允许为空 |
| `partition` | 分区对象；未生成时为 `null` |
| `audio` | 录音对象数组，允许为空 |
| `display` | 随标注保存的显示状态；可以是空对象 |
| `updated_at` | 最近保存时间；从未保存的记录为 `null` |

`display` 可包含 `window`、`level`、`color`、`field_of_view`、`origin`。它们是 Slicer 的显示参数，不是原图像素修改、解剖测量或评分。历史的 `annotation` 不保证包含导出时附加的 `updated_at`。

### 六区评价

| 代号 | 肺区 | 标签图数值 |
| --- | --- | --- |
| `RU` | 患者右上 | 1 |
| `RM` | 患者右中 | 2 |
| `RL` | 患者右下 | 3 |
| `LU` | 患者左上 | 4 |
| `LM` | 患者左中 | 5 |
| `LL` | 患者左下 | 6 |

每区包含 `state` 和 `grade`：

| `state` | `grade` | 完成条件 |
| --- | --- | --- |
| `unread` | `null` | 未评价，不满足 |
| `scored` | 12 小级之一 | 已评价，满足 |
| `not_scorable` | `null` | 已判定不可评，满足，不要求文字或录音 |

12 小级及 CSV 编码如下；应按此顺序处理，不能按字符串字典序排序：

| 小级 | CSV 编码 |
| --- | --- |
| `0/-` | `P0_minus` |
| `0/0` | `P0_0` |
| `0/1` | `P0_1` |
| `1/0` | `P1_0` |
| `1/1` | `P1_1` |
| `1/2` | `P1_2` |
| `2/1` | `P2_1` |
| `2/2` | `P2_2` |
| `2/3` | `P2_3` |
| `3/2` | `P3_2` |
| `3/3` | `P3_3` |
| `3/+` | `P3_plus` |

JSON 保留 `0/1` 等原始字符串；CSV 使用 `P0_1`，防止表格软件转成日期。

### 整片质量

`quality.grade` 是 `null` 或整数 1–4：1 优片、2 良片、3 差片、4 废片。未评为 `null`，不能用 0 替代。`quality.reason` 是可为空的字符串，最多 2000 字；界面没有原因输入框，此字段不是完成条件，也不要求差片 / 废片填写。导出的 `quality_reason` 可以为空。

版本 4 的新记录没有通用文字 `note` 字段。补充评价使用可选语音记录，相关变更见 [CHANGELOG](../CHANGELOG.md)。

## 空间标注与坐标

每个 `spatial_annotations` 元素包含：

| 字段 | 含义 |
| --- | --- |
| `id` | 稳定 UUID，不能用列表行号代替 |
| `kind` | `point` 或 `polygon` |
| `label` | 非空名称，最多 80 字 |
| `state` | `ready` 或 `draft`，由几何有效性计算 |
| `points_ijk` | 按顺序排列的 `[i, j, k]` 数组 |

坐标为**原始存储图像的零基像素中心**：I 是列、J 是行、K 固定为 0。左上像素中心是 `[0, 0, 0]`，右下像素中心是 `[columns-1, rows-1, 0]`。允许小数，保存精度为小数点后六位；边界范围含半像素外缘，即 I 在 `[-0.5, columns-0.5]`、J 在 `[-0.5, rows-0.5]`。显示的缩放、平移和窗宽窗位不改变该坐标系统。

点恰好有一个坐标。多边形按顶点顺序保存，末点自动连首点，首点不重复写入；矩形也是多边形。至少三个不同顶点、无交叉 / 重叠边且面积非零时为 `ready`；几何不完整时可保存 `draft`，但阻止完成病例。鼠标移动预览点不属于顶点列表。

这些矢量轮廓与肺野掩膜、六区分界是独立对象，不自动生成区域分割，也不输出解剖面积或毫米测量。

## 分区与 NPZ

`partition` 非空时包含以下信息：

| 字段 | 含义 |
| --- | --- |
| `rule` | `GBZ70-2015-section3.7-apex-to-diaphragm-dome-thirds` |
| `state` | `pending_review`、`edited` 或 `confirmed` |
| `baseline`, `masks` | 初始机器肺野和当前肺野，各含 `path`、`sha256` |
| `baseline_landmarks`, `landmarks` | 初始与当前定位点；`R` / `L` 下各有 `apex`、`dome`，值为 IJK 坐标 |
| `model` | 模型名称、库版本、权重 URL / SHA256、预处理、阈值及后处理等来源信息 |
| `source_sha256` | 原始 DICOM 文件哈希，绑定来源图像 |
| `created_at` | 机器结果建立时间 |
| `confirmed_at`, `confirmed_by` | 确认时间和读者；未确认时为空或未设置 |
| `pixel_counts` | 六个标签的像素数，键为字符串 `1`–`6` |
| `zone_map` | 仅在当前记录导出时附加：`path`、`sha256`、`axes`、`background`、`labels` |

初始肺野和当前肺野相同时可以引用同一个文件。人工修改后保留初始引用，生成新的不可变附件，并撤销确认。历史保留当时的掩膜和定位点；如需历史标签图，可按该版数据重新计算，不能把当前 `zone_map` 当作所有历史的标签图。

肺野 NPZ 的键是 `right`、`left`，数组均为 `uint8`、值为 0/1、形状为 `(rows, columns)`，分别代表患者右肺和左肺。两肺不能重叠。它与六区 NPZ 不同：六区 NPZ 只有键 `zones`，同形状、`uint8`，0 为肺野外，1–6 的含义见六区表。NumPy 索引为 `[j, i]`，不是 `[i, j]`。

对每侧当前肺野，以肺尖 J 为 `a`、膈顶 J 为 `d`，两条水平线为 `a+(d-a)/3` 和 `a+2*(d-a)/3`；行坐标小于第一条为上区，小于第二条为中区，其余为下区。分区仅应用于对应肺野掩膜内。

## 录音

每段 `audio` 元素包含 `id`、`path`、`sha256`、`frames`、`sample_rate`、`duration_seconds`、`created_at`、`interrupted`。WAV 为单声道、16 位有符号 PCM，采样率由实际设备决定，不能假定固定 16 kHz。时长由帧数 / 采样率重新计算，保留三位小数。

`interrupted=true` 表示中断后保存或恢复的有效片段。录音原声不做转写或脱敏；当前移除的录音若仍被历史引用，也会包含在导出附件中。

## CSV 字段

三个 CSV 都使用 UTF-8 BOM 和表头；空值为空单元格。评分和空间 CSV 中可能被表格软件当作公式的文本加前导单引号，JSON 保持原始文字，精确名称以 JSON 为准。原片对照表的路径使用上述 `./` 前缀，不添加单引号。

评分 CSV 的列顺序为：

```text
case_id,reader_id,status,standard,quality_grade,quality_name,quality_reason,
zone,zone_name,state,profusion_code,major_category,alternative_category,
partition_state,audio_count,audio_paths,updated_at
```

质量、病例状态、分区状态及录音索引在该病例的六行中重复。`major_category`、`alternative_category` 表示小级斜线前后的分量；末端可能为 `-` 或 `+`，CSV 保护会为这些符号加前导单引号，不应强转为整数。需要精确原始分量时，从 JSON 的 `grade` 拆分。未评 / 不可评的三个密集度列为空，区别由 `state` 决定。无分区时 `partition_state=not_generated`；多段录音的相对路径在 `audio_paths` 中用 `|` 分隔。

空间 CSV 的列顺序为：

```text
case_id,reader_id,annotation_id,kind,label,state,vertex_index,i,j,k,columns,rows
```

`vertex_index` 从 0 开始；以病例、读者、标注 ID 分组，再按该列恢复顶点顺序。分区定位点不在这个 CSV 中，须读取 JSON 的 `partition.landmarks`。

## 读取示例

以下示例使用标准库和 NumPy（可在项目 `.venv` 运行），显式检查版本，只读取当前记录：

```python
import json
from pathlib import Path
import numpy as np

bundle = Path("导出文件夹")
data = json.loads(next(bundle.glob("*.json")).read_text(encoding="utf-8"))
if data["schema_version"] != 4:
    raise ValueError("不支持的导出封装版本")

for record in data["records"]:
    if record["schema_version"] != 4:
        raise ValueError("不支持的记录版本")
    print(record["case_id"], record["quality"]["grade"], record["zones"]["RU"])
    if record["partition"] is not None:
        reference = record["partition"]["zone_map"]
        with np.load(bundle / reference["path"], allow_pickle=False) as arrays:
            zones = arrays["zones"]
        assert zones.shape == (record["image"]["rows"], record["image"]["columns"])
```

本地数据库含来源文件路径、原始 UID 和文件哈希，属于工作数据。导出省略绝对路径和原始 UID，但原片对照表保留原文件名和相对目录；它们可能含身份信息。`case_id`、哈希、空间标注及录音也应按研究数据管理；省略直接标识不等于音频或图像内容已经脱敏。
