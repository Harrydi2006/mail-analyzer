# -*- coding: utf-8 -*-
"""
任务互斥锁管理 - 确保流式处理和自动获取不会同时执行
"""

import threading
import time
from typing import Dict, Optional
from ..core.logger import get_logger

logger = get_logger(__name__)


class TaskLockManager:
    """任务锁管理器 - 单例模式"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._user_locks = {}  # {user_id: {'type': 'stream/auto', 'timestamp': time}}
                    cls._instance._locks_lock = threading.Lock()
        return cls._instance
    
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
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            with self._locks_lock:
                current_lock = self._user_locks.get(user_id)
                
                # 如果没有锁，直接获取
                if current_lock is None:
                    self._user_locks[user_id] = {
                        'type': task_type,
                        'timestamp': time.time()
                    }
                    logger.info(f"用户 {user_id} 获取任务锁: {task_type}")
                    return True
                
                # 如果是同类型任务，允许（重入）
                if current_lock['type'] == task_type:
                    logger.debug(f"用户 {user_id} 重入任务锁: {task_type}")
                    return True
                
                # 检查是否超时（超过5分钟的锁自动释放）
                if time.time() - current_lock['timestamp'] > 300:
                    logger.warning(f"用户 {user_id} 的锁超时，强制释放: {current_lock['type']}")
                    self._user_locks[user_id] = {
                        'type': task_type,
                        'timestamp': time.time()
                    }
                    return True
            
            # 等待一小段时间后重试
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
        with self._locks_lock:
            current_lock = self._user_locks.get(user_id)
            if current_lock and current_lock['type'] == task_type:
                del self._user_locks[user_id]
                logger.info(f"用户 {user_id} 释放任务锁: {task_type}")
            else:
                logger.debug(f"用户 {user_id} 尝试释放不存在的锁: {task_type}")
    
    def force_release_all_locks(self, user_id: int):
        """
        强制释放用户的所有锁（用于流式处理开始时）
        
        Args:
            user_id: 用户ID
        """
        with self._locks_lock:
            if user_id in self._user_locks:
                old_type = self._user_locks[user_id]['type']
                del self._user_locks[user_id]
                logger.warning(f"用户 {user_id} 强制释放所有锁（之前类型: {old_type}）")
            else:
                logger.debug(f"用户 {user_id} 没有活动的锁")
    
    def get_lock_status(self, user_id: int) -> Optional[Dict]:
        """
        获取用户当前的锁状态
        
        Args:
            user_id: 用户ID
            
        Returns:
            锁状态字典，如果没有锁则返回 None
        """
        with self._locks_lock:
            return self._user_locks.get(user_id)
    
    def is_locked(self, user_id: int) -> bool:
        """
        检查用户是否有活动的锁
        
        Args:
            user_id: 用户ID
            
        Returns:
            是否被锁定
        """
        with self._locks_lock:
            return user_id in self._user_locks


# 全局单例
task_lock_manager = TaskLockManager()

