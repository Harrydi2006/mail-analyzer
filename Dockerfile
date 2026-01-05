# 邮件智能日程管理系统 Docker 配置文件
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# 切换更快的 Debian 源并安装系统依赖（强制 IPv4，避免国外源超时）
RUN set -eux; \
    echo 'Acquire::ForceIPv4 "true";' > /etc/apt/apt.conf.d/99force-ipv4; \
    . /etc/os-release; \
    rm -f /etc/apt/sources.list.d/debian.sources || true; \
    echo "deb http://mirrors.aliyun.com/debian $VERSION_CODENAME main contrib non-free non-free-firmware"      >  /etc/apt/sources.list; \
    echo "deb http://mirrors.aliyun.com/debian $VERSION_CODENAME-updates main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb http://mirrors.aliyun.com/debian $VERSION_CODENAME-backports main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    echo "deb http://mirrors.aliyun.com/debian-security $VERSION_CODENAME-security main contrib non-free non-free-firmware" >> /etc/apt/sources.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      gcc \
      g++ \
      curl \
      git; \
    rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 配置pip使用国内镜像源并安装Python依赖
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ && \
    pip config set global.trusted-host mirrors.aliyun.com && \
    pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt --timeout 300

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p data logs static/css static/js templates ssl

# 安装SSL相关依赖（使用国内镜像源）
RUN pip install --no-cache-dir pyOpenSSL cryptography --timeout 300

# 设置权限
RUN chmod +x main.py generate_ssl_cert.py

# 创建非root用户
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app

# 切换到非root用户
USER app

# 暴露端口 (HTTP和HTTPS)
EXPOSE 5000 443

# 健康检查：检查应用是否响应（容器内统一使用 5000 端口）
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:5000/healthz || exit 1

# 启动命令（容器内统一监听 5000，外部通过端口映射暴露 443）
CMD ["/bin/sh", "-c", "\
if [ \"$SSL_ENABLED\" = \"true\" ]; then \
  python main.py run --host 0.0.0.0 --port 5000 --ssl --ssl-cert \"$SSL_CERT_PATH\" --ssl-key \"$SSL_KEY_PATH\"; \
else \
  python main.py run --host 0.0.0.0 --port 5000; \
fi"]