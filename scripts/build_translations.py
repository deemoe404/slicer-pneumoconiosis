"""Compile the vendored Chinese catalogs with Slicer's bundled Qt lrelease."""
import argparse
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "PneumoconiosisAnnotator/Resources/Translations"


def build(lrelease, directory=DIRECTORY):
    for source in sorted(Path(directory).glob("*.ts")):
        # Retain translated entries marked unfinished upstream; untranslated
        # entries still fall back to their source, as in Slicer's LanguagePacks.
        subprocess.run([str(lrelease), str(source), "-qm", str(source.with_suffix(".qm"))], check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("lrelease", type=Path)
    build(parser.parse_args().lrelease)
