#!/bin/bash

# 邮件智能日程管理系统 - Docker 快速部署脚本
# 使用方法: bash deploy.sh

set -e

echo "=================================================="
echo "  📦 邮件智能日程管理系统 Docker 部署脚本"
echo "=================================================="
echo ""

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检查 Docker
echo -e "${YELLOW}[1/8] 检查 Docker 环境...${NC}"
if ! command -v docker &> /dev/null; then
    echo -e "${RED}错误: Docker 未安装，请先安装 Docker${NC}"
    exit 1
fi

if ! command -v docker-compose &> /dev/null; then
    echo -e "${RED}错误: Docker Compose 未安装，请先安装 Docker Compose${NC}"
    exit 1
fi

echo -e "${GREEN}✓ Docker 环境检查通过${NC}"
echo ""

# 检查必需文件
echo -e "${YELLOW}[2/8] 检查必需文件...${NC}"
REQUIRED_FILES=("Dockerfile" "docker-compose.yml" "requirements.txt" "config.yaml" "main.py")
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo -e "${RED}错误: 缺少必需文件 $file${NC}"
        exit 1
    fi
done
echo -e "${GREEN}✓ 必需文件检查通过${NC}"
echo ""

# 创建必要的目录
echo -e "${YELLOW}[3/8] 创建必要的目录...${NC}"
mkdir -p data logs ssl
echo -e "${GREEN}✓ 目录创建完成${NC}"
echo ""

# 检查 prod.env
echo -e "${YELLOW}[4/8] 检查环境配置...${NC}"
if [ ! -f "prod.env" ]; then
    echo -e "${YELLOW}⚠️  prod.env 不存在，创建默认配置...${NC}"
    cat > prod.env << 'EOF'
# SSL配置
SSL_ENABLED=true
SSL_CERT_PATH=/app/ssl/cert.pem
SSL_KEY_PATH=/app/ssl/key.pem

# 应用配置
FLASK_SECRET_KEY=CHANGE_THIS_TO_A_RANDOM_SECRET_KEY
FLASK_ENV=production

# 数据库路径
DATABASE_PATH=/app/data/mail_scheduler.db

# 日志配置
LOG_LEVEL=INFO
LOG_PATH=/app/logs
EOF
    echo -e "${RED}⚠️  请编辑 prod.env 文件，修改 FLASK_SECRET_KEY 和其他配置！${NC}"
    echo -e "${YELLOW}生成随机密钥: python -c 'import secrets; print(secrets.token_urlsafe(32))'${NC}"
    read -p "按 Enter 继续，或 Ctrl+C 退出去修改配置..."
fi
echo -e "${GREEN}✓ 环境配置检查完成${NC}"
echo ""

# 检查 SSL 证书
echo -e "${YELLOW}[5/8] 检查 SSL 证书...${NC}"
if [ ! -f "ssl/cert.pem" ] || [ ! -f "ssl/key.pem" ]; then
    echo -e "${YELLOW}⚠️  SSL 证书不存在${NC}"
    read -p "是否生成自签名证书（测试用）? [y/N]: " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        if [ -f "generate_ssl_cert.py" ]; then
            echo "生成自签名证书..."
            python generate_ssl_cert.py
            echo -e "${GREEN}✓ 自签名证书生成完成${NC}"
        else
            echo -e "${RED}错误: 找不到 generate_ssl_cert.py${NC}"
            echo "请手动创建 SSL 证书并放置在 ssl/ 目录下"
            exit 1
        fi
    else
        echo -e "${RED}请手动创建 SSL 证书并放置在 ssl/ 目录下${NC}"
        echo "文件位置: ssl/cert.pem 和 ssl/key.pem"
        exit 1
    fi
else
    echo -e "${GREEN}✓ SSL 证书已存在${NC}"
fi
echo ""

# 停止旧容器
echo -e "${YELLOW}[6/8] 停止旧容器（如果存在）...${NC}"
if docker-compose ps -q 2>/dev/null | grep -q .; then
    echo "发现运行中的容器，正在停止..."
    docker-compose down
    echo -e "${GREEN}✓ 旧容器已停止${NC}"
else
    echo "没有运行中的容器"
fi
echo ""

# 构建镜像
echo -e "${YELLOW}[7/8] 构建 Docker 镜像...${NC}"
echo "这可能需要几分钟时间，请耐心等待..."
docker-compose build --no-cache
echo -e "${GREEN}✓ 镜像构建完成${NC}"
echo ""

# 启动服务
echo -e "${YELLOW}[8/8] 启动服务...${NC}"
docker-compose up -d
echo -e "${GREEN}✓ 服务启动完成${NC}"
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
        break
    fi
    
    echo -n "."
    sleep 1
done
echo ""

# 显示部署信息
echo "=================================================="
echo -e "${GREEN}✅ 部署完成！${NC}"
echo "=================================================="
echo ""
echo "📊 容器状态:"
docker-compose ps
echo ""
echo "🌐 访问地址:"
echo "  https://localhost:443"
echo "  或"
echo "  https://$(hostname -I | awk '{print $1}'):443"
echo ""
echo "📝 常用命令:"
echo "  查看日志:    docker-compose logs -f"
echo "  重启服务:    docker-compose restart"
echo "  停止服务:    docker-compose down"
echo "  进入容器:    docker exec -it mail-scheduler-app /bin/bash"
echo ""
echo "🔍 健康检查:"
echo "  curl -k https://localhost:443/healthz"
echo ""
echo "📚 完整文档:"
echo "  查看 DOCKER_DEPLOYMENT.md"
echo ""
echo "=================================================="

