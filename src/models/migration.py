# -*- coding: utf-8 -*-
"""
数据库迁移脚本
"""

import sqlite3
from pathlib import Path
from ..core.config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


def migrate_database():
    """执行数据库迁移"""
    try:
        config = Config()
        # 使用主配置中的数据库路径，确保迁移作用于同一数据库
        db_path = Path(config.database_config.get('path', 'data/mail_scheduler.db'))
        
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # 检查是否需要迁移
        migrations = [
            ('add_user_id_to_emails', add_user_id_to_emails),
            ('add_user_id_to_email_analysis', add_user_id_to_email_analysis),
            ('add_user_id_to_events', add_user_id_to_events),
            ('add_user_id_to_reminders', add_user_id_to_reminders),
            ('add_user_id_to_notion_archive', add_user_id_to_notion_archive),
            ('add_user_id_to_keyword_matches', add_user_id_to_keyword_matches),
            ('add_user_role_to_invitation_codes', add_user_role_to_invitation_codes),
            ('add_color_to_events', add_color_to_events),
            ('add_reminder_times_to_events', add_reminder_times_to_events),
            ('create_reminder_deliveries', create_reminder_deliveries),
        ]
        
        for migration_name, migration_func in migrations:
            if not is_migration_applied(cursor, migration_name):
                logger.info(f"执行迁移: {migration_name}")
                migration_func(cursor)
                mark_migration_applied(cursor, migration_name)
                conn.commit()
                logger.info(f"迁移完成: {migration_name}")
        
        conn.close()
        logger.info("数据库迁移完成")
        
    except Exception as e:
        logger.error(f"数据库迁移失败: {e}")
        raise


def is_migration_applied(cursor, migration_name):
    """检查迁移是否已应用"""
    try:
        # 创建迁移记录表（如果不存在）
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS migrations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 检查迁移是否已应用
        cursor.execute("SELECT 1 FROM migrations WHERE name = ?", (migration_name,))
        return cursor.fetchone() is not None
    except Exception:
        return False


def mark_migration_applied(cursor, migration_name):
    """标记迁移为已应用"""
    cursor.execute("INSERT INTO migrations (name) VALUES (?)", (migration_name,))


def column_exists(cursor, table_name, column_name):
    """检查表中是否存在指定列"""
    try:
        cursor.execute(f"PRAGMA table_info({table_name})")
        columns = [row[1] for row in cursor.fetchall()]
        return column_name in columns
    except Exception:
        return False


def add_user_id_to_emails(cursor):
    """为emails表添加user_id列"""
    if not column_exists(cursor, 'emails', 'user_id'):
        cursor.execute("ALTER TABLE emails ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_emails_user_id ON emails (user_id)")


def add_user_id_to_email_analysis(cursor):
    """为email_analysis表添加user_id列"""
    if not column_exists(cursor, 'email_analysis', 'user_id'):
        cursor.execute("ALTER TABLE email_analysis ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_email_analysis_user_id ON email_analysis (user_id)")


def add_user_id_to_events(cursor):
    """为events表添加user_id列"""
    if not column_exists(cursor, 'events', 'user_id'):
        cursor.execute("ALTER TABLE events ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_user_id ON events (user_id)")


def add_user_id_to_reminders(cursor):
    """为reminders表添加user_id列"""
    if not column_exists(cursor, 'reminders', 'user_id'):
        cursor.execute("ALTER TABLE reminders ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminders_user_id ON reminders (user_id)")


def add_user_id_to_notion_archive(cursor):
    """为notion_archive表添加user_id列"""
    if not column_exists(cursor, 'notion_archive', 'user_id'):
        cursor.execute("ALTER TABLE notion_archive ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_notion_archive_user_id ON notion_archive (user_id)")


def add_user_id_to_keyword_matches(cursor):
    """为keyword_matches表添加user_id列"""
    if not column_exists(cursor, 'keyword_matches', 'user_id'):
        cursor.execute("ALTER TABLE keyword_matches ADD COLUMN user_id INTEGER NOT NULL DEFAULT 1")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keyword_matches_user_id ON keyword_matches (user_id)")


def add_user_role_to_invitation_codes(cursor):
    """为invitation_codes表添加user_role列"""
    if not column_exists(cursor, 'invitation_codes', 'user_role'):
        cursor.execute("ALTER TABLE invitation_codes ADD COLUMN user_role TEXT DEFAULT 'user'")


def add_color_to_events(cursor):
    """为events表添加color列"""
    if not column_exists(cursor, 'events', 'color'):
        cursor.execute("ALTER TABLE events ADD COLUMN color TEXT DEFAULT '#007bff'")


def add_reminder_times_to_events(cursor):
    """为events表添加reminder_times列"""
    if not column_exists(cursor, 'events', 'reminder_times'):
        cursor.execute("ALTER TABLE events ADD COLUMN reminder_times TEXT DEFAULT '[]'")


def create_reminder_deliveries(cursor):
    """创建 reminder_deliveries 表（按渠道追踪提醒投递）"""
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS reminder_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            reminder_id INTEGER NOT NULL,
            channel TEXT NOT NULL,
            is_sent BOOLEAN DEFAULT FALSE,
            sent_at DATETIME,
            last_error TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(reminder_id, channel)
        )
        """
    )
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminder_deliveries_user ON reminder_deliveries (user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminder_deliveries_reminder ON reminder_deliveries (reminder_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_reminder_deliveries_sent ON reminder_deliveries (is_sent)")


if __name__ == '__main__':
    migrate_database()