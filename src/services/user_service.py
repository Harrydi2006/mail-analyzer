# -*- coding: utf-8 -*-
"""
用户管理服务模块
"""

import hashlib
import secrets
import string
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from werkzeug.security import generate_password_hash, check_password_hash

from ..core.config import Config
from ..core.logger import get_logger
from ..models.database import DatabaseManager

logger = get_logger(__name__)


class UserService:
    """用户管理服务类"""
    
    def __init__(self, config: Config):
        """初始化用户服务
        
        Args:
            config: 配置对象
        """
        self.config = config
        self.db = DatabaseManager(config)
    
    def register_user(self, username: str, email: str, password: str, invitation_code: str) -> Dict[str, Any]:
        """用户注册
        
        Args:
            username: 用户名
            email: 邮箱
            password: 密码
            invitation_code: 邀请码
            
        Returns:
            注册结果
        """
        try:
            # 验证邀请码并获取用户角色信息
            invite_query = """
            SELECT id, max_uses, current_uses, expires_at, is_used, user_role 
            FROM invitation_codes 
            WHERE code = ? AND (expires_at IS NULL OR expires_at > ?)
            """
            
            invite_result = self.db.execute_query(invite_query, (invitation_code, datetime.now()))
            if not invite_result:
                return {'success': False, 'error': '邀请码无效或已过期'}
            
            invite_data = invite_result[0]
            if invite_data['is_used'] or invite_data['current_uses'] >= invite_data['max_uses']:
                return {'success': False, 'error': '邀请码已被使用完毕'}
            
            # 获取邀请码指定的用户角色
            user_role = invite_data.get('user_role', 'user')
            is_admin = (user_role == 'admin')
            
            # 检查用户名和邮箱是否已存在
            if self.get_user_by_username(username):
                return {'success': False, 'error': '用户名已存在'}
            
            if self.get_user_by_email(email):
                return {'success': False, 'error': '邮箱已被注册'}
            
            # 生成密码哈希
            password_hash = generate_password_hash(password)
            
            # 生成用户专属的订阅key
            subscribe_key = self.generate_subscribe_key()
            
            # 创建用户
            query = """
            INSERT INTO users (username, email, password_hash, subscribe_key, invitation_code, is_admin)
            VALUES (?, ?, ?, ?, ?, ?)
            """
            # 注意：execute_update 返回 rowcount，不是新插入的ID；这里必须用 execute_insert
            user_id = self.db.execute_insert(
                query,
                (username, email, password_hash, subscribe_key, invitation_code, is_admin),
            )
            
            if user_id:
                # 更新邀请码使用状态
                update_invite_query = """
                UPDATE invitation_codes 
                SET current_uses = current_uses + 1,
                    is_used = CASE WHEN current_uses + 1 >= max_uses THEN 1 ELSE 0 END
                WHERE code = ?
                """
                self.db.execute_update(update_invite_query, (invitation_code,))
                
                logger.info(f"用户注册成功: {username} ({email})")
                return {
                    'success': True,
                    'message': '用户注册成功',
                    'user': {
                        'id': user_id,
                        'username': username,
                        'email': email,
                        'subscribe_key': subscribe_key,
                        'is_admin': is_admin
                    }
                }
            else:
                return {'success': False, 'error': '注册失败'}
                
        except Exception as e:
            logger.error(f"用户注册失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def login_user(self, username: str, password: str) -> Dict[str, Any]:
        """用户登录
        
        Args:
            username: 用户名或邮箱
            password: 密码
            
        Returns:
            登录结果
        """
        try:
            # 获取用户信息（支持用户名或邮箱登录）
            user = self.get_user_by_username(username)
            if not user:
                user = self.get_user_by_email(username)
            
            if not user:
                return {'success': False, 'error': '用户不存在'}
            
            if not user['is_active']:
                return {'success': False, 'error': '账户已被禁用'}
            
            # 验证密码
            if not check_password_hash(user['password_hash'], password):
                return {'success': False, 'error': '密码错误'}
            
            # 更新最后登录时间
            self.update_last_login(user['id'])
            
            logger.info(f"用户登录成功: {user['username']}")
            return {
                'success': True,
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'email': user['email'],
                    'is_admin': user['is_admin'],
                    'subscribe_key': user['subscribe_key']
                }
            }
            
        except Exception as e:
            logger.error(f"用户登录失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_user_by_username(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名获取用户信息"""
        try:
            query = "SELECT * FROM users WHERE username = ?"
            results = self.db.execute_query(query, (username,))
            return results[0] if results else None
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None
    
    def get_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        """根据邮箱获取用户信息"""
        try:
            query = "SELECT * FROM users WHERE email = ?"
            results = self.db.execute_query(query, (email,))
            return results[0] if results else None
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None
    
    def get_user_by_subscribe_key(self, subscribe_key: str) -> Optional[Dict[str, Any]]:
        """根据订阅key获取用户信息"""
        try:
            query = "SELECT * FROM users WHERE subscribe_key = ?"
            results = self.db.execute_query(query, (subscribe_key,))
            return results[0] if results else None
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None
    
    def generate_subscribe_key(self) -> str:
        """生成用户专属的订阅key"""
        # 生成32位随机字符串
        alphabet = string.ascii_letters + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(32))

    def rotate_subscribe_key(self, user_id: int) -> Optional[str]:
        """重置用户订阅key并返回新key"""
        try:
            new_key = self.generate_subscribe_key()
            query = "UPDATE users SET subscribe_key = ? WHERE id = ?"
            updated = self.db.execute_update(query, (new_key, user_id))
            if updated:
                logger.info(f"用户 {user_id} 的订阅key已重置")
                return new_key
            return None
        except Exception as e:
            logger.error(f"重置订阅key失败: {e}")
            return None
    
    def update_last_login(self, user_id: int):
        """更新用户最后登录时间"""
        try:
            query = "UPDATE users SET last_login = ? WHERE id = ?"
            self.db.execute_update(query, (datetime.now(), user_id))
        except Exception as e:
            logger.error(f"更新登录时间失败: {e}")
    
    def validate_invitation_code(self, code: str) -> bool:
        """验证邀请码
        
        Args:
            code: 邀请码
            
        Returns:
            是否有效
        """
        try:
            query = """
            SELECT * FROM invitation_codes 
            WHERE code = ? AND is_used = FALSE 
            AND (expires_at IS NULL OR expires_at > ?)
            AND current_uses < max_uses
            """
            
            results = self.db.execute_query(query, (code, datetime.now()))
            return len(results) > 0
            
        except Exception as e:
            logger.error(f"验证邀请码失败: {e}")
            return False
    
    def use_invitation_code(self, code: str, user_id: int) -> bool:
        """使用邀请码
        
        Args:
            code: 邀请码
            user_id: 使用者用户ID
            
        Returns:
            是否成功
        """
        try:
            # 更新邀请码使用状态
            query = """
            UPDATE invitation_codes 
            SET current_uses = current_uses + 1,
                used_by = ?,
                used_at = ?,
                is_used = CASE WHEN current_uses + 1 >= max_uses THEN TRUE ELSE FALSE END
            WHERE code = ?
            """
            
            self.db.execute_update(query, (user_id, datetime.now(), code))
            return True
            
        except Exception as e:
            logger.error(f"使用邀请码失败: {e}")
            return False
    
    def generate_invitation_code(self, created_by: int, max_uses: int = 1, expires_days: int = 30, user_role: str = 'user') -> Dict[str, Any]:
        """生成邀请码
        
        Args:
            created_by: 创建者用户ID
            max_uses: 最大使用次数
            expires_days: 过期天数
            user_role: 用户角色 ('user' 或 'admin')
            
        Returns:
            生成结果
        """
        try:
            # 验证用户角色
            if user_role not in ['user', 'admin']:
                return {'success': False, 'error': '无效的用户角色'}
            
            # 生成8位邀请码
            code = ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            
            # 计算过期时间
            expires_at = datetime.now() + timedelta(days=expires_days)
            
            # 插入数据库
            query = """
            INSERT INTO invitation_codes (code, created_by, max_uses, expires_at, user_role)
            VALUES (?, ?, ?, ?, ?)
            """
            # 这里用 execute_insert 可以拿到新记录ID（虽然当前逻辑只用作 truthy 判断）
            code_id = self.db.execute_insert(query, (code, created_by, max_uses, expires_at, user_role))
            
            if code_id:
                logger.info(f"邀请码生成成功: {code}")
                return {
                    'success': True,
                    'code': code,
                    'expires_at': expires_at.isoformat(),
                    'user_role': user_role
                }
            else:
                return {'success': False, 'error': '生成失败'}
                
        except Exception as e:
            logger.error(f"生成邀请码失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_all_users(self) -> List[Dict[str, Any]]:
        """获取所有用户列表（管理员功能）"""
        try:
            query = """
            SELECT u.id, u.username, u.email, u.is_active, u.is_admin, 
                   u.created_at, u.last_login, u.invitation_code,
                   COUNT(ar.id) as ai_request_count
            FROM users u
            LEFT JOIN ai_requests ar ON u.id = ar.user_id
            GROUP BY u.id
            ORDER BY u.created_at DESC
            """
            
            return self.db.execute_query(query)
            
        except Exception as e:
            logger.error(f"获取用户列表失败: {e}")
            return []
    
    def delete_user(self, user_id: int) -> Dict[str, Any]:
        """删除用户（管理员功能）
        
        Args:
            user_id: 用户ID
            
        Returns:
            删除结果
        """
        try:
            # 检查用户是否存在
            user_query = "SELECT username FROM users WHERE id = ?"
            user_result = self.db.execute_query(user_query, (user_id,))
            
            if not user_result:
                return {'success': False, 'error': '用户不存在'}
            
            username = user_result[0]['username']
            
            # 删除用户相关数据（级联删除）
            # 注意：这里需要根据实际需求决定是否删除用户的邮件、事件等数据
            
            # 删除AI请求记录
            self.db.execute_update("DELETE FROM ai_requests WHERE user_id = ?", (user_id,))
            
            # 删除用户
            self.db.execute_update("DELETE FROM users WHERE id = ?", (user_id,))
            
            logger.info(f"用户删除成功: {username}")
            return {'success': True, 'message': f'用户 {username} 已删除'}
            
        except Exception as e:
            logger.error(f"删除用户失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def get_user_ai_stats(self, user_id: int, days: int = 30) -> Dict[str, Any]:
        """获取用户AI使用统计
        
        Args:
            user_id: 用户ID
            days: 统计天数
            
        Returns:
            统计结果
        """
        try:
            since_date = datetime.now() - timedelta(days=days)
            
            query = """
            SELECT 
                COUNT(*) as total_requests,
                SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as successful_requests,
                SUM(tokens_used) as total_tokens,
                SUM(cost) as total_cost,
                request_type,
                COUNT(*) as type_count
            FROM ai_requests 
            WHERE user_id = ? AND created_at >= ?
            GROUP BY request_type
            """
            
            results = self.db.execute_query(query, (user_id, since_date))
            
            # 汇总统计
            total_query = """
            SELECT 
                COUNT(*) as total_requests,
                SUM(CASE WHEN success = TRUE THEN 1 ELSE 0 END) as successful_requests,
                SUM(tokens_used) as total_tokens,
                SUM(cost) as total_cost
            FROM ai_requests 
            WHERE user_id = ? AND created_at >= ?
            """
            
            total_result = self.db.execute_query(total_query, (user_id, since_date))
            
            return {
                'success': True,
                'total_stats': total_result[0] if total_result else {},
                'by_type': results
            }
            
        except Exception as e:
            logger.error(f"获取AI统计失败: {e}")
            return {'success': False, 'error': str(e)}
    
    def record_ai_request(self, user_id: int, request_type: str, email_id: Optional[int] = None, 
                         tokens_used: int = 0, cost: float = 0, success: bool = True, 
                         error_message: Optional[str] = None) -> bool:
        """记录AI请求
        
        Args:
            user_id: 用户ID
            request_type: 请求类型
            email_id: 关联邮件ID
            tokens_used: 使用的token数量
            cost: 请求成本
            success: 是否成功
            error_message: 错误信息
            
        Returns:
            是否记录成功
        """
        try:
            query = """
            INSERT INTO ai_requests (user_id, request_type, email_id, tokens_used, cost, success, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            
            self.db.execute_update(query, (user_id, request_type, email_id, tokens_used, cost, success, error_message))
            return True
            
        except Exception as e:
            logger.error(f"记录AI请求失败: {e}")
            return False