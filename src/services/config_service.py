# -*- coding: utf-8 -*-
"""
用户配置服务
"""

import json
from typing import Dict, Any, Optional, List
from ..models.database import DatabaseManager
from ..core.config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class UserConfigService:
    """用户配置服务"""
    
    def __init__(self):
        self.config = Config()
        self.db = DatabaseManager(self.config)
    
    def get_user_config(self, user_id: int, config_type: str, config_key: str, default_value: Any = None) -> Any:
        """获取用户配置
        
        Args:
            user_id: 用户ID
            config_type: 配置类型（email, ai, notification等）
            config_key: 配置键
            default_value: 默认值
            
        Returns:
            配置值
        """
        try:
            query = """
            SELECT config_value FROM user_configs 
            WHERE user_id = ? AND config_type = ? AND config_key = ?
            """
            
            result = self.db.execute_query(query, (user_id, config_type, config_key))
            
            if result:
                config_value = result[0]['config_value']
                try:
                    # 尝试解析JSON
                    return json.loads(config_value) if config_value else default_value
                except json.JSONDecodeError:
                    # 如果不是JSON，直接返回字符串值
                    return config_value
            
            return default_value
            
        except Exception as e:
            logger.error(f"获取用户配置失败: {e}")
            return default_value
    
    def set_user_config(self, user_id: int, config_type: str, config_key: str, config_value: Any) -> bool:
        """设置用户配置
        
        Args:
            user_id: 用户ID
            config_type: 配置类型
            config_key: 配置键
            config_value: 配置值
            
        Returns:
            是否设置成功
        """
        try:
            # 将配置值转换为存储字符串（优先JSON，确保bool/int/float能被还原）
            if isinstance(config_value, (dict, list, bool, int, float)):
                value_str = json.dumps(config_value, ensure_ascii=False)
            else:
                value_str = str(config_value)
            
            # 使用UPSERT操作
            query = """
            INSERT OR REPLACE INTO user_configs 
            (user_id, config_type, config_key, config_value, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """
            
            result = self.db.execute_update(query, (user_id, config_type, config_key, value_str))
            
            if result:
                logger.info(f"用户配置设置成功: user_id={user_id}, type={config_type}, key={config_key}")
                return True
            else:
                logger.error(f"用户配置设置失败: user_id={user_id}, type={config_type}, key={config_key}")
                return False
                
        except Exception as e:
            logger.error(f"设置用户配置失败: {e}")
            return False
    
    def get_user_configs_by_type(self, user_id: int, config_type: str) -> Dict[str, Any]:
        """获取用户指定类型的所有配置
        
        Args:
            user_id: 用户ID
            config_type: 配置类型
            
        Returns:
            配置字典
        """
        try:
            query = """
            SELECT config_key, config_value FROM user_configs 
            WHERE user_id = ? AND config_type = ?
            """
            
            results = self.db.execute_query(query, (user_id, config_type))
            
            configs = {}
            for row in results:
                key = row['config_key']
                value = row['config_value']
                
                try:
                    # 尝试解析JSON
                    configs[key] = json.loads(value) if value else None
                except json.JSONDecodeError:
                    # 如果不是JSON，直接使用字符串值
                    configs[key] = value
            
            return configs
            
        except Exception as e:
            logger.error(f"获取用户配置失败: {e}")
            return {}
    
    def delete_user_config(self, user_id: int, config_type: str, config_key: str) -> bool:
        """删除用户配置
        
        Args:
            user_id: 用户ID
            config_type: 配置类型
            config_key: 配置键
            
        Returns:
            是否删除成功
        """
        try:
            query = """
            DELETE FROM user_configs 
            WHERE user_id = ? AND config_type = ? AND config_key = ?
            """
            
            result = self.db.execute_update(query, (user_id, config_type, config_key))
            
            if result:
                logger.info(f"用户配置删除成功: user_id={user_id}, type={config_type}, key={config_key}")
                return True
            else:
                logger.warning(f"用户配置不存在或删除失败: user_id={user_id}, type={config_type}, key={config_key}")
                return False
                
        except Exception as e:
            logger.error(f"删除用户配置失败: {e}")
            return False
    
    def get_email_config(self, user_id: int) -> Dict[str, Any]:
        """获取用户邮件配置
        
        Args:
            user_id: 用户ID
            
        Returns:
            邮件配置字典
        """
        default_config = {
            'imap_server': '',
            'imap_port': 993,
            'email': '',
            'password': '',
            'use_ssl': True,
            'auto_fetch': True,
            'fetch_interval': 1800,  # 30分钟（秒）
            'max_emails_per_fetch': 50
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'email')
        default_config.update(user_config)
        
        return default_config
    
    def set_email_config(self, user_id: int, config: Dict[str, Any]) -> bool:
        """设置用户邮件配置
        
        Args:
            user_id: 用户ID
            config: 邮件配置字典
            
        Returns:
            是否设置成功
        """
        try:
            success = True
            for key, value in config.items():
                if not self.set_user_config(user_id, 'email', key, value):
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"设置邮件配置失败: {e}")
            return False
    
    def get_ai_config(self, user_id: int) -> Dict[str, Any]:
        """获取用户AI配置
        
        Args:
            user_id: 用户ID
            
        Returns:
            AI配置字典
        """
        default_config = {
            'provider': 'openai',
            'api_key': '',
            'model': 'gpt-3.5-turbo',
            'max_tokens': 2000,
            'temperature': 0.7,
            'enable_analysis': True,
            'enable_event_extraction': True,
            'enable_summary': True
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'ai')
        default_config.update(user_config)
        
        return default_config
    
    def set_ai_config(self, user_id: int, config: Dict[str, Any]) -> bool:
        """设置用户AI配置
        
        Args:
            user_id: 用户ID
            config: AI配置字典
            
        Returns:
            是否设置成功
        """
        try:
            success = True
            for key, value in config.items():
                if not self.set_user_config(user_id, 'ai', key, value):
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"设置AI配置失败: {e}")
            return False
    
    def get_notification_config(self, user_id: int) -> Dict[str, Any]:
        """获取用户通知配置
        
        Args:
            user_id: 用户ID
            
        Returns:
            通知配置字典
        """
        default_config = {
            'enable_email_notifications': True,
            'enable_event_reminders': True,
            'reminder_advance_time': 15,  # 提前15分钟提醒
            'notification_email': '',
            'enable_daily_summary': False,
            'daily_summary_time': '09:00'
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'notification')
        default_config.update(user_config)
        
        return default_config

    # 订阅等级配置（决定订阅/CalDAV导出包含哪些重要性）
    def get_subscription_config(self, user_id: int) -> Dict[str, Any]:
        try:
            default_config = {
                'importance_levels': ['important','normal','unimportant'],
                'duration_as_markers': False  # 将持续性任务导出为仅开始/结束两个点
            }
            cfg = self.get_user_configs_by_type(user_id, 'subscription')
            if not cfg:
                return default_config
            # importance_levels 期望为字符串数组
            levels = cfg.get('importance_levels') or default_config['importance_levels']
            duration_as_markers = bool(cfg.get('duration_as_markers', default_config['duration_as_markers']))
            return {
                'importance_levels': levels,
                'duration_as_markers': duration_as_markers
            }
        except Exception as e:
            logger.error(f"获取订阅配置失败: {e}")
            return {
                'importance_levels': ['important','normal','unimportant'],
                'duration_as_markers': False
            }

    def set_subscription_config(self, user_id: int, importance_levels: Any = None, duration_as_markers: Any = None) -> bool:
        try:
            ok = True
            if importance_levels is not None:
                ok = ok and self.set_user_config(user_id, 'subscription', 'importance_levels', importance_levels)
            if duration_as_markers is not None:
                ok = ok and self.set_user_config(user_id, 'subscription', 'duration_as_markers', bool(duration_as_markers))
            return ok
        except Exception as e:
            logger.error(f"设置订阅配置失败: {e}")
            return False
    
    def set_notification_config(self, user_id: int, config: Dict[str, Any]) -> bool:
        """设置用户通知配置
        
        Args:
            user_id: 用户ID
            config: 通知配置字典
            
        Returns:
            是否设置成功
        """
        try:
            success = True
            for key, value in config.items():
                if not self.set_user_config(user_id, 'notification', key, value):
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"设置通知配置失败: {e}")
            return False
    
    def get_keywords_config(self, user_id: int) -> Dict[str, List[str]]:
        """获取用户关键词配置
        
        Args:
            user_id: 用户ID
            
        Returns:
            关键词配置字典
        """
        default_config = {
            'important': [],
            'normal': [],
            'unimportant': []
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'keywords')
        default_config.update(user_config)
        
        return default_config
    
    def set_keywords_config(self, user_id: int, config: Dict[str, List[str]]) -> bool:
        """设置用户关键词配置
        
        Args:
            user_id: 用户ID
            config: 关键词配置字典
            
        Returns:
            是否设置成功
        """
        try:
            success = True
            for key, value in config.items():
                if not self.set_user_config(user_id, 'keywords', key, value):
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"设置关键词配置失败: {e}")
            return False
    
    def get_notion_config(self, user_id: int) -> Dict[str, Any]:
        """获取用户Notion配置
        
        Args:
            user_id: 用户ID
            
        Returns:
            Notion配置字典
        """
        default_config = {
            'token': '',
            'database_id': '',
            'enable_auto_archive': True,
            'archive_important_only': False
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'notion')
        default_config.update(user_config)
        
        return default_config
    
    def set_notion_config(self, user_id: int, config: Dict[str, Any]) -> bool:
        """设置用户Notion配置
        
        Args:
            user_id: 用户ID
            config: Notion配置字典
            
        Returns:
            是否设置成功
        """
        try:
            success = True
            for key, value in config.items():
                if not self.set_user_config(user_id, 'notion', key, value):
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"设置Notion配置失败: {e}")
            return False
    
    def get_reminder_config(self, user_id: int) -> Dict[str, Any]:
        """获取用户提醒配置
        
        Args:
            user_id: 用户ID
            
        Returns:
            提醒配置字典
        """
        default_config = {
            'important': [
                {'value': 3, 'unit': 'days', 'enabled': True},
                {'value': 1, 'unit': 'days', 'enabled': True},
                {'value': 3, 'unit': 'hours', 'enabled': True},
                {'value': 1, 'unit': 'hours', 'enabled': True}
            ],
            'normal': [
                {'value': 1, 'unit': 'days', 'enabled': True},
                {'value': 3, 'unit': 'hours', 'enabled': True}
            ],
            'unimportant': []
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'reminder')
        default_config.update(user_config)
        
        return default_config
    
    def set_reminder_config(self, user_id: int, config: Dict[str, Any]) -> bool:
        """设置用户提醒配置
        
        Args:
            user_id: 用户ID
            config: 提醒配置字典
            
        Returns:
            是否设置成功
        """
        try:
            success = True
            for key, value in config.items():
                if not self.set_user_config(user_id, 'reminder', key, value):
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"设置提醒配置失败: {e}")
            return False