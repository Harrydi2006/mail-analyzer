#!/bin/bash

# Docker 和 Docker Compose 一键安装脚本
# 适用于 Ubuntu/Debian 系统

set -e

echo "=================================================="
echo "  🐋 Docker 和 Docker Compose 安装脚本"
echo "=================================================="
echo ""

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查是否为 root 用户
if [ "$EUID" -ne 0 ]; then 
    echo -e "${RED}请使用 root 用户运行此脚本${NC}"
    echo "使用命令: sudo bash install_docker.sh"
    exit 1
fi

# 检查系统
echo -e "${YELLOW}[1/5] 检查系统...${NC}"
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    echo "检测到系统: $OS $VERSION"
else
    echo -e "${RED}无法检测系统类型${NC}"
    exit 1
fi

if [[ "$OS" != "ubuntu" && "$OS" != "debian" ]]; then
    echo -e "${YELLOW}警告: 此脚本主要针对 Ubuntu/Debian，其他系统可能需要调整${NC}"
fi
echo ""

# 检查是否已安装 Docker
if command -v docker &> /dev/null; then
    echo -e "${GREEN}✓ Docker 已安装: $(docker --version)${NC}"
    DOCKER_INSTALLED=true
else
    echo "Docker 未安装，准备安装..."
    DOCKER_INSTALLED=false
fi

# 检查是否已安装 Docker Compose
if command -v docker-compose &> /dev/null; then
    echo -e "${GREEN}✓ Docker Compose 已安装: $(docker-compose --version)${NC}"
    COMPOSE_INSTALLED=true
else
    echo "Docker Compose 未安装，准备安装..."
    COMPOSE_INSTALLED=false
fi

if [ "$DOCKER_INSTALLED" = true ] && [ "$COMPOSE_INSTALLED" = true ]; then
    echo -e "${GREEN}Docker 和 Docker Compose 都已安装！${NC}"
    exit 0
fi
echo ""

# 更新软件包索引
echo -e "${YELLOW}[2/5] 更新软件包索引...${NC}"
apt-get update
echo -e "${GREEN}✓ 软件包索引更新完成${NC}"
echo ""

# 安装必要的依赖
echo -e "${YELLOW}[3/5] 安装必要的依赖...${NC}"
apt-get install -y \
    ca-certificates \
    curl \
    gnupg \
    lsb-release
echo -e "${GREEN}✓ 依赖安装完成${NC}"
echo ""

# 安装 Docker
if [ "$DOCKER_INSTALLED" = false ]; then
    echo -e "${YELLOW}[4/5] 安装 Docker...${NC}"
    
    # 添加 Docker 官方 GPG 密钥
    mkdir -p /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/$OS/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    
    # 设置 Docker 仓库
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/$OS \
      $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # 更新软件包索引
    apt-get update
    
    # 安装 Docker Engine
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    
    # 启动 Docker 服务
    systemctl start docker
    systemctl enable docker
    
    echo -e "${GREEN}✓ Docker 安装完成: $(docker --version)${NC}"
else
    echo -e "${YELLOW}[4/5] Docker 已安装，跳过${NC}"
fi
echo ""

# 安装 Docker Compose（独立版本）
if [ "$COMPOSE_INSTALLED" = false ]; then
    echo -e "${YELLOW}[5/5] 安装 Docker Compose...${NC}"
    
    # 方式1: 使用 apt 安装（推荐，简单）
    apt-get install -y docker-compose
    
    # 如果上面失败，尝试方式2: 下载二进制文件
    if ! command -v docker-compose &> /dev/null; then
        echo "apt 安装失败，尝试下载二进制文件..."
        COMPOSE_VERSION=$(curl -s https://api.github.com/repos/docker/compose/releases/latest | grep 'tag_name' | cut -d\" -f4)
        curl -L "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
        chmod +x /usr/local/bin/docker-compose
        ln -sf /usr/local/bin/docker-compose /usr/bin/docker-compose
    fi
    
    echo -e "${GREEN}✓ Docker Compose 安装完成: $(docker-compose --version)${NC}"
else
    echo -e "${YELLOW}[5/5] Docker Compose 已安装，跳过${NC}"
fi
echo ""

# 验证安装
echo "=================================================="
echo -e "${GREEN}✅ 安装完成！${NC}"
echo "=================================================="
echo ""
echo "🐋 Docker 版本:"
docker --version
echo ""
echo "🔧 Docker Compose 版本:"
docker-compose --version
echo ""
echo "📝 测试 Docker:"
echo "  docker run hello-world"
echo ""
echo "🚀 开始部署应用:"
echo "  cd /path/to/your/project"
echo "  docker-compose up -d"
echo ""
echo "=================================================="

