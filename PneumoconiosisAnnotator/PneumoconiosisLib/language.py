"""Load the bundled Chinese catalogs without changing the user's Slicer settings."""
from pathlib import Path

import qt
import slicer

_translators = []


def initialize():
    if _translators:
        return
    directory = Path(__file__).resolve().parents[1] / "Resources/Translations"
    translators = []
    for name in ("qtbase_zh_CN", "CTK_zh_CN", "Slicer_zh_CN"):
        translator = qt.QTranslator(slicer.app)
        if not translator.load(str(directory / (name + ".qm"))):
            raise RuntimeError("中文界面资源缺失，请修复安装：" + name)
        translators.append(translator)
    for translator in translators:
        slicer.app.installTranslator(translator)
    _translators.extend(translators)
