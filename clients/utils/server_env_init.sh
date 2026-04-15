#!/usr/bin/env bash
# =============================================================================
# Linux 服务器环境初始化脚本
# 检查并安装: git, docker (29.2.1), nginx, certbot
# 支持: Debian/Ubuntu, CentOS/RHEL/Fedora
# =============================================================================

set -euo pipefail

# ---- 颜色输出 ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

DOCKER_VERSION="29.2.1"
DISTRO=""
PKG_MANAGER=""

# ---- 日志函数 ----
log_info()  { echo -e "${BLUE}[INFO]${NC}  $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }

# ---- root 权限检查 ----
check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_error "此脚本需要 root 权限运行，请使用 sudo 或 root 用户执行"
        exit 1
    fi
}

# ---- 识别发行版 ----
detect_distro() {
    if [[ -f /etc/os-release ]]; then
        # shellcheck source=/dev/null
        source /etc/os-release
        case "$ID" in
            ubuntu|debian)
                DISTRO="debian"
                PKG_MANAGER="apt"
                ;;
            centos|rhel|rocky|almalinux)
                DISTRO="rhel"
                if command -v dnf &>/dev/null; then
                    PKG_MANAGER="dnf"
                else
                    PKG_MANAGER="yum"
                fi
                ;;
            fedora)
                DISTRO="fedora"
                PKG_MANAGER="dnf"
                ;;
            *)
                log_error "不支持的 Linux 发行版: $ID"
                exit 1
                ;;
        esac
    else
        log_error "无法识别 Linux 发行版（缺少 /etc/os-release）"
        exit 1
    fi
    log_info "检测到发行版: $ID, 包管理器: $PKG_MANAGER"
}

# ---- 更新包索引 ----
update_pkg_index() {
    log_info "更新包管理器索引..."
    case "$PKG_MANAGER" in
        apt) apt-get update -y ;;
        yum) yum makecache -y ;;
        dnf) dnf makecache -y ;;
    esac
    log_ok "包索引更新完成"
}

# ---- 安装依赖工具 ----
install_prerequisites() {
    log_info "安装基础依赖..."
    case "$PKG_MANAGER" in
        apt) apt-get install -y ca-certificates curl gnupg lsb-release ;;
        yum) yum install -y ca-certificates curl yum-utils ;;
        dnf) dnf install -y ca-certificates curl dnf-plugins-core ;;
    esac
    log_ok "基础依赖安装完成"
}

# ========== Git ==========
check_and_install_git() {
    log_info "===== 检查 Git ====="
    if command -v git &>/dev/null; then
        local git_ver
        git_ver=$(git --version | awk '{print $3}')
        log_ok "Git 已安装，版本: $git_ver"
        return 0
    fi

    log_warn "Git 未安装，开始安装..."
    case "$PKG_MANAGER" in
        apt) apt-get install -y git ;;
        yum) yum install -y git ;;
        dnf) dnf install -y git ;;
    esac

    if command -v git &>/dev/null; then
        log_ok "Git 安装成功，版本: $(git --version | awk '{print $3}')"
    else
        log_error "Git 安装失败"
        return 1
    fi
}

# ========== Docker ==========
check_and_install_docker() {
    log_info "===== 检查 Docker ====="
    if command -v docker &>/dev/null; then
        local docker_ver
        docker_ver=$(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1)
        log_ok "Docker 已安装，版本: $docker_ver"
        if [[ "$docker_ver" == "$DOCKER_VERSION" ]]; then
            log_ok "Docker 版本匹配目标版本 $DOCKER_VERSION"
        else
            log_warn "Docker 当前版本 $docker_ver 与目标版本 $DOCKER_VERSION 不一致，跳过重新安装"
        fi
        return 0
    fi

    log_warn "Docker 未安装，开始安装版本 $DOCKER_VERSION..."
    install_docker_"$DISTRO"

    # 启动 docker 服务
    systemctl start docker
    systemctl enable docker

    if command -v docker &>/dev/null; then
        log_ok "Docker 安装成功，版本: $(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1)"
    else
        log_error "Docker 安装失败"
        return 1
    fi
}

install_docker_debian() {
    # 移除旧版本
    apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

    # 添加 Docker 官方 GPG key
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$(. /etc/os-release && echo "$ID")/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc

    # 添加 Docker 源
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/$(. /etc/os-release && echo "$ID") \
$(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list

    apt-get update -y

    # 查找并安装指定版本
    local docker_pkg_version
    docker_pkg_version=$(apt-cache madison docker-ce | grep "$DOCKER_VERSION" | head -1 | awk '{print $3}')
    if [[ -z "$docker_pkg_version" ]]; then
        log_error "在 apt 源中未找到 Docker 版本 $DOCKER_VERSION，请检查版本号是否正确"
        return 1
    fi

    apt-get install -y \
        "docker-ce=$docker_pkg_version" \
        "docker-ce-cli=$docker_pkg_version" \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
}

install_docker_rhel() {
    # 移除旧版本
    $PKG_MANAGER remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null || true

    # 添加 Docker 源
    $PKG_MANAGER install -y yum-utils 2>/dev/null || true
    yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo

    # 查找并安装指定版本
    local docker_pkg_version
    docker_pkg_version=$($PKG_MANAGER list docker-ce --showduplicates 2>/dev/null | grep "$DOCKER_VERSION" | tail -1 | awk '{print $2}')
    if [[ -z "$docker_pkg_version" ]]; then
        log_error "在 yum/dnf 源中未找到 Docker 版本 $DOCKER_VERSION，请检查版本号是否正确"
        return 1
    fi

    $PKG_MANAGER install -y \
        "docker-ce-$docker_pkg_version" \
        "docker-ce-cli-$docker_pkg_version" \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
}

install_docker_fedora() {
    # 移除旧版本
    dnf remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-selinux docker-engine-selinux docker-engine 2>/dev/null || true

    # 添加 Docker 源
    dnf install -y dnf-plugins-core 2>/dev/null || true
    dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo

    # 查找并安装指定版本
    local docker_pkg_version
    docker_pkg_version=$(dnf list docker-ce --showduplicates 2>/dev/null | grep "$DOCKER_VERSION" | tail -1 | awk '{print $2}')
    if [[ -z "$docker_pkg_version" ]]; then
        log_error "在 dnf 源中未找到 Docker 版本 $DOCKER_VERSION，请检查版本号是否正确"
        return 1
    fi

    dnf install -y \
        "docker-ce-$docker_pkg_version" \
        "docker-ce-cli-$docker_pkg_version" \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
}

# ========== Nginx ==========
check_and_install_nginx() {
    log_info "===== 检查 Nginx ====="
    if command -v nginx &>/dev/null; then
        local nginx_ver
        nginx_ver=$(nginx -v 2>&1 | grep -oP '\d+\.\d+\.\d+')
        log_ok "Nginx 已安装，版本: $nginx_ver"
        return 0
    fi

    log_warn "Nginx 未安装，开始安装..."
    case "$PKG_MANAGER" in
        apt) apt-get install -y nginx ;;
        yum) yum install -y epel-release && yum install -y nginx ;;
        dnf) dnf install -y nginx ;;
    esac

    # 启动并设置开机自启
    systemctl start nginx
    systemctl enable nginx

    if command -v nginx &>/dev/null; then
        log_ok "Nginx 安装成功，版本: $(nginx -v 2>&1 | grep -oP '\d+\.\d+\.\d+')"
    else
        log_error "Nginx 安装失败"
        return 1
    fi
}

# ========== Certbot ==========
check_and_install_certbot() {
    log_info "===== 检查 Certbot ====="
    if command -v certbot &>/dev/null; then
        local certbot_ver
        certbot_ver=$(certbot --version 2>&1 | grep -oP '\d+\.\d+\.\d+')
        log_ok "Certbot 已安装，版本: $certbot_ver"
        return 0
    fi

    log_warn "Certbot 未安装，开始安装..."
    case "$PKG_MANAGER" in
        apt)
            apt-get install -y certbot python3-certbot-nginx
            ;;
        yum)
            yum install -y epel-release 2>/dev/null || true
            yum install -y certbot python3-certbot-nginx
            ;;
        dnf)
            dnf install -y certbot python3-certbot-nginx
            ;;
    esac

    # 设置自动续期定时任务（certbot 通常自带 systemd timer，这里做兜底）
    if ! systemctl is-active --quiet certbot-renew.timer 2>/dev/null && ! systemctl is-active --quiet certbot.timer 2>/dev/null; then
        if systemctl list-unit-files | grep -q "certbot.*timer"; then
            local timer_name
            timer_name=$(systemctl list-unit-files | grep "certbot.*timer" | awk '{print $1}' | head -1)
            systemctl enable --now "$timer_name"
            log_ok "已启用 certbot 自动续期定时器: $timer_name"
        else
            log_info "未发现 certbot systemd timer，添加 crontab 自动续期..."
            if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
                (crontab -l 2>/dev/null; echo "0 3 * * * certbot renew --quiet --deploy-hook \"systemctl reload nginx\"") | crontab -
                log_ok "已添加 crontab 自动续期任务（每天凌晨 3 点执行）"
            else
                log_ok "crontab 中已存在 certbot 续期任务"
            fi
        fi
    else
        log_ok "certbot 自动续期定时器已在运行"
    fi

    if command -v certbot &>/dev/null; then
        log_ok "Certbot 安装成功，版本: $(certbot --version 2>&1 | grep -oP '\d+\.\d+\.\d+')"
    else
        log_error "Certbot 安装失败"
        return 1
    fi
}

# ========== 汇总结果 ==========
print_summary() {
    echo ""
    echo "============================================"
    echo "  服务器环境初始化完成 - 安装结果汇总"
    echo "============================================"

    local items=("git" "docker" "nginx" "certbot")
    for item in "${items[@]}"; do
        if command -v "$item" &>/dev/null; then
            local ver
            case "$item" in
                git)     ver=$(git --version | awk '{print $3}') ;;
                docker)  ver=$(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1) ;;
                nginx)   ver=$(nginx -v 2>&1 | grep -oP '\d+\.\d+\.\d+') ;;
                certbot) ver=$(certbot --version 2>&1 | grep -oP '\d+\.\d+\.\d+') ;;
            esac
            echo -e "  ${GREEN}✓${NC} $item  (版本: $ver)"
        else
            echo -e "  ${RED}✗${NC} $item  (未安装)"
        fi
    done

    echo "============================================"
    echo ""
}

# ========== 主流程 ==========
main() {
    log_info "========== 开始 Linux 服务器环境初始化 =========="
    check_root
    detect_distro
    update_pkg_index
    install_prerequisites
    check_and_install_git
    check_and_install_docker
    check_and_install_nginx
    check_and_install_certbot
    print_summary
    log_info "========== 环境初始化脚本执行完毕 =========="
}

main "$@"
