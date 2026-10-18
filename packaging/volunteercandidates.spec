# PyInstaller spec: one folder with VolunteerCandidates.exe, no Python needed.
#   .\build.ps1   (runs the tests first, then this)
# The data folder (%LOCALAPPDATA%\VolunteerCandidates) is never inside the program folder,
# so replacing the program (an update) never touches the data.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - defined by PyInstaller

datas = [
    (str(ROOT / "app" / "web" / "templates"), "app/web/templates"),
    (str(ROOT / "app" / "web" / "static"), "app/web/static"),
]
datas += collect_data_files("tzdata")  # Asia/Jerusalem on Windows

hiddenimports = []
hiddenimports += collect_submodules("uvicorn")  # uvicorn picks its loops and protocols at run time
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("playwright")
hiddenimports += ["argon2", "multipart", "python_multipart", "cryptography"]
if sys.platform == "win32":
    hiddenimports += ["win32com", "win32com.client", "pythoncom", "pywintypes", "webview.platforms.edgechromium",
                      "webview.platforms.winforms", "clr"]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VolunteerCandidates",
    console=False,  # an application window, no black console
    disable_windowed_traceback=False,
    upx=False,  # UPX-packed files are flagged by antivirus more often
    icon=str(ROOT / "packaging" / "icon.ico"),  # blue bell with people; the shortcuts show it too
)
coll = COLLECT(exe, a.binaries, a.datas, name="VolunteerCandidates", upx=False)  # noqa: F821
