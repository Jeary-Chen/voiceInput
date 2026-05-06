# VoiceInput 管理脚本

项目管理脚本，支持交互式菜单和 `--xxx` CLI 参数两种模式。

## 快速使用

```bash
# Linux / macOS
./scripts/run.sh              # 交互式菜单
./scripts/run.sh --start      # 直接启动

# Windows (PowerShell)
.\scripts\run.ps1             # 交互式菜单
.\scripts\run.ps1 --start     # 直接启动

# Windows (cmd)
scripts\run.bat --start       # 通过 bat 包装调用 run.ps1
```

## 命令列表

| 参数 | 说明 | 备注 |
|------|------|------|
| `--start` | 启动应用 | 运行 `python -u src/main.py` |
| `--install` | 安装依赖 | 优先使用 `uv`，否则用 `pip` |
| `--build [type]` | 构建项目 | type: `portable` / `onefile` / `installer` / `all` / `clean` |
| `--clean` | 清理构建产物 | 删除 `dist/`、`build/`、`*.spec` |
| `--logs` | 查看日志 | tail 最新日志文件 (`~/.voiceinput/logs/`) |
| `--publish` | 发布文件 | 将发行文件复制到项目根目录 |
| `--rollback` | 回滚发布 | 删除 `README.md`、`LICENSE`、`docs/` |
| `--help` | 显示帮助 | |

## 文件说明

| 文件 | 用途 |
|------|------|
| `run.ps1` | PowerShell 主脚本（UTF-8 BOM） |
| `run.bat` | cmd.exe 薄包装，透传参数给 `run.ps1` |
| `run.sh` | Bash 脚本，功能与 `run.ps1` 对等 |
| `build.py` | 构建脚本（PyInstaller / 嵌入式 Python / Inno Setup） |
| `clean_build.py` | 构建产物清理 |

## 注意事项

- 首次使用请先运行 `--install` 创建虚拟环境并安装依赖
- `--build` 不带参数时进入构建子菜单
- 交互式菜单为单次执行模式（选择后执行完自动退出）
- 日志文件位于 `%USERPROFILE%\.voiceinput\logs\`（Windows）或 `~/.voiceinput/logs/`（Linux/macOS）
