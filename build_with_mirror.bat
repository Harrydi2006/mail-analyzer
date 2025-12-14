@echo off
chcp 65001 >nul
REM 使用国内镜像快速构建Docker镜像（Windows版）

echo ==========================================
echo 🚀 使用国内镜像构建Docker镜像
echo ==========================================
echo.

REM 检查Dockerfile
if not exist "Dockerfile" (
    echo ❌ 错误: 未找到 Dockerfile
    pause
    exit /b 1
)

echo 步骤 1/4: 停止现有容器...
docker-compose down 2>nul
echo ✓ 容器已停止
echo.

echo 步骤 2/4: 清理旧镜像（可选）...
set /p CLEANUP="是否清理旧镜像？这会删除未使用的镜像以节省空间 (Y/N): "
if /i "%CLEANUP%"=="Y" (
    docker image prune -f
    echo ✓ 旧镜像已清理
) else (
    echo 跳过清理
)
echo.

echo 步骤 3/4: 构建新镜像（使用国内镜像源）...
echo 提示: 这可能需要5-10分钟，请耐心等待...
echo.

REM 构建镜像
docker-compose build --no-cache

if %ERRORLEVEL% EQU 0 (
    echo ✓ 镜像构建成功
) else (
    echo ✗ 镜像构建失败
    echo.
    echo 故障排查建议：
    echo 1. 检查网络连接是否正常
    echo 2. 尝试使用其他镜像源（编辑 Dockerfile 第33行）：
    echo    - 阿里云: https://mirrors.aliyun.com/pypi/simple/
    echo    - 清华: https://pypi.tuna.tsinghua.edu.cn/simple
    echo    - 腾讯云: https://mirrors.cloud.tencent.com/pypi/simple
    echo 3. 如果在国外服务器，恢复使用官方源：
    echo    - https://pypi.org/simple
    echo.
    pause
    exit /b 1
)
echo.

echo 步骤 4/4: 启动服务...
docker-compose up -d

if %ERRORLEVEL% EQU 0 (
    echo ✓ 服务已启动
) else (
    echo ✗ 服务启动失败
    pause
    exit /b 1
)
echo.

echo ==========================================
echo 📊 部署状态
echo ==========================================
docker-compose ps
echo.

echo ==========================================
echo ✅ 构建完成！
echo ==========================================
echo.
echo 📌 查看日志：
echo    docker-compose logs -f
echo.
echo 📌 查看容器状态：
echo    docker-compose ps
echo.
echo 📌 测试应用：
echo    curl http://localhost:5000/healthz
echo.

pause

