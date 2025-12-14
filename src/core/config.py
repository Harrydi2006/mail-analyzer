# -*- coding: utf-8 -*-
"""
配置管理模块
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, List
from dotenv import load_dotenv
import sqlite3
import json
import copy


class Config:
    """配置管理类"""
    
    def __init__(self, config_file: str = None):
        """初始化配置
        
        Args:
            config_file: 配置文件路径，默认为项目根目录的config.yaml
        """
        # 加载环境变量
        load_dotenv()
        
        # 确定配置文件路径
        if config_file is None:
            project_root = Path(__file__).parent.parent.parent
            config_file = project_root / "config.yaml"
        
        self.config_file = Path(config_file)
        self._config = self._load_config()
        
        # 数据库路径
        self.db_path = Path(self._config.get('database', {}).get('path', 'data/mail_scheduler.db'))
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
    
    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        try:
            if self.config_file.exists():
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
                    # 空文件会返回 None
                    if config is None:
                        config = {}
                    
                # 用环境变量覆盖配置
                self._override_with_env(config)
                return config
            else:
                # 如果配置文件不存在，返回默认配置
                return self._get_default_config()
        except Exception as e:
            print(f"加载配置文件失败: {e}")
            return self._get_default_config()
    
    def _override_with_env(self, config: Dict[str, Any]):
        """用环境变量覆盖配置"""
        # 邮件配置
        if 'email' in config:
            config['email']['username'] = os.getenv('EMAIL_USERNAME', config['email'].get('username', ''))
            config['email']['password'] = os.getenv('EMAIL_PASSWORD', config['email'].get('password', ''))
            config['email']['imap_server'] = os.getenv('EMAIL_IMAP_SERVER', config['email'].get('imap_server', ''))
        
        # AI配置
        if 'ai' in config:
            config['ai']['api_key'] = os.getenv('AI_API_KEY', config['ai'].get('api_key', ''))
            config['ai']['base_url'] = os.getenv('AI_BASE_URL', config['ai'].get('base_url', ''))
        
        # Notion配置
        if 'notion' in config:
            config['notion']['token'] = os.getenv('NOTION_TOKEN', config['notion'].get('token', ''))
            config['notion']['database_id'] = os.getenv('NOTION_DATABASE_ID', config['notion'].get('database_id', ''))
    
    def _get_default_config(self) -> Dict[str, Any]:
        """获取默认配置"""
        return {
            'app': {
                'name': '邮件智能日程管理系统',
                'version': '1.0.0',
                'debug': True,
                'host': '0.0.0.0',
                'port': 5000
            },
            'database': {
                'type': 'sqlite',
                'path': 'data/mail_scheduler.db'
            },
            'email': {
                'imap_server': '',
                'imap_port': 993,
                'username': '',
                'password': '',
                'use_ssl': True,
                'check_interval': 300
            },
            'ai': {
                'provider': 'openai',
                'api_key': '',
                'model': 'gpt-3.5-turbo',
                'base_url': '',
                'max_tokens': 1000,
                'temperature': 0.3
            },
            'notion': {
                'token': '',
                'database_id': ''
            },
            'reminder': {
                'important_days_before': [3, 1],
                'important_hours_before': [1],
                'colors': {
                    'important': '#FF4444',
                    'normal': '#4444FF',
                    'lecture': '#44FF44'
                }
            },
            'keywords': {
                'important': ['考试', '作业', '截止', '提交', 'deadline', 'exam', 'assignment'],
                'normal': ['会议', 'meeting', '讨论'],
                'unimportant': ['讲座', '报名', 'lecture', 'registration']
            },
            'logging': {
                'level': 'INFO',
                'file': 'logs/app.log',
                'max_size': '10MB',
                'backup_count': 5
            }
        }
    
    def get(self, key: str, default=None):
        """获取配置值
        
        Args:
            key: 配置键，支持点号分隔的嵌套键，如 'email.username'
            default: 默认值
        
        Returns:
            配置值
        """
        keys = key.split('.')
        value = self._config
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def set(self, key: str, value: Any):
        """设置配置值
        
        Args:
            key: 配置键，支持点号分隔的嵌套键
            value: 配置值
        """
        keys = key.split('.')
        config = self._config
        
        # 导航到最后一级的父级
        for k in keys[:-1]:
            if k not in config:
                config[k] = {}
            config = config[k]
        
        # 设置值
        config[keys[-1]] = value
    
    def get_safe_config(self) -> Dict[str, Any]:
        """获取安全的配置（隐藏敏感信息）"""
        # 必须深拷贝：否则会把敏感字段的掩码写回内存配置，导致后续逻辑/保存被污染
        safe_config = copy.deepcopy(self._config)
        
        # 隐藏敏感信息
        if 'email' in safe_config and 'password' in safe_config['email']:
            safe_config['email']['password'] = '***' if safe_config['email']['password'] else ''
        
        if 'ai' in safe_config and 'api_key' in safe_config['ai']:
            safe_config['ai']['api_key'] = '***' if safe_config['ai']['api_key'] else ''
        
        if 'notion' in safe_config and 'token' in safe_config['notion']:
            safe_config['notion']['token'] = '***' if safe_config['notion']['token'] else ''
        
        return safe_config
    
    def get_full_config(self) -> Dict[str, Any]:
        """获取完整配置（包含真实的敏感信息）"""
        # 深拷贝：避免把真实敏感信息写回 self._config
        full_config = copy.deepcopy(self._config)
        
        # 从数据库获取敏感信息
        sensitive_keys = [
            'email.password',
            'ai.api_key', 
            'notion.token'
        ]
        
        for key_path in sensitive_keys:
            real_value = self._get_sensitive_config(key_path)
            if real_value:
                # 解析路径并设置值
                keys = key_path.split('.')
                current = full_config
                for key in keys[:-1]:
                    if key not in current:
                        current[key] = {}
                    current = current[key]
                current[keys[-1]] = real_value
        
        return full_config
    
    def _get_db_connection(self):
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def _save_sensitive_config(self, key: str, value: str, description: str = None):
        """保存敏感配置到数据库
        
        Args:
            key: 配置键
            value: 配置值
            description: 描述
        """
        try:
            with self._get_db_connection() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO system_config 
                    (config_key, config_value, config_type, description, updated_at)
                    VALUES (?, ?, 'string', ?, datetime('now'))
                """, (key, value, description))
                conn.commit()
        except Exception as e:
            print(f"保存敏感配置失败: {e}")
    
    def _get_sensitive_config(self, key: str, default: str = '') -> str:
        """从数据库获取敏感配置
        
        Args:
            key: 配置键
            default: 默认值
        
        Returns:
            配置值
        """
        try:
            with self._get_db_connection() as conn:
                cursor = conn.execute(
                    "SELECT config_value FROM system_config WHERE config_key = ?", 
                    (key,)
                )
                row = cursor.fetchone()
                return row['config_value'] if row else default
        except Exception as e:
            print(f"获取敏感配置失败: {e}")
            return default
    
    def update_config(self, new_config: Dict[str, Any]):
        """更新配置
        
        Args:
            new_config: 新的配置数据
        """
        def deep_update(base_dict, update_dict, path=''):
            for key, value in update_dict.items():
                current_path = f"{path}.{key}" if path else key
                
                if key in base_dict and isinstance(base_dict[key], dict) and isinstance(value, dict):
                    deep_update(base_dict[key], value, current_path)
                else:
                    # 特殊处理密码字段
                    if self._is_password_field(key, value):
                        if value != '***' and value:  # 只有非空且非占位符的值才保存
                            # 保存到数据库
                            self._save_sensitive_config(current_path, value, f"{key} configuration")
                            # 在内存配置中设置占位符
                            base_dict[key] = '***'
                        # 如果是'***'或空值，保持原值不变
                    else:
                        base_dict[key] = value
        
        deep_update(self._config, new_config)
        self.save_config()
    
    def _is_password_field(self, key: str, value: Any) -> bool:
        """判断是否为密码字段
        
        Args:
            key: 字段名
            value: 字段值
        
        Returns:
            是否为密码字段
        """
        password_fields = ['password', 'api_key', 'token']
        return key in password_fields and isinstance(value, str)
    
    def save_config(self):
        """保存配置到文件"""
        try:
            # 创建配置文件目录
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(self.config_file, 'w', encoding='utf-8') as f:
                yaml.dump(self._config, f, default_flow_style=False, allow_unicode=True)
        except Exception as e:
            print(f"保存配置文件失败: {e}")
    
    def get_keywords(self) -> Dict[str, List[str]]:
        """获取关键词配置"""
        return self.get('keywords', {
            'important': [],
            'normal': [],
            'unimportant': []
        })
    
    def update_keywords(self, keywords_data: Dict[str, List[str]]):
        """更新关键词配置"""
        self.set('keywords', keywords_data)
        self.save_config()
    
    def is_configured(self) -> bool:
        """检查是否已配置基本信息"""
        email_configured = bool(self.get('email.username') and self.get('email.password'))
        ai_configured = bool(self.get('ai.api_key'))
        
        return email_configured and ai_configured
    
    @property
    def app_config(self) -> Dict[str, Any]:
        """应用配置"""
        return self.get('app', {})
    
    @property
    def email_config(self) -> Dict[str, Any]:
        """邮件配置"""
        return self.get('email', {})
    
    @property
    def ai_config(self) -> Dict[str, Any]:
        """AI配置"""
        return self.get('ai', {})
    
    @property
    def notion_config(self) -> Dict[str, Any]:
        """Notion配置"""
        return self.get('notion', {})
    
    @property
    def database_config(self) -> Dict[str, Any]:
        """数据库配置"""
        return self.get('database', {})
    
    @property
    def reminder_config(self) -> Dict[str, Any]:
        """提醒配置"""
        return self.get('reminder', {})
    
    @property
    def logging_config(self) -> Dict[str, Any]:
        """日志配置"""
        return self.get('logging', {})