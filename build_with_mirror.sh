#!/bin/bash
# 使用国内镜像快速构建Docker镜像

set -e

echo "=========================================="
echo "🚀 使用国内镜像构建Docker镜像"
echo "=========================================="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 检查Dockerfile
if [ ! -f "Dockerfile" ]; then
    echo -e "${RED}错误: 未找到 Dockerfile${NC}"
    exit 1
fi

echo -e "${YELLOW}步骤 1/4: 停止现有容器...${NC}"
docker-compose down 2>/dev/null || true
echo -e "${GREEN}✓ 容器已停止${NC}"
echo ""

echo -e "${YELLOW}步骤 2/4: 清理旧镜像（可选）...${NC}"
read -p "是否清理旧镜像？这会删除未使用的镜像以节省空间 (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker image prune -f
    echo -e "${GREEN}✓ 旧镜像已清理${NC}"
else
    echo -e "${YELLOW}跳过清理${NC}"
fi
echo ""

echo -e "${YELLOW}步骤 3/4: 构建新镜像（使用国内镜像源）...${NC}"
echo -e "${YELLOW}提示: 这可能需要5-10分钟，请耐心等待...${NC}"
echo ""

# 使用buildkit和国内镜像
DOCKER_BUILDKIT=1 docker-compose build \
    --no-cache \
    --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    --build-arg PIP_TRUSTED_HOST=mirrors.aliyun.com

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 镜像构建成功${NC}"
else
    echo -e "${RED}✗ 镜像构建失败${NC}"
    echo ""
    echo -e "${YELLOW}故障排查建议：${NC}"
    echo "1. 检查网络连接是否正常"
    echo "2. 尝试使用其他镜像源（编辑 Dockerfile 第33行）："
    echo "   - 阿里云: https://mirrors.aliyun.com/pypi/simple/"
    echo "   - 清华: https://pypi.tuna.tsinghua.edu.cn/simple"
    echo "   - 腾讯云: https://mirrors.cloud.tencent.com/pypi/simple"
    echo "3. 如果在国外服务器，恢复使用官方源："
    echo "   - https://pypi.org/simple"
    echo ""
    exit 1
fi
echo ""

echo -e "${YELLOW}步骤 4/4: 启动服务...${NC}"
docker-compose up -d

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 服务已启动${NC}"
else
    echo -e "${RED}✗ 服务启动失败${NC}"
    exit 1
fi
echo ""

echo "=========================================="
echo "📊 部署状态"
echo "=========================================="
docker-compose ps
echo ""

echo "=========================================="
echo "✅ 构建完成！"
echo "=========================================="
echo ""
echo "📌 查看日志："
echo "   docker-compose logs -f"
echo ""
echo "📌 查看容器状态："
echo "   docker-compose ps"
echo ""
echo "📌 测试应用："
echo "   curl http://localhost:5000/healthz"
echo ""

