#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  VoiceInput 管理脚本
#  用法:
#    ./scripts/run.sh              # 交互式菜单（单次执行后退出）
#    ./scripts/run.sh --start      # 直接执行命令
#    ./scripts/run.sh --help       # 查看帮助
#
#  参数: --start | --install | --build [type] | --clean
#        --logs | --publish | --rollback | --help
# ============================================================

# —— 编码 ——

export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"
export PYTHONIOENCODING="${PYTHONIOENCODING:-utf-8}"

# —— 配置区 ——

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
REQ_FILE="$PROJECT_DIR/src/requirements.txt"
LOG_DIR="$HOME/.voiceinput/logs"
RELEASE_SRC="$PROJECT_DIR/_release/_发布和运营指南/相关资源"

# —— 颜色 & 日志 ——

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${RESET}  $*"; }
log_ok()    { echo -e "${GREEN}[ OK ]${RESET}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${RESET}  $*"; }
log_error() { echo -e "${RED}[ERROR]${RESET} $*"; }
log_step()  { echo -e "${CYAN}  →${RESET} $*"; }
log_cmd()   { echo -e "${DIM}  \$${RESET} ${BOLD}$*${RESET}"; }
divider()   { echo -e "${DIM}$(printf '─%.0s' {1..55})${RESET}"; }

# —— 辅助函数 ——

assert_venv() {
    if [[ ! -x "$PYTHON" ]]; then
        log_error "未找到 .venv，请先运行: ./run.sh --install"
        exit 1
    fi
}

# —— 核心操作 ——

do_start() {
    assert_venv
    log_info "启动 VoiceInput..."
    log_cmd "$PYTHON -u src/main.py"
    echo ""
    cd "$PROJECT_DIR"
    exec "$PYTHON" -u src/main.py
}

do_install() {
    log_info "安装依赖..."
    cd "$PROJECT_DIR"

    if command -v uv &>/dev/null; then
        if [[ ! -d ".venv" ]]; then
            log_cmd "uv venv"
            uv venv
        fi
        log_cmd "uv pip install -r src/requirements.txt"
        uv pip install -r src/requirements.txt
    else
        if [[ ! -d ".venv" ]]; then
            log_cmd "python3 -m venv .venv"
            python3 -m venv .venv
        fi
        log_cmd "$PYTHON -m pip install -r src/requirements.txt"
        "$PYTHON" -m pip install -r src/requirements.txt
    fi

    echo ""
    log_ok "依赖安装完成"
}

do_build() {
    assert_venv
    local build_type="${1:-}"
    local valid_types=("portable" "onefile" "installer" "all" "clean")

    if [[ -z "$build_type" ]]; then
        echo ""
        echo -e "  ${BOLD}构建选项${RESET}"
        echo ""
        echo -e "    ${GREEN}1)${RESET} 嵌入式 Python 便携包    ${DIM}portable${RESET}"
        echo -e "    ${GREEN}2)${RESET} PyInstaller 单文件 exe   ${DIM}onefile${RESET}"
        echo -e "    ${GREEN}3)${RESET} Inno Setup 安装包        ${DIM}installer${RESET}"
        echo -e "    ${GREEN}4)${RESET} 全部构建                 ${DIM}all${RESET}"
        echo -e "    ${RED}5)${RESET} 清理 dist/ build/        ${DIM}clean${RESET}"
        echo ""
        read -rp "$(echo -e "  ${CYAN}请选择 [1-5]: ${RESET}")" choice
        case "$choice" in
            1) build_type="portable" ;;
            2) build_type="onefile" ;;
            3) build_type="installer" ;;
            4) build_type="all" ;;
            5) build_type="clean" ;;
            *) log_error "无效选项: $choice"; exit 1 ;;
        esac
    fi

    local found=false
    for t in "${valid_types[@]}"; do
        [[ "$t" == "$build_type" ]] && found=true
    done
    if ! $found; then
        log_error "无效构建类型: $build_type (可选: ${valid_types[*]})"
        exit 1
    fi

    echo ""
    log_info "构建: --$build_type"
    log_cmd "$PYTHON scripts/build.py --$build_type"
    echo ""
    cd "$PROJECT_DIR"
    "$PYTHON" scripts/build.py "--$build_type"
}

do_clean() {
    assert_venv
    log_info "清理构建产物..."
    log_cmd "$PYTHON scripts/clean_build.py --confirm"
    echo ""
    cd "$PROJECT_DIR"
    "$PYTHON" scripts/clean_build.py --confirm
}

do_logs() {
    if [[ ! -d "$LOG_DIR" ]]; then
        log_error "日志目录不存在: $LOG_DIR"
        return
    fi

    local latest
    latest=$(ls -t "$LOG_DIR"/*.log 2>/dev/null | head -1)
    if [[ -z "$latest" ]]; then
        log_warn "暂无日志文件"
        return
    fi

    log_info "最新日志: $(basename "$latest")"
    divider
    tail -n 50 -f "$latest"
}

do_publish() {
    cd "$PROJECT_DIR"
    log_info "发布文件到项目根目录..."
    if [[ ! -f "$RELEASE_SRC/README.md" ]]; then
        log_error "未找到: $RELEASE_SRC/README.md"
        return
    fi
    cp -f "$RELEASE_SRC/README.md" "README.md"
    log_step "README.md"

    if [[ -f "$RELEASE_SRC/LICENSE" ]]; then
        cp -f "$RELEASE_SRC/LICENSE" "LICENSE"
        log_step "LICENSE"
    fi

    mkdir -p docs
    if compgen -G "$RELEASE_SRC/docs/*.gif" >/dev/null 2>&1; then
        cp -f "$RELEASE_SRC/docs/"*.gif docs/
        log_step "docs/*.gif"
    fi

    echo ""
    log_ok "已发布"
}

do_rollback() {
    cd "$PROJECT_DIR"
    log_info "回滚发布文件..."
    if [[ -d ".git" ]]; then
        if git restore README.md LICENSE docs/ 2>/dev/null; then
            log_step "已 git restore README.md LICENSE docs/"
        else
            log_warn "git restore 失败，尝试删除发布副本..."
            [[ -f "README.md" ]] && rm -f "README.md" && log_step "已删除 README.md"
            [[ -f "LICENSE" ]]   && rm -f "LICENSE"   && log_step "已删除 LICENSE"
            [[ -d "docs" ]]      && rm -rf "docs"     && log_step "已删除 docs/"
        fi
    else
        [[ -f "README.md" ]] && rm -f "README.md" && log_step "已删除 README.md"
        [[ -f "LICENSE" ]]   && rm -f "LICENSE"   && log_step "已删除 LICENSE"
        [[ -d "docs" ]]      && rm -rf "docs"     && log_step "已删除 docs/"
    fi
    echo ""
    log_ok "已回滚"
}

# —— 交互式菜单（单次执行后退出） ——

show_menu() {
    echo ""
    echo -e "  ${BOLD}╔════════════════════════════════════════╗${RESET}"
    echo -e "  ${BOLD}║       VoiceInput 管理脚本              ║${RESET}"
    echo -e "  ${BOLD}╚════════════════════════════════════════╝${RESET}"
    echo ""
    echo -e "    ${GREEN}1)${RESET} 启动应用      ${DIM}--start${RESET}"
    echo -e "    ${GREEN}2)${RESET} 安装依赖      ${DIM}--install${RESET}"
    echo -e "    ${GREEN}3)${RESET} 构建项目      ${DIM}--build${RESET}"
    echo -e "    ${GREEN}4)${RESET} 清理构建      ${DIM}--clean${RESET}"
    echo -e "    ${GREEN}5)${RESET} 查看日志      ${DIM}--logs${RESET}"
    echo -e "    ${GREEN}6)${RESET} 发布文件      ${DIM}--publish${RESET}"
    echo -e "    ${GREEN}7)${RESET} 回滚发布      ${DIM}--rollback${RESET}"
    echo ""
}

interactive() {
    show_menu
    read -rp "$(echo -e "  ${CYAN}请选择 [1-7]: ${RESET}")" choice
    echo ""
    case "$choice" in
        1) do_start   ;; 2) do_install ;; 3) do_build   ;;
        4) do_clean   ;; 5) do_logs    ;; 6) do_publish ;;
        7) do_rollback ;;
        *) log_error "无效选项: $choice"; exit 1 ;;
    esac
}

# —— 帮助 ——

usage() {
    echo ""
    echo -e "  ${BOLD}VoiceInput 管理脚本${RESET}"
    echo ""
    echo -e "  ${BOLD}用法:${RESET}  $0 [参数]"
    echo ""
    echo -e "  ${BOLD}参数:${RESET}"
    echo -e "    --start       启动应用"
    echo -e "    --install     安装/更新依赖"
    echo -e "    --build       构建项目 (可选: portable|onefile|installer|all|clean)"
    echo -e "    --clean       清理构建产物 (dist/, build/, *.spec)"
    echo -e "    --logs        查看最新日志 (tail -f)"
    echo -e "    --publish     将发布文件复制到项目根目录"
    echo -e "    --rollback    回滚发布文件"
    echo -e "    --help        显示此帮助信息"
    echo ""
    echo -e "  不带参数则进入交互式菜单。"
    echo ""
}

# —— 入口 ——

main() {
    if [[ $# -eq 0 ]]; then
        interactive
        exit 0
    fi
    case "$1" in
        --start)    do_start ;;
        --install)  do_install ;;
        --build)    do_build "${2:-}" ;;
        --clean)    do_clean ;;
        --logs)     do_logs ;;
        --publish)  do_publish ;;
        --rollback) do_rollback ;;
        --help)     usage ;;
        *) log_error "未知参数: $1"; usage; exit 1 ;;
    esac
    exit 0
}

main "$@"
