# HTTPS 配置指南

本文档介绍如何为邮件智能日程管理系统配置HTTPS支持。

## 🚀 快速开始

### 1. 开发环境（自签名证书）

```bash
# 生成自签名证书
python generate_ssl_cert.py

# 启动HTTPS服务器
python main.py run --ssl --ssl-cert ssl_cert.pem --ssl-key ssl_key.pem
```

### 2. 生产环境（正式证书）

```bash
# 使用正式SSL证书启动
python main.py run --ssl --ssl-cert /path/to/your/cert.pem --ssl-key /path/to/your/key.pem --host 0.0.0.0 --port 443
```

## 📋 详细配置

### 证书生成选项

```bash
# 基本用法
python generate_ssl_cert.py

# 自定义域名和有效期
python generate_ssl_cert.py --domain yourdomain.com --days 730

# 自定义文件名
python generate_ssl_cert.py --cert my_cert.pem --key my_key.pem
```

### 命令行选项

| 选项 | 描述 | 默认值 |
|------|------|--------|
| `--ssl` | 启用HTTPS | false |
| `--ssl-cert` | SSL证书文件路径 | 无 |
| `--ssl-key` | SSL私钥文件路径 | 无 |
| `--host` | 服务器主机地址 | 127.0.0.1 |
| `--port` | 服务器端口 | 5000 (HTTP) / 443 (HTTPS) |

## 🐳 Docker 部署

### 1. 准备SSL证书

```bash
# 创建ssl目录
mkdir ssl

# 生成自签名证书（开发环境）
python generate_ssl_cert.py --cert ssl/ssl_cert.pem --key ssl/ssl_key.pem

# 或复制正式证书到ssl目录
cp your_cert.pem ssl/ssl_cert.pem
cp your_key.pem ssl/ssl_key.pem
```

### 2. 配置环境变量

```bash
# 复制SSL配置模板
cp .env.ssl .env

# 编辑配置文件
vim .env
```

### 3. 启动服务

```bash
# 启用HTTPS的Docker Compose
SSL_ENABLED=true docker-compose up -d
```

## 🔧 高级配置

### 环境变量配置

创建 `.env` 文件：

```env
SSL_ENABLED=true
SSL_CERT_PATH=./ssl/ssl_cert.pem
SSL_KEY_PATH=./ssl/ssl_key.pem
HOST=0.0.0.0
PORT=443
FORCE_HTTPS=true
```

### Nginx 反向代理

如果使用Nginx作为反向代理，创建 `nginx.conf`：

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name yourdomain.com;
    
    ssl_certificate /etc/nginx/ssl/ssl_cert.pem;
    ssl_certificate_key /etc/nginx/ssl/ssl_key.pem;
    
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers ECDHE-RSA-AES128-GCM-SHA256:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    
    location / {
        proxy_pass http://mail-scheduler:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

## 🛡️ 安全最佳实践

### 1. 证书管理

- **生产环境**: 使用Let's Encrypt或商业CA颁发的证书
- **开发环境**: 使用自签名证书
- **定期更新**: 设置证书自动续期

### 2. 安全头配置

在Flask应用中添加安全头：

```python
@app.after_request
def add_security_headers(response):
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response
```

### 3. 强制HTTPS重定向

```python
@app.before_request
def force_https():
    if not request.is_secure and app.env != 'development':
        return redirect(request.url.replace('http://', 'https://'))
```

## 🔍 故障排除

### 常见问题

1. **证书错误**
   ```
   错误: [SSL: CERTIFICATE_VERIFY_FAILED]
   解决: 检查证书文件路径和权限
   ```

2. **端口占用**
   ```
   错误: [Errno 98] Address already in use
   解决: 更改端口或停止占用端口的进程
   ```

3. **权限问题**
   ```
   错误: Permission denied
   解决: 使用sudo运行或更改端口到1024以上
   ```

### 调试命令

```bash
# 检查证书信息
openssl x509 -in ssl_cert.pem -text -noout

# 测试SSL连接
openssl s_client -connect localhost:443

# 检查端口占用
netstat -tlnp | grep :443
```

## 📚 相关资源

- [Let's Encrypt](https://letsencrypt.org/) - 免费SSL证书
- [SSL Labs Test](https://www.ssllabs.com/ssltest/) - SSL配置测试
- [Mozilla SSL Configuration Generator](https://ssl-config.mozilla.org/) - SSL配置生成器

## ⚠️ 注意事项

1. **自签名证书**: 浏览器会显示安全警告，仅用于开发环境
2. **端口权限**: 在Linux/macOS上绑定443端口需要root权限
3. **防火墙**: 确保防火墙允许443端口的入站连接
4. **证书路径**: 确保应用有读取证书文件的权限