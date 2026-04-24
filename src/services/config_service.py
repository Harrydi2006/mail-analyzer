# -*- coding: utf-8 -*-
"""
用户配置服务
"""

import json
import os
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
    
    def set_user_configs_batch(self, user_id: int, config_type: str, config: Dict[str, Any]) -> bool:
        """在单个事务内批量 UPSERT 同一 config_type 下的多个 key，大幅减少 DB 往返次数。"""
        if not config:
            return True
        try:
            params_list = []
            for key, value in config.items():
                if isinstance(value, (dict, list, bool, int, float)):
                    value_str = json.dumps(value, ensure_ascii=False)
                else:
                    value_str = str(value)
                params_list.append((user_id, config_type, key, value_str))
            query = """
            INSERT OR REPLACE INTO user_configs
            (user_id, config_type, config_key, config_value, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """
            self.db.execute_many(query, params_list)
            logger.info(f"批量配置保存成功: user_id={user_id}, type={config_type}, keys={len(params_list)}")
            return True
        except Exception as e:
            logger.error(f"批量配置保存失败: {e}")
            return False

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
    
    def get_full_config(self, user_id: int) -> Dict[str, Any]:
        """单次 DB 查询读取全部 section 并合并默认值，用于 GET /api/config。"""
        raw = self.get_all_user_configs(
            user_id,
            ['email', 'ai', 'notification', 'notion', 'keywords', 'reminder', 'dedup_beta'],
        )

        def _m(defaults: Dict, stored: Dict) -> Dict:
            result = dict(defaults)
            result.update(stored or {})
            return result

        email = _m({
            'imap_server': '', 'imap_port': 993, 'email': '', 'password': '',
            'use_ssl': True, 'auto_fetch': True, 'fetch_interval': 1800, 'max_emails_per_fetch': 50,
        }, raw.get('email', {}))

        ai = _m({
            'provider': 'openai', 'api_key': '', 'model': 'gpt-3.5-turbo',
            'max_tokens': 2000, 'temperature': 0.7, 'enable_analysis': True,
            'enable_event_extraction': True, 'enable_summary': True,
            'custom_judgement_prompt': '', 'focus_keywords': [],
        }, raw.get('ai', {}))

        notification = _m({
            'enable_email_notifications': False,
            'enable_serverchan_notifications': False,
            'enable_browser_notifications': False,
            'enable_fcm_notifications': False,
            'enable_jpush_notifications': False,
            'mobile_push_priority': 'fcm_first',
            'notification_email': '', 'smtp_host': '', 'smtp_port': 587,
            'smtp_user': '', 'smtp_password': '', 'smtp_from': '',
            'smtp_use_tls': True, 'smtp_use_ssl': False,
            'serverchan_sendkey': '', 'serverchan_title_prefix': '事件提醒',
            'fcm_service_account_path': '',
            'fcm_push_on_weekend': True, 'fcm_push_quiet_hours_enabled': False,
            'fcm_push_start_time': '08:00', 'fcm_push_end_time': '22:00',
            'fcm_push_reminder': True, 'fcm_push_task': True, 'fcm_push_system': True,
            'fcm_push_email_new': True, 'fcm_push_email_analysis': True,
            'fcm_push_event': True, 'fcm_push_digest': True,
            'jpush_app_key': os.environ.get('JPUSH_APP_KEY', ''),
            'jpush_master_secret': os.environ.get('JPUSH_MASTER_SECRET', ''),
            'mobile_fcm_token': '', 'mobile_fcm_platform': '',
            'mobile_jpush_registration_id': '', 'mobile_jpush_platform': '',
            'mobile_push_prefs': {},
        }, raw.get('notification', {}))

        notion = _m({
            'token': '', 'database_id': '',
            'enable_auto_archive': True, 'archive_important_only': False,
        }, raw.get('notion', {}))

        keywords = _m({'important': [], 'normal': [], 'unimportant': []}, raw.get('keywords', {}))

        reminder = _m({
            'important': [
                {'value': 3, 'unit': 'days', 'enabled': True},
                {'value': 1, 'unit': 'days', 'enabled': True},
                {'value': 3, 'unit': 'hours', 'enabled': True},
                {'value': 1, 'unit': 'hours', 'enabled': True},
            ],
            'normal': [
                {'value': 1, 'unit': 'days', 'enabled': True},
                {'value': 3, 'unit': 'hours', 'enabled': True},
            ],
            'unimportant': [],
        }, raw.get('reminder', {}))

        dedup_stored = raw.get('dedup_beta') or {}
        weights = dedup_stored.get('weights') or {}
        if not isinstance(weights, dict):
            weights = {}
        dedup = {
            'enabled': dedup_stored.get('enabled', True) if dedup_stored.get('enabled') is not None else True,
            'time_window_hours': dedup_stored.get('time_window_hours', 72),
            'auto_merge_threshold': dedup_stored.get('auto_merge_threshold', 0.85),
            'weights': {
                'title': float(weights.get('title', 0.35) or 0.0),
                'time': float(weights.get('time', 0.30) or 0.0),
                'tags': float(weights.get('tags', 0.20) or 0.0),
                'sender': float(weights.get('sender', 0.10) or 0.0),
                'location': float(weights.get('location', 0.05) or 0.0),
            },
        }

        return {
            'email': email,
            'ai': ai,
            'notification': notification,
            'notion': notion,
            'keywords': keywords,
            'reminder': reminder,
            'dedup_beta': dedup,
        }

    def get_all_user_configs(self, user_id: int, config_types: List[str]) -> Dict[str, Dict[str, Any]]:
        """一次性读取多个 config_type 的所有配置（单次 DB 查询）。

        Returns:
            {config_type: {key: value, ...}, ...}
        """
        try:
            if not config_types:
                return {}
            placeholders = ','.join(['?'] * len(config_types))
            real_query = f"""
            SELECT config_type, config_key, config_value FROM user_configs
            WHERE user_id = ? AND config_type IN ({placeholders})
            """
            rows = self.db.execute_query(real_query, (user_id, *config_types))
            result: Dict[str, Dict[str, Any]] = {t: {} for t in config_types}
            for row in rows:
                t = row['config_type']
                key = row['config_key']
                value = row['config_value']
                try:
                    result[t][key] = json.loads(value) if value else None
                except json.JSONDecodeError:
                    result[t][key] = value
            return result
        except Exception as e:
            logger.error(f"批量获取用户配置失败: {e}")
            return {t: {} for t in config_types}

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
        """设置用户邮件配置"""
        return self.set_user_configs_batch(user_id, 'email', config)
    
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
            'enable_summary': True,
            'custom_judgement_prompt': '',
            'focus_keywords': []
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'ai')
        default_config.update(user_config)
        
        return default_config
    
    def set_ai_config(self, user_id: int, config: Dict[str, Any]) -> bool:
        """设置用户AI配置"""
        return self.set_user_configs_batch(user_id, 'ai', config)
    
    def get_notification_config(self, user_id: int) -> Dict[str, Any]:
        """获取用户通知配置
        
        Args:
            user_id: 用户ID
            
        Returns:
            通知配置字典
        """
        # 注意：这里的“通知渠道”与“提醒时间规则(reminder)”分离
        default_config = {
            # 渠道开关
            'enable_email_notifications': False,
            'enable_serverchan_notifications': False,
            'enable_browser_notifications': False,
            'enable_fcm_notifications': False,
            'enable_jpush_notifications': False,
            'mobile_push_priority': 'fcm_first',

            # 邮件通知（SMTP）
            'notification_email': '',
            'smtp_host': '',
            'smtp_port': 587,
            'smtp_user': '',
            'smtp_password': '',
            'smtp_from': '',
            'smtp_use_tls': True,
            'smtp_use_ssl': False,

            # Server酱（微信）
            'serverchan_sendkey': '',
            'serverchan_title_prefix': '事件提醒',

            # FCM（服务端主动推送）
            'fcm_service_account_path': '',
            'fcm_push_on_weekend': True,
            'fcm_push_quiet_hours_enabled': False,
            'fcm_push_start_time': '08:00',
            'fcm_push_end_time': '22:00',
            'fcm_push_reminder': True,
            'fcm_push_task': True,
            'fcm_push_system': True,
            'fcm_push_email_new': True,
            'fcm_push_email_analysis': True,
            'fcm_push_event': True,
            'fcm_push_digest': True,

            # JPush（极光）服务端参数
            'jpush_app_key': os.environ.get('JPUSH_APP_KEY', ''),
            'jpush_master_secret': os.environ.get('JPUSH_MASTER_SECRET', ''),

            # 客户端上报（移动端）
            'mobile_fcm_token': '',
            'mobile_fcm_platform': '',
            'mobile_jpush_registration_id': '',
            'mobile_jpush_platform': '',
            'mobile_push_prefs': {},
        }
        
        user_config = self.get_user_configs_by_type(user_id, 'notification')
        default_config.update(user_config)
        
        return default_config

    # 订阅等级配置（决定订阅/CalDAV导出包含哪些重要性）
    def get_subscription_config(self, user_id: int) -> Dict[str, Any]:
        try:
            default_config = {
                'importance_levels': ['important','normal','unimportant','subscribed'],
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
                'importance_levels': ['important','normal','unimportant','subscribed'],
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
        """设置用户通知配置"""
        return self.set_user_configs_batch(user_id, 'notification', config)
    
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
        """设置用户关键词配置"""
        return self.set_user_configs_batch(user_id, 'keywords', config)
    
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
        """设置用户Notion配置"""
        return self.set_user_configs_batch(user_id, 'notion', config)
    
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
        """设置用户提醒配置"""
        return self.set_user_configs_batch(user_id, 'reminder', config)

    def get_dedup_beta_config(self, user_id: int) -> Dict[str, Any]:
        """获取事件去重Beta配置"""
        default_config = {
            'enabled': True,
            'time_window_hours': 72,
            'auto_merge_threshold': 0.85,
            'weights': {
                'title': 0.35,
                'time': 0.30,
                'tags': 0.20,
                'sender': 0.10,
                'location': 0.05,
            }
        }
        user_config = self.get_user_configs_by_type(user_id, 'dedup_beta')
        default_config.update(user_config or {})
        weights = default_config.get('weights') or {}
        merged_weights = {
            'title': float(weights.get('title', 0.35) or 0.0),
            'time': float(weights.get('time', 0.30) or 0.0),
            'tags': float(weights.get('tags', 0.20) or 0.0),
            'sender': float(weights.get('sender', 0.10) or 0.0),
            'location': float(weights.get('location', 0.05) or 0.0),
        }
        default_config['weights'] = merged_weights
        return default_config

    def set_dedup_beta_config(self, user_id: int, config: Dict[str, Any]) -> bool:
        """设置事件去重Beta配置"""
        return self.set_user_configs_batch(user_id, 'dedup_beta', config or {})