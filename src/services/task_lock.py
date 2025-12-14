# -*- coding: utf-8 -*-
"""
任务互斥锁管理 - 确保流式处理与自动同步不会同时执行。

注意：线上部署通常是多进程/多容器（app + scheduler），仅靠内存锁无法跨进程生效。
这里使用共享 SQLite（data/mail_scheduler.db）实现跨进程锁；并保留锁超时（默认 5 分钟）以防进程崩溃。
"""

import threading
import time
import uuid
import sqlite3
from typing import Dict, Optional

from ..core.logger import get_logger
from ..core.config import Config
from ..models.database import DatabaseManager

logger = get_logger(__name__)


class TaskLockManager:
    """任务锁管理器 - 单例模式（跨进程：SQLite）"""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._locks_lock = threading.Lock()
                    cls._instance._owner_id = uuid.uuid4().hex  # 当前进程的锁拥有者标识
                    cls._instance._db = DatabaseManager(Config())
                    cls._instance._inited = False
        return cls._instance

    def _ensure_table(self):
        if self._inited:
            return
        with self._locks_lock:
            if self._inited:
                return
            try:
                self._db.execute_update(
                    """
                    CREATE TABLE IF NOT EXISTS task_locks (
                        user_id INTEGER PRIMARY KEY,
                        task_type TEXT NOT NULL,
                        owner_id TEXT NOT NULL,
                        timestamp REAL NOT NULL
                    )
                    """
                )
                self._inited = True
            except Exception as e:
                logger.error(f"初始化 task_locks 表失败: {e}")
                # 不抛出，避免应用无法启动；但跨进程互斥会失效
                self._inited = False
    
    def acquire_lock(self, user_id: int, task_type: str, timeout: int = 5) -> bool:
        """
        尝试获取用户的任务锁
        
        Args:
            user_id: 用户ID
            task_type: 任务类型 ('stream' 或 'auto')
            timeout: 超时时间（秒）
            
        Returns:
            是否成功获取锁
        """
        self._ensure_table()
        start_time = time.time()
        now = time.time()

        while time.time() - start_time < timeout:
            now = time.time()
            try:
                # 尝试直接插入锁（user_id 为主键）
                self._db.execute_update(
                    "INSERT INTO task_locks (user_id, task_type, owner_id, timestamp) VALUES (?, ?, ?, ?)",
                    (user_id, task_type, self._owner_id, now),
                )
                logger.info(f"用户 {user_id} 获取任务锁: {task_type}")
                return True
            except sqlite3.IntegrityError:
                # 已有锁：读取并判断是否可重入/可抢占
                try:
                    rows = self._db.execute_query(
                        "SELECT task_type, owner_id, timestamp FROM task_locks WHERE user_id = ?",
                        (user_id,),
                    )
                    if not rows:
                        time.sleep(0.2)
                        continue
                    current = rows[0]
                    cur_type = current.get('task_type')
                    cur_owner = current.get('owner_id')
                    ts = float(current.get('timestamp') or 0)

                    # 同 owner_id 允许重入（刷新时间戳）
                    if cur_owner == self._owner_id and cur_type == task_type:
                        try:
                            self._db.execute_update(
                                "UPDATE task_locks SET timestamp = ? WHERE user_id = ? AND owner_id = ?",
                                (now, user_id, self._owner_id),
                            )
                        except Exception:
                            pass
                        return True

                    # 超时锁（默认 5 分钟）允许抢占
                    if now - ts > 300:
                        logger.warning(f"用户 {user_id} 的锁超时，强制抢占: {cur_type} -> {task_type}")
                        self._db.execute_update(
                            "UPDATE task_locks SET task_type = ?, owner_id = ?, timestamp = ? WHERE user_id = ?",
                            (task_type, self._owner_id, now, user_id),
                        )
                        return True

                except Exception as e:
                    logger.warning(f"读取任务锁失败: {e}")

            except Exception as e:
                logger.warning(f"写入任务锁失败: {e}")

            time.sleep(0.5)

        logger.warning(f"用户 {user_id} 获取任务锁超时: {task_type}")
        return False
    
    def release_lock(self, user_id: int, task_type: str):
        """
        释放用户的任务锁
        
        Args:
            user_id: 用户ID
            task_type: 任务类型
        """
        self._ensure_table()
        try:
            rows = self._db.execute_update(
                "DELETE FROM task_locks WHERE user_id = ? AND task_type = ? AND owner_id = ?",
                (user_id, task_type, self._owner_id),
            )
            if rows:
                logger.info(f"用户 {user_id} 释放任务锁: {task_type}")
            else:
                logger.debug(f"用户 {user_id} 尝试释放不存在/非本进程的锁: {task_type}")
        except Exception as e:
            logger.warning(f"释放任务锁失败: {e}")
    
    def force_release_all_locks(self, user_id: int):
        """
        强制释放用户的所有锁（用于流式处理开始时）
        
        Args:
            user_id: 用户ID
        """
        self._ensure_table()
        try:
            old = self.get_lock_status(user_id)
            self._db.execute_update("DELETE FROM task_locks WHERE user_id = ?", (user_id,))
            if old:
                logger.warning(f"用户 {user_id} 强制释放所有锁（之前类型: {old.get('type') or old.get('task_type')}）")
            else:
                logger.debug(f"用户 {user_id} 没有活动的锁")
        except Exception as e:
            logger.warning(f"强制释放锁失败: {e}")
    
    def get_lock_status(self, user_id: int) -> Optional[Dict]:
        """
        获取用户当前的锁状态
        
        Args:
            user_id: 用户ID
            
        Returns:
            锁状态字典，如果没有锁则返回 None
        """
        self._ensure_table()
        try:
            rows = self._db.execute_query(
                "SELECT user_id, task_type, owner_id, timestamp FROM task_locks WHERE user_id = ?",
                (user_id,),
            )
            return rows[0] if rows else None
        except Exception:
            return None
    
    def is_locked(self, user_id: int) -> bool:
        """
        检查用户是否有活动的锁
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否被锁定
        """
        return self.get_lock_status(user_id) is not None


# 全局单例
task_lock_manager = TaskLockManager()

