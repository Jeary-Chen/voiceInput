"""
清理构建产物，保留纯粹的开发代码。

清理对象：
  - dist/, build/, *.spec   — 构建产物

用法:
    python clean_build.py              # 预览将要删除的文件
    python clean_build.py --confirm    # 执行删除
"""

import argparse
import shutil
from pathlib import Path

ROOT = Path(__file__).parent.parent

BUILD_ARTIFACTS = [
    "dist",
    "build",
    "VoiceInput.spec",
]


def preview():
    print("[PREVIEW] The following items will be removed:\n")
    found = []
    for name in BUILD_ARTIFACTS:
        p = ROOT / name
        if p.exists():
            kind = "DIR " if p.is_dir() else "FILE"
            found.append((kind, p))
            print(f"  {kind}  {p}")
        else:
            print(f"  ---   {p}  (not found, skip)")
    print()
    if found:
        print(f"Total: {len(found)} items. Run with --confirm to delete.")
    else:
        print("Nothing to clean.")
    return found


def clean(items: list[tuple[str, Path]]):
    for kind, p in items:
        if p.is_dir():
            shutil.rmtree(p)
            print(f"[DEL] {p}/")
        else:
            p.unlink()
            print(f"[DEL] {p}")
    print(f"\n[OK] Cleaned {len(items)} items.")


def main():
    parser = argparse.ArgumentParser(description="Clean build artifacts (dist/, build/, *.spec)")
    parser.add_argument("--confirm", action="store_true",
                        help="Actually delete (without this flag, only preview)")
    args = parser.parse_args()

    found = preview()
    if args.confirm and found:
        clean(found)


if __name__ == "__main__":
    main()
