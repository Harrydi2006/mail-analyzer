@echo off
chcp 65001 >nul
REM 快速修复Docker容器崩溃问题（Windows版）

echo ==========================================
echo 🔧 Docker容器崩溃问题修复脚本
echo ==========================================
echo.

REM 检查是否在项目目录
if not exist "docker-compose.yml" (
    echo ❌ 错误: 请在项目根目录运行此脚本
    pause
    exit /b 1
)

echo 步骤 1/5: 备份数据...
set BACKUP_DIR=backup_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%
set BACKUP_DIR=%BACKUP_DIR: =0%
mkdir "%BACKUP_DIR%" 2>nul
if exist "data" (
    xcopy /E /I /Q "data" "%BACKUP_DIR%\data\" >nul
    echo ✓ 数据已备份到: %BACKUP_DIR%\data
)
if exist "logs" (
    xcopy /E /I /Q "logs" "%BACKUP_DIR%\logs\" >nul
    echo ✓ 日志已备份到: %BACKUP_DIR%\logs
)
echo.

echo 步骤 2/5: 停止所有容器...
docker-compose down
echo ✓ 容器已停止
echo.

echo 步骤 3/5: 重新构建镜像（不使用缓存）...
docker-compose build --no-cache
echo ✓ 镜像构建完成
echo.

echo 步骤 4/5: 启动所有服务...
docker-compose up -d
echo ✓ 服务已启动
echo.

echo 步骤 5/5: 等待服务就绪...
timeout /t 10 /nobreak >nul
echo.

echo ==========================================
echo 📊 容器状态检查
echo ==========================================
docker-compose ps
echo.

echo ==========================================
echo 📝 最近的日志
echo ==========================================
echo.
echo --- Scheduler日志 ---
docker-compose logs --tail=20 scheduler
echo.
echo --- 主应用日志 ---
docker-compose logs --tail=20 mail-scheduler
echo.

echo ==========================================
echo ✅ 修复完成！
echo ==========================================
echo.
echo 📌 接下来的操作：
echo    1. 查看实时日志: docker-compose logs -f
echo    2. 查看容器状态: docker-compose ps
echo    3. 测试手动流式处理（在浏览器中点击）
echo.
echo 🔍 监控Worker稳定性（持续观察30分钟）：
echo    docker-compose logs -f scheduler
echo.
echo ❓ 如果仍有问题，查看详细诊断：
echo    type FIX_DOCKER_CRASH.md
echo.

pause

