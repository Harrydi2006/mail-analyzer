#!/bin/bash

# 邮件智能日程管理系统 - Docker 更新脚本
# 使用方法: bash update.sh

set -e

echo "=================================================="
echo "  🔄 邮件智能日程管理系统 更新脚本"
echo "=================================================="
echo ""

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 备份数据
echo -e "${YELLOW}[1/6] 备份数据...${NC}"
BACKUP_DIR="./backups"
mkdir -p $BACKUP_DIR
DATE=$(date +%Y%m%d_%H%M%S)

if [ -f "data/mail_scheduler.db" ]; then
    cp data/mail_scheduler.db "$BACKUP_DIR/mail_scheduler_$DATE.db"
    echo -e "${GREEN}✓ 数据库已备份到: $BACKUP_DIR/mail_scheduler_$DATE.db${NC}"
else
    echo -e "${YELLOW}⚠️  数据库文件不存在，跳过备份${NC}"
fi

if [ -d "logs" ]; then
    tar -czf "$BACKUP_DIR/logs_$DATE.tar.gz" logs/
    echo -e "${GREEN}✓ 日志已备份到: $BACKUP_DIR/logs_$DATE.tar.gz${NC}"
fi
echo ""

# 拉取最新代码（如果是 git 仓库）
echo -e "${YELLOW}[2/6] 检查代码更新...${NC}"
if [ -d ".git" ]; then
    echo "检测到 Git 仓库，拉取最新代码..."
    git pull
    echo -e "${GREEN}✓ 代码更新完成${NC}"
else
    echo -e "${YELLOW}⚠️  非 Git 仓库，请手动更新代码${NC}"
    read -p "代码已手动更新？按 Enter 继续，或 Ctrl+C 退出..."
fi
echo ""

# 停止旧容器
echo -e "${YELLOW}[3/6] 停止旧容器...${NC}"
docker-compose down
echo -e "${GREEN}✓ 旧容器已停止${NC}"
echo ""

# 清理旧镜像（可选）
echo -e "${YELLOW}[4/6] 清理旧镜像...${NC}"
read -p "是否清理旧镜像（可节省空间）? [y/N]: " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "清理未使用的镜像..."
    docker image prune -f
    echo -e "${GREEN}✓ 旧镜像已清理${NC}"
else
    echo "跳过镜像清理"
fi
echo ""

# 重新构建镜像
echo -e "${YELLOW}[5/6] 重新构建镜像...${NC}"
echo "这可能需要几分钟时间，请耐心等待..."
docker-compose build --no-cache
echo -e "${GREEN}✓ 镜像构建完成${NC}"
echo ""

# 启动新容器
echo -e "${YELLOW}[6/6] 启动新容器...${NC}"
docker-compose up -d
echo -e "${GREEN}✓ 新容器已启动${NC}"
echo ""

# 等待服务就绪
echo -e "${YELLOW}等待服务启动（最多60秒）...${NC}"
for i in {1..60}; do
    if docker exec mail-scheduler-app curl -f -k https://localhost:5000/healthz &>/dev/null; then
        echo -e "${GREEN}✓ 服务已就绪！${NC}"
        break
    fi
    
    if [ $i -eq 60 ]; then
        echo -e "${RED}⚠️  服务启动超时，请检查日志${NC}"
        echo "运行以下命令查看日志："
        echo "  docker-compose logs -f"
        
        echo ""
        echo -e "${YELLOW}是否回滚到之前的版本？${NC}"
        read -p "[y/N]: " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Yy]$ ]]; then
            echo "回滚中..."
            docker-compose down
            if [ -f "$BACKUP_DIR/mail_scheduler_$DATE.db" ]; then
                cp "$BACKUP_DIR/mail_scheduler_$DATE.db" data/mail_scheduler.db
                echo "数据库已恢复"
            fi
            git reset --hard HEAD~1 2>/dev/null || echo "无法回滚代码"
            docker-compose build
            docker-compose up -d
            echo -e "${YELLOW}已尝试回滚，请检查服务状态${NC}"
        fi
        break
    fi
    
    echo -n "."
    sleep 1
done
echo ""

# 显示更新信息
echo "=================================================="
echo -e "${GREEN}✅ 更新完成！${NC}"
echo "=================================================="
echo ""
echo "📊 容器状态:"
docker-compose ps
echo ""
echo "📝 查看日志:"
echo "  docker-compose logs -f"
echo ""
echo "🔍 健康检查:"
echo "  curl -k https://localhost:443/healthz"
echo ""
echo "💾 备份位置:"
echo "  数据库: $BACKUP_DIR/mail_scheduler_$DATE.db"
echo "  日志:   $BACKUP_DIR/logs_$DATE.tar.gz"
echo ""
echo "=================================================="

