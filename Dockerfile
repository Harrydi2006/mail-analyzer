# 邮件智能日程管理系统 Docker 配置文件
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p data logs static/css static/js templates ssl

# 安装SSL相关依赖
RUN pip install --no-cache-dir pyOpenSSL cryptography

# 设置权限
RUN chmod +x main.py generate_ssl_cert.py

# 创建非root用户
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app

# 切换到非root用户
USER app

# 暴露端口 (HTTP和HTTPS)
EXPOSE 5000 443

# 健康检查 (支持HTTP和HTTPS)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f -k https://localhost:443/ || curl -f http://localhost:5000/ || exit 1

# 启动命令 (默认HTTP，可通过环境变量启用HTTPS)
CMD ["python", "main.py", "run", "--host", "0.0.0.0", "--port", "5000"]