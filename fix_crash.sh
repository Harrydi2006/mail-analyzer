#!/bin/bash
# 快速修复Docker容器崩溃问题

set -e

echo "=========================================="
echo "🔧 Docker容器崩溃问题修复脚本"
echo "=========================================="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查是否在项目目录
if [ ! -f "docker-compose.yml" ]; then
    echo -e "${RED}错误: 请在项目根目录运行此脚本${NC}"
    exit 1
fi

echo -e "${YELLOW}步骤 1/5: 备份数据...${NC}"
BACKUP_DIR="backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
if [ -d "data" ]; then
    cp -r data "$BACKUP_DIR/"
    echo -e "${GREEN}✓ 数据已备份到: $BACKUP_DIR/data${NC}"
fi
if [ -d "logs" ]; then
    cp -r logs "$BACKUP_DIR/"
    echo -e "${GREEN}✓ 日志已备份到: $BACKUP_DIR/logs${NC}"
fi
echo ""

echo -e "${YELLOW}步骤 2/5: 停止所有容器...${NC}"
docker-compose down
echo -e "${GREEN}✓ 容器已停止${NC}"
echo ""

echo -e "${YELLOW}步骤 3/5: 重新构建镜像（不使用缓存）...${NC}"
docker-compose build --no-cache
echo -e "${GREEN}✓ 镜像构建完成${NC}"
echo ""

echo -e "${YELLOW}步骤 4/5: 启动所有服务...${NC}"
docker-compose up -d
echo -e "${GREEN}✓ 服务已启动${NC}"
echo ""

echo -e "${YELLOW}步骤 5/5: 等待服务就绪...${NC}"
sleep 10
echo ""

echo "=========================================="
echo "📊 容器状态检查"
echo "=========================================="
docker-compose ps
echo ""

echo "=========================================="
echo "📝 最近的日志"
echo "=========================================="
echo ""
echo -e "${YELLOW}--- Scheduler日志 ---${NC}"
docker-compose logs --tail=20 scheduler
echo ""
echo -e "${YELLOW}--- 主应用日志 ---${NC}"
docker-compose logs --tail=20 mail-scheduler
echo ""

echo "=========================================="
echo "✅ 修复完成！"
echo "=========================================="
echo ""
echo "📌 接下来的操作："
echo "   1. 查看实时日志: docker-compose logs -f"
echo "   2. 查看容器状态: docker-compose ps"
echo "   3. 测试手动流式处理（在浏览器中点击）"
echo ""
echo "🔍 监控Worker稳定性（持续观察30分钟）："
echo "   docker-compose logs -f scheduler"
echo ""
echo "❓ 如果仍有问题，查看详细诊断："
echo "   cat FIX_DOCKER_CRASH.md"
echo ""

