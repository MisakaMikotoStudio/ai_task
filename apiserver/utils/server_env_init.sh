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
SUDO=""

# ---- Docker apt/yum 源镜像 ----
# 背景：大陆服务器访问 download.docker.com 会被 GFW 基于 TLS SNI 发送 RST，
#       curl 报 `OpenSSL SSL_connect: Connection reset by peer`（exit=35）。
# 方案：统一走阿里云开源镜像站（mirrors.aliyun.com/docker-ce），跨云可用、HTTPS 稳定。
DOCKER_MIRROR_BASE="https://mirrors.aliyun.com/docker-ce/linux"

# ---- 日志函数 ----
log_info()  { echo -e "${BLUE}[INFO]${NC}  $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $(date -u '+%Y-%m-%d %H:%M:%S UTC') $*"; }

# ---- 设置 sudo ----
setup_sudo() {
    if [[ $EUID -eq 0 ]]; then
        SUDO=""
    elif command -v sudo &>/dev/null; then
        SUDO="sudo"
        log_info "非 root 用户，将使用 sudo 提权执行"
    else
        log_error "非 root 用户且未安装 sudo，无法继续"
        exit 1
    fi
}

# ---- 非交互式 apt 包装器 ----
# 背景：Ubuntu 22.04+ 默认带 `needrestart`，在每次 apt install 后弹出 whiptail
#       询问是否重启服务。SSH 非交互会话里该弹窗拿不到 stdin，apt 会退出 1。
# 方案：通过 env 在 apt 前置环境变量（sudo 默认会 reset env，所以用 `env` 透传）：
#   - DEBIAN_FRONTEND=noninteractive   关闭 debconf 交互
#   - NEEDRESTART_MODE=a               needrestart 自动重启受影响服务
#   - NEEDRESTART_SUSPEND=1            needrestart 完全跳过检查（双保险）
#   - DPkg::Lock::Timeout=120          等待其他 apt 进程（如 unattended-upgrades）释放锁
apt_get() {
    $SUDO env \
        DEBIAN_FRONTEND=noninteractive \
        NEEDRESTART_MODE=a \
        NEEDRESTART_SUSPEND=1 \
        apt-get -o DPkg::Lock::Timeout=120 "$@"
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
        apt) apt_get update -y ;;
        yum) $SUDO yum makecache -y ;;
        dnf) $SUDO dnf makecache -y ;;
    esac
    log_ok "包索引更新完成"
}

# ---- 安装依赖工具 ----
# 说明：
#   - jq             合并 /etc/docker/daemon.json，避免覆盖用户既有配置
#   - dnsutils/bind-utils（提供 dig）：部署侧在签 Let's Encrypt 证书前会主动
#     轮询权威 NS + 公共递归验证 DNS 传播，避免 certbot 因 NXDOMAIN 失败
install_prerequisites() {
    log_info "安装基础依赖..."
    case "$PKG_MANAGER" in
        apt) apt_get install -y ca-certificates curl gnupg lsb-release jq dnsutils ;;
        yum) $SUDO yum install -y ca-certificates curl yum-utils jq bind-utils ;;
        dnf) $SUDO dnf install -y ca-certificates curl dnf-plugins-core jq bind-utils ;;
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
        apt) apt_get install -y git ;;
        yum) $SUDO yum install -y git ;;
        dnf) $SUDO dnf install -y git ;;
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
        grant_docker_access
        configure_docker_mirrors
        return 0
    fi

    log_warn "Docker 未安装，开始安装版本 $DOCKER_VERSION..."
    install_docker_"$DISTRO"

    # 启动 docker 服务
    $SUDO systemctl start docker
    $SUDO systemctl enable docker

    if command -v docker &>/dev/null; then
        log_ok "Docker 安装成功，版本: $(docker --version | grep -oP '\d+\.\d+\.\d+' | head -1)"
        grant_docker_access
        configure_docker_mirrors
    else
        log_error "Docker 安装失败"
        return 1
    fi
}

# ---- 配置 Docker 镜像加速（解决 registry-1.docker.io DNS 污染 / 网络不通问题）----
# 背景：大陆服务器直连 Docker Hub 时常因 DNS 污染或出站封锁而失败，
#       表现为 docker build 拉镜像时报 `dial tcp X.X.X.X:443: i/o timeout`。
# 做法：往 /etc/docker/daemon.json 中追加 registry-mirrors（保留既有配置），
#       仅当配置实际发生变化时才重启 docker。
configure_docker_mirrors() {
    log_info "===== 配置 Docker 镜像加速 ====="

    # 期望的镜像源列表（顺序即优先级）
    # - mirror.ccs.tencentyun.com：腾讯云内网镜像（本机是腾讯云时内网直连，最快）
    # - docker.m.daocloud.io     ：DaoCloud 公网镜像
    # - hub-mirror.c.163.com     ：网易 163 镜像
    local mirrors_json='["https://mirror.ccs.tencentyun.com","https://docker.m.daocloud.io","https://hub-mirror.c.163.com"]'
    local daemon_json="/etc/docker/daemon.json"
    local tmp_file="/tmp/daemon.json.new.$$"

    $SUDO mkdir -p /etc/docker

    # 确保 jq 可用（上面 install_prerequisites 已装，这里兜底）
    if ! command -v jq &>/dev/null; then
        log_warn "未检测到 jq，跳过 daemon.json 合并（避免破坏既有配置）"
        return 0
    fi

    # 读取现有配置（不存在或为空则用 {} 起步）
    local current_json="{}"
    if [[ -f "$daemon_json" ]] && [[ -s "$daemon_json" ]]; then
        if $SUDO jq empty "$daemon_json" 2>/dev/null; then
            current_json=$($SUDO cat "$daemon_json")
        else
            log_warn "$daemon_json 非合法 JSON，已备份为 ${daemon_json}.bak 后重写"
            $SUDO cp "$daemon_json" "${daemon_json}.bak"
            current_json="{}"
        fi
    fi

    # 合并 registry-mirrors：用期望列表覆盖（保持幂等，避免重复追加）
    local new_json
    new_json=$(echo "$current_json" | jq --argjson m "$mirrors_json" '. + {"registry-mirrors": $m}')

    # 与现有配置按规范化形式比对，未变化则不重启
    local current_norm new_norm
    current_norm=$(echo "$current_json" | jq -S .)
    new_norm=$(echo "$new_json" | jq -S .)

    if [[ "$current_norm" == "$new_norm" ]]; then
        log_ok "Docker 镜像加速配置已是最新，无需重启"
        return 0
    fi

    # 写入并重启 docker
    echo "$new_json" | jq -S . > "$tmp_file"
    $SUDO mv "$tmp_file" "$daemon_json"
    $SUDO chmod 0644 "$daemon_json"
    log_ok "已写入镜像加速配置到 $daemon_json"

    if systemctl is-active --quiet docker 2>/dev/null; then
        log_info "重启 docker 使镜像加速生效..."
        $SUDO systemctl restart docker
        log_ok "docker 已重启"
    else
        log_info "docker 尚未运行，将在启动时加载新配置"
    fi
}

# ---- 将当前用户加入 docker 组 ----
# 避免后续 docker 命令访问 /var/run/docker.sock 时 permission denied
# 注意：组成员变更在当前登录会话不生效，需重新 SSH 登录后才能无 sudo 使用
grant_docker_access() {
    local current_user="${USER:-$(id -un)}"
    if [[ "$current_user" == "root" ]]; then
        return 0
    fi
    if ! getent group docker &>/dev/null; then
        $SUDO groupadd docker || true
    fi
    if id -nG "$current_user" | grep -qw docker; then
        log_ok "用户 $current_user 已在 docker 组"
    else
        $SUDO usermod -aG docker "$current_user"
        log_ok "已将用户 $current_user 加入 docker 组（下次登录会话生效）"
    fi
}

install_docker_debian() {
    # 移除旧版本
    apt_get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true

    # 只读取一次 /etc/os-release，避免重复 subshell
    local os_id os_codename
    os_id=$(. /etc/os-release && echo "$ID")
    os_codename=$(. /etc/os-release && echo "$VERSION_CODENAME")

    # 添加 Docker GPG key（走阿里云镜像，避开 download.docker.com 的 GFW TLS 重置）
    $SUDO install -m 0755 -d /etc/apt/keyrings
    curl -fsSL \
        --retry 3 --retry-delay 2 \
        --connect-timeout 10 --max-time 60 \
        "${DOCKER_MIRROR_BASE}/${os_id}/gpg" | $SUDO tee /etc/apt/keyrings/docker.asc >/dev/null
    $SUDO chmod a+r /etc/apt/keyrings/docker.asc

    # 添加 Docker apt 源
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
${DOCKER_MIRROR_BASE}/${os_id} ${os_codename} stable" | $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null

    apt_get update -y

    # 查找并安装指定版本
    local docker_pkg_version
    docker_pkg_version=$(apt-cache madison docker-ce | grep "$DOCKER_VERSION" | head -1 | awk '{print $3}')
    if [[ -z "$docker_pkg_version" ]]; then
        log_error "在 apt 源中未找到 Docker 版本 $DOCKER_VERSION，请检查版本号是否正确"
        return 1
    fi

    apt_get install -y \
        "docker-ce=$docker_pkg_version" \
        "docker-ce-cli=$docker_pkg_version" \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
}

install_docker_rhel() {
    # 移除旧版本
    $SUDO $PKG_MANAGER remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null || true

    # 添加 Docker 源（走阿里云镜像，避开 download.docker.com 的 GFW TLS 重置）
    $SUDO $PKG_MANAGER install -y yum-utils 2>/dev/null || true
    $SUDO yum-config-manager --add-repo "${DOCKER_MIRROR_BASE}/centos/docker-ce.repo"

    # 查找并安装指定版本
    local docker_pkg_version
    docker_pkg_version=$($PKG_MANAGER list docker-ce --showduplicates 2>/dev/null | grep "$DOCKER_VERSION" | tail -1 | awk '{print $2}')
    if [[ -z "$docker_pkg_version" ]]; then
        log_error "在 yum/dnf 源中未找到 Docker 版本 $DOCKER_VERSION，请检查版本号是否正确"
        return 1
    fi

    $SUDO $PKG_MANAGER install -y \
        "docker-ce-$docker_pkg_version" \
        "docker-ce-cli-$docker_pkg_version" \
        containerd.io \
        docker-buildx-plugin \
        docker-compose-plugin
}

install_docker_fedora() {
    # 移除旧版本
    $SUDO dnf remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-selinux docker-engine-selinux docker-engine 2>/dev/null || true

    # 添加 Docker 源（走阿里云镜像，避开 download.docker.com 的 GFW TLS 重置）
    $SUDO dnf install -y dnf-plugins-core 2>/dev/null || true
    $SUDO dnf config-manager --add-repo "${DOCKER_MIRROR_BASE}/fedora/docker-ce.repo"

    # 查找并安装指定版本
    local docker_pkg_version
    docker_pkg_version=$(dnf list docker-ce --showduplicates 2>/dev/null | grep "$DOCKER_VERSION" | tail -1 | awk '{print $2}')
    if [[ -z "$docker_pkg_version" ]]; then
        log_error "在 dnf 源中未找到 Docker 版本 $DOCKER_VERSION，请检查版本号是否正确"
        return 1
    fi

    $SUDO dnf install -y \
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
        apt) apt_get install -y nginx ;;
        yum) $SUDO yum install -y epel-release && $SUDO yum install -y nginx ;;
        dnf) $SUDO dnf install -y nginx ;;
    esac

    # 启动并设置开机自启
    $SUDO systemctl start nginx
    $SUDO systemctl enable nginx

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
            apt_get install -y certbot python3-certbot-nginx
            ;;
        yum)
            $SUDO yum install -y epel-release 2>/dev/null || true
            $SUDO yum install -y certbot python3-certbot-nginx
            ;;
        dnf)
            $SUDO dnf install -y certbot python3-certbot-nginx
            ;;
    esac

    # 设置自动续期定时任务（certbot 通常自带 systemd timer，这里做兜底）
    if ! systemctl is-active --quiet certbot-renew.timer 2>/dev/null && ! systemctl is-active --quiet certbot.timer 2>/dev/null; then
        if systemctl list-unit-files | grep -q "certbot.*timer"; then
            local timer_name
            timer_name=$(systemctl list-unit-files | grep "certbot.*timer" | awk '{print $1}' | head -1)
            $SUDO systemctl enable --now "$timer_name"
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
    setup_sudo
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
