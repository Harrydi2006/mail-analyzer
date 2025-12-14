#!/bin/bash

# 重启邮件调度器服务脚本
# 用于解决用户不在线时邮件列表不更新的问题

echo "正在重启邮件调度器服务..."

# 停止现有服务
echo "停止现有服务..."
docker-compose down

# 重新构建并启动服务（包括scheduler服务）
echo "重新构建并启动服务..."
docker-compose up -d --build

# 检查服务状态
echo "检查服务状态..."
docker-compose ps

# 查看scheduler服务日志
echo "查看scheduler服务日志..."
docker-compose logs -f scheduler

echo "服务重启完成！"
echo "现在scheduler服务将每30分钟自动同步一次邮件，即使用户不在线也会更新邮件列表。"
