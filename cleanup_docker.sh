#!/bin/bash
# Docker磁盘清理脚本

echo "=========================================="
echo "🧹 Docker磁盘空间清理"
echo "=========================================="
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}检查当前磁盘使用情况...${NC}"
df -h /
echo ""

echo -e "${BLUE}检查Docker磁盘使用情况...${NC}"
docker system df
echo ""

echo -e "${YELLOW}开始清理Docker缓存和未使用的资源...${NC}"
echo ""

echo -e "${BLUE}1/5 停止所有容器...${NC}"
docker-compose down 2>/dev/null || true
docker stop $(docker ps -aq) 2>/dev/null || true
echo -e "${GREEN}✓ 容器已停止${NC}"
echo ""

echo -e "${BLUE}2/5 删除未使用的容器...${NC}"
docker container prune -f
echo -e "${GREEN}✓ 未使用的容器已删除${NC}"
echo ""

echo -e "${BLUE}3/5 删除未使用的镜像...${NC}"
docker image prune -af
echo -e "${GREEN}✓ 未使用的镜像已删除${NC}"
echo ""

echo -e "${BLUE}4/5 删除未使用的卷...${NC}"
docker volume prune -f
echo -e "${GREEN}✓ 未使用的卷已删除${NC}"
echo ""

echo -e "${BLUE}5/5 清理构建缓存...${NC}"
docker builder prune -af
echo -e "${GREEN}✓ 构建缓存已清理${NC}"
echo ""

echo "=========================================="
echo -e "${GREEN}清理完成！${NC}"
echo "=========================================="
echo ""

echo -e "${BLUE}清理后的磁盘使用情况：${NC}"
df -h /
echo ""

echo -e "${BLUE}清理后的Docker磁盘使用：${NC}"
docker system df
echo ""

echo -e "${YELLOW}提示：${NC}"
echo "如果磁盘空间仍然不足，请考虑："
echo "1. 删除其他不需要的文件"
echo "2. 清理系统日志：journalctl --vacuum-time=7d"
echo "3. 清理apt缓存：apt-get clean"
echo "4. 升级磁盘空间"
echo ""


