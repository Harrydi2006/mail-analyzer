#!/bin/bash

# 快速重启脚本 - 修复健康检查和阻塞问题后使用

set -e

echo "=================================================="
echo "  🔄 重启邮件智能日程管理系统"
echo "=================================================="
echo ""

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${YELLOW}[1/4] 停止旧容器...${NC}"
docker-compose down
echo -e "${GREEN}✓ 旧容器已停止${NC}"
echo ""

echo -e "${YELLOW}[2/4] 重新构建镜像（应用最新修复）...${NC}"
docker-compose build --no-cache
echo -e "${GREEN}✓ 镜像构建完成${NC}"
echo ""

echo -e "${YELLOW}[3/4] 启动新容器...${NC}"
docker-compose up -d
echo -e "${GREEN}✓ 新容器已启动${NC}"
echo ""

echo -e "${YELLOW}[4/4] 等待服务就绪（最多60秒）...${NC}"
for i in {1..60}; do
    if curl -sf http://localhost:443/healthz &>/dev/null || \
       docker exec mail-scheduler-app curl -sf http://localhost:5000/healthz &>/dev/null; then
        echo -e "\n${GREEN}✓ 服务已就绪！${NC}"
        break
    fi
    
    if [ $i -eq 60 ]; then
        echo -e "\n${RED}⚠️  服务启动超时，检查日志...${NC}"
        docker-compose logs --tail=20 mail-scheduler
        break
    fi
    
    echo -n "."
    sleep 1
done
echo ""

echo "=================================================="
echo -e "${GREEN}✅ 重启完成！${NC}"
echo "=================================================="
echo ""
echo "📊 容器状态:"
docker-compose ps
echo ""
echo "🔍 健康检查:"
echo "  内部: docker exec mail-scheduler-app curl -sf http://localhost:5000/healthz"
echo "  外部: curl -k https://localhost:443/healthz"
echo ""
echo "📝 查看日志:"
echo "  docker-compose logs -f mail-scheduler"
echo ""
echo "=================================================="

