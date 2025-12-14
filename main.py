#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
邮件智能日程管理系统 - 主入口文件

功能：
- 读取邮件内容
- 通过AI分析提取事件和时间
- 自动添加到日程表
- 归档到Notion
- 提供Web界面管理
"""

import os
import sys
import click
from pathlib import Path

# 添加项目根目录到Python路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from src.app import create_app
from src.core.config import Config
from src.core.logger import setup_logger
from src.services.email_service import EmailService
from src.services.scheduler_service import SchedulerService


def setup_directories():
    """创建必要的目录结构"""
    directories = [
        "data",
        "logs",
        "static/css",
        "static/js",
        "templates"
    ]
    
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)


@click.group()
def cli():
    """邮件智能日程管理系统命令行工具"""
    pass


@cli.command()
@click.option('--host', default='127.0.0.1', help='服务器主机地址')
@click.option('--port', default=5000, help='服务器端口')
@click.option('--debug', is_flag=True, help='启用调试模式')
@click.option('--ssl', is_flag=True, help='启用HTTPS')
@click.option('--ssl-cert', help='SSL证书文件路径')
@click.option('--ssl-key', help='SSL私钥文件路径')
@click.option('--max-mails', default=None, type=int, help='仅同步前N封新邮件（用于check-email命令）')
def run(host, port, debug, ssl, ssl_cert, ssl_key, max_mails):
    """启动Web服务器"""
    setup_directories()
    
    # 设置日志
    logger = setup_logger()
    logger.info("启动邮件智能日程管理系统")
    
    # 创建Flask应用
    app = create_app()
    
    # 配置SSL
    ssl_context = None
    if ssl:
        if ssl_cert and ssl_key:
            # 使用提供的证书文件
            ssl_context = (ssl_cert, ssl_key)
            logger.info(f"使用SSL证书: {ssl_cert}")
        else:
            # 使用自签名证书（仅用于开发）
            ssl_context = 'adhoc'
            logger.warning("使用自签名证书（仅用于开发环境）")
        
        # 如果启用SSL但端口仍是5000，建议使用443
        if port == 5000:
            port = 443
            logger.info("SSL模式下端口自动设置为443")
    
    # 启动服务器（启用多线程以避免阻塞）
    try:
        app.run(
            host=host, 
            port=port, 
            debug=debug, 
            ssl_context=ssl_context,
            threaded=True,  # 启用多线程
            processes=1     # 单进程多线程
        )
    except Exception as e:
        if ssl and 'adhoc' in str(ssl_context):
            logger.error("自签名证书需要安装pyOpenSSL: pip install pyOpenSSL")
        logger.error(f"服务器启动失败: {e}")
        raise


@cli.command()
@click.option('--user-id', default=1, help='用户ID')
@click.option('--days-back', default=1, help='向前多少天拉取')
@click.option('--max-count', default=None, type=int, help='仅同步前N封（最新优先）')
def check_email(user_id, days_back, max_count):
    """手动检查邮件"""
    setup_directories()
    logger = setup_logger()
    
    try:
        config = Config()
        email_service = EmailService(config)
        
        logger.info("开始检查邮件...")
        emails = email_service.fetch_new_emails(user_id, days_back, max_count)
        
        if emails:
            logger.info(f"发现 {len(emails)} 封新邮件")
            # 这里可以添加处理邮件的逻辑
        else:
            logger.info("没有新邮件")
            
    except Exception as e:
        logger.error(f"检查邮件时出错: {e}")


@cli.command()
def init_db():
    """初始化数据库"""
    setup_directories()
    logger = setup_logger()
    
    try:
        from src.models.database import init_database
        init_database()
        logger.info("数据库初始化完成")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")


@cli.command()
@click.option('--user-id', default=1, help='用户ID')
def test_ai(user_id):
    """测试AI服务连接"""
    setup_directories()
    logger = setup_logger()
    
    try:
        from src.services.ai_service import AIService
        config = Config()
        ai_service = AIService(config)
        
        test_content = "明天下午2点有一个重要的期末考试，请大家准时参加。"
        result = ai_service.analyze_email_content(test_content, user_id=user_id)
        
        logger.info("AI服务测试成功")
        logger.info(f"分析结果: {result}")
        
    except Exception as e:
        logger.error(f"AI服务测试失败: {e}")


if __name__ == '__main__':
    cli()