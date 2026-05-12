"""
VoiceInput 打包脚本

用法:
    python build.py --portable      # 嵌入式 Python 便携目录（推荐分发）
    python build.py --onefile       # PyInstaller 单文件 exe
    python build.py --installer     # Inno Setup 安装包
    python build.py --all           # 同时生成单文件 + 便携 zip + 安装包
    python build.py --clean         # 清理构建产物
"""

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPTS = Path(__file__).parent
SRC = ROOT / "src"
APP_ICON_ICO = ROOT / "assets" / "app_icon.ico"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
DIST_APP = DIST / "VoiceInput"

PYTHON_VERSION = "3.12.10"
PYTHON_EMBED_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
DEFAULT_DEV_VERSION = "0.0.0-dev"

LAUNCHER_CS = r"""
using System;
using System.Diagnostics;
using System.IO;

class Program {
    static void Main(string[] args) {
        string dir = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(dir, "python", "python.exe");
        string script = Path.Combine(dir, "src", "main.py");

        if (!File.Exists(python) || !File.Exists(script)) {
            Environment.Exit(1);
        }

        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = python;
        psi.Arguments = "\"" + script + "\"";
        psi.WorkingDirectory = dir;
        psi.UseShellExecute = false;
        psi.CreateNoWindow = true;

        try {
            Process proc = Process.Start(psi);
            proc.WaitForExit();
            Environment.Exit(proc.ExitCode);
        } catch (Exception) {
            Environment.Exit(1);
        }
    }
}
"""


def clean():
    print("[CLEAN] Removing build artifacts...")
    for d in [DIST, BUILD, ROOT / "VoiceInput.spec", SCRIPTS / "VoiceInput.spec"]:
        if d.exists():
            if d.is_dir():
                shutil.rmtree(d)
            else:
                d.unlink()
    for d in ROOT.glob("__pycache__"):
        shutil.rmtree(d)
    print("[CLEAN] Done.")


def copy_src(dest: Path):
    """Copy src/ to dest, excluding caches and sensitive files."""
    src_dest = dest / "src"
    if src_dest.exists():
        shutil.rmtree(src_dest)
    shutil.copytree(SRC, src_dest, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc",
        "config.json", ".env", ".env.*", "*.key", "*.pem", "*.secret",
    ))
    print(f"[COPY] src/ -> {src_dest}")


def _normalize_tag_version(tag: str) -> str:
    tag = (tag or "").strip()
    if tag.lower().startswith("refs/tags/"):
        tag = tag.rsplit("/", 1)[-1]
    return tag.lstrip("vV") or DEFAULT_DEV_VERSION


def _git_latest_tag() -> str:
    try:
        return subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=ROOT,
            text=True,
            encoding="utf-8",
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _resolve_app_version() -> str:
    ref_name = os.environ.get("GITHUB_REF_NAME") or os.environ.get("GITHUB_REF", "")
    if ref_name:
        return _normalize_tag_version(ref_name)
    return _normalize_tag_version(_git_latest_tag())


APP_VERSION = _resolve_app_version()


def _write_embedded_version(version: str = APP_VERSION):
    version_py = SRC / "_version.py"
    version_py.write_text(
        f'"""Build-time application version."""\n\nVERSION = "{version}"\n',
        encoding="utf-8",
    )
    print(f"[WRITE] {version_py} ({version})")


def _pip_install(*packages: str):
    """Install packages, using uv if available, otherwise pip."""
    if shutil.which("uv"):
        cmd = ["uv", "pip", "install", *packages]
    else:
        cmd = [sys.executable, "-m", "pip", "install", *packages]
    print(f"[CMD] {' '.join(cmd)}")
    subprocess.check_call(cmd)


def _ensure_pyinstaller():
    try:
        import PyInstaller
    except ImportError:
        print("[INFO] Installing PyInstaller...")
        _pip_install("pyinstaller")


def _ensure_app_icon() -> Path | None:
    if APP_ICON_ICO.exists():
        return APP_ICON_ICO
    print("[WARN] app icon missing — run:  uv run python _scripts/render_app_icons.py")
    return None


def _pyinstaller_cmd(onefile: bool = False) -> list[str]:
    main_py = SRC / "main.py"
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--name", "VoiceInput",
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--noconsole",
        "--add-data", f"{SRC / 'core'};core",
        "--add-data", f"{SRC / 'ui'};ui",
        "--add-data", f"{SRC / 'config.py'};.",
        "--add-data", f"{SRC / '_version.py'};.",
        "--hidden-import", "pyaudio",
        "--hidden-import", "dashscope",
        "--hidden-import", "pynput",
        "--hidden-import", "pynput.keyboard",
        "--hidden-import", "pynput.keyboard._win32",
        "--hidden-import", "pyperclip",
        "--hidden-import", "numpy",
        "--hidden-import", "tzdata",
        "--hidden-import", "comtypes",
        "--hidden-import", "comtypes.stream",
    ]
    ico = _ensure_app_icon()
    if ico:
        cmd.extend(["--icon", str(ico)])
    if onefile:
        cmd.append("--onefile")
    cmd.append(str(main_py))
    return cmd


def build_onefile():
    """PyInstaller --onefile: 单文件便携 exe。"""
    print("[BUILD] PyInstaller onefile mode")
    _ensure_pyinstaller()
    _write_embedded_version()

    cmd = _pyinstaller_cmd(onefile=True)
    print(f"[CMD] {' '.join(cmd)}")
    subprocess.check_call(cmd)

    exe = DIST / "VoiceInput.exe"
    size_mb = exe.stat().st_size / (1024 * 1024)
    print(f"\n[OK] Single-file build: {exe} ({size_mb:.1f} MB)")


def build_portable():
    """使用嵌入式 Python 打包为便携式发行包。"""
    print("[BUILD] Portable (embedded Python) mode")

    if DIST_APP.exists():
        shutil.rmtree(DIST_APP)
    DIST_APP.mkdir(parents=True, exist_ok=True)

    python_dir = DIST_APP / "python"
    if not python_dir.exists():
        _download_embedded_python(python_dir)

    _patch_pth(python_dir)
    _install_pip(python_dir)
    _install_deps(python_dir)
    _prune_runtime_only_python(python_dir)

    _write_embedded_version()
    copy_src(DIST_APP)

    _build_launcher(DIST_APP)

    _write_run_bat(DIST_APP)

    print(f"\n[OK] Portable build complete: {DIST_APP}")
    print(f"[OK] Run: {DIST_APP / 'VoiceInput.exe'}  or  {DIST_APP / 'run.bat'}")


def _download_embedded_python(dest: Path):
    zip_path = BUILD / f"python-{PYTHON_VERSION}-embed-amd64.zip"
    BUILD.mkdir(parents=True, exist_ok=True)

    if zip_path.exists():
        try:
            zipfile.ZipFile(zip_path).close()
            print(f"[CACHE] {zip_path}")
        except zipfile.BadZipFile:
            print(f"[WARN] Cached zip is corrupt, re-downloading")
            zip_path.unlink()

    if not zip_path.exists():
        print(f"[DOWNLOAD] {PYTHON_EMBED_URL}")
        urllib.request.urlretrieve(PYTHON_EMBED_URL, zip_path)

    print(f"[EXTRACT] -> {dest}")
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)


def _patch_pth(python_dir: Path):
    """Remove 'import site' comment from ._pth file to enable site-packages."""
    for pth in python_dir.glob("python*._pth"):
        text = pth.read_text()
        if "#import site" in text:
            text = text.replace("#import site", "import site")
            pth.write_text(text)
            print(f"[PATCH] {pth.name}: enabled site-packages")


def _install_pip(python_dir: Path):
    python_exe = python_dir / "python.exe"
    if (python_dir / "Scripts" / "pip.exe").exists():
        print("[SKIP] pip already installed")
        return

    get_pip = BUILD / "get-pip.py"
    if not get_pip.exists():
        print(f"[DOWNLOAD] {GET_PIP_URL}")
        urllib.request.urlretrieve(GET_PIP_URL, get_pip)

    print("[INSTALL] pip")
    subprocess.check_call([str(python_exe), str(get_pip), "--no-warn-script-location"])


def _install_deps(python_dir: Path):
    python_exe = python_dir / "python.exe"
    req = SRC / "requirements.txt"

    print("[INSTALL] dependencies from requirements.txt")
    subprocess.check_call([
        str(python_exe), "-m", "pip", "install",
        "-r", str(req),
        "--no-warn-script-location",
        "--disable-pip-version-check",
    ])


def _prune_runtime_only_python(python_dir: Path):
    """Remove build-time and type-check-only files the shipped app never uses."""
    site_packages = python_dir / "Lib" / "site-packages"
    remove_paths = [
        python_dir / "Scripts",
        site_packages / "pip",
        site_packages / "PyQt6" / "uic",
        site_packages / "PyQt6" / "lupdate",
        site_packages / "PyQt6" / "Qt6" / "translations",
        site_packages / "PyQt6" / "Qt6" / "qml",
    ]
    remove_paths.extend(site_packages.glob("pip-*.dist-info"))
    remove_paths.extend(site_packages.rglob("__pycache__"))
    remove_paths.extend(site_packages.rglob("*.pyi"))
    remove_paths.extend(site_packages.rglob("py.typed"))

    for path in remove_paths:
        if not path.exists():
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        print(f"[PRUNE] Removed {path}")


def _build_launcher(dest: Path):
    """Try to compile C# launcher; fall back to .bat only."""
    exe_path = dest / "VoiceInput.exe"

    cs_path = BUILD / "launcher.cs"
    BUILD.mkdir(parents=True, exist_ok=True)
    cs_path.write_text(LAUNCHER_CS, encoding="utf-8")

    csc_paths = [
        r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe",
        r"C:\Windows\Microsoft.NET\Framework\v4.0.30319\csc.exe",
    ]
    csc = None
    for p in csc_paths:
        if os.path.exists(p):
            csc = p
            break

    if csc:
        print(f"[COMPILE] C# launcher with {csc}")
        csc_args = [
            csc,
            f"/out:{exe_path}",
            "/target:winexe",
            "/optimize+",
        ]
        if APP_ICON_ICO.exists():
            csc_args.append(f"/win32icon:{APP_ICON_ICO.resolve()}")
        csc_args.append(str(cs_path))
        subprocess.check_call(csc_args)
        print(f"[OK] {exe_path}")
    else:
        print("[WARN] C# compiler not found, skipping .exe launcher")
        print("[INFO] Use run.bat instead")


def _write_run_bat(dest: Path):
    bat = dest / "run.bat"
    bat.write_text(
        '@echo off\r\nchcp 65001 >nul\r\n'
        'cd /d "%~dp0"\r\n'
        'python\\python.exe -u src\\main.py %*\r\n',
        encoding="utf-8",
    )
    print(f"[WRITE] {bat}")


def _zip_dir(src_dir: Path, zip_path: Path):
    """将目录打成 zip 包。"""
    import zipfile as zf
    print(f"[ZIP] {src_dir} -> {zip_path}")
    with zf.ZipFile(zip_path, "w", zf.ZIP_DEFLATED) as z:
        for f in src_dir.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(src_dir.parent))
    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"[OK] {zip_path} ({size_mb:.1f} MB)")


def build_installer(*, refresh_portable: bool = True):
    """基于 portable 构建生成 Inno Setup 安装包。"""
    if refresh_portable or not DIST_APP.exists():
        print("[INFO] Refreshing portable build for installer...")
        build_portable()

    iss_path = BUILD / "installer.iss"
    BUILD.mkdir(parents=True, exist_ok=True)

    setup_icon = ""
    if APP_ICON_ICO.exists():
        setup_icon = f'SetupIconFile={APP_ICON_ICO.resolve().as_posix()}\n'

    iss_content = f"""; Inno Setup script for VoiceInput
; Auto-generated by build.py

[Setup]
AppName=VoiceInput
AppVersion={APP_VERSION}
AppPublisher=myuan19
AppPublisherURL=https://github.com/myuan19/voiceInput
DefaultDirName={{autopf}}\\VoiceInput
DefaultGroupName=VoiceInput
UninstallDisplayIcon={{app}}\\VoiceInput.exe
CloseApplications=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog commandline
{setup_icon}OutputDir={DIST}
OutputBaseFilename=VoiceInput-{APP_VERSION}-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "{DIST_APP}\\*"; DestDir: "{{app}}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\VoiceInput"; Filename: "{{app}}\\VoiceInput.exe"
Name: "{{group}}\\卸载 VoiceInput"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\\VoiceInput"; Filename: "{{app}}\\VoiceInput.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项:"

[Code]
const
  SHUTDOWN_EVENT_NAME = 'VoiceInput_Shutdown_Event';

function OpenEvent(dwDesiredAccess: DWORD; bInheritHandle: BOOL; lpName: String): THandle;
  external 'OpenEventW@kernel32.dll stdcall';
function SetEvent(hEvent: THandle): BOOL;
  external 'SetEvent@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function OpenMutex(dwDesiredAccess: DWORD; bInheritHandle: BOOL; lpName: String): THandle;
  external 'OpenMutexW@kernel32.dll stdcall';

function IsAppRunning: Boolean;
var
  H: THandle;
begin
  H := OpenMutex($00100000, False, 'VoiceInput_InstallAware_Mutex');
  if H <> 0 then
  begin
    CloseHandle(H);
    Result := True;
  end else
    Result := False;
end;

procedure SignalAndWaitForExit;
var
  H: THandle;
  Waited: Integer;
begin
  H := OpenEvent($0002, False, SHUTDOWN_EVENT_NAME);
  if H <> 0 then
  begin
    SetEvent(H);
    CloseHandle(H);
    Waited := 0;
    while IsAppRunning and (Waited < 10000) do
    begin
      Sleep(200);
      Waited := Waited + 200;
    end;
    Sleep(1500);
  end;
end;

function InitializeSetup: Boolean;
begin
  Result := True;
  if IsAppRunning then
    SignalAndWaitForExit;
end;

function InitializeUninstall: Boolean;
begin
  Result := True;
  if IsAppRunning then
    SignalAndWaitForExit;
end;

[Run]
Filename: "{{app}}\\VoiceInput.exe"; Description: "立即启动 VoiceInput"; Flags: nowait postinstall
"""

    iss_path.write_text(iss_content, encoding="utf-8")
    print(f"[WRITE] {iss_path}")

    iscc = _find_iscc()
    if iscc:
        print(f"[COMPILE] Inno Setup: {iscc}")
        subprocess.check_call([str(iscc), str(iss_path)])
        installer = DIST / f"VoiceInput-{APP_VERSION}-setup.exe"
        size_mb = installer.stat().st_size / (1024 * 1024)
        print(f"\n[OK] Installer: {installer} ({size_mb:.1f} MB)")
    else:
        print("[WARN] Inno Setup (ISCC.exe) not found")
        print(f"[INFO] .iss script saved to: {iss_path}")
        print("[INFO] Install Inno Setup from https://jrsoftware.org/isdl.php")
        print(f"[INFO] Then run: ISCC.exe \"{iss_path}\"")


def _find_iscc() -> str | None:
    """尝试在常见路径找到 Inno Setup 编译器。"""
    candidates = [
        r"C:\Users\myuan\AppData\Local\Programs\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 7\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    if shutil.which("ISCC"):
        return shutil.which("ISCC")
    return None


def build_all():
    """一次性生成所有发行格式。"""
    print("=" * 60)
    print(" Building ALL distribution formats")
    print("=" * 60)

    print("\n--- [1/3] Single-file exe ---")
    build_onefile()

    onefile_exe = DIST / "VoiceInput.exe"
    onefile_dest = DIST / f"VoiceInput-{APP_VERSION}-portable.exe"
    if onefile_exe.exists():
        if onefile_dest.exists():
            onefile_dest.unlink()
        onefile_exe.rename(onefile_dest)
        print(f"[RENAME] -> {onefile_dest}")

    print("\n--- [2/3] Portable zip ---")
    build_portable()
    _zip_dir(DIST_APP, DIST / f"VoiceInput-{APP_VERSION}-portable.zip")

    print("\n--- [3/3] Installer ---")
    build_installer(refresh_portable=False)

    print("\n" + "=" * 60)
    print(" Build artifacts in dist/:")
    for f in sorted(DIST.glob("VoiceInput-*")):
        if f.is_file():
            size_mb = f.stat().st_size / (1024 * 1024)
            print(f"   {f.name}  ({size_mb:.1f} MB)")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="VoiceInput build script")
    parser.add_argument("--portable", action="store_true",
                        help="Embedded Python portable directory")
    parser.add_argument("--onefile", action="store_true",
                        help="PyInstaller single-file exe")
    parser.add_argument("--installer", action="store_true",
                        help="Inno Setup installer (.exe)")
    parser.add_argument("--all", action="store_true",
                        help="Build all formats (onefile + portable zip + installer)")
    parser.add_argument("--clean", action="store_true",
                        help="Clean build artifacts")
    args = parser.parse_args()

    if args.clean:
        clean()
        return

    if args.all:
        build_all()
    elif args.onefile:
        build_onefile()
    elif args.installer:
        build_installer()
    elif args.portable:
        build_portable()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
