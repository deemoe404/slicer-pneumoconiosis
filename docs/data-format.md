# 数据格式规范

本规范对应 JSON 导出版本 **6**。操作方法见 [使用手册](usage.md)，实现入口是 [core.py](../PneumoconiosisAnnotator/PneumoconiosisLib/core.py)。

## 记录单位与版本

当前记录以 `(case_id, reader_id)` 为键：同一项目的所有胸片、所有读者记录保存在一个 SQLite 库中，录音和掩膜作为外部附件保存。`case_id` 为 `CXR-` 加 SOP Instance UID 的 SHA256 前 16 个十六进制字符，是图像标识，不是患者标识；`reader-01` 至 `reader-10` 对应界面 01–10。

一次导出包含当前病例列表中所有实际参与阅片的读者，每位读者一个子目录及一份 JSON。每份 JSON 顶层 `schema_version` 表示其封装格式，记录及历史载荷中的同名字段表示其写入格式；分析时分别检查版本。历史是原始保存载荷，可能来自其他格式版本，不保证与最新记录字段完全相同。

项目允许破坏性格式调整，不承诺跨版本兼容或自动迁移。应保留原导出、记录所用提交版本，按实际 `schema_version` 选择解析器；遇到未支持版本应明确报错。SQLite 的 `PRAGMA user_version` 属于数据库结构版本，与 JSON 版本独立。

## 导出目录与文件作用

根目录名为 `YYYYMMDD-HHMMSS-ffffff`，不含产品前缀或读者编号，内部按 `reader-01` 等读者编号分子目录。目录时间使用导出机器本地时间；JSON 的导出、保存及确认时间使用带时区的 ISO 8601 UTC 字符串，各读者的 `exported_at` 相同。

只纳入在当前病例列表的当前记录或修改历史中有实际阅片内容的读者：六区任一区已评分 / 不可评价，质量等级或缺陷说明非空，有圆形 / 多边形标注、录音，或分区状态为 `edited` / `confirmed`。显示参数和空草稿不作为参与依据，只有 `pending_review` 的机器分区也不算。历史中的真实标注即使已从当前记录移除，仍保留该读者用于追溯。仅在当前病例列表之外有标注的读者不纳入；无人参与时导出报错，不发布空目录。

以下路径均相对于各自的读者子目录；`<reader_id>` 与子目录同名：

| 路径 | 作用与粒度 |
| --- | --- |
| `<reader_id>.csv` | 该读者当前评分表，每病例固定六行，包含草稿和未评病例 |
| `<reader_id>-images.csv` | 当前病例与原始胸片的对照表，每病例一行，包含未评病例 |
| `<reader_id>-spatial.csv` | 该读者当前圆形 / 多边形顶点表；每个圆形控制点或多边形顶点一行，无标注时仅有表头 |
| `<reader_id>.json` | 该读者完整当前记录、修改历史及格式字典 |
| `assets/audio/<case_id>/<reader_id>/<clip_id>.wav` | 当前记录或历史引用的语音 |
| `assets/masks/<sha256>.npz` | 当前记录或历史引用的初始 / 修订肺野掩膜 |
| `zone-maps/<case_id>.npz` | 当前分区生成的六区标签图，不是历史各版标签图 |

附件目录按需生成。附件相对路径以 JSON 所在的读者子目录为根；原片对照表中的路径以打开胸片时选择的 DICOM 目录为根。交付时须保留整套目录。原始 DICOM 不在包内，导出也不是界面支持的项目恢复格式。

所有读者的记录和历史从同一个数据库快照读取，并在各自子目录复制其引用的附件、核对哈希；所有读者的文件全部生成后才将临时目录改为最终目录名。任一读者导出失败，整包都不发布；失败时可能留下隐藏的 `.partial-export-*` 目录，不应作为成功导出使用。

## 原片对照表

每个读者子目录中的 `<reader_id>-images.csv` 使用 UTF-8 BOM，可用 Excel 打开。列为：

| 字段 | 含义 |
| --- | --- |
| `case_id` | 与评分 CSV、空间 CSV 和 JSON 对应的图像编号 |
| `source_relative_path` | 相对于「选择胸片文件夹」所选目录的路径，保留子目录和原文件名 |
| `source_sha256` | 导入时原始 DICOM 文件的 SHA256，用于核对是否为同一份文件 |

例如 `CXR-…` 对应 `./批次 A/胸片.dcm`，就到所选原片目录下的 `批次 A` 文件夹找 `胸片.dcm`。路径统一使用 `/`，以 `./` 开头，避免文件名被表格软件识别为公式，同时保持程序可直接拼接读取。相同文件名在不同子目录中不会混淆；整批原片移动但内部结构不变时，对照表仍然有效。扫描时已合并的相同图像副本只列出当前病例实际引用的文件。

对照表只包含当前病例列表，每病例一行，不随六区或修改历史重复，也不包含标准片。`source_sha256` 来自导入记录，导出时不重新读取原片；需要验证当前文件内容时，应重新计算其 SHA256 并比较。原片绝对目录和原始 DICOM UID 不导出。对照表保留原文件名和目录名，应与原始数据一起保管；JSON 中不加入这些路径。

## JSON 顶层

交付包根目录另有 `交付说明.txt` 和 `manifest.json`。清单的 `format` 为 `pneumoconiosis-research-delivery`，`version` 为 `1`；`includes_source_images=false` 表示胸片另行交付。`readers[].status_counts` 对应当前记录的 `reading_state`，分 `complete`、`draft`、`unread`。`files` 列出包内文件的相对路径、大小与 SHA-256（不包含清单自身），不暴露本机绝对路径。

| 字段 | 含义 |
| --- | --- |
| `schema_version` | 整数 `6` |
| `standard` | 实现采用的参考规范标识：`GBZ 70-2015 + amendment 1` |
| `exported_at` | 导出时间 |
| `reader_id` | 该 JSON 对应的读者 |
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
| `reading_state` | 当前导出记录的阅片状态：`complete`、`draft`、`unread`；当前无人工阅片内容为 `unread`，不把浏览或机器分区当作已评。此字段不添加到历史修订 |
| `image` | 原图 `columns`、`rows`（像素）及 `measurement_scale` 尺度信息 |
| `zones` | 六区评价，见下表 |
| `quality` | 整片质量对象 |
| `spatial_annotations` | 圆形 / 多边形数组，允许为空 |
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

当前记录没有通用文字 `note` 字段。补充评价使用可选语音记录，相关变更见 [CHANGELOG](../CHANGELOG.md)。

## 空间标注与坐标

每个 `spatial_annotations` 元素包含：

| 字段 | 含义 |
| --- | --- |
| `id` | 稳定 UUID，不能用列表行号代替 |
| `kind` | `circle` 或 `polygon` |
| `label` | 非空名称，最多 80 字 |
| `state` | `ready` 或 `draft`，由几何有效性计算 |
| `points_ijk` | 圆形为直径的两个端点；多边形为有序 `[i, j, k]` 顶点数组 |
| `measurement` | 草稿为 null；有效标注包含方法、单位、间距来源和尺寸值 |

坐标为**原始存储图像的零基像素中心**：I 是列、J 是行、K 固定为 0。左上像素中心是 `[0, 0, 0]`，右下像素中心是 `[columns-1, rows-1, 0]`。允许小数，保存精度为小数点后六位；边界范围含半像素外缘，即 I 在 `[-0.5, columns-0.5]`、J 在 `[-0.5, rows-0.5]`。显示的缩放、平移和窗宽窗位不改变该坐标系统。

圆形由直径的两个不同端点定义，圆心为中点；只有一个端点或两端重合时为 draft。原生 ExtraMarkups Ring 使用 Circumferential 模式，第三个确定平面的点由程序生成并隐藏，不进入标注数据。多边形按顶点顺序保存，末点自动连首点，首点不重复写入；矩形也是多边形。至少三个不同顶点、无交叉 / 重叠边且面积非零时为 `ready`；几何不完整时可保存 `draft`，但阻止完成病例。鼠标移动预览点不属于顶点列表。

这些矢量轮廓与肺野掩膜、六区分界是独立对象，不自动生成解剖区域分割；几何尺寸及尺度来源随 measurement 保存。

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
case_id,reader_id,annotation_id,kind,label,state,vertex_index,i,j,k,columns,rows,
measurement_method,measurement_unit,diameter,width,spacing_source
```

`vertex_index` 从 0 开始；以病例、读者、标注 ID 分组，再按该列恢复顶点顺序。分区定位点不在这个 CSV 中，须读取 JSON 的 `partition.landmarks`。

## 自动尺寸

JSON schema_version=6。圆形 `kind=circle`，两个端点的 I/J 差分别乘以列 / 行间距，再计算欧氏距离，保存为 `measurement.diameter`，方法为 `circle_diameter`。圆形定义在该尺度对应的图像平面，Slicer 原生圆形与该尺度保持一致。多边形方法 `minimum_feret`，`width` 为经尺度变换的轮廓凸包的最小卡尺宽度。使用 SciPy ConvexHull，包含凹形轮廓、旋转和非等距像素。

`image.measurement_scale` 和每个有效标注的 `measurement` 记录 `unit`、`spacing=[column,row]`、`source`、`calibration`。优先 PixelSpacing；其次 ImagerPixelSpacing（显示为探测器尺度）；无有效间距则 `[1,1]` / px。PixelSpacingCalibrationType 仅记录，不推测校准情况。数值由几何重新计算，未完成或无效轮廓的 measurement 为 null。CSV 在每个控制点行重复方法、单位、直径、宽度和间距来源。测量是影像上的几何量，不自动生成小阴影分类或诊断。

## LabelMe 独立导出

「导出 LabelMe」使用独立目录，不向普通研究导出包添加文件。根目录为 `YYYYMMDD-HHMMSS-ffffff`，读者子目录中的每张图像有一组同名 `<case_id>.png` 和 `<case_id>.json`，根目录另有 `export-report.json`。读者参与规则与普通导出相同；只转换当前记录，不转换修改历史。

每图 JSON 的 LabelMe 字段为 `version`（兼容目标 `7.9.1`）、`flags`、`shapes`、`imagePath`、`imageData`、`imageHeight`、`imageWidth`。`imagePath` 是同目录 PNG 文件名，`imageData=null`。正方形像素上的圆形导出为 `shape_type=circle`，`points` 为圆心和圆周一点。非正方形像素上的物理圆在原图像素坐标中呈椭圆，导出为 `shape_type=polygon` 的 128 点轮廓，保持原始 PNG 尺寸和几何对应；额外保留 `source_kind=circle` 和直径两端 `source_points_ijk`。普通多边形导出为 `polygon` 并保留原顶点。每个形状均附 `measurement`。每个形状包含原标签 `label`、二维原图坐标 `points`、`group_id=null`、`flags={}`、`description=""` 和 `mask=null`。扩展字段 `case_id`、`reader_id` 与每形状 `annotation_id` 用于追溯；它们不是 LabelMe 的评分语义。

`flags.case_complete` 表示本项目的病例完成状态，`flags.has_skipped_shapes` 表示存在未转换的轮廓。有效形状保留顶点顺序和小数坐标，I/J 映射到 x/y，K=0 不写入；无效圆形或多边形的 ID、标签及原因保存在报告 `records[].skipped_shapes`。空形状列表不代表阴性；仅有评分、没有空间标注的病例也可能为空。

PNG 是保持 `(rows, columns)` 和存储像素顺序的 8 位灰度查看图，不缩放、裁剪或翻转像素，不嵌入 DICOM 标签。转换复用本项目的 DICOM 显示读取：先应用 Modality LUT / rescale，再应用首个 VOI LUT 或窗宽窗位；没有 VOI 时用图像范围归一化，MONOCHROME1 反相。所有读者使用相同图像，不使用读者的显示设置。转换读取的原片字节必须与导入时 SHA256 相符；不能解码时直接报错，不生成占位图。PNG 不保留 DICOM 完整灰度或物理像素间距的显示比例。

报告 `schema_version=1`，包含兼容版本、导出时间、读者列表、每例完成状态、形状数和跳过原因。`images` 记录原片相对路径、源文件哈希、PNG 哈希、原始尺寸及转换后的灰度映射范围、反相状态和间距；不输出原片绝对路径或原始 UID。图像转换在独立后台进程内使用最多四个线程，整包写完才发布最终日期目录。报告和 PNG 均是研究数据，片内文字不会自动脱敏。

## 读取示例

以下示例使用标准库和 NumPy（可在项目 `.venv` 运行），显式检查版本，只读取当前记录：

```python
import json
from pathlib import Path
import numpy as np

bundle = Path("导出文件夹") / "reader-01"
data = json.loads(next(bundle.glob("*.json")).read_text(encoding="utf-8"))
if data["schema_version"] != 6:
    raise ValueError("不支持的导出封装版本")

for record in data["records"]:
    if record["schema_version"] != 6:
        raise ValueError("不支持的记录版本")
    print(record["case_id"], record["quality"]["grade"], record["zones"]["RU"])
    if record["partition"] is not None:
        reference = record["partition"]["zone_map"]
        with np.load(bundle / reference["path"], allow_pickle=False) as arrays:
            zones = arrays["zones"]
        assert zones.shape == (record["image"]["rows"], record["image"]["columns"])
```

本地数据库含来源文件路径、原始 UID 和文件哈希，属于工作数据。导出省略绝对路径和原始 UID，但原片对照表保留原文件名和相对目录；它们可能含身份信息。`case_id`、哈希、空间标注及录音也应按研究数据管理；省略直接标识不等于音频或图像内容已经脱敏。
