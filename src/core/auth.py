# -*- coding: utf-8 -*-
"""
身份验证和会话管理模块
"""

import functools
from flask import session, request, jsonify, redirect, url_for, g
from typing import Optional, Dict, Any
import secrets
import os
from datetime import timedelta

from .logger import get_logger
import base64

logger = get_logger(__name__)


class AuthManager:
    """身份验证管理器"""
    
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """初始化Flask应用"""
        # SECRET_KEY 应由应用工厂设置（生产环境必须显式提供），避免这里写入不安全默认值

        # 配置 session
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
        is_production = os.environ.get('FLASK_ENV', 'development').lower() == 'production'
        app.config['SESSION_COOKIE_SECURE'] = is_production
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
        
        # 注册请求前处理器
        @app.before_request
        def load_logged_in_user():
            """在每个请求前加载当前登录用户"""
            user_id = session.get('user_id')
            
            if user_id is None:
                g.user = None
                # 补充：支持Basic Auth（用于CalDAV/CLI等无Cookie的客户端）
                try:
                    auth_header = request.headers.get('Authorization', '')
                    if auth_header.startswith('Basic '):
                        creds = auth_header.split(' ', 1)[1]
                        decoded = base64.b64decode(creds).decode('utf-8', errors='ignore')
                        if ':' in decoded:
                            username, password = decoded.split(':', 1)
                            # 延迟导入以避免循环依赖
                            from ..services.user_service import UserService
                            from .config import Config
                            user_service = UserService(Config())
                            result = user_service.login_user(username, password)
                            if result.get('success'):
                                user = result['user']
                                g.user = {
                                    'id': user['id'],
                                    'username': user['username'],
                                    'email': user['email'],
                                    'is_admin': user.get('is_admin', False),
                                    'subscribe_key': user.get('subscribe_key')
                                }
                                logger.info(f"BasicAuth 认证通过: {user['username']} (ID: {user['id']})")
                except Exception as e:
                    logger.warning(f"BasicAuth 认证失败: {e}")
            else:
                # 这里应该从数据库获取用户信息
                # 暂时使用session中的用户信息
                g.user = {
                    'id': user_id,
                    'username': session.get('username'),
                    'email': session.get('email'),
                    'is_admin': session.get('is_admin', False),
                    'subscribe_key': session.get('subscribe_key')
                }
    
    @staticmethod
    def login_user(user_data: Dict[str, Any], remember_me: bool = False):
        """用户登录，设置会话
        
        Args:
            user_data: 用户数据字典
            remember_me: 是否记住登录状态
        """
        session.clear()
        session['user_id'] = user_data['id']
        session['username'] = user_data['username']
        session['email'] = user_data['email']
        session['is_admin'] = user_data.get('is_admin', False)
        session['subscribe_key'] = user_data.get('subscribe_key')
        # 生成CSRF令牌
        session['csrf_token'] = secrets.token_hex(32)
        
        if remember_me:
            # 记住我：设置永久session（24小时）
            session.permanent = True
            logger.info(f"用户登录成功（记住我）: {user_data['username']} (ID: {user_data['id']})")
        else:
            # 不记住我：设置临时session（浏览器关闭时过期）
            session.permanent = False
            logger.info(f"用户登录成功（临时）: {user_data['username']} (ID: {user_data['id']})")
    
    @staticmethod
    def logout_user():
        """用户登出，清除会话"""
        username = session.get('username', 'Unknown')
        session.clear()
        logger.info(f"用户登出: {username}")

    @staticmethod
    def get_csrf_token() -> Optional[str]:
        """获取当前会话的CSRF令牌"""
        return session.get('csrf_token')
    
    @staticmethod
    def get_current_user() -> Optional[Dict[str, Any]]:
        """获取当前登录用户
        
        Returns:
            当前用户信息，如果未登录返回None
        """
        return getattr(g, 'user', None)
    
    @staticmethod
    def get_current_user_id() -> Optional[int]:
        """获取当前用户ID
        
        Returns:
            当前用户ID，如果未登录返回None
        """
        user = AuthManager.get_current_user()
        return user['id'] if user else None
    
    @staticmethod
    def is_logged_in() -> bool:
        """检查用户是否已登录
        
        Returns:
            是否已登录
        """
        return AuthManager.get_current_user() is not None
    
    @staticmethod
    def is_admin() -> bool:
        """检查当前用户是否为管理员
        
        Returns:
            是否为管理员
        """
        user = AuthManager.get_current_user()
        return user and user.get('is_admin', False)
    
    @staticmethod
    def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
        """根据用户ID获取用户信息
        
        Args:
            user_id: 用户ID
            
        Returns:
            用户信息字典，如果用户不存在返回None
        """
        try:
            from ..models.database import DatabaseManager
            from ..core.config import Config
            
            config = Config()
            db = DatabaseManager(config)
            
            users = db.execute_query(
                "SELECT id, username, email, is_active, is_admin, created_at FROM users WHERE id = ?",
                (user_id,)
            )
            
            if users:
                user = users[0]
                return {
                    'id': user['id'],
                    'username': user['username'],
                    'email': user['email'],
                    'is_active': bool(user['is_active']),
                    'is_admin': bool(user['is_admin']),
                    'created_at': user['created_at']
                }
            
            return None
            
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return None


def login_required(f):
    """登录验证装饰器
    
    用于保护需要登录才能访问的视图函数
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not AuthManager.is_logged_in():
            # 如果是API请求，返回JSON错误
            if request.path.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'error': '请先登录',
                    'code': 'LOGIN_REQUIRED'
                }), 401
            
            # 如果是页面请求，重定向到登录页
            return redirect(url_for('login_page'))
        
        return f(*args, **kwargs)
    
    return decorated_function


def admin_required(f):
    """管理员权限验证装饰器
    
    用于保护需要管理员权限才能访问的视图函数
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not AuthManager.is_logged_in():
            # 如果是API请求，返回JSON错误
            if request.path.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'error': '请先登录',
                    'code': 'LOGIN_REQUIRED'
                }), 401
            
            # 如果是页面请求，重定向到登录页
            return redirect(url_for('login_page'))
        
        if not AuthManager.is_admin():
            # 如果是API请求，返回JSON错误
            if request.path.startswith('/api/'):
                return jsonify({
                    'success': False,
                    'error': '需要管理员权限',
                    'code': 'ADMIN_REQUIRED'
                }), 403
            
            # 如果是页面请求，返回403错误
            return '需要管理员权限', 403
        
        return f(*args, **kwargs)
    
    return decorated_function


def api_auth_required(f):
    """API身份验证装饰器
    
    专门用于API接口的身份验证
    """
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        if not AuthManager.is_logged_in():
            return jsonify({
                'success': False,
                'error': '请先登录',
                'code': 'LOGIN_REQUIRED'
            }), 401
        
        return f(*args, **kwargs)
    
    return decorated_function


def get_user_filter() -> Dict[str, Any]:
    """获取当前用户的数据过滤条件
    
    用于数据库查询时过滤用户数据
    
    Returns:
        包含用户ID的过滤条件字典
    """
    user_id = AuthManager.get_current_user_id()
    if user_id is None:
        raise ValueError("用户未登录")
    
    return {'user_id': user_id}


def ensure_user_data_isolation(query_params: Dict[str, Any]) -> Dict[str, Any]:
    """确保用户数据隔离
    
    在查询参数中添加用户ID过滤条件
    
    Args:
        query_params: 原始查询参数
        
    Returns:
        添加了用户ID过滤的查询参数
    """
    user_filter = get_user_filter()
    query_params.update(user_filter)
    return query_params