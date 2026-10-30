# 尘肺胸片标注 · 3D Slicer

面向科研读片者的单帧 CR/DX 胸片 DICOM 标注模块。一张图像对应每位读者的一条记录，读片、录音和模型推理均在本机完成。

![主界面：使用 ImageGen 合成胸片的真实插件截图](docs/img/main.png)

*截图中的胸片由 ImageGen 生成，标注仅为操作演示，不含患者影像，也不用于评价模型准确率。*

## 功能

- 整片质量 1–4 级评价与六肺区 12 小级密集度评分。
- 自动肺野分割和六区划分，支持医生修改肺野、肺尖及膈顶后确认。
- 圆形与多边形空间标注。
- 从外部文件夹加载标准片，与当前胸片并排对照，独立调整显示。
- 本地语音备注，可录多段、回放和移除。
- 多读者独立记录、病例切换与自动保存。
- 导出评分 CSV、原片对照表、空间坐标 CSV、JSON，以及录音和掩膜附件。
- 小阴影圆形使用 ExtraMarkups，显示直径；多边形使用原生闭合曲线，显示宽度；拖动后同步更新。
- 读取、切换、自动分割及导出时显示覆盖整个窗口的操作提示；自动分割完成或失败后恢复操作。
- 独立「导出 LabelMe」按钮：按读者生成同名胸片 PNG 与人工圆形 / 多边形 JSON，后台执行并显示进度。

## 适用范围与限制

- 用于科研影像标注，不生成诊断、分期、总体密集度或自动密集度评分。功能测试不代表医生验收或分割准确率验证。
- 六区按 GBZ 70-2015 §3.7 的肺尖至膈顶垂直距离划分，属于读片区域，不是肺叶。自动掩膜及初始定位点需要医生核对，不是专家验证的病灶真值。
- 标准片由使用者从外部文件夹选择，软件不捆绑标准片；判读须遵循研究协议并使用匹配版本，患者左右须核对片内侧别标记。
- 读者编号区分记录，不提供登录或权限控制。受控盲读应使用独立项目目录或工作站。
- 项目使用本地 SQLite；不要在共享网络盘上让多台机器同时写入同一个项目。

## 环境要求

| 项目 | 要求 |
| --- | --- |
| 已验证平台 | macOS，3D Slicer **5.13.0-2026-10-22**；验证范围为功能与持久化 |
| 后台 Python | **3.12.12**，由 [mise](https://mise.jdx.dev/) 固定版本 |
| 磁盘与网络 | 首次下载约 **273 MB 模型权重**；另需 Slicer、依赖及标注数据空间。准备完成后可离线使用 |
| 输入 | 扩展名 `.dcm` 的单帧 CR/DX 灰度 DICOM，支持递归扫描子目录 |
| 标准片 | 外部文件夹中的单帧灰度 DICOM，支持 OT 等类型；不随软件分发 |
| 录音 | 系统默认麦克风、音频输出及麦克风访问许可 |
| Windows | 安装门槛为 **Windows 10 1903（内部版本 18362）及以上的 64 位系统**，包括 Windows 10 22H2 和 Windows 11；最低版本是依赖审查后的兼容目标，旧版 Windows 尚待实机验证。现有[安装检查](.github/workflows/windows-installer.yml)及[后台自动测试](.github/workflows/windows-x64.yml)运行在 Windows Server 2022，界面检查使用软件渲染；见[版本依据与验证范围](docs/usage.md#windows-版本依据与验证范围) |

环境分为两层：Slicer 自带 Python 运行界面和影像组件；项目用 **mise → venv → pip** 管理 `.venv` 中的模型及音频依赖，通过后台进程调用。无需向 Slicer Python 或全局 Python 安装这些依赖。直接依赖版本见 [requirements-runtime.txt](requirements-runtime.txt)；Windows 的完整依赖版本由 [requirements-windows-lock.txt](requirements-windows-lock.txt) 固定，避免重新打包时传递依赖变化影响兼容性。

## 快速开始

**Windows 使用离线安装包：**从[版本下载页](https://github.com/deemoe404/slicer-pneumoconiosis/releases/latest)下载 `PneumoconiosisAnnotator-…-Setup.exe`，双击并允许管理员安装，之后从桌面「尘肺胸片标注」启动。包内已带 Slicer、圆形工具、独立 Python、模型与录音依赖和权重，无需另装开发环境。下载页同时提供 SHA256、构建清单和安装检查报告；详见 [Windows 离线安装](docs/usage.md#windows-离线安装)。

**macOS 或源码开发：**

先安装 [3D Slicer](https://download.slicer.org/) 和 [mise](https://mise.jdx.dev/getting-started.html)，下载本仓库并进入项目目录。

**1. 准备环境，一台电脑执行一次：**

```sh
cd slicer-pneumoconiosis
mise install
mise exec -- python scripts/prepare_runtime.py
```

准备脚本创建 `.venv`、安装依赖，下载并校验模型，以及与 Slicer 匹配的官方 ExtraMarkups 扩展。圆形工具使用其原生 Shape/Ring 节点；多边形使用 Slicer 原生 Closed Curve。扩展固定版本及 SHA512 见 [扩展依赖清单](packaging/extra-markups.json)，本地位于 `local-data/extensions/ExtraMarkups`，不修改 Slicer 应用目录。已有环境可单独执行 `.venv/bin/python scripts/prepare_markups.py`。

**2. 启动，按实际安装位置填写 Slicer 路径：**

```sh
SLICER_APP="$HOME/Applications/3D Slicer 5.13.0 2026-10-22.app" ./启动尘肺标注.command
```

安装位置与启动器默认值一致时，也可双击 `启动尘肺标注.command`。它负责启动软件，不代替新电脑的环境安装。

**3. 标注一例：**

1. 启动为空白；「选择胸片文件夹」，再「新建项目」（选择空文件夹）或「打开项目」（选择已有项目文件夹），然后选择自己的读者编号。
2. 点选整片质量；需要补充意见时录入语音备注。
3. 「自动肺野 / 六区」→核对并修正→「确认分区」。
4. 点击或拖动六条分格滑杆评分；按 0、1、2、3 定位大档位，无法评价的区域选择「不可评」。
5. 「完成并下一例」；需要交给研究者时点「导出交付包」，胸片另行交付。

完整操作与常见问题见 [使用手册](docs/usage.md)。

需要对照片时，点击图像上方「标准片」选择文件夹；左侧为当前胸片，右侧为标准片。目录及选中片会自动记住，详见 [标准片对照](docs/usage.md#标准片对照)。

## 工作流概览

| 环节 | 结果 |
| --- | --- |
| 选择胸片文件夹 | 扫描并显示当前原片目录，等待选择项目 |
| 新建 / 打开项目、选择读者 | 明确指定进度保存位置；项目须匹配整批胸片，切换读者载入独立记录 |
| 评分、修正与补充标注 | 修改自动保存；「下一例」允许保留草稿 |
| 完成病例 | 要求质量及六区均已评、已有分区已确认、已有空间标注有效；「不可评」是有效结果 |
| 导出 | 一次导出当前病例列表中所有有实际阅片记录的读者，按读者分文件夹，保留各自的未评病例、修改历史及引用附件 |

完成条件和保存失败处理见 [完成条件与保存](docs/usage.md#完成条件与保存)。

## 数据与导出

日常标注自动保存在自己选择的项目文件夹中；界面常驻显示胸片目录、项目目录及保存状态。关闭前保存成功才退出，专用启动器不弹 Slicer 场景保存提示。导出则是医院交给 AI 研究者的独立标注快照，不包含原始胸片，不用于恢复标注项目。根目录只用日期时间命名，按实际参与的读者分文件夹：

```text
20261028-153045-123456/
├── 交付说明.txt              # 文件用途、病例关联和交付方式
├── manifest.json             # 各读者完成 / 草稿 / 未评统计及文件 SHA-256
├── reader-01/
│   ├── reader-01.csv          # 评分，每肺区一行
│   ├── reader-01-images.csv   # 病例编号 → 原片相对路径，每胸片一行
│   ├── reader-01-spatial.csv  # 圆形 / 多边形顶点
│   ├── reader-01.json         # 该读者的完整记录与修改历史
│   ├── assets/               # 该读者引用的录音与肺野掩膜，按需生成
│   └── zone-maps/            # 该读者当前六区标签图，按需生成
└── reader-02/                # 有实际阅片记录时才生成，内部结构相同
```

参与依据是当前病例列表中的已保存记录或修改历史：有评分 / 不可评价、质量评价、圆形 / 多边形、录音，或人工修改 / 确认分区。仅切换读者、浏览或调整显示、生成未经修改或确认的机器分区，不算实际阅片。没有任何参与读者时提示暂无记录，不生成空导出包。

原片对照表的路径以「选择胸片文件夹」时选择的文件夹为根，保留子目录；用 `case_id` 关联评分和 JSON。整批原片移动时，只要内部目录结构不变，对照表仍然有效。字段、坐标、标签和读取示例见 [数据格式规范](docs/data-format.md)。

需要在 LabelMe 中查看空间标注时，单独点击「导出 LabelMe」，选择目标目录。它生成独立的日期时间目录，每位读者目录中是同名的 `CXR-….png` / `.json`；用 LabelMe 的 Open Dir 打开读者目录即可。此导出包含当前人工圆形和有效多边形，未完成轮廓的跳过原因写入根目录 `export-report.json`。评分、肺野分区、录音和历史由「导出交付包」保存，详见 [LabelMe 导出](docs/usage.md#labelme-导出)。

## 隐私与数据位置

英文说明与联网范围见 [Privacy policy](PRIVACY.md)，组件来源、许可证与模型权重许可待确认事项见 [Third-party components](docs/third-party.md)。

原始 DICOM 只读，导出不打包原片或标准片。标准片从外部目录读取，本地只记住目录及所选文件。源码版和 Windows 安装版都将标注与录音保存在用户明确选择的项目文件夹。模型位于 `local-data/models/`（安装版在程序目录内），功能测试位于忽略的 `local-data/qa/`；Windows 的应用偏好与日志位于 `%LOCALAPPDATA%/PneumoconiosisAnnotator/`。导出不包含源文件绝对路径、患者姓名字段或原始 DICOM UID；原片对照表保留原有文件名和相对目录，可能含身份信息，应与原始数据一起保管。录音保留原声，不自动转写或脱敏。

备份时关闭模块，完整保存项目目录及原始 DICOM；只备份 SQLite 文件会遗漏录音和掩膜。具体目录说明见 [本地项目与备份](docs/usage.md#本地项目与备份)。

LabelMe 导出会生成胸片的 8 位 PNG 查看图，保留原图像素尺寸和片内文字，不自动脱敏；它不保留 DICOM 的完整灰度信息。原始 DICOM 仍须保留。

## Code signing policy

当前公开的 Windows 安装包尚未数字签名；SHA256 用于文件校验。项目正在准备 SignPath Foundation 开源签名申请，尚未获批或启用签名。维护者职责、签名范围与发行要求见 [Code signing policy](CODE_SIGNING.md)，隐私说明见 [Privacy policy](PRIVACY.md)。

## 开发与测试

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试覆盖持久化、完成条件、六区几何、附件校验、录音恢复及导出。[Windows x64 自动测试](docs/usage.md#windows-x64-自动测试)使用仓库中的合成胸片，验证依赖安装、真实 CPU 模型推理、跨进程保存恢复和完整导出。Slicer 集成脚本和运行方式见 [开发检查](docs/usage.md#开发检查)；其中的功能检查会实际使用麦克风及本地 DICOM 样本。

```text
PneumoconiosisAnnotator/     # Slicer 模块与核心逻辑
scripts/                    # 环境准备、启动及集成检查
tests/                      # 核心逻辑测试
docs/                       # 操作、数据规范与合成演示图
mise.toml                   # 后台 Python 版本
requirements-runtime.txt    # 后台依赖版本
```

变更记录见 [CHANGELOG.md](CHANGELOG.md)。项目处于调试阶段，数据格式允许破坏性调整，不承诺旧版本兼容。

## 致谢与引用

- [ExtraMarkups](https://github.com/chir-set/SlicerExtraMarkups)（Saleem Edah-Tally）提供 Shape 圆形工具；使用官方编译版本，按 [CeCILL-B 许可证](packaging/licenses/ExtraMarkups-LICENSE.txt) 分发。
- [3D Slicer](https://www.slicer.org/) 提供影像显示、Markups 和 Segment Editor。平台引用：Fedorov 等，*Magnetic Resonance Imaging*，2012，30(9):1323–1341；见 [官方引用说明](https://slicer.readthedocs.io/en/latest/user_guide/about.html#how-to-cite)。
- [TorchXRayVision ChestX-Det PSPNet](https://mlmed.org/torchxrayvision/models.html#chestx-det-segmentation) 提供肺野初始化；固定权重来源和 SHA256 见 [model_config.py](PneumoconiosisAnnotator/PneumoconiosisLib/model_config.py)。
- 读片分区与评分参考国家卫生健康委发布的 [GBZ 70-2015《职业性尘肺病的诊断》及第 1 号修改单](https://www.nhc.gov.cn/wjw/pyl/202305/ddda1ec278e24a808e12c0ec582d7137.shtml)。
- 演示胸片使用 ImageGen 生成；[原图](docs/img/synthetic-chest.png)和[生成提示词](docs/img/synthetic-chest.prompt.txt)随文档保存。

## 许可证

本项目原创代码和文档采用 [BSD-3-Clause](LICENSE)。这是与 Slicer 同类的宽松 BSD 风格许可证；[Slicer 原协议](https://github.com/Slicer/Slicer/blob/main/License.txt)还包含面向其维护机构的贡献条款，因此没有直接复制为本项目协议。Slicer、第三方依赖及下载模型分别遵循其自身条款。
