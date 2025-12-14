# -*- coding: utf-8 -*-
"""
日志管理模块
"""

import sys
from pathlib import Path
from loguru import logger
from .config import Config


def setup_logger(config: Config = None):
    """设置日志配置
    
    Args:
        config: 配置对象，如果为None则使用默认配置
    
    Returns:
        配置好的logger对象
    """
    if config is None:
        config = Config()
    
    # 移除默认的handler
    logger.remove()
    
    # 获取日志配置
    log_config = config.logging_config
    log_level = log_config.get('level', 'INFO')
    log_file = log_config.get('file', 'logs/app.log')
    max_size = log_config.get('max_size', '10MB')
    backup_count = log_config.get('backup_count', 5)
    
    # 确保日志目录存在
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 控制台输出格式
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    
    # 文件输出格式
    file_format = (
        "{time:YYYY-MM-DD HH:mm:ss} | "
        "{level: <8} | "
        "{name}:{function}:{line} - "
        "{message}"
    )
    
    # 添加控制台handler
    logger.add(
        sys.stdout,
        format=console_format,
        level=log_level,
        colorize=True
    )
    
    # 添加文件handler
    logger.add(
        log_file,
        format=file_format,
        level=log_level,
        rotation=max_size,
        retention=backup_count,
        compression="zip",
        encoding="utf-8"
    )
    
    # 添加错误文件handler（只记录ERROR及以上级别）
    error_log_file = log_path.parent / f"{log_path.stem}_error{log_path.suffix}"
    logger.add(
        str(error_log_file),
        format=file_format,
        level="ERROR",
        rotation=max_size,
        retention=backup_count,
        compression="zip",
        encoding="utf-8"
    )
    
    return logger


def get_logger(name: str = None):
    """获取logger实例
    
    Args:
        name: logger名称
    
    Returns:
        logger实例
    """
    if name:
        return logger.bind(name=name)
    return logger