# -*- coding: utf-8 -*-
"""
数据库模型和初始化
"""

import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import json

from ..core.config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """数据库管理器"""
    
    _instance = None
    _connection = None
    
    def __new__(cls, config: Config = None):
        """单例模式，复用数据库连接"""
        if cls._instance is None:
            cls._instance = super(DatabaseManager, cls).__new__(cls)
        return cls._instance
    
    def __init__(self, config: Config = None):
        """初始化数据库管理器
        
        Args:
            config: 配置对象
        """
        if hasattr(self, '_initialized'):
            return
            
        if config is None:
            config = Config()
        
        self.config = config
        db_config = config.database_config
        self.db_path = Path(db_config.get('path', 'data/mail_scheduler.db'))
        
        # 确保数据库目录存在
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialized = True
    
    def get_connection(self):
        """获取数据库连接（复用连接）"""
        if self._connection is None:
            self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._connection.row_factory = sqlite3.Row  # 使结果可以通过列名访问
            # 启用WAL模式提高并发性能
            self._connection.execute('PRAGMA journal_mode=WAL')
            self._connection.execute('PRAGMA synchronous=NORMAL')
            self._connection.execute('PRAGMA cache_size=10000')
            self._connection.execute('PRAGMA temp_store=MEMORY')
        return self._connection
    
    def execute_query(self, query: str, params: tuple = None) -> List[Dict]:
        """执行查询
        
        Args:
            query: SQL查询语句
            params: 查询参数
        
        Returns:
            查询结果列表
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            
            # 将结果转换为字典列表
            columns = [description[0] for description in cursor.description] if cursor.description else []
            results = []
            for row in cursor.fetchall():
                results.append(dict(zip(columns, row)))
            
            return results
    
    def execute_update(self, query: str, params: tuple = None) -> int:
        """执行更新操作
        
        Args:
            query: SQL语句
            params: 参数
        
        Returns:
            影响的行数
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            conn.commit()
            return cursor.rowcount
    
    def execute_insert(self, query: str, params: tuple = None) -> int:
        """执行插入操作
        
        Args:
            query: SQL语句
            params: 参数
        
        Returns:
            插入记录的ID
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if params:
                cursor.execute(query, params)
            else:
                cursor.execute(query)
            conn.commit()
            return cursor.lastrowid


def init_database(config: Config = None):
    """初始化数据库表结构
    
    Args:
        config: 配置对象
    """
    if config is None:
        config = Config()
    
    db_manager = DatabaseManager(config)
    
    # 创建邮件表
    create_emails_table = """
    CREATE TABLE IF NOT EXISTS emails (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,  -- 用户ID，用于数据隔离
        message_id TEXT UNIQUE NOT NULL,
        subject TEXT NOT NULL,
        sender TEXT NOT NULL,
        recipient TEXT,
        content TEXT NOT NULL,
        html_content TEXT,
        received_date DATETIME NOT NULL,
        processed_date DATETIME,
        is_processed BOOLEAN DEFAULT FALSE,
        importance_level TEXT DEFAULT 'normal',  -- important, normal, unimportant
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id)
    )
    """
    
    # 创建AI分析结果表
    create_analysis_table = """
    CREATE TABLE IF NOT EXISTS email_analysis (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,  -- 用户ID，用于数据隔离
        email_id INTEGER NOT NULL,
        summary TEXT,
        importance_score INTEGER,  -- 1-10分
        importance_reason TEXT,
        events_json TEXT,  -- JSON格式存储事件列表
        keywords_matched TEXT,  -- 匹配的关键词
        ai_model TEXT,
        analysis_date DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (email_id) REFERENCES emails (id)
    )
    """
    
    # 创建事件表
    create_events_table = """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,  -- 用户ID，用于数据隔离
        email_id INTEGER,
        title TEXT NOT NULL,
        description TEXT,
        start_time DATETIME NOT NULL,
        end_time DATETIME,
        location TEXT,
        importance_level TEXT DEFAULT 'normal',  -- important, normal, unimportant
        is_all_day BOOLEAN DEFAULT FALSE,
        reminder_time DATETIME,
        notion_page_id TEXT,  -- Notion页面ID
        notion_url TEXT,      -- Notion页面URL
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (email_id) REFERENCES emails (id)
    )
    """
    
    # 创建提醒表
    create_reminders_table = """
    CREATE TABLE IF NOT EXISTS reminders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,  -- 用户ID，用于数据隔离
        event_id INTEGER NOT NULL,
        reminder_time DATETIME NOT NULL,
        reminder_type TEXT NOT NULL,  -- days_before, hours_before, exact_time
        is_sent BOOLEAN DEFAULT FALSE,
        sent_at DATETIME,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (event_id) REFERENCES events (id)
    )
    """
    
    # 创建Notion归档表
    create_notion_archive_table = """
    CREATE TABLE IF NOT EXISTS notion_archive (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,  -- 用户ID，用于数据隔离
        email_id INTEGER NOT NULL,
        notion_page_id TEXT NOT NULL,
        notion_url TEXT,
        archived_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (email_id) REFERENCES emails (id)
    )
    """
    
    # 创建关键词匹配日志表
    create_keyword_log_table = """
    CREATE TABLE IF NOT EXISTS keyword_matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL DEFAULT 1,  -- 用户ID，用于数据隔离
        email_id INTEGER NOT NULL,
        keyword TEXT NOT NULL,
        keyword_type TEXT NOT NULL,  -- important, normal, unimportant
        match_context TEXT,  -- 匹配的上下文
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (email_id) REFERENCES emails (id)
    )
    """
    
    # 创建系统配置表
    create_config_table = """
    CREATE TABLE IF NOT EXISTS system_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        config_key TEXT UNIQUE NOT NULL,
        config_value TEXT NOT NULL,
        config_type TEXT DEFAULT 'string',  -- string, json, boolean, integer
        description TEXT,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """
    
    # 创建用户表
    create_users_table = """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        subscribe_key TEXT UNIQUE NOT NULL,  -- 用户专属的日历订阅key
        is_active BOOLEAN DEFAULT TRUE,
        is_admin BOOLEAN DEFAULT FALSE,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        last_login DATETIME,
        invitation_code TEXT  -- 注册时使用的邀请码
    )
    """
    
    # 创建邀请码表
    create_invitation_codes_table = """
    CREATE TABLE IF NOT EXISTS invitation_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        created_by INTEGER,  -- 创建者用户ID（管理员）
        used_by INTEGER,     -- 使用者用户ID
        is_used BOOLEAN DEFAULT FALSE,
        max_uses INTEGER DEFAULT 1,  -- 最大使用次数
        current_uses INTEGER DEFAULT 0,  -- 当前使用次数
        expires_at DATETIME,  -- 过期时间
        user_role TEXT DEFAULT 'user',  -- 'user' 或 'admin'
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        used_at DATETIME,
        FOREIGN KEY (created_by) REFERENCES users (id),
        FOREIGN KEY (used_by) REFERENCES users (id)
    )
    """
    
    # 创建AI请求统计表
    create_ai_requests_table = """
    CREATE TABLE IF NOT EXISTS ai_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        request_type TEXT NOT NULL,  -- analyze_email, test_connection等
        email_id INTEGER,  -- 关联的邮件ID（如果有）
        tokens_used INTEGER DEFAULT 0,  -- 使用的token数量
        cost DECIMAL(10,6) DEFAULT 0,  -- 请求成本
        success BOOLEAN DEFAULT TRUE,
        error_message TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        FOREIGN KEY (email_id) REFERENCES emails (id)
    )
    """
    
    # 创建用户配置表
    create_user_configs_table = """
    CREATE TABLE IF NOT EXISTS user_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        config_type TEXT NOT NULL,  -- 配置类型：email, ai, notification等
        config_key TEXT NOT NULL,   -- 配置键
        config_value TEXT,          -- 配置值（JSON格式）
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id),
        UNIQUE(user_id, config_type, config_key)
    )
    """
    
    # 执行创建表的SQL语句
    tables = [
        create_emails_table,
        create_analysis_table,
        create_events_table,
        create_reminders_table,
        create_notion_archive_table,
        create_keyword_log_table,
        create_config_table,
        create_users_table,
        create_invitation_codes_table,
        create_ai_requests_table,
        create_user_configs_table
    ]
    
    try:
        for table_sql in tables:
            db_manager.execute_update(table_sql)
        
        # 创建索引
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails (message_id)",
            "CREATE INDEX IF NOT EXISTS idx_emails_received_date ON emails (received_date)",
            "CREATE INDEX IF NOT EXISTS idx_emails_is_processed ON emails (is_processed)",
            "CREATE INDEX IF NOT EXISTS idx_emails_user_id ON emails (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_events_start_time ON events (start_time)",
            "CREATE INDEX IF NOT EXISTS idx_events_importance ON events (importance_level)",
            "CREATE INDEX IF NOT EXISTS idx_events_user_id ON events (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders (reminder_time)",
            "CREATE INDEX IF NOT EXISTS idx_reminders_sent ON reminders (is_sent)",
            "CREATE INDEX IF NOT EXISTS idx_reminders_user_id ON reminders (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_email_analysis_user_id ON email_analysis (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_notion_archive_user_id ON notion_archive (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_keyword_matches_user_id ON keyword_matches (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)",
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)",
            "CREATE INDEX IF NOT EXISTS idx_users_subscribe_key ON users (subscribe_key)",
            "CREATE INDEX IF NOT EXISTS idx_invitation_codes_code ON invitation_codes (code)",
            "CREATE INDEX IF NOT EXISTS idx_invitation_codes_used ON invitation_codes (is_used)",
            "CREATE INDEX IF NOT EXISTS idx_ai_requests_user_id ON ai_requests (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_ai_requests_created_at ON ai_requests (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_user_configs_user_id ON user_configs (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_user_configs_type_key ON user_configs (config_type, config_key)",
        ]
        
        # 执行数据库迁移（在创建索引之前）
        from .migration import migrate_database
        migrate_database()
        
        # 创建基础索引（不包含user_id相关的索引，这些在迁移中处理）
        basic_indexes = [
            "CREATE INDEX IF NOT EXISTS idx_emails_message_id ON emails (message_id)",
            "CREATE INDEX IF NOT EXISTS idx_emails_received_date ON emails (received_date)",
            "CREATE INDEX IF NOT EXISTS idx_emails_is_processed ON emails (is_processed)",
            "CREATE INDEX IF NOT EXISTS idx_events_start_time ON events (start_time)",
            "CREATE INDEX IF NOT EXISTS idx_events_importance ON events (importance_level)",
            "CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders (reminder_time)",
            "CREATE INDEX IF NOT EXISTS idx_reminders_sent ON reminders (is_sent)",
            "CREATE INDEX IF NOT EXISTS idx_users_username ON users (username)",
            "CREATE INDEX IF NOT EXISTS idx_users_email ON users (email)",
            "CREATE INDEX IF NOT EXISTS idx_users_subscribe_key ON users (subscribe_key)",
            "CREATE INDEX IF NOT EXISTS idx_invitation_codes_code ON invitation_codes (code)",
            "CREATE INDEX IF NOT EXISTS idx_invitation_codes_used ON invitation_codes (is_used)",
            "CREATE INDEX IF NOT EXISTS idx_ai_requests_user_id ON ai_requests (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_ai_requests_created_at ON ai_requests (created_at)",
            "CREATE INDEX IF NOT EXISTS idx_user_configs_user_id ON user_configs (user_id)",
            "CREATE INDEX IF NOT EXISTS idx_user_configs_type_key ON user_configs (config_type, config_key)",
        ]
        
        for index_sql in basic_indexes:
            db_manager.execute_update(index_sql)
        
        logger.info("数据库初始化完成")
        
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")
        raise


class EmailModel:
    """邮件数据模型"""
    
    def __init__(self, config: Config = None):
        self.db = DatabaseManager(config)
    
    def save_email(self, email_data: Dict[str, Any]) -> int:
        """保存邮件
        
        Args:
            email_data: 邮件数据
        
        Returns:
            邮件ID
        """
        query = """
        INSERT OR REPLACE INTO emails 
        (message_id, subject, sender, recipient, content, html_content, received_date, importance_level)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        params = (
            email_data.get('message_id'),
            email_data.get('subject', ''),
            email_data.get('sender', ''),
            email_data.get('recipient', ''),
            email_data.get('content', ''),
            email_data.get('html_content', ''),
            email_data.get('received_date'),
            email_data.get('importance_level', 'normal')
        )
        
        return self.db.execute_insert(query, params)
    
    def get_email_by_id(self, email_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取邮件"""
        query = "SELECT * FROM emails WHERE id = ?"
        results = self.db.execute_query(query, (email_id,))
        return results[0] if results else None
    
    def get_unprocessed_emails(self) -> List[Dict[str, Any]]:
        """获取未处理的邮件"""
        query = "SELECT * FROM emails WHERE is_processed = FALSE ORDER BY received_date DESC"
        return self.db.execute_query(query)
    
    def mark_email_processed(self, email_id: int):
        """标记邮件为已处理"""
        query = "UPDATE emails SET is_processed = TRUE, processed_date = ? WHERE id = ?"
        self.db.execute_update(query, (datetime.now(), email_id))
    
    def get_recent_emails(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近的邮件"""
        query = "SELECT * FROM emails ORDER BY received_date DESC LIMIT ?"
        return self.db.execute_query(query, (limit,))
    
    def update_email(self, email_id: int, email_data: Dict[str, Any]) -> bool:
        """更新邮件数据
        
        Args:
            email_id: 邮件ID
            email_data: 更新的邮件数据
        
        Returns:
            是否更新成功
        """
        try:
            query = """
            UPDATE emails SET 
                subject = ?, sender = ?, recipient = ?, content = ?, 
                html_content = ?, received_date = ?, importance_level = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """
            
            params = (
                email_data.get('subject', ''),
                email_data.get('sender', ''),
                email_data.get('recipient', ''),
                email_data.get('content', ''),
                email_data.get('html_content', ''),
                email_data.get('received_date'),
                email_data.get('importance_level', 'normal'),
                email_id
            )
            
            self.db.execute_update(query, params)
            return True
            
        except Exception as e:
            logger.error(f"更新邮件失败: {e}")
            return False
    
    def get_email_by_message_id(self, message_id: str) -> Optional[Dict[str, Any]]:
        """根据消息ID获取邮件
        
        Args:
            message_id: 邮件消息ID
        
        Returns:
            邮件数据
        """
        query = "SELECT * FROM emails WHERE message_id = ?"
        results = self.db.execute_query(query, (message_id,))
        return results[0] if results else None


class EventModel:
    """事件数据模型"""
    
    def __init__(self, config: Config = None):
        self.db = DatabaseManager(config)
    
    def save_event(self, event_data: Dict[str, Any]) -> int:
        """保存事件
        
        Args:
            event_data: 事件数据
        
        Returns:
            事件ID
        """
        query = """
        INSERT INTO events 
        (email_id, title, description, start_time, end_time, location, 
         importance_level, color, reminder_times, notion_page_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        # 处理reminder_times中的datetime对象
        reminder_times = event_data.get('reminder_times', [])
        serializable_reminder_times = []
        for rt in reminder_times:
            if isinstance(rt, datetime):
                serializable_reminder_times.append(rt.isoformat())
            else:
                serializable_reminder_times.append(rt)
        
        params = (
            event_data.get('email_id'),
            event_data.get('title', ''),
            event_data.get('description', ''),
            event_data.get('start_time'),
            event_data.get('end_time'),
            event_data.get('location', ''),
            event_data.get('importance_level', 'normal'),
            event_data.get('color', ''),
            json.dumps(serializable_reminder_times),
            event_data.get('notion_page_id', '')
        )
        
        return self.db.execute_insert(query, params)
    
    def get_upcoming_events(self, days: int = 30) -> List[Dict[str, Any]]:
        """获取即将到来的事件"""
        query = """
        SELECT * FROM events 
        WHERE start_time >= datetime('now') 
        AND start_time <= datetime('now', '+{} days')
        ORDER BY start_time ASC
        """.format(days)
        
        events = self.db.execute_query(query)
        
        # 解析reminder_times JSON
        for event in events:
            if event.get('reminder_times'):
                try:
                    event['reminder_times'] = json.loads(event['reminder_times'])
                except json.JSONDecodeError:
                    event['reminder_times'] = []
        
        return events
    
    def get_events_by_email(self, email_id: int) -> List[Dict[str, Any]]:
        """获取邮件相关的事件"""
        query = "SELECT * FROM events WHERE email_id = ? ORDER BY start_time ASC"
        return self.db.execute_query(query, (email_id,))