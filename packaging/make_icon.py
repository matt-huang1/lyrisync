"""Render packaging/appicon.svg into packaging/SottoVoce.icns.

Every size is rendered from the SVG rather than downscaled from the 1024,
because a 16pt icon that is a shrunken 1024 is mud: the bars are drawn at
the size they will be seen at, so their edges land on whole pixels.

Qt does the rasterising (already a dependency) and iconutil does the
packing (ships with macOS). Off macOS this writes the .iconset and stops,
which is enough to check the artwork without a Mac.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "appicon.svg"
ICONSET = HERE / "SottoVoce.iconset"
ICNS = HERE / "SottoVoce.icns"

# The set macOS asks for: each logical size at 1x and 2x.
SIZES = [16, 32, 128, 256, 512]


def render(size: int) -> QImage:
    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(SOURCE)).render(painter)
    painter.end()
    return image


def main() -> int:
    if not SOURCE.exists():
        print(f"missing {SOURCE}", file=sys.stderr)
        return 1
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    app = QGuiApplication.instance() or QGuiApplication(["-platform", "offscreen"])

    if ICONSET.exists():
        shutil.rmtree(ICONSET)
    ICONSET.mkdir(parents=True)

    for size in SIZES:
        render(size).save(str(ICONSET / f"icon_{size}x{size}.png"))
        render(size * 2).save(str(ICONSET / f"icon_{size}x{size}@2x.png"))

    if sys.platform != "darwin":
        print(f"wrote {ICONSET} (iconutil is macOS-only, no .icns built)")
        return 0

    subprocess.run(
        ["iconutil", "--convert", "icns", str(ICONSET), "--output", str(ICNS)],
        check=True,
    )
    shutil.rmtree(ICONSET)
    print(f"wrote {ICNS} ({ICNS.stat().st_size // 1024} KB)")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
