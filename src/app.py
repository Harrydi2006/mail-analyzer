# -*- coding: utf-8 -*-
"""
Flask应用主文件
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
from flask_cors import CORS
import os
import json
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from threading import Thread
from uuid import uuid4
from typing import Optional

from .core.config import Config
from .core.logger import setup_logger, get_logger
from .core.auth import AuthManager, login_required, admin_required, api_auth_required
from .models.database import init_database
from .services.email_service import EmailService
from .services.ai_service import AIService
from .services.scheduler_service import SchedulerService
from .services.notion_service import NotionService
from .services.user_service import UserService
from .services.tag_service import TagService


def create_app():
    """创建Flask应用实例"""
    app = Flask(__name__, 
                template_folder='../templates',
                static_folder='../static')
    
    # 启用CORS（开发环境允许 localhost/127.0.0.1 任意端口，便于 Flutter Web 调试）
    default_dev_origins = [r"http://localhost:\d+", r"http://127\.0\.0\.1:\d+"]
    env_origins = [x.strip() for x in os.environ.get('CORS_ALLOW_ORIGINS', '').split(',') if x.strip()]
    allowed_origins = env_origins if env_origins else default_dev_origins
    CORS(app, supports_credentials=True, resources={r"/api/*": {"origins": allowed_origins}})
    
    # 配置密钥：生产环境必须提供SECRET_KEY
    env = os.environ.get('FLASK_ENV', 'development').lower()
    is_production = env == 'production'
    secret_key = os.environ.get('SECRET_KEY')
    if env == 'production' and not secret_key:
        raise RuntimeError('生产环境必须通过环境变量SECRET_KEY设置应用密钥')
    app.config['SECRET_KEY'] = secret_key or 'dev-secret-key-change-in-production'
    
    # 初始化配置和服务
    config = Config()
    logger = setup_logger()
    app_logger = get_logger(__name__)
    
    # 初始化数据库（必须使用同一个 config，避免 DatabaseManager 单例锁定到错误的 db_path）
    init_database(config)
    
    # 初始化身份验证
    auth_manager = AuthManager(app)

    # CSRF保护：对修改类请求进行CSRF校验（基于会话令牌）
    @app.before_request
    def csrf_protect():
        if request.method in ['POST', 'PUT', 'PATCH', 'DELETE'] and request.path.startswith('/api/'):
            # 登录接口与公开只读接口跳过
            if request.path in ['/api/auth/login', '/api/auth/register']:
                return
            token = request.headers.get('X-CSRF-Token') or request.cookies.get('csrf_token')
            from .core.auth import AuthManager as AM
            # 未登录请求不在这里报 CSRF，交给各接口的鉴权装饰器返回更准确的“请先登录”。
            if not AM.is_logged_in():
                return
            expected = AM.get_csrf_token()
            # 兼容历史会话：若已登录但尚无csrf_token，则按需补发并放行本次请求。
            if not expected:
                import secrets
                session['csrf_token'] = secrets.token_hex(32)
                return
            if not expected or token != expected:
                return jsonify({'success': False, 'error': 'CSRF校验失败'}), 403
    
    # 添加请求日志记录
    @app.before_request
    def log_request_info():
        logger.info(f'HTTP请求: {request.method} {request.path} - 来自 {request.remote_addr}')
    
    @app.after_request
    def log_response_info(response):
        logger.info(f'HTTP响应: {request.method} {request.path} - 状态码 {response.status_code}')
        return response
    
    # 若会话已有CSRF令牌但浏览器未携带csrf_token Cookie，则在响应中补发
    @app.after_request
    def ensure_csrf_cookie(response):
        try:
            from .core.auth import AuthManager as AM
            token = AM.get_csrf_token()
            if token and not request.cookies.get('csrf_token'):
                response.set_cookie('csrf_token', token, httponly=False, samesite='Lax', secure=is_production)
        except Exception:
            pass
        return response
    
    # 延迟初始化服务（避免启动时的重复连接）
    _services = {}
    
    def get_email_service():
        if 'email_service' not in _services:
            _services['email_service'] = EmailService(config)
        return _services['email_service']
    
    def get_ai_service():
        if 'ai_service' not in _services:
            _services['ai_service'] = AIService(config)
        return _services['ai_service']
    
    def get_scheduler_service():
        if 'scheduler_service' not in _services:
            _services['scheduler_service'] = SchedulerService(config)
        return _services['scheduler_service']
    
    def get_notion_service(user_id: int = None):
        # 为每个用户创建独立的Notion服务实例
        service_key = f'notion_service_{user_id}' if user_id else 'notion_service_global'
        if service_key not in _services:
            _services[service_key] = NotionService(config, user_id)
        return _services[service_key]
    
    def get_user_service():
        if 'user_service' not in _services:
            _services['user_service'] = UserService(config)
        return _services['user_service']
    
    # 为了向后兼容，保留原有的服务实例引用
    email_service = get_email_service()
    ai_service = get_ai_service()
    scheduler_service = get_scheduler_service()
    notion_service = get_notion_service()
    user_service = get_user_service()
    tag_service = TagService(config)

    def _build_keywords_payload(analysis_result):
        """统一构建 email_analysis.keywords_matched 载荷，兼容旧结构。"""
        tags = TagService.normalize_tags(
            (analysis_result or {}).get('tags', {}),
            int((analysis_result or {}).get('importance_score', 5) or 5),
        )
        payload = {
            'matched_keywords': (analysis_result or {}).get('matched_keywords', []) or [],
            'tags': tags,
        }
        return json.dumps(payload, ensure_ascii=False)

    def _render_page(template_name: str, **kwargs):
        return render_template(template_name, **kwargs)

    @app.route('/')
    @login_required
    def index():
        """主页"""
        return _render_page('index.html')
    
    @app.route('/login')
    def login_page():
        """登录页面"""
        return render_template('login.html')
    
    @app.route('/register')
    def register_page():
        """注册页面"""
        return render_template('register.html')
    
    @app.route('/emails')
    @login_required
    def emails():
        """邮件管理页面"""
        try:
            # 获取当前用户ID
            user_id = AuthManager.get_current_user_id()
            # 获取邮件列表，支持分页
            limit = request.args.get('limit', 200, type=int)
            emails = email_service.get_processed_emails(user_id, limit=limit)
            return _render_page('emails.html', emails=emails)
        except Exception as e:
            logger.error(f"获取邮件列表失败: {e}")
            return _render_page('emails.html', emails=[], error=str(e))
    
    @app.route('/schedule')
    @login_required
    def schedule():
        """日程表页面"""
        try:
            # 获取当前用户ID
            user_id = AuthManager.get_current_user_id()
            # 获取日程事件
            events = scheduler_service.get_upcoming_events(user_id)
            return _render_page('schedule.html', events=events)
        except Exception as e:
            logger.error(f"获取日程失败: {e}")
            return _render_page('schedule.html', events=[], error=str(e))
    
    @app.route('/config')
    @login_required
    def config_page():
        """配置页面"""
        return _render_page('config.html', config=config.get_safe_config())
    
    @app.route('/admin')
    @admin_required
    def admin_page():
        """管理员后台页面"""
        return _render_page('admin.html')
    
    def _process_new_email(email_data, user_id):
        """处理新邮件的AI分析（多线程函数）"""
        try:
            if not user_id:
                raise ValueError("user_id is required for multi-user isolation")
            # 为每个线程创建独立的服务实例
            thread_ai_service = AIService(config)
            thread_email_service = EmailService(config)
            thread_scheduler_service = SchedulerService(config)
            thread_notion_service = NotionService(config, user_id)
            
            # 先保存邮件到数据库（确保邮件不丢失）
            email_id = thread_email_service.email_model.save_email(email_data, user_id)
            logger.info(f"邮件已保存到数据库，ID: {email_id}, 主题: {email_data.get('subject', 'Unknown')}")
            
            # AI分析邮件内容（不传递user_id，因为配置已经合并到实例中）
            analysis_result = thread_ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject'],
                user_id=user_id,
                reference_time=email_data.get('received_date')
            )
            
            # 如果AI分析成功，保存分析结果
            if analysis_result:
                # 若AI未配置或被判定不可用，则按失败处理
                is_unconfigured = (
                    (analysis_result.get('ai_model') == 'none') or
                    ('未配置AI' in (analysis_result.get('summary', '') or '')) or
                    ('未配置' in (analysis_result.get('importance_reason', '') or '') and 'AI' in (analysis_result.get('importance_reason', '') or ''))
                )
                if is_unconfigured:
                    logger.warning("AI未配置，记为失败以便后续重试")
                    from .models.database import DatabaseManager
                    db = DatabaseManager(config)
                    try:
                        delete_query = "DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?"
                        db.execute_update(delete_query, (email_id, user_id))
                        analysis_query = (
                            "INSERT INTO email_analysis (user_id, email_id, summary, importance_score, importance_reason, events_json, keywords_matched, ai_model, analysis_date)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        )
                        db.execute_insert(analysis_query, (
                            user_id,
                            email_id,
                            '未配置AI服务',
                            5,
                            'AI API密钥未配置',
                            json.dumps([], ensure_ascii=False),
                            json.dumps([], ensure_ascii=False),
                            'none',
                            datetime.now()
                        ))
                    except Exception as _e:
                        logger.warning(f"写入未配置标记时出错: {_e}")
                    return {'success': False, 'error': '未配置AI服务', 'email_subject': email_data.get('subject', 'Unknown')}
                # 保存分析结果到数据库
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                
                analysis_query = """
                INSERT INTO email_analysis 
                (user_id, email_id, summary, importance_score, importance_reason, 
                 events_json, keywords_matched, ai_model, analysis_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                
                # 处理events中的datetime对象
                events = analysis_result.get('events', [])
                serializable_events = []
                for event in events:
                    serializable_event = event.copy()
                    # 将datetime对象转换为字符串
                    if 'start_time' in serializable_event and serializable_event['start_time']:
                        st = serializable_event['start_time']
                        if hasattr(st, 'isoformat') and not isinstance(st, str):
                            serializable_event['start_time'] = st.isoformat()
                    if 'end_time' in serializable_event and serializable_event['end_time']:
                        et = serializable_event['end_time']
                        if hasattr(et, 'isoformat') and not isinstance(et, str):
                            serializable_event['end_time'] = et.isoformat()
                    if 'reminder_times' in serializable_event and serializable_event['reminder_times']:
                        reminder_times = []
                        for rt in serializable_event['reminder_times']:
                            if isinstance(rt, datetime):
                                reminder_times.append(rt.isoformat())
                            else:
                                reminder_times.append(rt)
                        serializable_event['reminder_times'] = reminder_times
                    serializable_events.append(serializable_event)
                
                analysis_params = (
                    user_id,
                    email_id,
                    analysis_result.get('summary', ''),
                    analysis_result.get('importance_score', 5),
                    analysis_result.get('importance_reason', ''),
                    json.dumps(serializable_events, ensure_ascii=False),
                    _build_keywords_payload(analysis_result),
                    analysis_result.get('ai_model', ''),
                    datetime.now()
                )
                
                db.execute_insert(analysis_query, analysis_params)
                logger.info(f"AI分析结果已保存，邮件ID: {email_id}")

                # 标记邮件为已处理（否则列表仍显示“未处理”）
                try:
                    db.execute_update(
                        "UPDATE emails SET is_processed = 1, processed_date = COALESCE(processed_date, CURRENT_TIMESTAMP) WHERE id = ? AND user_id = ?",
                        (email_id, user_id)
                    )
                except Exception as _e:
                    logger.warning(f"标记邮件已处理失败: email_id={email_id}, user_id={user_id}, err={_e}")
                
                # 如果有事件，添加到日程
                if analysis_result.get('events'):
                    for event in analysis_result['events']:
                        event['email_id'] = email_id
                        thread_scheduler_service.add_event(event, user_id)
                    logger.info(f"已添加 {len(analysis_result['events'])} 个事件到日程")
                
                # 归档到Notion
                thread_notion_service.archive_email(email_data, analysis_result)
            else:
                logger.warning(f"AI分析失败，但邮件已保存: {email_data.get('subject', 'Unknown')}")
            
            return {'success': True, 'email_subject': email_data.get('subject', 'Unknown'), 'email_id': email_id}
            
        except Exception as e:
            logger.error(f"处理新邮件失败: {e}")
            return {'success': False, 'error': str(e), 'email_subject': email_data.get('subject', 'Unknown')}
    
    def _retry_email_analysis(email_data, user_id):
        """重试邮件AI分析（多线程函数）"""
        try:
            # 为每个线程创建独立的服务实例
            thread_ai_service = AIService(config)
            thread_scheduler_service = SchedulerService(config)
            thread_notion_service = NotionService(config, user_id)
            
            email_id = email_data['id']
            logger.info(f"开始重试分析邮件，ID: {email_id}, 主题: {email_data.get('subject', 'Unknown')}")
            
            # AI分析邮件内容
            analysis_result = thread_ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject'],
                user_id=user_id,
                reference_time=email_data.get('received_date')
            )
            
            # 如果AI分析成功，更新分析结果
            if analysis_result:
                # 删除旧的分析结果
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                
                delete_query = "DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?"
                db.execute_update(delete_query, (email_id, user_id))
                
                # 保存新的分析结果
                analysis_query = """
                INSERT INTO email_analysis 
                (user_id, email_id, summary, importance_score, importance_reason, 
                 events_json, keywords_matched, ai_model, analysis_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                
                # 处理events中的datetime对象
                events = analysis_result.get('events', [])
                serializable_events = []
                for event in events:
                    serializable_event = event.copy()
                    # 将datetime对象转换为字符串
                    if 'start_time' in serializable_event and serializable_event['start_time']:
                        if isinstance(serializable_event['start_time'], datetime):
                            serializable_event['start_time'] = serializable_event['start_time'].isoformat()
                    if 'end_time' in serializable_event and serializable_event['end_time']:
                        if isinstance(serializable_event['end_time'], datetime):
                            serializable_event['end_time'] = serializable_event['end_time'].isoformat()
                    # 归一化 reminder_times 列表中的 datetime
                    if 'reminder_times' in serializable_event and serializable_event['reminder_times']:
                        normalized = []
                        for rt in serializable_event['reminder_times']:
                            if hasattr(rt, 'isoformat') and not isinstance(rt, str):
                                normalized.append(rt.isoformat())
                            else:
                                normalized.append(rt)
                        serializable_event['reminder_times'] = normalized
                    serializable_events.append(serializable_event)
                
                analysis_params = (
                    user_id,
                    email_id,
                    analysis_result.get('summary', ''),
                    analysis_result.get('importance_score', 5),
                    analysis_result.get('importance_reason', ''),
                    json.dumps(serializable_events, ensure_ascii=False),
                    _build_keywords_payload(analysis_result),
                    analysis_result.get('ai_model', ''),
                    datetime.now()
                )
                
                db.execute_insert(analysis_query, analysis_params)
                logger.info(f"重试分析成功，邮件ID: {email_id}")

                # 标记邮件为已处理（否则列表仍显示“未处理”）
                try:
                    db.execute_update(
                        "UPDATE emails SET is_processed = 1, processed_date = COALESCE(processed_date, CURRENT_TIMESTAMP) WHERE id = ? AND user_id = ?",
                        (email_id, user_id)
                    )
                except Exception as _e:
                    logger.warning(f"标记邮件已处理失败: email_id={email_id}, user_id={user_id}, err={_e}")
                
                # 删除旧的事件（如果有）
                delete_events_query = "DELETE FROM events WHERE email_id = ? AND user_id = ?"
                db.execute_update(delete_events_query, (email_id, user_id))
                
                # 如果有事件，添加到日程
                if analysis_result.get('events'):
                    for event in analysis_result['events']:
                        event['email_id'] = email_id
                        thread_scheduler_service.add_event(event, user_id)
                    logger.info(f"已更新 {len(analysis_result['events'])} 个事件到日程")
                
                # 归档到Notion
                thread_notion_service.archive_email(email_data, analysis_result)
                
                return {'success': True, 'email_subject': email_data.get('subject', 'Unknown'), 'email_id': email_id}
            else:
                logger.warning(f"重试分析仍然失败: {email_data.get('subject', 'Unknown')}")
                return {'success': False, 'error': '重试分析失败', 'email_subject': email_data.get('subject', 'Unknown')}
            
        except Exception as e:
            logger.error(f"重试邮件分析失败: {e}")
            return {'success': False, 'error': str(e), 'email_subject': email_data.get('subject', 'Unknown')}
    
    def _analyze_email_only(email_data, user_id, task_id: str = None):
        """仅进行AI分析的函数（多线程函数）"""
        try:
            if not user_id:
                raise ValueError("user_id is required for multi-user isolation")
            # 为每个线程创建独立的服务实例，并获取用户配置
            from .services.config_service import UserConfigService
            config_service = UserConfigService()
            
            # 创建临时配置对象，合并用户配置
            thread_config = Config()
            user_ai_config = config_service.get_ai_config(user_id)
            
            # 调试：记录用户配置读取结果
            logger.info(f"批量分析 - 用户ID: {user_id}, 读取到的用户AI配置: {user_ai_config}")
            logger.info(f"批量分析 - 默认AI配置: {thread_config._config.get('ai', {})}")
            
            if user_ai_config:
                # 临时覆盖AI配置（仅合并非空、非占位符字段）
                base_ai = thread_config._config.get('ai', {}) or {}
                cleaned_user_ai = {}
                for k, v in user_ai_config.items():
                    if v is None:
                        continue
                    if isinstance(v, str):
                        vv = v.strip()
                        if vv == '' or vv == '***':
                            continue
                    cleaned_user_ai[k] = v
                merged_config = {**base_ai, **cleaned_user_ai}
                thread_config._config['ai'] = merged_config
                logger.info(f"批量分析 - 合并后的AI配置: {merged_config}")
            else:
                logger.warning(f"批量分析 - 用户ID {user_id} 没有AI配置，使用默认配置")
            
            thread_ai_service = AIService(thread_config)
            thread_scheduler_service = SchedulerService(config)
            thread_notion_service = NotionService(config, user_id)
            
            email_id = email_data['id']
            logger.info(f"开始AI分析邮件，ID: {email_id}, 主题: {email_data.get('subject', 'Unknown')}")
            
            # 调试：记录实际使用的AI配置
            logger.info(f"批量分析实际配置 - 模型: {thread_ai_service.model}, 提供商: {thread_ai_service.provider}, API密钥前缀: {thread_ai_service.api_key[:10] if thread_ai_service.api_key else 'None'}..., 用户ID: {user_id}")
            logger.info(f"批量分析实际配置 - base_url: {thread_ai_service.base_url}")
            
            # AI分析邮件内容
            analysis_result = thread_ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject']
            )
            
            # 如果AI分析成功，保存分析结果
            if analysis_result:
                # 删除旧的分析结果（如果有）
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                
                delete_query = "DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?"
                db.execute_update(delete_query, (email_id, user_id))
                
                # 保存新的分析结果
                analysis_query = """
                INSERT INTO email_analysis 
                (user_id, email_id, summary, importance_score, importance_reason, 
                 events_json, keywords_matched, ai_model, analysis_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                
                # 处理events中的datetime对象（深度转换，防止遗漏）
                def _jsonify_dt(val):
                    if isinstance(val, dict):
                        return {k: _jsonify_dt(v) for k, v in val.items()}
                    if isinstance(val, list):
                        return [_jsonify_dt(v) for v in val]
                    try:
                        if hasattr(val, 'isoformat') and not isinstance(val, str):
                            return val.isoformat()
                    except Exception:
                        pass
                    return val
                events = analysis_result.get('events', [])
                serializable_events = _jsonify_dt(events)
                
                analysis_params = (
                    user_id,
                    email_id,
                    analysis_result.get('summary', ''),
                    analysis_result.get('importance_score', 5),
                    analysis_result.get('importance_reason', ''),
                    json.dumps(serializable_events, ensure_ascii=False),
                    _build_keywords_payload(analysis_result),
                    analysis_result.get('ai_model', ''),
                    datetime.now()
                )
                
                db.execute_insert(analysis_query, analysis_params)
                logger.info(f"AI分析结果已保存，邮件ID: {email_id}")

                # 标记邮件为已处理（否则列表仍显示“未处理”）
                try:
                    db.execute_update(
                        "UPDATE emails SET is_processed = 1, processed_date = COALESCE(processed_date, CURRENT_TIMESTAMP) WHERE id = ? AND user_id = ?",
                        (email_id, user_id)
                    )
                except Exception as _e:
                    logger.warning(f"标记邮件已处理失败: email_id={email_id}, user_id={user_id}, err={_e}")
                
                # 删除旧的事件（如果有）
                delete_events_query = "DELETE FROM events WHERE email_id = ? AND user_id = ?"
                db.execute_update(delete_events_query, (email_id, user_id))
                
                # 如果有事件，添加到日程
                if analysis_result.get('events'):
                    for event in analysis_result['events']:
                        event['email_id'] = email_id
                        thread_scheduler_service.add_event(event, user_id)
                    logger.info(f"已添加 {len(analysis_result['events'])} 个事件到日程")
                
                # 归档到Notion
                try:
                    thread_notion_service.archive_email(email_data, analysis_result)
                    # 进度：同步到Notion计数
                    if task_id and hasattr(api_check_email, '_progress'):
                        try:
                            with api_check_email._lock:
                                prog = api_check_email._progress.get(task_id)
                                if prog and 'synced' in prog:
                                    prog['synced'] += 1
                                    prog['status'] = 'syncing'
                                    api_check_email._progress[task_id] = prog
                        except Exception:
                            pass
                except Exception as _e:
                    logger.warning(f"归档到Notion失败: {str(_e)}")
                
                return {'success': True, 'email_subject': email_data.get('subject', 'Unknown'), 'email_id': email_id}
            else:
                logger.warning(f"AI分析失败: {email_data.get('subject', 'Unknown')}")
                # 标记失败记录，便于后续筛选"失败邮件"
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                try:
                    delete_query = "DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?"
                    db.execute_update(delete_query, (email_id, user_id))
                    analysis_query = (
                        "INSERT INTO email_analysis (user_id, email_id, summary, importance_score, importance_reason, events_json, keywords_matched, ai_model, analysis_date)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                    )
                    db.execute_insert(analysis_query, (
                        user_id,
                        email_id,
                        'AI分析失败',
                        5,
                        '',
                        json.dumps([], ensure_ascii=False),
                        json.dumps([], ensure_ascii=False),
                        '',
                        datetime.now()
                    ))
                except Exception as _e:
                    logger.warning(f"写入失败标记时出错: {_e}")
                return {'success': False, 'error': 'AI分析失败', 'email_subject': email_data.get('subject', 'Unknown')}
                
        except Exception as e:
            logger.error(f"AI分析邮件失败: {e}")
            # 发生异常时也写入失败标记
            try:
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                delete_query = "DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?"
                db.execute_update(delete_query, (email_id, user_id))
                analysis_query = (
                    "INSERT INTO email_analysis (user_id, email_id, summary, importance_score, importance_reason, events_json, keywords_matched, ai_model, analysis_date)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                )
                db.execute_insert(analysis_query, (
                    user_id,
                    email_id,
                    'AI分析失败',
                    5,
                    str(e),
                    json.dumps([], ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    '',
                    datetime.now()
                ))
            except Exception as _e:
                logger.warning(f"写入失败标记时出错: {_e}")
            return {'success': False, 'error': str(e), 'email_subject': email_data.get('subject', 'Unknown')}
    
    @app.route('/api/check_email', methods=['POST'])
    @login_required
    def api_check_email():
        """API: 手动检查邮件（后台任务 + 进度查询）"""
        try:
            # 简单的进程内任务进度存储
            if not hasattr(api_check_email, '_progress'):
                api_check_email._progress = {}
                api_check_email._lock = threading.Lock()

            user_id = AuthManager.get_current_user_id()

            # 读取参数（例如仅同步前N封）
            try:
                limit_n = (request.json or {}).get('max_count') if request and request.is_json else None
            except Exception:
                limit_n = None

            # 启动后台任务
            task_id = str(uuid4())
            task_cancel_event = threading.Event()
            with api_check_email._lock:
                api_check_email._progress[task_id] = {
                    'user_id': user_id,
                    'task_type': 'check_email',
                    'task_name': '检查新邮件',
                    'created_at': datetime.now().isoformat(),
                    'ended_at': None,
                    'error_summary': '',
                    'status': 'starting',
                    'new_count': 0,
                    'total': 0,
                    'analyzed': 0,
                    'failed': 0,
                    'saved': 0,
                    'synced': 0,
                    'message': '',
                    'cancel_requested': False,
                    'cancel_event': task_cancel_event,
                }

            def _job(uid: int, tid: str, max_count: int = None, cancel_event: threading.Event = None):
                try:
                    # 获取服务实例
                    local_email_service = EmailService(config)

                    # 使用新的流式处理：获取、保存、分析三步并行执行
                    with api_check_email._lock:
                        api_check_email._progress[tid]['status'] = 'fetching'
                    saved_count = 0
                    analyzed_count = 0
                    failed_count = 0
                    
                    was_cancelled = False
                    for result in local_email_service.fetch_and_process_emails_stream(uid, max_count=max_count, cancel_event=cancel_event):
                        if cancel_event is not None and cancel_event.is_set():
                            was_cancelled = True
                            break
                        try:
                            if result.get('status') == 'cancelled':
                                was_cancelled = True
                                break
                            if result['status'] == 'saved':
                                saved_count += 1
                                logger.info(f"邮件已保存: {result['subject']}")
                                with api_check_email._lock:
                                    api_check_email._progress[tid]['saved'] = saved_count
                                    api_check_email._progress[tid]['new_count'] = saved_count
                            elif result['status'] == 'analyzed':
                                analyzed_count += 1
                                logger.info(f"邮件分析完成: {result['subject']}")
                                with api_check_email._lock:
                                    api_check_email._progress[tid]['analyzed'] = analyzed_count
                            elif result['status'] == 'error':
                                failed_count += 1
                                logger.error(f"处理失败: {result.get('message', '未知错误')}")
                                with api_check_email._lock:
                                    api_check_email._progress[tid]['failed'] = failed_count
                        except Exception as e:
                            logger.error(f"处理流式结果失败: {e}")
                            failed_count += 1
                    if was_cancelled:
                        with api_check_email._lock:
                            api_check_email._progress[tid]['status'] = 'cancelled'
                            api_check_email._progress[tid]['ended_at'] = datetime.now().isoformat()
                            api_check_email._progress[tid]['error_summary'] = ''
                            api_check_email._progress[tid]['message'] = '任务已取消'
                        return
                    
                    # 为了兼容性，创建emails_to_analyze列表（虽然已经处理过了）
                    emails_to_analyze = []

                    # 兼容：若没有新流，仍获取未分析/失败的旧邮件
                    from .models.database import DatabaseManager
                    db = DatabaseManager(config)
                    unanalyzed_query = """
                    SELECT e.* FROM emails e
                    LEFT JOIN email_analysis ea ON e.id = ea.email_id AND ea.user_id = e.user_id
                    WHERE (ea.email_id IS NULL OR ea.summary IN ('AI分析失败', '邮件内容分析失败', ''))
                      AND e.user_id = ?
                    ORDER BY e.received_date DESC
                    LIMIT 100
                    """
                    if not emails_to_analyze:
                        rows = db.execute_query(unanalyzed_query, (uid,))
                        for row in rows:
                            emails_to_analyze.append({
                                'id': row['id'],
                                'message_id': row['message_id'],
                                'subject': row['subject'],
                                'sender': row['sender'],
                                'content': row['content'],
                                'received_date': row['received_date']
                            })

                    with api_check_email._lock:
                        api_check_email._progress[tid]['total'] = len(emails_to_analyze)
                        api_check_email._progress[tid]['status'] = 'analyzing'

                    analyzed_count = 0
                    failed_count = 0

                    if emails_to_analyze:
                        # AI分析线程池大小与超时从配置读取（默认：线程<=3，超时30秒）
                        try:
                            ai_cfg = config._config.get('ai', {}) or {}
                            analysis_timeout = int(ai_cfg.get('analysis_timeout_seconds', 30))
                        except Exception:
                            analysis_timeout = 30
                        max_workers = min(3, len(emails_to_analyze))
                        try:
                            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                                futures = []
                                for email in emails_to_analyze:
                                    if cancel_event is not None and cancel_event.is_set():
                                        break
                                    futures.append((executor.submit(_analyze_email_only, email, uid, tid), email))
                                for future, email_data in futures:
                                    if cancel_event is not None and cancel_event.is_set():
                                        for fut, _ in futures:
                                            try:
                                                fut.cancel()
                                            except Exception:
                                                pass
                                        with api_check_email._lock:
                                            api_check_email._progress[tid]['status'] = 'cancelled'
                                            api_check_email._progress[tid]['ended_at'] = datetime.now().isoformat()
                                            api_check_email._progress[tid]['error_summary'] = ''
                                            api_check_email._progress[tid]['message'] = '任务已取消'
                                        return
                                    try:
                                        result = future.result(timeout=analysis_timeout)
                                        if result['success']:
                                            analyzed_count += 1
                                        else:
                                            failed_count += 1
                                    except Exception as e:
                                        logger.error(f"AI分析邮件失败: {email_data.get('subject', 'Unknown')}, 错误: {e}")
                                        failed_count += 1
                                    finally:
                                        with api_check_email._lock:
                                            api_check_email._progress[tid]['analyzed'] = analyzed_count
                                            api_check_email._progress[tid]['failed'] = failed_count
                        except RuntimeError as e:
                            logger.warning(f"线程执行异常: {e}")
                            failed_count = len(emails_to_analyze)

                    # 完成
                    try:
                        clear_email_cache()
                    except Exception:
                        pass
                    with api_check_email._lock:
                        api_check_email._progress[tid]['status'] = 'done'
                        api_check_email._progress[tid]['ended_at'] = datetime.now().isoformat()
                        api_check_email._progress[tid]['error_summary'] = ''
                        # 若无新邮件，明确提示
                        if (api_check_email._progress[tid]['new_count'] or 0) == 0:
                            api_check_email._progress[tid]['message'] = '未发现新邮件'
                        else:
                            api_check_email._progress[tid]['message'] = (
                                f"处理完成: {api_check_email._progress[tid]['new_count']} 封新邮件获取, {analyzed_count} 封AI分析成功"
                                + (f", {failed_count} 封分析失败" if failed_count > 0 else '')
                            )
                    # 任务进度自动清理（延迟清除，避免前端拉取race）
                    try:
                        def _cleanup(task_key):
                            import time as _t
                            _t.sleep(300)
                            with api_check_email._lock:
                                api_check_email._progress.pop(task_key, None)
                        Thread(target=_cleanup, args=(tid,), daemon=True).start()
                    except Exception:
                        pass
                except Exception as e:
                    logger.error(f"检查邮件后台任务错误: {e}")
                    with api_check_email._lock:
                        api_check_email._progress[tid]['status'] = 'error'
                        api_check_email._progress[tid]['message'] = str(e)
                        api_check_email._progress[tid]['ended_at'] = datetime.now().isoformat()
                        api_check_email._progress[tid]['error_summary'] = str(e)[:500]
                    # 出错也安排清理
                    try:
                        def _cleanup_err(task_key):
                            import time as _t
                            _t.sleep(300)
                            with api_check_email._lock:
                                api_check_email._progress.pop(task_key, None)
                        Thread(target=_cleanup_err, args=(tid,), daemon=True).start()
                    except Exception:
                        pass

            Thread(target=_job, args=(user_id, task_id, limit_n, task_cancel_event), daemon=True).start()
            return jsonify({'success': True, 'task_id': task_id})

        except Exception as e:
            logger.error(f"检查邮件API错误: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tasks/<task_id>/progress')
    @login_required
    def api_task_progress(task_id):
        try:
            user_id = AuthManager.get_current_user_id()
            if not hasattr(api_check_email, '_progress'):
                return jsonify({'success': False, 'error': '任务不存在'}), 404
            with api_check_email._lock:
                prog = api_check_email._progress.get(task_id)
            if not prog:
                return jsonify({'success': False, 'error': '任务不存在'}), 404
            if int(prog.get('user_id') or -1) != int(user_id):
                return jsonify({'success': False, 'error': '任务不存在'}), 404

            # 对外返回时隐藏服务端内部字段
            progress = dict(prog)
            progress.pop('user_id', None)
            progress.pop('cancel_event', None)
            return jsonify({'success': True, 'task_id': task_id, 'progress': progress})
        except Exception as e:
            logger.error(f"获取任务进度失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tasks/<task_id>/stop', methods=['POST'])
    @login_required
    def api_stop_task(task_id):
        """API: 终止任务（支持流式任务与普通后台任务）"""
        try:
            user_id = AuthManager.get_current_user_id()

            # 流式任务：task_id 形如 stream:<user_id>
            if str(task_id).startswith('stream:'):
                from .services.stream_manager import stream_manager
                result = stream_manager.stop(user_id)
                return jsonify({'success': True, **result})

            if not hasattr(api_check_email, '_progress'):
                return jsonify({'success': False, 'error': '任务不存在'}), 404

            with api_check_email._lock:
                prog = api_check_email._progress.get(task_id)
                if not prog:
                    return jsonify({'success': False, 'error': '任务不存在'}), 404
                if int(prog.get('user_id') or -1) != int(user_id):
                    return jsonify({'success': False, 'error': '任务不存在'}), 404

                status = str(prog.get('status') or '')
                if status in ('done', 'error', 'cancelled'):
                    return jsonify({'success': True, 'stopped': False, 'message': '任务已结束'})

                evt = prog.get('cancel_event')
                if evt is not None and hasattr(evt, 'set'):
                    evt.set()
                prog['cancel_requested'] = True
                prog['status'] = 'canceling'
                prog['message'] = '已收到终止请求，正在停止...'
                api_check_email._progress[task_id] = prog

            return jsonify({'success': True, 'stopped': True, 'message': '已发送终止请求'})
        except Exception as e:
            logger.error(f"终止任务失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tasks/active', methods=['GET'])
    @login_required
    def api_active_tasks():
        """API: 获取当前用户正在执行中的任务列表（用于全局悬浮任务面板）"""
        try:
            user_id = AuthManager.get_current_user_id()

            active_statuses = {'starting', 'fetching', 'saving', 'analyzing', 'syncing', 'canceling'}
            recent_terminal_statuses = {'done', 'error', 'cancelled'}
            keep_recent_seconds = 30

            def _calc_percent(item: dict) -> int:
                status = str(item.get('status') or '')
                total = int(item.get('total') or 0)
                saved = int(item.get('saved') or 0)
                analyzed = int(item.get('analyzed') or 0)
                synced = int(item.get('synced') or 0)
                new_count = int(item.get('new_count') or 0)

                if status in ('starting',):
                    return 5
                if status in ('fetching',):
                    return 15
                if status in ('saving',):
                    base, span = 15, 20
                    denom = max(1, new_count)
                    return min(100, base + int((saved / denom) * span))
                if status in ('analyzing',):
                    base, span = 35, 55
                    denom = max(1, total)
                    return min(100, base + int((analyzed / denom) * span))
                if status in ('syncing',):
                    base, span = 90, 10
                    denom = max(1, total)
                    return min(100, base + int((synced / denom) * span))
                if status in ('done',):
                    return 100
                return 0

            tasks = []
            now_dt = datetime.now()
            if hasattr(api_check_email, '_progress'):
                with api_check_email._lock:
                    for tid, prog in api_check_email._progress.items():
                        if int(prog.get('user_id') or -1) != int(user_id):
                            continue
                        status = str(prog.get('status') or '')
                        if status not in active_statuses and status not in recent_terminal_statuses:
                            continue
                        if status in recent_terminal_statuses:
                            ended_at = prog.get('ended_at')
                            if not ended_at:
                                continue
                            try:
                                ended_dt = datetime.fromisoformat(str(ended_at))
                            except Exception:
                                continue
                            if (now_dt - ended_dt).total_seconds() > keep_recent_seconds:
                                continue
                        tasks.append({
                            'task_id': tid,
                            'task_type': prog.get('task_type', ''),
                            'task_name': prog.get('task_name', '后台任务'),
                            'created_at': prog.get('created_at'),
                            'status': status,
                            'percent': _calc_percent(prog),
                            'saved': int(prog.get('saved') or 0),
                            'new_count': int(prog.get('new_count') or 0),
                            'analyzed': int(prog.get('analyzed') or 0),
                            'failed': int(prog.get('failed') or 0),
                            'synced': int(prog.get('synced') or 0),
                            'total': int(prog.get('total') or 0),
                            'message': prog.get('message') or '',
                            'error_summary': prog.get('error_summary') or '',
                            'ended_at': prog.get('ended_at'),
                            'can_stop': status in active_statuses and status != 'canceling',
                        })

            # 合并流式处理任务（stream_manager）
            try:
                from .services.stream_manager import stream_manager
                snap = stream_manager.get_task_snapshot(user_id)
                started_at = None
                ended_at = None
                if snap.get('started_at'):
                    started_at = datetime.fromtimestamp(float(snap['started_at'])).isoformat()
                if snap.get('ended_at'):
                    ended_at = datetime.fromtimestamp(float(snap['ended_at'])).isoformat()

                running = bool(snap.get('running'))
                status = str(snap.get('task_status') or '')
                if status not in active_statuses and status not in recent_terminal_statuses:
                    status = 'fetching' if running else 'done'
                include_stream = False
                if running:
                    include_stream = True
                elif ended_at:
                    try:
                        ended_dt = datetime.fromisoformat(str(ended_at))
                        include_stream = (now_dt - ended_dt).total_seconds() <= keep_recent_seconds
                    except Exception:
                        include_stream = False

                if include_stream:
                    tasks.append({
                        'task_id': f"stream:{user_id}",
                        'task_type': 'stream_fetch',
                        'task_name': '流式处理邮件',
                        'created_at': started_at,
                        'status': status,
                        'percent': int(snap.get('percent') or 0),
                        'saved': int(snap.get('saved') or 0),
                        'new_count': int(snap.get('new_count') or 0),
                        'analyzed': int(snap.get('analyzed') or 0),
                        'failed': int(snap.get('failed') or 0),
                        'synced': 0,
                        'total': int(snap.get('total') or 0),
                        'message': snap.get('message') or '',
                        'error_summary': snap.get('message') if status == 'error' else '',
                        'ended_at': ended_at,
                        'can_stop': bool(running),
                    })
            except Exception as _e:
                logger.warning(f"读取流式任务快照失败: {_e}")

            # 自动同步任务（仅展示状态/倒计时，不展示明细数量）
            try:
                from .services.config_service import UserConfigService
                from .models.database import DatabaseManager
                cfg_svc = UserConfigService()
                email_cfg = cfg_svc.get_email_config(user_id) or {}
                auto_fetch_enabled = bool(email_cfg.get('auto_fetch', True))
                if auto_fetch_enabled:
                    try:
                        fetch_interval = int(email_cfg.get('fetch_interval', 1800))
                    except Exception:
                        fetch_interval = 1800
                    if fetch_interval < 60:
                        fetch_interval = 60

                    # 直接读共享数据库锁表，避免跨进程单例状态不一致
                    is_auto_running = False
                    try:
                        db = DatabaseManager(config)
                        rows = db.execute_query(
                            "SELECT task_type, timestamp FROM task_locks WHERE user_id = ?",
                            (int(user_id),),
                        )
                        if rows:
                            lock_type = str(rows[0].get('task_type') or '')
                            lock_ts = float(rows[0].get('timestamp') or 0)
                            # 锁在最近 5 分钟内活跃，视为运行中
                            is_auto_running = (lock_type == 'auto') and ((now_dt.timestamp() - lock_ts) <= 300)
                    except Exception:
                        is_auto_running = False

                    last_fetch_at = cfg_svc.get_user_config(user_id, 'email', 'last_fetch_at', None)
                    worker_heartbeat_at = cfg_svc.get_user_config(user_id, 'email', 'worker_heartbeat_at', None)
                    created_at = None
                    remaining_seconds = None
                    worker_alive = False
                    if last_fetch_at:
                        try:
                            last_dt = datetime.fromisoformat(str(last_fetch_at))
                            created_at = last_dt.isoformat()
                            next_dt = last_dt + timedelta(seconds=fetch_interval)
                            remaining_seconds = max(0, int((next_dt - now_dt).total_seconds()))
                        except Exception:
                            remaining_seconds = None
                    if worker_heartbeat_at:
                        try:
                            hb_dt = datetime.fromisoformat(str(worker_heartbeat_at))
                            # 最近 2 个轮询周期内有心跳，视为 worker 仍在运行
                            worker_alive = ((now_dt - hb_dt).total_seconds() <= max(180, fetch_interval * 2))
                        except Exception:
                            worker_alive = False

                    def _fmt_secs(seconds: int) -> str:
                        s = max(0, int(seconds or 0))
                        h = s // 3600
                        m = (s % 3600) // 60
                        sec = s % 60
                        if h > 0:
                            return f"{h:02d}:{m:02d}:{sec:02d}"
                        return f"{m:02d}:{sec:02d}"

                    if is_auto_running:
                        status = 'auto_syncing'
                        message = '正在自动同步'
                        percent = 10
                    else:
                        status = 'auto_waiting'
                        if remaining_seconds is None:
                            if worker_alive:
                                message = '等待首次自动同步'
                            else:
                                message = '等待首次自动同步（请确认已启动 scheduler/worker）'
                            percent = 0
                        elif int(remaining_seconds) <= 0:
                            if worker_alive:
                                message = '已到自动同步时间，等待 worker 执行'
                            else:
                                message = '已到自动同步时间，但未检测到 worker（请启动 scheduler/worker）'
                            percent = 100
                        else:
                            message = f"距离下一次自动同步：{_fmt_secs(remaining_seconds)}"
                            elapsed = max(0, fetch_interval - remaining_seconds)
                            percent = min(100, int((elapsed / max(1, fetch_interval)) * 100))

                    tasks.append({
                        'task_id': f'auto:{user_id}',
                        'task_type': 'auto_sync',
                        'task_name': '自动同步邮件',
                        'created_at': created_at,
                        'status': status,
                        'percent': percent,
                        'saved': 0,
                        'new_count': 0,
                        'analyzed': 0,
                        'failed': 0,
                        'synced': 0,
                        'total': 0,
                        'message': message,
                        'remaining_seconds': remaining_seconds,
                        'error_summary': '',
                        'ended_at': None,
                        'can_stop': False,
                    })
            except Exception as _e:
                logger.warning(f"读取自动同步任务状态失败: {_e}")

            tasks.sort(key=lambda x: x.get('created_at') or '', reverse=True)
            return jsonify({'success': True, 'tasks': tasks})
        except Exception as e:
            logger.error(f"获取活跃任务失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    def _get_user_config_revision(user_id, config_type=None):
        """读取用户配置版本（用于多端并发保存冲突检测）。"""
        from .models.database import DatabaseManager
        db = DatabaseManager(config)
        if config_type:
            row = db.execute_query(
                """
                SELECT COALESCE(MAX(updated_at), '') AS rev
                FROM user_configs
                WHERE user_id = ? AND config_type = ?
                """,
                (user_id, config_type),
            )
        else:
            row = db.execute_query(
                """
                SELECT COALESCE(MAX(updated_at), '') AS rev
                FROM user_configs
                WHERE user_id = ?
                """,
                (user_id,),
            )
        return (row[0].get('rev') if row else '') or ''

    @app.route('/api/config', methods=['GET', 'POST'])
    @login_required
    def api_config():
        """API: 配置管理（用户级）"""
        user_id = AuthManager.get_current_user_id()
        
        if request.method == 'GET':
            # 获取用户配置
            from .services.config_service import UserConfigService
            config_service = UserConfigService()
            
            user_config = {
                'email': config_service.get_email_config(user_id),
                'ai': config_service.get_ai_config(user_id),
                'notification': config_service.get_notification_config(user_id),
                'notion': config_service.get_notion_config(user_id),
                'keywords': config_service.get_keywords_config(user_id),
                'reminder': config_service.get_reminder_config(user_id),
                'dedup_beta': config_service.get_dedup_beta_config(user_id)
            }
            
            user_config['_meta'] = {
                'revision': _get_user_config_revision(user_id)
            }
            return jsonify(user_config)
        
        elif request.method == 'POST':
            try:
                from .services.config_service import UserConfigService
                config_service = UserConfigService()
                
                new_config = request.get_json() or {}
                client_revision = str(new_config.get('_base_revision') or '').strip()
                force_save = bool(new_config.get('_force', False))
                touched_sections = [
                    k for k in ('email', 'ai', 'notification', 'notion', 'keywords', 'reminder', 'dedup_beta')
                    if k in new_config
                ]
                global_revision = _get_user_config_revision(user_id)
                if len(touched_sections) == 1:
                    # 单 section 保存时，按 section 自身 revision 做冲突检测，避免被其他配置变更误伤。
                    server_revision = _get_user_config_revision(user_id, touched_sections[0])
                else:
                    server_revision = global_revision
                revision_candidates = {server_revision, global_revision}
                if client_revision and client_revision not in revision_candidates and not force_save:
                    return jsonify({
                        'success': False,
                        'error': '配置已在其他端被修改，请先刷新后再保存',
                        'conflict': True,
                        'current_revision': server_revision,
                    }), 409
                success = True
                
                # 更新各类配置
                if 'email' in new_config:
                    if not config_service.set_email_config(user_id, new_config['email']):
                        success = False
                
                if 'ai' in new_config:
                    if not config_service.set_ai_config(user_id, new_config['ai']):
                        success = False
                
                if 'notification' in new_config:
                    if not config_service.set_notification_config(user_id, new_config['notification']):
                        success = False
                
                if 'notion' in new_config:
                    if not config_service.set_notion_config(user_id, new_config['notion']):
                        success = False
                
                if 'keywords' in new_config:
                    if not config_service.set_keywords_config(user_id, new_config['keywords']):
                        success = False
                
                if 'reminder' in new_config:
                    if not config_service.set_reminder_config(user_id, new_config['reminder']):
                        success = False

                if 'dedup_beta' in new_config:
                    if not config_service.set_dedup_beta_config(user_id, new_config['dedup_beta']):
                        success = False
                
                if success:
                    return jsonify({
                        'success': True,
                        'message': '配置更新成功',
                        'revision': _get_user_config_revision(user_id),
                    })
                else:
                    return jsonify({
                        'success': False,
                        'error': '部分配置更新失败'
                    }), 500
                    
            except Exception as e:
                logger.error(f"更新配置失败: {e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500
    
    @app.route('/api/keywords', methods=['GET', 'POST'])
    @login_required
    def api_keywords():
        """API: 关键词管理"""
        if request.method == 'GET':
            return jsonify(config.get_keywords())
        
        elif request.method == 'POST':
            try:
                keywords_data = request.get_json()
                config.update_keywords(keywords_data)
                return jsonify({
                    'success': True,
                    'message': '关键词更新成功'
                })
            except Exception as e:
                logger.error(f"更新关键词失败: {e}")
                return jsonify({
                    'success': False,
                    'error': str(e)
                }), 500

    @app.route('/api/mobile/fcm-token', methods=['GET', 'POST'])
    @login_required
    def api_mobile_fcm_token():
        """API: 保存/读取当前用户移动端 FCM Token。"""
        try:
            user_id = AuthManager.get_current_user_id()
            from .services.config_service import UserConfigService
            cfg = UserConfigService()

            if request.method == 'GET':
                token = cfg.get_user_config(user_id, 'notification', 'mobile_fcm_token', '')
                platform = cfg.get_user_config(user_id, 'notification', 'mobile_fcm_platform', '')
                return jsonify({
                    'success': True,
                    'token': token or '',
                    'platform': platform or '',
                })

            data = request.get_json() or {}
            token = str(data.get('token') or '').strip()
            platform = str(data.get('platform') or '').strip().lower()
            if not token:
                return jsonify({'success': False, 'error': 'token不能为空'}), 400
            if platform not in ('android', 'ios', 'unknown', ''):
                platform = 'unknown'

            ok1 = cfg.set_user_config(user_id, 'notification', 'mobile_fcm_token', token)
            ok2 = cfg.set_user_config(user_id, 'notification', 'mobile_fcm_platform', platform or 'unknown')
            if not (ok1 and ok2):
                return jsonify({'success': False, 'error': '保存FCM Token失败'}), 500
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"保存FCM Token失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/mobile/push-token', methods=['POST'])
    @login_required
    def api_mobile_push_token():
        """API: 保存移动端推送标识（FCM token / Getui clientId）。"""
        try:
            user_id = AuthManager.get_current_user_id()
            from .services.config_service import UserConfigService
            cfg = UserConfigService()

            data = request.get_json() or {}
            provider = str(data.get('provider') or '').strip().lower()
            token = str(data.get('token') or '').strip()
            platform = str(data.get('platform') or 'unknown').strip().lower()
            if provider not in ('fcm', 'getui'):
                return jsonify({'success': False, 'error': 'provider仅支持 fcm/getui'}), 400
            if not token:
                return jsonify({'success': False, 'error': 'token不能为空'}), 400
            if platform not in ('android', 'ios', 'unknown', ''):
                platform = 'unknown'

            if provider == 'fcm':
                ok1 = cfg.set_user_config(user_id, 'notification', 'mobile_fcm_token', token)
                ok2 = cfg.set_user_config(user_id, 'notification', 'mobile_fcm_platform', platform or 'unknown')
                if not (ok1 and ok2):
                    return jsonify({'success': False, 'error': '保存FCM Token失败'}), 500
                return jsonify({'success': True, 'provider': 'fcm'})

            ok1 = cfg.set_user_config(user_id, 'notification', 'mobile_getui_client_id', token)
            ok2 = cfg.set_user_config(user_id, 'notification', 'mobile_getui_platform', platform or 'unknown')
            if not (ok1 and ok2):
                return jsonify({'success': False, 'error': '保存Getui ClientID失败'}), 500
            return jsonify({'success': True, 'provider': 'getui'})
        except Exception as e:
            logger.error(f"保存移动推送标识失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags', methods=['GET', 'POST'])
    @login_required
    def api_tags():
        """API: 标签与订阅配置（用户级）"""
        try:
            user_id = AuthManager.get_current_user_id()

            if request.method == 'GET':
                settings = tag_service.get_user_tag_settings(user_id)
                # 配置页不再使用历史分析自动填入候选，避免“自动填充”干扰手工维护
                existing = tag_service.get_existing_tag_candidates(user_id, include_history=False)
                return jsonify({
                    'success': True,
                    '_meta': {
                        'revision': _get_user_config_revision(user_id, 'tags')
                    },
                    'level2_fixed': TagService.LEVEL2_FIXED,
                    'library': settings.get('library', {}),
                    'subscriptions': settings.get('subscriptions', []),
                    'history_manual': settings.get('history_manual', {}),
                    'history_retention_days': int(settings.get('history_retention_days') or 30),
                    'existing': existing,
                })

            data = request.get_json() or {}
            client_revision = str(data.get('_base_revision') or '').strip()
            force_save = bool(data.get('_force', False))
            server_revision = _get_user_config_revision(user_id, 'tags')
            if client_revision and client_revision != server_revision and not force_save:
                return jsonify({
                    'success': False,
                    'error': '标签设置已在其他端被修改，请先刷新后再保存',
                    'conflict': True,
                    'current_revision': server_revision,
                }), 409
            library = data.get('library') or {}
            subscriptions = data.get('subscriptions') or []
            history_retention_days = data.get('history_retention_days', None)
            history_manual = data.get('history_manual') or {}

            # 仅允许编辑/订阅 2~4 级标签，一级固定重要程度
            clean_library = {
                'level3': [str(x).strip() for x in (library.get('level3') or []) if str(x).strip()],
                'level4': [str(x).strip() for x in (library.get('level4') or []) if str(x).strip()],
                'other_level2': [str(x).strip() for x in (library.get('other_level2') or []) if str(x).strip()],
            }

            clean_subs = []
            for s in subscriptions:
                if isinstance(s, dict):
                    lv = int(s.get('level', 0) or 0)
                    val = str(s.get('value') or '').strip()
                else:
                    # 兼容简写：默认三级
                    lv = 3
                    val = str(s or '').strip()
                if lv not in (2, 3, 4) or not val:
                    continue
                clean_subs.append({'level': lv, 'value': val[:128]})

            ok = tag_service.set_user_tag_settings(user_id, clean_library, clean_subs, history_retention_days=history_retention_days)
            # 可选：一次性更新手工历史标签（新UI会用单独接口，这里保留兼容）
            if ok and isinstance(history_manual, dict):
                for _lv, _key in ((3, 'level3'), (4, 'level4'), (2, 'other_level2')):
                    for _v in (history_manual.get(_key) or []):
                        tag_service.add_manual_history_candidate(user_id, _lv, str(_v))
            return jsonify({
                'success': bool(ok),
                'revision': _get_user_config_revision(user_id, 'tags'),
            })
        except Exception as e:
            logger.error(f"标签配置接口失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags/reapply-subscriptions', methods=['POST'])
    @login_required
    def api_reapply_tag_subscriptions():
        """API: 将当前标签订阅规则重新应用到历史事件。"""
        try:
            user_id = AuthManager.get_current_user_id()
            stats = tag_service.apply_subscriptions_to_events(user_id, include_revert=True)
            return jsonify({
                'success': True,
                'stats': stats,
                'message': (
                    f"已应用订阅规则：共 {stats.get('total',0)} 条事件，"
                    f"升级 {stats.get('upgraded',0)} 条，回退 {stats.get('reverted',0)} 条"
                )
            })
        except Exception as e:
            logger.error(f"重算订阅标签事件失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags/subscribe', methods=['POST'])
    @login_required
    def api_subscribe_single_tag():
        """API: 订阅单个标签（便于在邮件列表快速订阅）"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            level = int(data.get('level') or 0)
            value = str(data.get('value') or '').strip()
            apply_now = bool(data.get('apply_now', True))

            if level not in (2, 3, 4):
                return jsonify({'success': False, 'error': '仅支持订阅二/三/四级标签'}), 400
            if not value:
                return jsonify({'success': False, 'error': '标签值不能为空'}), 400

            # 规范化 level2 文本
            if level == 2:
                normalized = TagService.normalize_tags({'level2': value}, 5)
                if normalized.get('level2') != '其他':
                    value = normalized.get('level2') or value
                else:
                    c = normalized.get('level2_custom') or ''
                    value = f"其他[{c}]" if c else "其他"

            if level == 3:
                value = value[:64]
            if level == 4:
                value = value[:128]

            settings = tag_service.get_user_tag_settings(user_id)
            library = settings.get('library') or {}
            subscriptions = settings.get('subscriptions') or []

            exists = any(
                isinstance(s, dict) and int(s.get('level', 0) or 0) == level and str(s.get('value') or '').strip() == value
                for s in subscriptions
            )
            if not exists:
                subscriptions.append({'level': level, 'value': value})

            ok = tag_service.set_user_tag_settings(user_id, library, subscriptions)
            if not ok:
                return jsonify({'success': False, 'error': '保存订阅失败'}), 500

            resp = {
                'success': True,
                'already_exists': exists,
                'subscription': {'level': level, 'value': value},
                'message': '该标签已在订阅列表中' if exists else '订阅成功',
            }
            if apply_now:
                stats = tag_service.apply_subscriptions_to_events(user_id, include_revert=False)
                resp['apply_stats'] = stats
            return jsonify(resp)
        except Exception as e:
            logger.error(f"订阅单标签失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags/unsubscribe', methods=['POST'])
    @login_required
    def api_unsubscribe_single_tag():
        """API: 取消订阅单个标签"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            level = int(data.get('level') or 0)
            value = str(data.get('value') or '').strip()
            apply_now = bool(data.get('apply_now', True))
            if level not in (2, 3, 4) or not value:
                return jsonify({'success': False, 'error': '参数错误'}), 400
            settings = tag_service.get_user_tag_settings(user_id)
            library = settings.get('library') or {}
            subscriptions = settings.get('subscriptions') or []
            new_subs = [
                s for s in subscriptions
                if not (
                    isinstance(s, dict)
                    and int(s.get('level', 0) or 0) == level
                    and str(s.get('value') or '').strip() == value
                )
            ]
            ok = tag_service.set_user_tag_settings(
                user_id,
                library,
                new_subs,
                history_retention_days=int(settings.get('history_retention_days') or 30),
            )
            if not ok:
                return jsonify({'success': False, 'error': '取消订阅失败'}), 500
            resp = {'success': True, 'message': '已取消订阅'}
            if apply_now:
                resp['apply_stats'] = tag_service.apply_subscriptions_to_events(user_id, include_revert=True)
            return jsonify(resp)
        except Exception as e:
            logger.error(f"取消订阅失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags/history-candidates', methods=['GET'])
    @login_required
    def api_tag_history_candidates():
        """API: 获取历史候选标签（历史+手工）。"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = tag_service.get_history_tag_candidates(user_id)
            settings = tag_service.get_user_tag_settings(user_id)
            return jsonify({
                'success': True,
                'candidates': data,
                'history_retention_days': int(settings.get('history_retention_days') or 30),
            })
        except Exception as e:
            logger.error(f"获取历史候选标签失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags/history-candidates/add-to-library', methods=['POST'])
    @login_required
    def api_add_history_candidate_to_library():
        """API: 兼容旧接口，改为直接订阅该历史候选标签。"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            level = int(data.get('level') or 0)
            value = str(data.get('value') or '').strip()
            if level not in (2, 3, 4) or not value:
                return jsonify({'success': False, 'error': '参数错误'}), 400
            settings = tag_service.get_user_tag_settings(user_id)
            library = settings.get('library') or {}
            subscriptions = settings.get('subscriptions') or []
            exists = any(
                isinstance(s, dict) and int(s.get('level', 0) or 0) == level and str(s.get('value') or '').strip() == value
                for s in subscriptions
            )
            if not exists:
                subscriptions.append({'level': level, 'value': value})
            ok = tag_service.set_user_tag_settings(
                user_id,
                library,
                subscriptions,
                history_retention_days=int(settings.get('history_retention_days') or 30),
            )
            return jsonify({'success': bool(ok), 'already_exists': exists, 'message': '订阅成功' if ok else '订阅失败'})
        except Exception as e:
            logger.error(f"历史候选订阅失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags/history-candidates/add-manual', methods=['POST'])
    @login_required
    def api_add_manual_history_candidate():
        """API: 手工添加历史标签（不受N天清理影响）。"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            level = int(data.get('level') or 0)
            value = str(data.get('value') or '').strip()
            if level not in (2, 3, 4) or not value:
                return jsonify({'success': False, 'error': '参数错误'}), 400
            ok = tag_service.add_manual_history_candidate(user_id, level, value)
            return jsonify({'success': bool(ok)})
        except Exception as e:
            logger.error(f"手工添加历史标签失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tags/history-candidates/delete', methods=['POST'])
    @login_required
    def api_delete_history_candidate():
        """API: 删除（隐藏）单条历史候选标签。"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            level = int(data.get('level') or 0)
            value = str(data.get('value') or '').strip()
            manual = bool(data.get('manual', False))
            if level not in (2, 3, 4) or not value:
                return jsonify({'success': False, 'error': '参数错误'}), 400
            ok = tag_service.remove_history_candidate(user_id, level, value, manual=manual)
            return jsonify({'success': bool(ok)})
        except Exception as e:
            logger.error(f"删除历史候选标签失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/email/<int:email_id>')
    @login_required
    def api_get_email(email_id):
        """API: 获取邮件详情"""
        try:
            # 获取当前用户ID
            user_id = AuthManager.get_current_user_id()
            email_data = email_service.get_email_by_id(email_id, user_id)
            if email_data:
                # 返回时包含html内容，供前端展示富文本
                return jsonify(email_data)
            else:
                return jsonify({'error': '邮件不存在'}), 404
        except Exception as e:
            logger.error(f"获取邮件详情失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/email/<int:email_id>/reanalyze', methods=['POST'])
    @login_required
    def api_reanalyze_email(email_id):
        """API: 重新分析单个邮件"""
        try:
            # 获取当前用户ID
            user_id = AuthManager.get_current_user_id()
            # 可选：清理该邮件关联事件
            try:
                from .models.database import DatabaseManager
                db_tmp = DatabaseManager(config)
                db_tmp.execute_update("DELETE FROM events WHERE email_id = ? AND user_id = ?", (email_id, user_id))
                db_tmp.execute_update("DELETE FROM reminders WHERE user_id = ? AND event_id NOT IN (SELECT id FROM events)", (user_id,))
            except Exception:
                pass
            # 获取邮件数据
            email_data = email_service.get_email_by_id(email_id, user_id)
            if not email_data:
                return jsonify({'error': '邮件不存在'}), 404
            
            # 重新进行AI分析（传递用户ID以使用用户配置）
            analysis_result = ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject'],
                user_id=user_id
            )
            
            # 更新分析结果
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 删除旧的分析结果
            delete_query = "DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?"
            db.execute_update(delete_query, (email_id, user_id))
            
            # 保存新的分析结果
            analysis_query = """
            INSERT INTO email_analysis 
            (user_id, email_id, summary, importance_score, importance_reason, 
             events_json, keywords_matched, ai_model, analysis_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            # 处理events中的datetime对象
            events = analysis_result.get('events', [])
            serializable_events = []
            for event in events:
                serializable_event = event.copy()
                # 将datetime对象转换为字符串
                if 'start_time' in serializable_event and serializable_event['start_time']:
                    if isinstance(serializable_event['start_time'], datetime):
                        serializable_event['start_time'] = serializable_event['start_time'].isoformat()
                if 'end_time' in serializable_event and serializable_event['end_time']:
                    if isinstance(serializable_event['end_time'], datetime):
                        serializable_event['end_time'] = serializable_event['end_time'].isoformat()
                if 'reminder_times' in serializable_event and serializable_event['reminder_times']:
                    reminder_times = []
                    for rt in serializable_event['reminder_times']:
                        if isinstance(rt, datetime):
                            reminder_times.append(rt.isoformat())
                        else:
                            reminder_times.append(rt)
                    serializable_event['reminder_times'] = reminder_times
                serializable_events.append(serializable_event)
            
            analysis_params = (
                user_id,
                email_id,
                analysis_result.get('summary', ''),
                analysis_result.get('importance_score', 5),
                analysis_result.get('importance_reason', ''),
                json.dumps(serializable_events, ensure_ascii=False),
                    _build_keywords_payload(analysis_result),
                analysis_result.get('ai_model', ''),
                datetime.now()
            )
            
            db.execute_insert(analysis_query, analysis_params)

            # 标记邮件为已处理（否则前端仍会显示“未处理”）
            try:
                db.execute_update(
                    "UPDATE emails SET is_processed = 1, processed_date = COALESCE(processed_date, CURRENT_TIMESTAMP) WHERE id = ? AND user_id = ?",
                    (email_id, user_id)
                )
            except Exception as _e:
                logger.warning(f"标记邮件已处理失败: email_id={email_id}, user_id={user_id}, err={_e}")
            
            # 保存事件到日程表
            if analysis_result.get('events'):
                scheduler_service = SchedulerService(config)
                for event in analysis_result['events']:
                    event['email_id'] = email_id
                    scheduler_service.add_event(event, user_id)
            
            # 清除列表缓存，确保前端首次刷新即可看到更新
            try:
                clear_email_cache()
            except Exception:
                pass
            
            # 为返回结果处理datetime对象
            return_result = analysis_result.copy()
            if 'events' in return_result:
                return_result['events'] = serializable_events
            
            return jsonify({
                'success': True,
                'message': '邮件重新分析完成',
                'analysis_result': return_result
            })
            
        except Exception as e:
            logger.error(f"重新分析邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/email/<int:email_id>/retry-analysis', methods=['POST'])
    @login_required
    def api_retry_email_analysis(email_id):
        """API: 重试邮件分析（增强调试模式）"""
        try:
            data = request.get_json() or {}
            debug_mode = data.get('debug', False)
            
            # 获取邮件信息
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            user_id = AuthManager.get_current_user_id()
            
            query = "SELECT * FROM emails WHERE id = ? AND user_id = ?"
            email_result = db.execute_query(query, (email_id, user_id))
            
            if not email_result:
                return jsonify({
                    'success': False,
                    'error': '邮件不存在'
                }), 404
            
            email_data = email_result[0]
            
            # 记录重试开始
            logger.info(f"=== 开始重试分析邮件 ID: {email_id} ===")
            logger.info(f"调试模式: {debug_mode}")
            logger.info(f"邮件主题: {email_data['subject']}")
            logger.info(f"邮件发件人: {email_data['sender']}")
            logger.info(f"邮件内容长度: {len(email_data['content'])} 字符")
            
            debug_info = {
                'email_id': email_id,
                'subject': email_data['subject'],
                'sender': email_data['sender'],
                'content_length': len(email_data['content']),
                'retry_time': datetime.now().isoformat()
            }
            
            # 重新进行AI分析
            analysis_result = ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject'],
                user_id=user_id
            )
            
            debug_info['analysis_success'] = analysis_result is not None
            debug_info['has_events'] = len(analysis_result.get('events', [])) if analysis_result else 0
            
            if not analysis_result:
                logger.error("重试分析失败：AI服务返回空结果")
                return jsonify({
                    'success': False,
                    'error': 'AI分析服务返回空结果',
                    'debug_info': debug_info if debug_mode else None
                })
            
            # 删除旧的分析结果
            delete_query = "DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?"
            db.execute_update(delete_query, (email_id, user_id))
            
            # 保存新的分析结果
            analysis_query = """
            INSERT INTO email_analysis 
            (user_id, email_id, summary, importance_score, importance_reason, 
             events_json, keywords_matched, ai_model, analysis_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            # 处理events中的datetime对象
            events = analysis_result.get('events', [])
            serializable_events = []
            for event in events:
                serializable_event = event.copy()
                # 将datetime对象转换为字符串
                if 'start_time' in serializable_event and serializable_event['start_time']:
                    if isinstance(serializable_event['start_time'], datetime):
                        serializable_event['start_time'] = serializable_event['start_time'].isoformat()
                if 'end_time' in serializable_event and serializable_event['end_time']:
                    if isinstance(serializable_event['end_time'], datetime):
                        serializable_event['end_time'] = serializable_event['end_time'].isoformat()
                if 'reminder_times' in serializable_event and serializable_event['reminder_times']:
                    reminder_times = []
                    for rt in serializable_event['reminder_times']:
                        if isinstance(rt, datetime):
                            reminder_times.append(rt.isoformat())
                        else:
                            reminder_times.append(rt)
                    serializable_event['reminder_times'] = reminder_times
                serializable_events.append(serializable_event)
            
            analysis_params = (
                user_id,
                email_id,
                analysis_result.get('summary', ''),
                analysis_result.get('importance_score', 5),
                analysis_result.get('importance_reason', ''),
                json.dumps(serializable_events, ensure_ascii=False),
                _build_keywords_payload(analysis_result),
                analysis_result.get('ai_model', ''),
                datetime.now()
            )
            
            db.execute_insert(analysis_query, analysis_params)

            # 标记邮件为已处理（否则前端仍会显示“未处理”）
            try:
                db.execute_update(
                    "UPDATE emails SET is_processed = 1, processed_date = COALESCE(processed_date, CURRENT_TIMESTAMP) WHERE id = ? AND user_id = ?",
                    (email_id, user_id)
                )
            except Exception as _e:
                logger.warning(f"标记邮件已处理失败: email_id={email_id}, user_id={user_id}, err={_e}")
            
            # 保存事件到日程表（用户隔离）
            if analysis_result.get('events'):
                scheduler_service = SchedulerService(config)
                for event in analysis_result['events']:
                    event['user_id'] = user_id
                    event['email_id'] = email_id
                    scheduler_service.add_event(event, user_id)
            
            debug_info['events_saved'] = len(analysis_result.get('events', []))
            
            # 为返回结果处理datetime对象
            return_result = analysis_result.copy()
            if 'events' in return_result:
                return_result['events'] = serializable_events
            
            logger.info(f"=== 重试分析完成 邮件 ID: {email_id} ===")
            
            return jsonify({
                'success': True,
                'message': '邮件重试分析完成',
                'analysis_result': return_result,
                'debug_info': debug_info if debug_mode else None
            })
            
        except Exception as e:
            logger.error(f"重试分析邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e),
                'debug_info': {'error_details': str(e)} if data.get('debug', False) else None
            }), 500
    
    def _process_single_email(email_data, user_id: int):
        """处理单个邮件的AI分析（多线程函数，按用户配置）"""
        try:
            from .models.database import DatabaseManager
            
            email_id = email_data['id']
            
            # 为每个线程创建独立的服务实例
            thread_ai_service = AIService(config)
            thread_db = DatabaseManager(config)
            
            # 重新进行AI分析
            analysis_result = thread_ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject'],
                user_id=user_id,
                reference_time=email_data.get('received_date')
            )
            
            # 删除旧的分析结果
            delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
            thread_db.execute_update(delete_query, (email_id,))
            
            # 保存新的分析结果
            analysis_query = """
            INSERT INTO email_analysis 
            (email_id, summary, importance_score, importance_reason, 
             events_json, keywords_matched, ai_model, analysis_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            # 处理events中的datetime对象
            events = analysis_result.get('events', [])
            serializable_events = []
            for event in events:
                serializable_event = event.copy()
                # 将datetime对象转换为字符串
                if 'start_time' in serializable_event and serializable_event['start_time']:
                    if isinstance(serializable_event['start_time'], datetime):
                        serializable_event['start_time'] = serializable_event['start_time'].isoformat()
                if 'end_time' in serializable_event and serializable_event['end_time']:
                    if isinstance(serializable_event['end_time'], datetime):
                        serializable_event['end_time'] = serializable_event['end_time'].isoformat()
                if 'reminder_times' in serializable_event and serializable_event['reminder_times']:
                    reminder_times = []
                    for rt in serializable_event['reminder_times']:
                        if isinstance(rt, datetime):
                            reminder_times.append(rt.isoformat())
                        else:
                            reminder_times.append(rt)
                    serializable_event['reminder_times'] = reminder_times
                serializable_events.append(serializable_event)
            
            analysis_params = (
                email_id,
                analysis_result.get('summary', ''),
                analysis_result.get('importance_score', 5),
                analysis_result.get('importance_reason', ''),
                json.dumps(serializable_events, ensure_ascii=False),
                json.dumps([], ensure_ascii=False),
                analysis_result.get('ai_model', ''),
                datetime.now()
            )
            
            thread_db.execute_insert(analysis_query, analysis_params)
            
            # 保存事件到日程表
            if analysis_result.get('events'):
                thread_scheduler_service = SchedulerService(config)
                for event in analysis_result['events']:
                    event['email_id'] = email_id
                    thread_scheduler_service.add_event(event, user_id)
            
            return {'success': True, 'email_id': email_id}
            
        except Exception as e:
            logger.error(f"重新分析邮件 {email_id} 失败: {e}")
            return {'success': False, 'email_id': email_id, 'error': str(e)}
    
    @app.route('/api/emails/reanalyze_all', methods=['POST'])
    def api_reanalyze_all_emails():
        """API: 重新分析所有邮件（多线程版本）"""
        try:
            # 获取当前用户，先清空其日程与提醒
            user_id = AuthManager.get_current_user_id() if hasattr(AuthManager, 'get_current_user_id') else None
            if user_id:
                from .models.database import DatabaseManager
                db_clear = DatabaseManager(config)
                try:
                    db_clear.execute_update("DELETE FROM reminders WHERE user_id = ?", (user_id,))
                    db_clear.execute_update("DELETE FROM events WHERE user_id = ?", (user_id,))
                    logger.info(f"已清空用户 {user_id} 的日程与提醒")
                except Exception as _e:
                    logger.warning(f"清空用户日程失败: {_e}")

            # 获取所有邮件
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            query = "SELECT id, user_id, subject, content, received_date FROM emails ORDER BY received_date DESC"
            emails = db.execute_query(query)
            
            if not emails:
                return jsonify({
                    'success': True,
                    'message': '没有邮件需要分析',
                    'processed_count': 0
                })
            
            processed_count = 0
            failed_count = 0
            
            # 使用线程池并行处理邮件
            max_workers = min(5, len(emails))  # 最多5个线程，避免过多并发
            
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # 提交所有任务
                future_to_email = {executor.submit(_process_single_email, email, email.get('user_id') or user_id): email for email in emails}
                
                # 收集结果
                for future in as_completed(future_to_email):
                    result = future.result()
                    if result['success']:
                        processed_count += 1
                    else:
                        failed_count += 1
            
            # 清除邮件缓存
            clear_email_cache()
            
            return jsonify({
                'success': True,
                'message': f'批量重新分析完成，成功处理 {processed_count} 封邮件，失败 {failed_count} 封',
                'processed_count': processed_count,
                'failed_count': failed_count
            })
            
        except Exception as e:
            logger.error(f"批量重新分析邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/emails/reanalyze_failed', methods=['POST'])
    @login_required
    def api_reanalyze_failed_emails():
        """API: 仅重新分析未分析/失败的邮件（带进度，纳入统一任务轮询）"""
        try:
            # 初始化进度容器（复用 check_email 的进度字典）
            if not hasattr(api_check_email, '_progress'):
                api_check_email._progress = {}
                api_check_email._lock = threading.Lock()

            user_id = AuthManager.get_current_user_id()

            # 查询需要重分析的邮件列表
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            query = (
                "SELECT e.* FROM emails e "
                "LEFT JOIN email_analysis ea ON e.id = ea.email_id "
                "WHERE (ea.email_id IS NULL OR ea.summary IN ('AI分析失败', '邮件内容分析失败', '')) "
                "AND e.user_id = ? "
                "ORDER BY e.received_date DESC"
            )
            rows = db.execute_query(query, (user_id,))
            emails_to_analyze = []
            for row in rows:
                emails_to_analyze.append({
                    'id': row['id'],
                    'message_id': row['message_id'],
                    'subject': row['subject'],
                    'sender': row['sender'],
                    'content': row['content'],
                    'received_date': row['received_date']
                })

            # 创建任务
            task_id = str(uuid4())
            task_cancel_event = threading.Event()
            with api_check_email._lock:
                api_check_email._progress[task_id] = {
                    'user_id': user_id,
                    'task_type': 'reanalyze_failed',
                    'task_name': '重新分析失败邮件',
                    'created_at': datetime.now().isoformat(),
                    'ended_at': None,
                    'error_summary': '',
                    'status': 'analyzing',
                    'new_count': 0,
                    'total': len(emails_to_analyze),
                    'analyzed': 0,
                    'failed': 0,
                    'saved': 0,
                    'synced': 0,
                    'message': '',
                    'cancel_requested': False,
                    'cancel_event': task_cancel_event,
                }

            def _job(uid: int, tid: str, emails: list, cancel_event: threading.Event = None):
                analyzed_count = 0
                failed_count = 0
                try:
                    # 仅清理“本次失败重分析邮件”对应的历史事件与提醒，避免误删其他日程
                    try:
                        target_email_ids = [e.get('id') for e in emails if e.get('id')]
                        if target_email_ids:
                            placeholders = ','.join(['?'] * len(target_email_ids))
                            params = tuple([uid] + target_email_ids)
                            db.execute_update(
                                f"DELETE FROM reminders WHERE user_id = ? AND event_id IN (SELECT id FROM events WHERE user_id = ? AND email_id IN ({placeholders}))",
                                tuple([uid, uid] + target_email_ids)
                            )
                            db.execute_update(
                                f"DELETE FROM events WHERE user_id = ? AND email_id IN ({placeholders})",
                                params
                            )
                            logger.info(f"已清理用户 {uid} 的失败邮件关联日程，邮件数: {len(target_email_ids)}")
                    except Exception as _e:
                        logger.warning(f"清理失败邮件关联日程失败（失败重分析）: {_e}")
                    if emails:
                        max_workers = min(3, len(emails))
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = []
                            for email in emails:
                                if cancel_event is not None and cancel_event.is_set():
                                    break
                                futures.append((executor.submit(_analyze_email_only, email, uid, tid), email))
                            for future, email_data in futures:
                                if cancel_event is not None and cancel_event.is_set():
                                    for fut, _ in futures:
                                        try:
                                            fut.cancel()
                                        except Exception:
                                            pass
                                    with api_check_email._lock:
                                        api_check_email._progress[tid]['status'] = 'cancelled'
                                        api_check_email._progress[tid]['ended_at'] = datetime.now().isoformat()
                                        api_check_email._progress[tid]['error_summary'] = ''
                                        api_check_email._progress[tid]['message'] = '任务已取消'
                                    return
                                try:
                                    result = future.result(timeout=30)
                                    if result['success']:
                                        analyzed_count += 1
                                    else:
                                        failed_count += 1
                                except Exception as e:
                                    logger.error(f"重分析失败邮件时出错: {email_data.get('subject', 'Unknown')}, 错误: {e}")
                                    failed_count += 1
                                finally:
                                    with api_check_email._lock:
                                        api_check_email._progress[tid]['analyzed'] = analyzed_count
                                        api_check_email._progress[tid]['failed'] = failed_count
                    with api_check_email._lock:
                        api_check_email._progress[tid]['status'] = 'done'
                        api_check_email._progress[tid]['ended_at'] = datetime.now().isoformat()
                        api_check_email._progress[tid]['error_summary'] = ''
                        api_check_email._progress[tid]['message'] = (
                            f"失败邮件重分析完成，共 {len(emails)} 封，成功 {analyzed_count} 封"
                            + (f"，失败 {failed_count} 封" if failed_count > 0 else '')
                        )
                except Exception as e:
                    logger.error(f"批量重新分析失败邮件任务错误: {e}")
                    with api_check_email._lock:
                        api_check_email._progress[tid]['status'] = 'error'
                        api_check_email._progress[tid]['message'] = str(e)
                        api_check_email._progress[tid]['ended_at'] = datetime.now().isoformat()
                        api_check_email._progress[tid]['error_summary'] = str(e)[:500]

            Thread(target=_job, args=(user_id, task_id, emails_to_analyze, task_cancel_event), daemon=True).start()
            return jsonify({'success': True, 'task_id': task_id, 'count': len(emails_to_analyze)})

        except Exception as e:
            logger.error(f"创建重新分析失败邮件任务出错: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/test_email', methods=['POST'])
    @login_required
    def api_test_email():
        """API: 测试邮件服务器连接"""
        try:
            data = request.get_json()
            
            # 创建临时邮件服务实例进行测试
            from .services.email_service import EmailService
            
            # 创建临时配置
            temp_config = Config()
            temp_config._config['email'] = {
                'imap_server': data.get('imap_server'),
                'imap_port': data.get('imap_port', 993),
                'username': data.get('username'),
                'password': data.get('password'),
                'use_ssl': data.get('use_ssl', True)
            }
            
            # 测试连接
            temp_email_service = EmailService(temp_config)
            connection_result = temp_email_service.test_connection()
            
            if connection_result:
                return jsonify({
                    'success': True,
                    'message': '邮件服务器连接成功'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': '邮件服务器连接失败，请检查配置信息'
                }), 400
                
        except Exception as e:
            logger.error(f"邮件连接测试失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/test_ai', methods=['POST'])
    @login_required
    def api_test_ai():
        """API: 测试AI服务"""
        try:
            # 获取当前用户ID
            user_id = AuthManager.get_current_user_id()
            
            # 兼容：允许 JSON 或表单提交，避免 415 导致前端“快速操作”不可用
            data = request.get_json(silent=True) or {}
            test_content = (data.get('content') or request.form.get('content') or '明天下午2点有一个重要的期末考试。')
            
            # 传递用户ID给AI服务
            result = ai_service.analyze_email_content(test_content, user_id=user_id)
            
            # 检查AI分析结果是否包含错误
            if result and result.get('summary') == 'AI分析失败':
                error_msg = result.get('importance_reason', '未知错误')
                logger.error(f"AI测试失败: {error_msg}")
                return jsonify({
                    'success': False,
                    'error': error_msg
                }), 500
            
            return jsonify({
                'success': True,
                'result': result
            })
        except Exception as e:
            logger.error(f"AI测试失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/ai/providers')
    def api_get_ai_providers():
        """API: 获取AI提供商信息"""
        try:
            providers = ai_service.get_provider_info()
            return jsonify({
                'success': True,
                'providers': providers
            })
        except Exception as e:
            logger.error(f"获取AI提供商信息失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/ai/models')
    def api_get_ai_models():
        """API: 获取支持的AI模型"""
        try:
            models = ai_service.get_supported_models()
            return jsonify({
                'success': True,
                'models': models
            })
        except Exception as e:
            logger.error(f"获取AI模型列表失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    # 邮件列表缓存
    # 注意：该缓存会导致“刚处理完/重新分析完邮件后列表不更新”的错觉。
    # 为保证一致性，默认关闭缓存（ttl=0）。如需性能优化可在生产环境再打开并配合可靠的失效机制。
    _email_cache = {'data': None, 'timestamp': 0, 'ttl': 0}  # 0=禁用缓存（仅用于第一页）
    
    def clear_email_cache():
        """清除邮件缓存"""
        _email_cache['data'] = None
        _email_cache['timestamp'] = 0
    
    @app.route('/api/emails')
    @login_required
    def api_get_emails():
        """API: 获取邮件列表（优化版本）"""
        try:
            user_id = AuthManager.get_current_user_id()
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 20))
            limit = int(request.args.get('limit', 200))
            importance = request.args.get('importance', '')
            status = request.args.get('status', '')
            search = request.args.get('search', '')
            start_date = request.args.get('start_date', '').strip()
            end_date = request.args.get('end_date', '').strip()

            # 日期格式校验：仅接受 YYYY-MM-DD
            if start_date:
                try:
                    datetime.strptime(start_date, '%Y-%m-%d')
                except ValueError:
                    return jsonify({'success': False, 'error': 'start_date格式错误，应为YYYY-MM-DD'}), 400
            if end_date:
                try:
                    datetime.strptime(end_date, '%Y-%m-%d')
                except ValueError:
                    return jsonify({'success': False, 'error': 'end_date格式错误，应为YYYY-MM-DD'}), 400
            if start_date and end_date and start_date > end_date:
                return jsonify({'success': False, 'error': '开始日期不能晚于结束日期'}), 400
            
            # 检查缓存（仅缓存第一页、无筛选条件）
            import time
            current_time = time.time()
            cache_key = f"{user_id}_{importance}_{status}_{search}_{start_date}_{end_date}"
            
            # 如果有筛选条件或缓存过期，重新查询
            use_cache = (
                page == 1 and not importance and not status and not search and not start_date and not end_date and
                _email_cache.get('ttl', 0) and _email_cache.get('ttl', 0) > 0 and
                _email_cache.get('user_id') == user_id and
                (_email_cache['data'] is not None) and
                (current_time - _email_cache['timestamp'] <= _email_cache['ttl'])
            )
            if not use_cache:
                
                # 直接在数据库层面进行筛选，避免获取大量数据后再筛选
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                
                # 构建查询条件（添加用户ID过滤）
                where_conditions = ["e.user_id = ?"]
                params = [user_id]
                
                if importance:
                    where_conditions.append("ea.importance_score BETWEEN ? AND ?")
                    if importance == 'important':
                        params.extend([8, 10])
                    elif importance == 'normal':
                        params.extend([4, 7])
                    elif importance == 'unimportant':
                        params.extend([1, 3])
                
                # 处理状态判定：优先使用“有效已处理”口径（emails.is_processed=1 或存在分析记录）
                # 这样可兼容历史数据里 is_processed 未及时回填，但 email_analysis 已存在的情况。
                processed_expr = "CASE WHEN e.is_processed = 1 OR ea.id IS NOT NULL THEN 1 ELSE 0 END"
                if status:
                    if status == 'processed':
                        where_conditions.append(f"{processed_expr} = 1")
                    elif status == 'unprocessed':
                        where_conditions.append(f"{processed_expr} = 0")
                
                if search:
                    where_conditions.append("(e.subject LIKE ? OR e.sender LIKE ?)")
                    search_pattern = f"%{search}%"
                    params.extend([search_pattern, search_pattern])

                if start_date:
                    where_conditions.append("date(e.received_date) >= date(?)")
                    params.append(start_date)
                if end_date:
                    where_conditions.append("date(e.received_date) <= date(?)")
                    params.append(end_date)
                
                # 构建完整查询
                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                
                query = f"""
                SELECT e.*, ea.summary, ea.importance_score, ea.events_json, ea.keywords_matched,
                       {processed_expr} as is_processed_effective,
                       CASE 
                           WHEN ea.importance_score >= 8 THEN 'important'
                           WHEN ea.importance_score >= 4 THEN 'normal'
                           ELSE 'unimportant'
                       END as importance_level
                FROM emails e
                LEFT JOIN email_analysis ea ON e.id = ea.email_id AND ea.user_id = e.user_id
                WHERE {where_clause}
                ORDER BY e.received_date DESC
                LIMIT ? OFFSET ?
                """
                
                # 统一分页：per_page 优先，其次退化为 limit
                if not request.args.get('per_page'):
                    per_page = min(limit, 50)  # 限制最大每页数量，避免查询超时
                offset = (page - 1) * per_page
                emails = db.execute_query(query, params + [per_page, offset])

                # 统计总数（不受分页限制）
                count_query = f"""
                SELECT COUNT(*) as count
                FROM emails e
                LEFT JOIN email_analysis ea ON e.id = ea.email_id AND ea.user_id = e.user_id
                WHERE {where_clause}
                """
                count_result = db.execute_query(count_query, tuple(params))
                total_count = count_result[0]['count'] if count_result else 0
                
                # 解析events_json
                import json
                for email in emails:
                    email['is_processed'] = bool(email.get('is_processed_effective'))
                    if email.get('events_json'):
                        try:
                            email['events'] = json.loads(email['events_json'])
                        except json.JSONDecodeError:
                            email['events'] = []
                    else:
                        email['events'] = []
                    # 从 keywords_matched 中提取标签（兼容旧格式）
                    tags_payload = {}
                    if email.get('keywords_matched'):
                        try:
                            tags_payload = json.loads(email['keywords_matched']) if isinstance(email['keywords_matched'], str) else (email['keywords_matched'] or {})
                        except Exception:
                            tags_payload = {}
                    email['tags'] = TagService.normalize_tags((tags_payload or {}).get('tags', {}), int(email.get('importance_score') or 5))
                
                # 仅在第一页且无筛选条件时缓存当前页数据
                if page == 1 and not (importance or status or search or start_date or end_date):
                    _email_cache['data'] = emails
                    _email_cache['timestamp'] = current_time
                    _email_cache['user_id'] = user_id
                    _email_cache['total_count'] = total_count
            else:
                # 使用缓存数据
                emails = _email_cache['data']
                total_count = _email_cache.get('total_count', len(emails) if emails else 0)
                # 缓存只代表第一页结果
                per_page = len(emails) if emails else 0
                page = 1
                total = total_count

            # 非缓存路径已在SQL层面完成分页
            if use_cache:
                pass
            else:
                total = total_count
            
            return jsonify({
                'success': True,
                'emails': emails,
                'total': total,
                'page': page,
                'per_page': per_page
            })
        except Exception as e:
            logger.error(f"获取邮件列表失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    @app.route('/api/emails/search')
    @login_required
    def api_search_emails():
        """API: 搜索邮件（分页 + 重要性 + 时间范围）"""
        try:
            user_id = AuthManager.get_current_user_id()
            keyword = request.args.get('q', '').strip()
            importance = request.args.get('importance', '').strip()
            days_back = int(request.args.get('days_back', 30))
            limit = int(request.args.get('limit', 50))

            emails = email_service.search_emails(user_id, keyword, importance, days_back, limit)
            return jsonify({'success': True, 'emails': emails})
        except Exception as e:
            logger.error(f"搜索邮件失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/user/subscribe_key/rotate', methods=['POST'])
    @login_required
    def api_rotate_subscribe_key():
        """API: 重置当前用户订阅key"""
        try:
            user_id = AuthManager.get_current_user_id()
            new_key = user_service.rotate_subscribe_key(user_id)
            if not new_key:
                return jsonify({'success': False, 'error': '重置失败'}), 500
            # 更新会话
            AuthManager.login_user({
                'id': user_id,
                'username': AuthManager.get_current_user()['username'],
                'email': AuthManager.get_current_user()['email'],
                'is_admin': AuthManager.get_current_user().get('is_admin', False),
                'subscribe_key': new_key
            })
            return jsonify({'success': True, 'subscribe_key': new_key})
        except Exception as e:
            logger.error(f"重置订阅key失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/ai/validate', methods=['POST'])
    @login_required
    def api_validate_ai_config():
        """API: 验证AI配置有效性（不保存，仅测试）"""
        try:
            data = request.get_json() or {}
            # 临时构造配置
            temp_config = Config()
            temp_ai = temp_config._config.get('ai', {}).copy()
            for k in ['api_key', 'provider', 'model', 'base_url', 'max_tokens', 'temperature', 'custom_judgement_prompt', 'focus_keywords']:
                if k in data:
                    temp_ai[k] = data[k]
            temp_config._config['ai'] = temp_ai

            temp_ai_service = AIService(temp_config)
            result = temp_ai_service.test_connection(user_id=AuthManager.get_current_user_id())
            return jsonify(result if result else {'success': False, 'error': '验证失败'})
        except Exception as e:
            logger.error(f"验证AI配置失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/emails/recent')
    @login_required
    def api_get_recent_emails():
        """API: 获取最近邮件"""
        try:
            user_id = AuthManager.get_current_user_id()
            limit = int(request.args.get('limit', 5))
            emails = email_service.get_processed_emails(user_id, limit)
            
            return jsonify({
                'success': True,
                'emails': emails
            })
            
        except Exception as e:
            logger.error(f"获取最近邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/events/upcoming')
    @login_required
    def api_get_upcoming_events():
        """API: 获取即将到来的事件"""
        try:
            user_id = AuthManager.get_current_user_id()
            days = int(request.args.get('days', 30))
            importance = request.args.get('importance', '')
            search = request.args.get('search', '')
            
            events = scheduler_service.get_upcoming_events(user_id, days)
            
            # 应用筛选
            if importance:
                events = [e for e in events if e.get('importance_level') == importance]
            
            if search:
                search_lower = search.lower()
                events = [e for e in events if 
                         search_lower in (e.get('title', '') or '').lower() or
                         search_lower in (e.get('description', '') or '').lower()]
            
            return jsonify({
                'success': True,
                'events': events
            })
            
        except Exception as e:
            logger.error(f"获取即将到来的事件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/system/status')
    def api_system_status():
        """API: 获取系统状态（手动测试版本，仅在用户主动测试时调用）"""
        try:
            def _is_email_configured(email_cfg):
                """兼容历史字段：email/username 均视为账号字段。"""
                email_cfg = email_cfg or {}
                account = str(
                    email_cfg.get('username')
                    or email_cfg.get('email')
                    or ''
                ).strip()
                password = str(email_cfg.get('password') or '').strip()
                imap_server = str(email_cfg.get('imap_server') or '').strip()
                # 兼容老配置：有账号+密码即视为已配置；若含 imap_server 也同样判定为已配置。
                return bool(account and password) or bool(account and password and imap_server)

            # 检查是否是手动测试请求
            manual_test = request.args.get('manual', 'false').lower() == 'true'
            
            if not manual_test:
                # 非手动测试时，只返回配置状态，不进行实际连接测试
                status = {
                    'email': _is_email_configured(config.email_config),
                    'ai': bool(config.ai_config.get('api_key')),
                    'notion': bool(config.notion_config.get('token'))
                }
                
                return jsonify({
                    'success': True,
                    'status': status,
                    'note': '配置状态检查，如需实际连接测试请使用手动测试'
                })
            
            # 手动测试时才进行实际连接测试
            status = {
                'email': False,
                'ai': False,
                'notion': False
            }
            
            # 检查邮件服务状态
            try:
                if config.email_config.get('username') and config.email_config.get('password'):
                    status['email'] = email_service.test_connection()
            except Exception as e:
                logger.warning(f"邮件服务测试失败: {e}")
            
            # 检查AI服务状态
            try:
                if config.ai_config.get('api_key'):
                    test_result = ai_service.test_connection()
                    status['ai'] = test_result.get('success', False)
            except Exception as e:
                logger.warning(f"AI服务测试失败: {e}")
            
            # 检查Notion服务状态
            try:
                if config.notion_config.get('token'):
                    test_result = notion_service.test_connection()
                    status['notion'] = test_result.get('success', False)
            except Exception as e:
                logger.warning(f"Notion服务测试失败: {e}")
            
            return jsonify({
                'success': True,
                'status': status,
                'note': '手动连接测试完成'
            })
            
        except Exception as e:
            logger.error(f"获取系统状态失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/system/status_basic')
    def api_system_status_basic():
        """API: 获取基础系统状态（仅检查配置，不做连接测试）"""
        try:
            def _is_email_configured(email_cfg):
                """兼容历史字段：email/username 均视为账号字段。"""
                email_cfg = email_cfg or {}
                account = str(
                    email_cfg.get('username')
                    or email_cfg.get('email')
                    or ''
                ).strip()
                password = str(email_cfg.get('password') or '').strip()
                imap_server = str(email_cfg.get('imap_server') or '').strip()
                # 兼容老配置：有账号+密码即视为已配置；若含 imap_server 也同样判定为已配置。
                return bool(account and password) or bool(account and password and imap_server)

            # 首页状态卡片期望的是“是否已配置”，而不是实时连接结果。
            # 优先读取当前登录用户配置；未登录时回退到全局配置。
            user_id = AuthManager.get_current_user_id()
            if user_id:
                from .services.config_service import UserConfigService
                cfg_svc = UserConfigService()
                user_email_cfg = cfg_svc.get_email_config(user_id)
                user_ai_cfg = cfg_svc.get_ai_config(user_id)
                user_notion_cfg = cfg_svc.get_notion_config(user_id)

                status = {
                    'email': _is_email_configured(user_email_cfg),
                    'ai': bool((user_ai_cfg.get('api_key') or '').strip()),
                    'notion': bool((user_notion_cfg.get('token') or '').strip()),
                }
            else:
                status = {
                    'email': _is_email_configured(config.email_config),
                    'ai': bool(config.ai_config.get('api_key')),
                    'notion': bool(config.notion_config.get('token'))
                }
            
            return jsonify({
                'success': True,
                'status': status
            })
            
        except Exception as e:
            logger.error(f"获取基础系统状态失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/statistics')
    @login_required
    def api_get_statistics():
        """API: 获取系统统计数据（按当前用户隔离）"""
        try:
            user_id = AuthManager.get_current_user_id()
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 获取邮件总数
            email_count_query = "SELECT COUNT(*) as count FROM emails WHERE user_id = ?"
            email_result = db.execute_query(email_count_query, (user_id,))
            total_emails = email_result[0]['count'] if email_result else 0
            
            # 获取事件总数
            event_count_query = "SELECT COUNT(*) as count FROM events WHERE user_id = ?"
            event_result = db.execute_query(event_count_query, (user_id,))
            total_events = event_result[0]['count'] if event_result else 0
            
            # 获取重要事件数量
            important_events_query = "SELECT COUNT(*) as count FROM events WHERE user_id = ? AND importance_level = 'important'"
            important_result = db.execute_query(important_events_query, (user_id,))
            important_events = important_result[0]['count'] if important_result else 0
            
            # 获取待提醒事件数量（未来7天内的事件）
            pending_reminders_query = """
            SELECT COUNT(*) as count FROM events 
            WHERE user_id = ?
              AND start_time BETWEEN datetime('now') AND datetime('now', '+7 days')
            """
            pending_result = db.execute_query(pending_reminders_query, (user_id,))
            pending_reminders = pending_result[0]['count'] if pending_result else 0
            
            return jsonify({
                'success': True,
                'statistics': {
                    'total_emails': total_emails,
                    'total_events': total_events,
                    'important_events': important_events,
                    'pending_reminders': pending_reminders
                }
            })
            
        except Exception as e:
            logger.error(f"获取统计数据失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    # 健康检查：无鉴权、轻量、只返回200
    @app.route('/healthz')
    def healthz():
        """健康检查端点 - 用于容器健康检查"""
        return jsonify({'status': 'healthy', 'message': 'ok'}), 200
    
    @app.route('/api/events/<int:event_id>', methods=['PUT'])
    @login_required
    def api_update_event(event_id):
        """API: 更新事件信息"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'error': '请求数据为空'
                }), 400
            
            importance_level = data.get('importance_level')
            if not importance_level or importance_level not in ['important', 'normal', 'unimportant', 'subscribed']:
                return jsonify({
                    'success': False,
                    'error': '无效的重要性级别'
                }), 400
            
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 检查事件是否存在且属于当前用户
            check_query = "SELECT id FROM events WHERE id = ? AND user_id = ?"
            existing = db.execute_query(check_query, (event_id, user_id))
            if not existing:
                return jsonify({
                    'success': False,
                    'error': '事件不存在或无权限访问'
                }), 404
            
            # 更新事件重要性
            update_query = "UPDATE events SET importance_level = ? WHERE id = ? AND user_id = ?"
            db.execute_update(update_query, (importance_level, event_id, user_id))
            
            # 重新计算提醒时间
            event_query = "SELECT * FROM events WHERE id = ? AND user_id = ?"
            event_result = db.execute_query(event_query, (event_id, user_id))
            if event_result:
                event_data = event_result[0]
                # 删除旧的提醒
                delete_reminders_query = "DELETE FROM reminders WHERE event_id = ? AND user_id = ?"
                db.execute_update(delete_reminders_query, (event_id, user_id))
                
                # 根据新的重要性级别创建提醒
                if importance_level != 'unimportant':
                    scheduler_service.create_reminders_for_event(event_data)
            
            logger.info(f"事件 {event_id} 重要性已更新为 {importance_level}")
            
            return jsonify({
                'success': True,
                'message': '事件更新成功'
            })
            
        except Exception as e:
            logger.error(f"更新事件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/events/<int:event_id>', methods=['DELETE'])
    @login_required
    def api_delete_event(event_id):
        """API: 删除事件"""
        try:
            user_id = AuthManager.get_current_user_id()
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 检查事件是否存在且属于当前用户
            check_query = "SELECT id FROM events WHERE id = ? AND user_id = ?"
            existing = db.execute_query(check_query, (event_id, user_id))
            if not existing:
                return jsonify({
                    'success': False,
                    'error': '事件不存在或无权限访问'
                }), 404
            
            # 删除相关的提醒
            delete_reminders_query = "DELETE FROM reminders WHERE event_id = ? AND user_id = ?"
            db.execute_update(delete_reminders_query, (event_id, user_id))
            
            # 删除事件
            delete_event_query = "DELETE FROM events WHERE id = ? AND user_id = ?"
            db.execute_update(delete_event_query, (event_id, user_id))
            
            logger.info(f"事件 {event_id} 已删除")
            
            return jsonify({
                'success': True,
                'message': '事件删除成功'
            })
            
        except Exception as e:
            logger.error(f"删除事件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/notifications')
    @login_required
    def api_get_notifications():
        """API: 获取通知（仅浏览器通知渠道）
        
        说明：
        - 不再做“页面内提醒 UI”
        - 邮件/Server酱 由后台 worker 发送
        - 浏览器系统通知由前端轮询该接口拉取并回执
        """
        try:
            user_id = AuthManager.get_current_user_id()
            return jsonify({
                'success': True,
                'notifications': scheduler_service.get_pending_browser_deliveries(user_id, limit=20)
            })
            
        except Exception as e:
            logger.error(f"获取通知失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @app.route('/api/notifications/ack', methods=['POST'])
    @login_required
    def api_ack_notification():
        """浏览器通知回执：标记投递为已发送"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            delivery_id = data.get('delivery_id')
            if not delivery_id:
                return jsonify({'success': False, 'error': 'missing delivery_id'}), 400
            ok = scheduler_service.ack_browser_delivery(user_id, int(delivery_id))
            if not ok:
                return jsonify({'success': False, 'error': 'not found'}), 404
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"通知回执失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/notifications/test', methods=['POST'])
    @login_required
    def api_test_notification():
        """测试通知：立即发送一次测试消息（不落库）"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            channel = (data.get('channel') or '').strip().lower()
            cfg = data.get('config') or {}
            if channel == 'serverchan':
                meta = scheduler_service.send_test_notification_detail(user_id, channel, cfg)
                if not meta.get('ok'):
                    return jsonify({'success': False, 'error': meta.get('error', 'Server酱测试失败'), 'channel': channel})
                pushid = meta.get('pushid', '') or ''
                readkey = meta.get('readkey', '') or ''
                query_url = ''
                if pushid and readkey:
                    query_url = f"https://sctapi.ftqq.com/push?id={pushid}&readkey={readkey}"
                msg = '测试通知已入队'
                if query_url:
                    msg += f"（送达状态查询：{query_url}）"
                return jsonify({
                    'success': True,
                    'message': msg,
                    'channel': channel,
                    'detail': {'pushid': pushid, 'readkey': readkey, 'query_url': query_url, 'raw': meta.get('raw')}
                })

            err = scheduler_service.send_test_notification(user_id, channel, cfg)
            # 说明：这里即使失败也返回 200，避免前端进入 ajaxError 分支导致信息不直观
            if err:
                return jsonify({'success': False, 'error': err, 'channel': channel})
            return jsonify({'success': True, 'message': '测试通知已发送', 'channel': channel})
        except Exception as e:
            logger.error(f"测试通知失败: {e}")
            # 测试接口：同样返回 200，保证前端能直接展示错误文本
            return jsonify({'success': False, 'error': str(e)})

    @app.route('/api/notifications/push/manual', methods=['POST'])
    @login_required
    def api_manual_fcm_push():
        """API: 手动触发一次移动端主动推送（FCM/Getui自动回退）。"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            title = str(data.get('title') or '').strip()
            body = str(data.get('body') or '').strip()
            push_type = str(data.get('push_type') or 'system').strip().lower()
            force = bool(data.get('force', False))
            if not title:
                return jsonify({'success': False, 'error': 'title不能为空'}), 400
            if not body:
                return jsonify({'success': False, 'error': 'body不能为空'}), 400
            err = scheduler_service.send_mobile_push(
                user_id=user_id,
                title=title,
                body=body,
                push_type=push_type,
                data={'manual': '1'},
                force=force,
            )
            if err:
                return jsonify({'success': False, 'error': err})
            return jsonify({'success': True, 'message': '主动推送已发送'})
        except Exception as e:
            logger.error(f"主动推送失败: {e}")
            return jsonify({'success': False, 'error': str(e)})
    
    @app.route('/api/calendar/export.ics', methods=['GET', 'HEAD'])
    @login_required
    def api_export_ical():
        """API: 导出iCal格式日历"""
        try:
            user_id = AuthManager.get_current_user_id()
            days = int(request.args.get('days', 365))
            importance = request.args.get('importance', '')
            
            # 获取事件
            events = scheduler_service.get_upcoming_events(user_id, days)
            # 应用用户订阅等级过滤
            try:
                from .services.config_service import UserConfigService
                _svc = UserConfigService()
                sub_cfg = _svc.get_subscription_config(user_id)
                allowed = set((sub_cfg.get('importance_levels') or []))
                if allowed:
                    events = [e for e in events if (e.get('importance_level') in allowed)]
            except Exception as _e:
                logger.warning(f"读取订阅等级失败，使用默认: {_e}")
            
            # 按重要性筛选
            if importance:
                events = [e for e in events if e.get('importance_level') == importance]
            
            # 导出iCal
            ical_content = scheduler_service.export_to_ical(events, user_id=user_id)
            
            # 计算缓存头
            def _coerce_dt(val):
                if isinstance(val, datetime):
                    return val
                try:
                    return datetime.fromisoformat(str(val).replace('Z', '+00:00'))
                except Exception:
                    return None
            max_dt = None
            for e in events:
                dt = _coerce_dt(e.get('updated_at')) or _coerce_dt(e.get('start_time'))
                if dt and (max_dt is None or dt > max_dt):
                    max_dt = dt
            if not max_dt:
                max_dt = datetime.now()
            last_modified = max_dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
            etag = f'W/"calendar-user-{user_id}-{len(events)}-{int(max_dt.timestamp())}"'
            headers = {
                'Content-Type': 'text/calendar',
                'Content-Disposition': 'attachment; filename=calendar.ics',
                'Cache-Control': 'no-cache',
                'ETag': etag,
                'Last-Modified': last_modified
            }

            if request.method == 'HEAD':
                return '', 200, headers
            
            if ical_content:
                response = app.response_class(
                    ical_content,
                    mimetype='text/calendar',
                    headers=headers
                )
                return response
            else:
                return jsonify({
                    'success': False,
                    'error': '导出失败'
                }), 500
                
        except Exception as e:
            logger.error(f"导出iCal失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/calendar/subscribe', methods=['GET', 'HEAD'])
    def api_calendar_subscribe():
        """API: 日历订阅链接（iCal格式，支持用户隔离）"""
        try:
            # 获取用户key参数
            user_key = request.args.get('key', '')
            if not user_key:
                logger.warning("日历订阅请求缺少用户key")
                return 'BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Mail Scheduler//EN\nEND:VCALENDAR', 200, {'Content-Type': 'text/calendar'}
            
            # 验证用户key并获取用户ID
            user = user_service.get_user_by_subscribe_key(user_key)
            if not user:
                logger.warning(f"无效的用户key: {user_key}")
                return 'BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Mail Scheduler//EN\nEND:VCALENDAR', 200, {'Content-Type': 'text/calendar'}
            
            user_id = user['id']
            
            days = int(request.args.get('days', 365))
            importance = request.args.get('importance', '')
            
            # 获取该用户的事件
            events = scheduler_service.get_upcoming_events(user_id, days)
            # 应用用户订阅等级过滤
            try:
                from .services.config_service import UserConfigService
                _svc = UserConfigService()
                sub_cfg = _svc.get_subscription_config(user_id)
                allowed = set((sub_cfg.get('importance_levels') or []))
                if allowed:
                    events = [e for e in events if (e.get('importance_level') in allowed)]
            except Exception as _e:
                logger.warning(f"读取订阅等级失败，使用默认: {_e}")
            
            # 按重要性筛选
            if importance:
                events = [e for e in events if e.get('importance_level') == importance]
            
            # 导出iCal
            ical_content = scheduler_service.export_to_ical(events, user_id=user_id)
            
            # 计算缓存头
            def _coerce_dt(val):
                if isinstance(val, datetime):
                    return val
                try:
                    return datetime.fromisoformat(str(val).replace('Z', '+00:00'))
                except Exception:
                    return None
            max_dt = None
            for e in events:
                dt = _coerce_dt(e.get('updated_at')) or _coerce_dt(e.get('start_time'))
                if dt and (max_dt is None or dt > max_dt):
                    max_dt = dt
            if not max_dt:
                max_dt = datetime.now()
            last_modified = max_dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
            etag = f'W/"subcal-user-{user_id}-{len(events)}-{int(max_dt.timestamp())}"'
            headers = {
                        'Content-Type': 'text/calendar; charset=utf-8',
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                        'Pragma': 'no-cache',
                'Expires': '0',
                'ETag': etag,
                'Last-Modified': last_modified
            }
            if request.method == 'HEAD':
                return '', 200, headers
            if ical_content:
                response = app.response_class(
                    ical_content,
                    mimetype='text/calendar; charset=utf-8',
                    headers=headers
                )
                return response
            else:
                return 'BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Mail Scheduler//EN\nEND:VCALENDAR', 200, {'Content-Type': 'text/calendar'}
                
        except Exception as e:
            logger.error(f"日历订阅失败: {e}")
            return 'BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//Mail Scheduler//EN\nEND:VCALENDAR', 200, {'Content-Type': 'text/calendar'}
    
    def validate_user_key(user_key: str) -> Optional[int]:
        """验证用户key并返回用户ID
        
        Args:
            user_key: 用户订阅key
            
        Returns:
            用户ID，如果验证失败返回None
        """
        try:
            user = user_service.get_user_by_subscribe_key(user_key)
            if user and user['is_active']:
                return user['id']
            return None
        except Exception as e:
            logger.error(f"验证用户key失败: {e}")
            return None
    
    # ==================== 用户管理API ====================
    
    @app.route('/api/auth/register', methods=['POST'])
    def api_register():
        """API: 用户注册"""
        try:
            data = request.get_json()
            username = data.get('username', '').strip()
            email = data.get('email', '').strip()
            password = data.get('password', '')
            invitation_code = data.get('invitation_code', '').strip()
            
            # 验证输入
            if not all([username, email, password, invitation_code]):
                return jsonify({'success': False, 'error': '所有字段都是必填的'}), 400
            
            if len(password) < 6:
                return jsonify({'success': False, 'error': '密码长度至少6位'}), 400
            
            # 注册用户
            result = user_service.register_user(username, email, password, invitation_code)
            
            if result['success']:
                return jsonify(result)
            else:
                return jsonify(result), 400
                
        except Exception as e:
            logger.error(f"用户注册失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/auth/login', methods=['POST'])
    def api_login():
        """API: 用户登录"""
        try:
            data = request.get_json()
            username = data.get('username', '').strip()
            password = data.get('password', '')
            remember_me = data.get('remember_me', False)
            
            if not all([username, password]):
                return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
            
            # 用户登录
            result = user_service.login_user(username, password)
            
            if result['success']:
                # 设置session
                AuthManager.login_user(result['user'], remember_me)
                # 非浏览器客户端（如 Flutter）无法自动读取 csrf_token Cookie，
                # 这里在响应体内也返回一份，便于客户端放入 X-CSRF-Token 头。
                result['csrf_token'] = AuthManager.get_csrf_token()
                # 将csrf_token同时下发到cookie
                resp = jsonify(result)
                resp.set_cookie('csrf_token', AuthManager.get_csrf_token() or '', httponly=False, samesite='Lax', secure=is_production)
                return resp
            else:
                return jsonify(result), 401
                
        except Exception as e:
            logger.error(f"用户登录失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/auth/logout', methods=['POST'])
    def api_logout():
        """API: 用户登出"""
        try:
            AuthManager.logout_user()
            return jsonify({'success': True, 'message': '登出成功'})
        except Exception as e:
            logger.error(f"用户登出失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/auth/check', methods=['GET'])
    @login_required
    def check_auth_status():
        """API: 检查登录状态"""
        try:
            user_id = AuthManager.get_current_user_id()
            user = AuthManager.get_user_by_id(user_id)
            
            if not user:
                return jsonify({
                    'success': False,
                    'authenticated': False,
                    'error': '用户不存在'
                }), 401
            
            if not user.get('is_active', False):
                return jsonify({
                    'success': False,
                    'authenticated': False,
                    'error': '用户账户已被禁用'
                }), 401
            
            # 调试信息：检查session和数据库中的管理员状态
            current_user = AuthManager.get_current_user()
            session_admin = session.get('is_admin', False)
            db_admin = user.get('is_admin', False)
            
            logger.info(f"用户 {user_id} 权限检查: session.is_admin={session_admin}, db.is_admin={db_admin}")
            
            return jsonify({
                'success': True,
                'authenticated': True,
                'csrf_token': AuthManager.get_csrf_token(),
                'user': {
                    'id': user['id'],
                    'username': user['username'],
                    'email': user['email'],
                    'is_active': user['is_active'],
                    'is_admin': user['is_admin'],
                    'session_admin': session_admin,
                    'db_admin': db_admin
                }
            })
            
        except Exception as e:
            logger.error(f"检查登录状态失败: {e}")
            return jsonify({
                'success': False,
                'authenticated': False,
                'error': str(e)
            }), 401

    @app.route('/api/user/profile')
    @api_auth_required
    def api_get_user_profile():
        """API: 获取用户资料"""
        try:
            user = AuthManager.get_current_user()
            return jsonify({
                'success': True,
                'user': user
            })
        except Exception as e:
            logger.error(f"获取用户资料失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/user/subscription', methods=['GET'])
    @api_auth_required
    def api_get_subscription_config():
        """API: 获取用户订阅等级配置"""
        try:
            user_id = AuthManager.get_current_user_id()
            from .services.config_service import UserConfigService
            svc = UserConfigService()
            cfg = svc.get_subscription_config(user_id)
            return jsonify(cfg)
        except Exception as e:
            logger.error(f"获取订阅配置失败: {e}")
            return jsonify({'importance_levels': ['important','normal','unimportant','subscribed']}), 200

    @app.route('/api/user/subscription', methods=['POST'])
    @api_auth_required
    def api_set_subscription_config():
        """API: 设置用户订阅等级配置"""
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            levels = data.get('importance_levels')
            duration_markers = data.get('duration_as_markers')
            if not isinstance(levels, list):
                return jsonify({'success': False, 'error': 'importance_levels必须为数组'}), 400
            # 校验可选值
            valid = {'important','normal','unimportant','subscribed'}
            clean_levels = [str(v) for v in levels if str(v) in valid]
            if not clean_levels:
                # 允许空则默认全选
                clean_levels = ['important','normal','unimportant','subscribed']
            from .services.config_service import UserConfigService
            svc = UserConfigService()
            ok = svc.set_subscription_config(user_id, clean_levels, duration_markers)
            return jsonify({'success': bool(ok), 'importance_levels': clean_levels, 'duration_as_markers': bool(duration_markers) if duration_markers is not None else None})
        except Exception as e:
            logger.error(f"设置订阅配置失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    # ==================== 管理员API ====================
    
    @app.route('/api/admin/users')
    @admin_required
    def api_admin_get_users():
        """API: 获取所有用户（管理员）"""
        try:
            users = user_service.get_all_users()
            return jsonify({'success': True, 'users': users})
        except Exception as e:
            logger.error(f"获取用户列表失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
    @admin_required
    def api_admin_delete_user(user_id):
        """API: 删除用户（管理员）"""
        try:
            result = user_service.delete_user(user_id)
            
            if result['success']:
                return jsonify(result)
            else:
                return jsonify(result), 400
                
        except Exception as e:
            logger.error(f"删除用户失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/admin/invitation-codes', methods=['POST'])
    @admin_required
    def api_admin_generate_invitation_code():
        """API: 生成邀请码（管理员）"""
        try:
            data = request.get_json() or {}
            user_role = data.get('user_role', 'user')
            max_uses = data.get('max_uses', 1)
            expires_days = data.get('expires_days', 30)
            
            # 验证用户角色参数
            if user_role not in ['user', 'admin']:
                return jsonify({'success': False, 'error': '无效的用户角色'}), 400
            
            # 使用当前管理员用户ID
            admin_user_id = AuthManager.get_current_user_id()
            result = user_service.generate_invitation_code(admin_user_id, max_uses, expires_days, user_role)
            
            if result['success']:
                return jsonify(result)
            else:
                return jsonify(result), 400
                
        except Exception as e:
            logger.error(f"生成邀请码失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/admin/users/<int:user_id>/ai-stats')
    @admin_required
    def api_admin_get_user_ai_stats(user_id):
        """API: 获取用户AI使用统计（管理员）"""
        try:
            days = int(request.args.get('days', 30))
            result = user_service.get_user_ai_stats(user_id, days)
            
            if result['success']:
                return jsonify(result)
            else:
                return jsonify(result), 400
                
        except Exception as e:
            logger.error(f"获取AI统计失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/calendar/caldav', methods=['GET', 'PROPFIND', 'REPORT', 'HEAD', 'OPTIONS'])
    @app.route('/api/calendar/caldav/', methods=['GET', 'PROPFIND', 'REPORT', 'HEAD', 'OPTIONS'])
    @login_required
    def api_caldav():
        """API: CalDAV协议支持（基础实现）"""
        try:
            # 强制CalDAV使用Basic认证，避免浏览器Cookie误用
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Basic '):
                return '', 401, {'WWW-Authenticate': 'Basic realm="CalDAV"'}
            # 统一增加 CalDAV 必要响应头，提升兼容性
            common_headers = {
                'DAV': '1, 2, calendar-access',
                'Allow': 'OPTIONS, GET, HEAD, PROPFIND, REPORT'
            }

            if request.method == 'OPTIONS':
                return '', 200, common_headers

            if request.method == 'HEAD':
                h = {'Content-Type': 'application/xml'}
                h.update(common_headers)
                return '', 200, h
            
            if request.method == 'GET':
                # 返回日历信息
                resp = jsonify({
                    'calendar_name': '邮件智能日程',
                    'description': '从邮件中提取的智能日程事件',
                    'subscribe_url': url_for('api_calendar_subscribe', _external=True),
                    'export_url': url_for('api_export_ical', _external=True)
                })
                for k, v in common_headers.items():
                    resp.headers[k] = v
                return resp
            
            elif request.method == 'PROPFIND':
                # CalDAV属性查询（包含事件资源列表）
                user_id = AuthManager.get_current_user_id()
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                rows = db.execute_query(
                    "SELECT id, updated_at FROM events WHERE user_id = ? ORDER BY updated_at DESC LIMIT 500",
                    (user_id,)
                )
                base_href = '/api/calendar/caldav'
                principal_href = f"/api/calendar/caldav/users/{user_id}/"
                parts = [
                    '<?xml version="1.0" encoding="utf-8" ?>',
                    '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">',
                    '  <D:response>',
                    f'    <D:href>{base_href}</D:href>',
                    '    <D:propstat>',
                    '      <D:prop>',
                    '        <D:displayname>邮件智能日程</D:displayname>',
                    '        <C:calendar-description>从邮件中提取的智能日程事件</C:calendar-description>',
                    '        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>',
                    f'        <D:current-user-principal><D:href>{principal_href}</D:href></D:current-user-principal>',
                    f'        <C:calendar-home-set><D:href>{principal_href}</D:href></C:calendar-home-set>',
                    '      </D:prop>',
                    '      <D:status>HTTP/1.1 200 OK</D:status>',
                    '    </D:propstat>',
                    '  </D:response>'
                ]
                for r in rows:
                    href = f"{base_href}/events/{r['id']}.ics"
                    etag = f"\"event-{r['id']}-{r.get('updated_at','')}\""
                    parts.extend([
                        '  <D:response>',
                        f'    <D:href>{href}</D:href>',
                        '    <D:propstat>',
                        '      <D:prop>',
                        '        <D:getcontenttype>text/calendar</D:getcontenttype>',
                        f'        <D:getetag>{etag}</D:getetag>',
                        '      </D:prop>',
                        '      <D:status>HTTP/1.1 200 OK</D:status>',
                        '    </D:propstat>',
                        '  </D:response>'
                    ])
                parts.append('</D:multistatus>')
                xml_response = "\n".join(parts)
                headers = {'Content-Type': 'application/xml'}
                headers.update(common_headers)
                return xml_response, 207, headers
            
            elif request.method == 'REPORT':
                # CalDAV报告查询（用户隔离）
                user_id = AuthManager.get_current_user_id()
                events = scheduler_service.get_upcoming_events(user_id, 365)
                ical_content = scheduler_service.export_to_ical(events, user_id=user_id)
                headers = {'Content-Type': 'text/calendar'}
                headers.update(common_headers)
                return ical_content, 200, headers
                
        except Exception as e:
            logger.error(f"CalDAV请求失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @app.route('/api/calendar/caldav/events/<int:event_id>.ics', methods=['GET', 'HEAD'])
    @login_required
    def api_caldav_event_ics(event_id: int):
        """CalDAV: 单事件 iCal 获取（支持 HEAD/GET，含缓存头）"""
        try:
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Basic '):
                return '', 401, {'WWW-Authenticate': 'Basic realm="CalDAV"'}
            user_id = AuthManager.get_current_user_id()
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            row_list = db.execute_query(
                "SELECT * FROM events WHERE id = ? AND user_id = ?",
                (event_id, user_id)
            )
            if not row_list:
                return 'Not Found', 404

            event = row_list[0]
            # 导出 iCal
            ical_content = scheduler_service.export_to_ical([event], user_id=user_id)

            # 缓存头
            updated_at = event.get('updated_at') or datetime.now()
            if isinstance(updated_at, datetime):
                last_modified = updated_at.strftime('%a, %d %b %Y %H:%M:%S GMT')
            else:
                # 字符串：尽力转换为 HTTP-date，否则原样
                try:
                    dt = datetime.fromisoformat(str(updated_at).replace('Z', '+00:00'))
                    last_modified = dt.strftime('%a, %d %b %Y %H:%M:%S GMT')
                except Exception:
                    last_modified = str(updated_at)
            etag = f'W/"event-{event_id}-{event.get("updated_at","")}"'
            headers = {
                'Content-Type': 'text/calendar',
                'ETag': etag,
                'Last-Modified': last_modified
            }

            if request.method == 'HEAD':
                return '', 200, headers
            return ical_content, 200, headers
        except Exception as e:
            logger.error(f"CalDAV单事件获取失败: {e}")
            return 'Internal Server Error', 500

    @app.route('/api/calendar/caldav/users/<int:user_id>/', methods=['GET', 'PROPFIND', 'REPORT', 'HEAD', 'OPTIONS'])
    @login_required
    def api_caldav_user_home(user_id: int):
        """CalDAV: 用户专属集合（principal/calendar-home-set）"""
        try:
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Basic '):
                return '', 401, {'WWW-Authenticate': 'Basic realm="CalDAV"'}
            current_id = AuthManager.get_current_user_id()
            if not current_id or current_id != user_id:
                return 'Forbidden', 403
            common_headers = {
                'DAV': '1, 2, calendar-access',
                'Allow': 'OPTIONS, GET, HEAD, PROPFIND, REPORT'
            }
            if request.method == 'OPTIONS':
                return '', 200, common_headers
            if request.method == 'HEAD':
                h = {'Content-Type': 'application/xml'}
                h.update(common_headers)
                return '', 200, h
            if request.method == 'PROPFIND':
                # 返回用户home-set，以及默认日历集合
                xml_response = f'''<?xml version="1.0" encoding="utf-8" ?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:response>
    <D:href>/api/calendar/caldav/users/{user_id}/</D:href>
        <D:propstat>
            <D:prop>
        <D:displayname>用户 {user_id} 的日历Home</D:displayname>
        <D:resourcetype><D:collection/></D:resourcetype>
        <C:calendar-home-set><D:href>/api/calendar/caldav/users/{user_id}/</D:href></C:calendar-home-set>
      </D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
  <D:response>
    <D:href>/api/calendar/caldav/users/{user_id}/default/</D:href>
    <D:propstat>
      <D:prop>
        <D:displayname>默认日历</D:displayname>
        <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>
        <C:supported-calendar-component-set>
          <C:comp name="VEVENT"/>
        </C:supported-calendar-component-set>
            </D:prop>
            <D:status>HTTP/1.1 200 OK</D:status>
        </D:propstat>
    </D:response>
</D:multistatus>'''
                headers = {'Content-Type': 'application/xml'}
                headers.update(common_headers)
                return xml_response, 207, headers
            # GET/REPORT 返回与根一致的汇总（可扩展为用户过滤）
            events = scheduler_service.get_upcoming_events(user_id, 365)
            ical_content = scheduler_service.export_to_ical(events, user_id=user_id)
            headers = {'Content-Type': 'text/calendar'}
            headers.update(common_headers)
            return ical_content, 200, headers
        except Exception as e:
            logger.error(f"CalDAV用户集合失败: {e}")
            return 'Internal Server Error', 500

    @app.route('/api/calendar/caldav/users/<int:user_id>/default/', methods=['GET', 'PROPFIND', 'REPORT', 'HEAD', 'OPTIONS'])
    @login_required
    def api_caldav_user_default(user_id: int):
        try:
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Basic '):
                return '', 401, {'WWW-Authenticate': 'Basic realm="CalDAV"'}
            current_id = AuthManager.get_current_user_id()
            if not current_id or current_id != user_id:
                return 'Forbidden', 403
            common_headers = {
                'DAV': '1, 2, calendar-access',
                'Allow': 'OPTIONS, GET, HEAD, PROPFIND, REPORT'
            }
            if request.method == 'OPTIONS':
                return '', 200, common_headers
            if request.method == 'HEAD':
                h = {'Content-Type': 'application/xml'}
                h.update(common_headers)
                return '', 200, h
            if request.method == 'PROPFIND':
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                rows = db.execute_query(
                    "SELECT id, updated_at FROM events WHERE user_id = ? ORDER BY updated_at DESC LIMIT 500",
                    (user_id,)
                )
                # 读取订阅偏好
                duration_as_markers = False
                try:
                    from .services.config_service import UserConfigService
                    _svc = UserConfigService()
                    sub_cfg = _svc.get_subscription_config(user_id)
                    duration_as_markers = bool(sub_cfg.get('duration_as_markers', False))
                except Exception:
                    duration_as_markers = False
                parts = [
                    '<?xml version="1.0" encoding="utf-8" ?>',
                    '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">',
                    f'  <D:response><D:href>/api/calendar/caldav/users/{user_id}/default/</D:href>',
                    '    <D:propstat><D:prop>',
                    '      <D:resourcetype><D:collection/><C:calendar/></D:resourcetype>',
                    '    </D:prop><D:status>HTTP/1.1 200 OK</D:status></D:propstat></D:response>'
                ]
                for r in rows:
                    href = f"/api/calendar/caldav/users/{user_id}/default/{r['id']}.ics"
                    etag = f"\"event-{r['id']}-{r.get('updated_at','')}\""
                    parts.extend([
                        '  <D:response>',
                        f'    <D:href>{href}</D:href>',
                        '    <D:propstat>',
                        '      <D:prop>',
                        '        <D:getcontenttype>text/calendar</D:getcontenttype>',
                        f'        <D:getetag>{etag}</D:getetag>',
                        '      </D:prop>',
                        '      <D:status>HTTP/1.1 200 OK</D:status>',
                        '    </D:propstat>',
                        '  </D:response>'
                    ])
                    # 如需导出结束标记，增加一个虚拟资源 {id}-end.ics
                    if duration_as_markers:
                        href2 = f"/api/calendar/caldav/users/{user_id}/default/{r['id']}-end.ics"
                        etag2 = f"\"event-end-{r['id']}-{r.get('updated_at','')}\""
                        parts.extend([
                            '  <D:response>',
                            f'    <D:href>{href2}</D:href>',
                            '    <D:propstat>',
                            '      <D:prop>',
                            '        <D:getcontenttype>text/calendar</D:getcontenttype>',
                            f'        <D:getetag>{etag2}</D:getetag>',
                            '      </D:prop>',
                            '      <D:status>HTTP/1.1 200 OK</D:status>',
                            '    </D:propstat>',
                            '  </D:response>'
                        ])
                parts.append('</D:multistatus>')
                xml_response = "\n".join(parts)
                headers = {'Content-Type': 'application/xml'}
                headers.update(common_headers)
                return xml_response, 207, headers
            # REPORT/GET 返回该用户事件集
            if request.method == 'REPORT':
                # 解析 time-range（可选）并返回 Multistatus + calendar-data
                start_dt = None
                end_dt = None
                try:
                    import xml.etree.ElementTree as ET
                    ns = {'D': 'DAV:', 'C': 'urn:ietf:params:xml:ns:caldav'}
                    tree = ET.fromstring(request.data or b'')
                    tr = tree.find('.//C:time-range', ns)
                    if tr is not None:
                        s = tr.attrib.get('start')
                        e = tr.attrib.get('end')
                        from datetime import datetime
                        def _parse_ical_dt(v):
                            # 形如 20250101T000000Z 或 20250101T000000
                            if not v:
                                return None
                            v = v.replace('Z', '')
                            return datetime.strptime(v, '%Y%m%dT%H%M%S')
                        start_dt = _parse_ical_dt(s)
                        end_dt = _parse_ical_dt(e)
                except Exception:
                    start_dt = None
                    end_dt = None

                # 读取事件
                from .models.database import DatabaseManager
                db = DatabaseManager(config)
                rows = db.execute_query(
                    "SELECT * FROM events WHERE user_id = ? ORDER BY start_time ASC",
                    (user_id,)
                )
                # 运行时应用标签订阅升级，保证历史事件也能按“订阅”级导出
                try:
                    email_ids = [int(r['email_id']) for r in rows if r.get('email_id')]
                    tag_map = tag_service.get_email_tags_bulk(user_id, email_ids)
                    for r in rows:
                        tags = tag_map.get(int(r.get('email_id') or 0), [])
                        if not tags:
                            continue
                        hit, _ = tag_service.is_subscribed(user_id, tags[0])
                        if hit:
                            r['importance_level'] = 'subscribed'
                            r['color'] = '#28a745'
                except Exception as _e:
                    logger.warning(f"CalDAV应用订阅标签升级失败: {_e}")
                def _in_range(ev):
                    try:
                        st = ev.get('start_time')
                        from datetime import datetime
                        if isinstance(st, str):
                            st_dt = datetime.fromisoformat(st)
                        else:
                            st_dt = st
                        if start_dt and st_dt < start_dt:
                            return False
                        if end_dt and st_dt > end_dt:
                            return False
                        return True
                    except Exception:
                        return True
                # 应用订阅等级
                try:
                    from .services.config_service import UserConfigService
                    _svc = UserConfigService()
                    sub_cfg = _svc.get_subscription_config(user_id)
                    allowed = set((sub_cfg.get('importance_levels') or []))
                except Exception:
                    allowed = set(['important','normal','unimportant','subscribed'])
                events = [r for r in rows if _in_range(r) and (r.get('importance_level') in allowed)]

                # 读取订阅偏好
                duration_as_markers = False
                try:
                    from .services.config_service import UserConfigService
                    _svc = UserConfigService()
                    sub_cfg = _svc.get_subscription_config(user_id)
                    duration_as_markers = bool(sub_cfg.get('duration_as_markers', False))
                except Exception:
                    duration_as_markers = False

                # 组装 multistatus
                parts = [
                    '<?xml version="1.0" encoding="utf-8" ?>',
                    '<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
                ]
                for ev in events:
                    # 起始标记
                    href = f"/api/calendar/caldav/users/{user_id}/default/{ev['id']}.ics"
                    etag = f"\"event-{ev['id']}-{ev.get('updated_at','')}\""
                    if duration_as_markers and ev.get('end_time'):
                        # 构建仅开始点的 iCal
                        ev_start = dict(ev)
                        ev_start['end_time'] = ev_start.get('start_time')
                        ical_start = scheduler_service.export_to_ical([ev_start], user_id=user_id)
                        parts.extend([
                            '  <D:response>',
                            f'    <D:href>{href}</D:href>',
                            '    <D:propstat>',
                            '      <D:prop>',
                            '        <D:getcontenttype>text/calendar</D:getcontenttype>',
                            f'        <D:getetag>{etag}</D:getetag>',
                            '        <C:calendar-data><![CDATA[' + (ical_start or '') + ']]></C:calendar-data>',
                            '      </D:prop>',
                            '      <D:status>HTTP/1.1 200 OK</D:status>',
                            '    </D:propstat>',
                            '  </D:response>'
                        ])
                        # 结束标记资源
                        href2 = f"/api/calendar/caldav/users/{user_id}/default/{ev['id']}-end.ics"
                        etag2 = f"\"event-end-{ev['id']}-{ev.get('updated_at','')}\""
                        ev_end = dict(ev)
                        try:
                            end_iso = ev_end.get('end_time')
                            from datetime import datetime
                            if isinstance(end_iso, str):
                                end_dt = datetime.fromisoformat(end_iso)
                            else:
                                end_dt = end_iso
                            ev_end['start_time'] = end_dt
                            ev_end['end_time'] = end_dt
                            # 修改标题以提示结束
                            title = ev_end.get('title', '')
                            ev_end['title'] = f"结束: {title}"
                        except Exception:
                            pass
                        ical_end = scheduler_service.export_to_ical([ev_end], user_id=user_id)
                        parts.extend([
                            '  <D:response>',
                            f'    <D:href>{href2}</D:href>',
                            '    <D:propstat>',
                            '      <D:prop>',
                            '        <D:getcontenttype>text/calendar</D:getcontenttype>',
                            f'        <D:getetag>{etag2}</D:getetag>',
                            '        <C:calendar-data><![CDATA[' + (ical_end or '') + ']]></C:calendar-data>',
                            '      </D:prop>',
                            '      <D:status>HTTP/1.1 200 OK</D:status>',
                            '    </D:propstat>',
                            '  </D:response>'
                        ])
                    else:
                        # 默认行为（单资源）
                        ical_single = scheduler_service.export_to_ical([ev], user_id=user_id)
                        parts.extend([
                            '  <D:response>',
                            f'    <D:href>{href}</D:href>',
                            '    <D:propstat>',
                            '      <D:prop>',
                            '        <D:getcontenttype>text/calendar</D:getcontenttype>',
                            f'        <D:getetag>{etag}</D:getetag>',
                            '        <C:calendar-data><![CDATA[' + (ical_single or '') + ']]></C:calendar-data>',
                            '      </D:prop>',
                            '      <D:status>HTTP/1.1 200 OK</D:status>',
                            '    </D:propstat>',
                            '  </D:response>'
                        ])
                parts.append('</D:multistatus>')
                xml_response = "\n".join(parts)
                headers = {'Content-Type': 'application/xml'}
                headers.update(common_headers)
                return xml_response, 207, headers

            # GET 返回汇总 ICS
                events = scheduler_service.get_upcoming_events(user_id, 365)
            try:
                from .services.config_service import UserConfigService
                _svc = UserConfigService()
                sub_cfg = _svc.get_subscription_config(user_id)
                allowed = set((sub_cfg.get('importance_levels') or []))
                if allowed:
                    events = [e for e in events if (e.get('importance_level') in allowed)]
            except Exception:
                pass
            ical_content = scheduler_service.export_to_ical(events, user_id=user_id)
            headers = {'Content-Type': 'text/calendar'}
            headers.update(common_headers)
            return ical_content, 200, headers
        except Exception as e:
            logger.error(f"CalDAV默认日历失败: {e}")
            return 'Internal Server Error', 500

    @app.route('/api/calendar/caldav/users/<int:user_id>/default/<int:event_id>.ics', methods=['GET', 'HEAD'])
    @login_required
    def api_caldav_user_event(user_id: int, event_id: int):
        """CalDAV: 用户路径下的单事件导出

        注意：必须校验 path user_id 与当前认证用户一致，避免出现“/users/2/... 返回 user1 的事件”的语义混乱。
        """
        try:
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Basic '):
                return '', 401, {'WWW-Authenticate': 'Basic realm="CalDAV"'}
            current_id = AuthManager.get_current_user_id()
            if not current_id or current_id != user_id:
                return 'Forbidden', 403
        except Exception:
            return 'Forbidden', 403
        return api_caldav_event_ics(event_id)

    # 支持结束标记资源 /users/{uid}/default/{event_id}-end.ics
    @app.route('/api/calendar/caldav/users/<int:user_id>/default/<res>.ics', methods=['GET', 'HEAD'])
    @login_required
    def api_caldav_user_event_ext(user_id: int, res: str):
        try:
            auth_header = request.headers.get('Authorization', '')
            if not auth_header.startswith('Basic '):
                return '', 401, {'WWW-Authenticate': 'Basic realm="CalDAV"'}
            current_id = AuthManager.get_current_user_id()
            if not current_id or current_id != user_id:
                return 'Forbidden', 403
            is_end = False
            event_id_str = res
            if res.endswith('-end'):
                is_end = True
                event_id_str = res[:-4]
            if not event_id_str.isdigit():
                return 'Not Found', 404
            event_id = int(event_id_str)
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            rows = db.execute_query(
                "SELECT * FROM events WHERE id = ? AND user_id = ?",
                (event_id, user_id)
            )
            if not rows:
                return 'Not Found', 404
            ev = dict(rows[0])
            # 构造单一的开始或结束标记 iCal
            from icalendar import Calendar, Event as ICalEvent
            from datetime import datetime
            cal = Calendar()
            cal.add('prodid', '-//Mail Scheduler//EN')
            cal.add('version', '2.0')
            ical_ev = ICalEvent()
            title = ev.get('title', '未命名事件')
            start = ev.get('start_time')
            end = ev.get('end_time')
            if isinstance(start, str):
                start = datetime.fromisoformat(start)
            if isinstance(end, str) if end is not None else False:
                end = datetime.fromisoformat(end)
            if is_end and end:
                ical_ev.add('summary', f"🔚 结束: {title}")
                ical_ev.add('dtstart', end)
                ical_ev.add('dtend', end)
                ical_ev.add('uid', f"event-end-{event_id}@mail-scheduler")
            else:
                ical_ev.add('summary', title)
                ical_ev.add('dtstart', start)
                ical_ev.add('dtend', start)
                ical_ev.add('uid', f"event-start-{event_id}@mail-scheduler")
            ical_ev.add('dtstamp', datetime.now())
            importance_level = ev.get('importance_level', 'normal')
            category = '重要事件' if importance_level == 'important' else ('一般事件' if importance_level == 'unimportant' else '普通事件')
            ical_ev.add('categories', category)
            desc = ev.get('description', '')
            importance_text = {
                'important': '重要程度：🔴 重要',
                'normal': '重要程度：🟡 普通',
                'unimportant': '重要程度：🔵 一般'
            }.get(importance_level, '重要程度：🟡 普通')
            ical_ev.add('description', f"{importance_text}\n\n{desc}" if desc else importance_text)
            if ev.get('location'):
                ical_ev.add('location', ev['location'])
            ical_ev.add('priority', 1 if importance_level == 'important' else (9 if importance_level == 'unimportant' else 5))
            cal.add_component(ical_ev)
            ical_str = cal.to_ical().decode('utf-8')
            headers = {'Content-Type': 'text/calendar'}
            if request.method == 'HEAD':
                return '', 200, headers
            return ical_str, 200, headers
        except Exception as e:
            logger.error(f"CalDAV单资源扩展获取失败: {e}")
            return 'Internal Server Error', 500
    # 兼容 CalDAV 客户端的 well-known 发现
    @app.route('/.well-known/caldav', methods=['GET', 'HEAD', 'PROPFIND', 'REPORT'])
    def well_known_caldav():
        try:
            # 使用 308 永久重定向，保留原请求方法（PROPFIND/REPORT 等）
            target = '/api/calendar/caldav'
            return redirect(target, code=308)
        except Exception as e:
            logger.error(f"well-known CalDAV 重定向失败: {e}")
            return 'Not Found', 404

    @app.route('/api/events/bulk_delete', methods=['POST'])
    @login_required
    def api_bulk_delete_events():
        """API: 批量删除日程
        body 可选参数：
        - all: true 删除当前用户全部事件
        - start/end: ISO 日期字符串，按开始时间范围删除
        """
        try:
            user_id = AuthManager.get_current_user_id()
            data = request.get_json() or {}
            delete_all = bool(data.get('all'))
            start = data.get('start')
            end = data.get('end')
            from .models.database import DatabaseManager
            db = DatabaseManager(config)

            if delete_all:
                db.execute_update("DELETE FROM reminders WHERE user_id = ?", (user_id,))
                db.execute_update("DELETE FROM events WHERE user_id = ?", (user_id,))
                return jsonify({'success': True, 'deleted': 'all'})

            conditions = ["user_id = ?"]
            params = [user_id]
            if start:
                conditions.append("start_time >= ?")
                params.append(start)
            if end:
                conditions.append("start_time <= ?")
                params.append(end)
            where = " AND ".join(conditions)

            # 先找出要删的事件ID，清 reminders 后删 events
            ids = db.execute_query(f"SELECT id FROM events WHERE {where}", tuple(params))
            id_list = [row['id'] for row in ids]
            if id_list:
                q_marks = ",".join(["?"] * len(id_list))
                db.execute_update(f"DELETE FROM reminders WHERE user_id = ? AND event_id IN ({q_marks})", tuple([user_id] + id_list))
                db.execute_update(f"DELETE FROM events WHERE id IN ({q_marks}) AND user_id = ?", tuple(id_list + [user_id]))
            return jsonify({'success': True, 'deleted_ids': id_list})
        except Exception as e:
            logger.error(f"批量删除日程失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/notion/test', methods=['POST'])
    @login_required
    def api_test_notion():
        """API: 测试Notion连接"""
        try:
            user_id = AuthManager.get_current_user_id()
            user_notion_service = get_notion_service(user_id)
            result = user_notion_service.test_connection()
            return jsonify(result)
        except Exception as e:
            logger.error(f"Notion连接测试失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/notion/create_database', methods=['POST'])
    @login_required
    def api_create_notion_database():
        """API: 创建Notion数据库"""
        try:
            user_id = AuthManager.get_current_user_id()
            user_notion_service = get_notion_service(user_id)
            
            data = request.get_json() or {}
            parent_page_id = data.get('parent_page_id')
            
            database_id = user_notion_service.create_database_if_not_exists(parent_page_id)
            
            if database_id:
                return jsonify({
                    'success': True,
                    'database_id': database_id,
                    'message': '数据库创建成功'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': '数据库创建失败'
                }), 500
        except Exception as e:
            logger.error(f"创建Notion数据库失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/notion/archive/<int:email_id>', methods=['POST'])
    @login_required
    def api_archive_to_notion(email_id):
        """API: 归档邮件到Notion"""
        try:
            # 创建数据库管理器实例
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            user_id = AuthManager.get_current_user_id()
            
            # 获取邮件数据
            email_query = "SELECT * FROM emails WHERE id = ? AND user_id = ?"
            email_results = db.execute_query(email_query, (email_id, user_id))
            
            if not email_results:
                return jsonify({
                    'success': False,
                    'error': '邮件不存在'
                }), 404
            
            email_data = dict(email_results[0])
            
            # 获取AI分析结果
            analysis_result = {
                'summary': email_data.get('ai_summary', ''),
                'events': []
            }
            
            # 获取相关事件
            events_query = "SELECT * FROM events WHERE email_id = ? AND user_id = ?"
            events_results = db.execute_query(events_query, (email_id, user_id))
            analysis_result['events'] = [dict(event) for event in events_results]
            
            # 归档到Notion
            from .services.notion_service import NotionService
            user_notion_service = NotionService(config, user_id)
            page_id = user_notion_service.archive_email(email_data, analysis_result)
            
            if page_id:
                return jsonify({
                    'success': True,
                    'page_id': page_id,
                    'message': '邮件归档成功'
                })
            else:
                return jsonify({
                    'success': False,
                    'error': '归档失败'
                }), 500
                
        except Exception as e:
            logger.error(f"归档邮件到Notion失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/notion/archived')
    @login_required
    def api_get_archived_emails():
        """API: 获取已归档的邮件列表"""
        try:
            limit = int(request.args.get('limit', 50))
            archived_emails = notion_service.get_archived_emails(limit)
            
            return jsonify({
                'success': True,
                'emails': archived_emails
            })
            
        except Exception as e:
            logger.error(f"获取已归档邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/notion/page/<int:email_id>')
    @login_required
    def api_get_notion_page_url(email_id):
        """API: 获取邮件对应的Notion页面URL"""
        try:
            page_url = notion_service.get_notion_page_url(email_id)
            
            if page_url:
                return jsonify({
                    'success': True,
                    'page_url': page_url
                })
            else:
                return jsonify({
                    'success': False,
                    'error': '未找到对应的Notion页面'
                }), 404
                
        except Exception as e:
            logger.error(f"获取Notion页面URL失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/notion/search')
    @login_required
    def api_search_notion_pages():
        """API: 搜索Notion页面"""
        try:
            query = request.args.get('q', '')
            limit = int(request.args.get('limit', 10))
            
            if not query:
                return jsonify({
                    'success': False,
                    'error': '搜索关键词不能为空'
                }), 400
            
            results = notion_service.search_pages(query, limit)
            
            return jsonify({
                'success': True,
                'results': results
            })
            
        except Exception as e:
            logger.error(f"搜索Notion页面失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/notion/resync_all', methods=['POST'])
    @admin_required
    def api_resync_all_emails():
        """API: 重新同步所有邮件到Notion"""
        try:
            # 创建数据库管理器实例
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 清除所有Notion归档记录
            clear_query = "DELETE FROM notion_archive"
            db.execute_insert(clear_query, ())
            
            # 获取所有邮件
            emails_query = "SELECT * FROM emails ORDER BY received_date DESC"
            emails = db.execute_query(emails_query, ())
            
            success_count = 0
            fail_count = 0
            
            for email in emails:
                try:
                    email_data = dict(email)
                    
                    # 获取AI分析结果
                    analysis_result = {
                        'summary': email_data.get('ai_summary', ''),
                        'events': []
                    }
                    
                    # 获取相关事件（按 user_id 隔离，避免多用户时混入别人的事件）
                    events_query = "SELECT * FROM events WHERE email_id = ? AND user_id = ?"
                    events_results = db.execute_query(events_query, (email_data['id'], email_data.get('user_id', 1)))
                    analysis_result['events'] = [dict(event) for event in events_results]
                    
                    # 归档到Notion
                    page_id = notion_service.archive_email(email_data, analysis_result)
                    
                    if page_id:
                        success_count += 1
                    else:
                        fail_count += 1
                        
                except Exception as e:
                    logger.error(f"重新同步邮件 {email.get('id')} 失败: {e}")
                    fail_count += 1
            
            return jsonify({
                'success': True,
                'success_count': success_count,
                'fail_count': fail_count,
                'message': f'重新同步完成，成功: {success_count} 封，失败: {fail_count} 封'
            })
            
        except Exception as e:
            logger.error(f"重新同步所有邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/emails/fetch-stream', methods=['GET', 'POST'])
    @login_required
    def fetch_emails_stream():
        """API: 流式获取和处理邮件（SSE）
        
        - start=1: 如无任务则启动后台流式任务，然后订阅输出
        - start 未提供: 仅订阅当前输出（用于断线重连/重新登录恢复显示）
        """
        try:
            user_id = AuthManager.get_current_user_id()
            
            if request.method == 'GET':
                # EventSource请求
                days_back = int(request.args.get('days_back', 1))
                max_count_str = request.args.get('max_count')
                max_count = int(max_count_str) if max_count_str and max_count_str != 'undefined' and max_count_str.isdigit() else None
                start = request.args.get('start') in ('1', 'true', 'True')
                try:
                    analysis_workers = int(request.args.get('analysis_workers', 3))
                except Exception:
                    analysis_workers = 3
            else:
                # POST请求
                data = request.get_json() or {}
                days_back = data.get('days_back', 1)
                max_count = data.get('max_count', None)
                start = True
                try:
                    analysis_workers = int(data.get('analysis_workers', 3))
                except Exception:
                    analysis_workers = 3

            # 合理范围限制，避免把机器打爆 / 触发上游限流
            if analysis_workers < 1:
                analysis_workers = 1
            if analysis_workers > 8:
                analysis_workers = 8
            
            from .services.stream_manager import stream_manager

            if start:
                logger.info(f"[SSE] 启动/复用后台流式任务: user_id={user_id}, days_back={days_back}, max_count={max_count}")
                stream_manager.start_email_stream(user_id, days_back, max_count, analysis_workers, config)
            else:
                logger.info(f"[SSE] 仅订阅后台流式输出: user_id={user_id}")

            def generate():
                try:
                    for ev in stream_manager.subscribe(user_id):
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except Exception as e:
                    logger.error(f"SSE订阅失败: {e}")
                    yield f"data: {json.dumps({'status': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
            
            if request.method == 'GET':
                # EventSource响应
                return Response(generate(), 
                              mimetype='text/event-stream', 
                              headers={
                                  'Cache-Control': 'no-cache',
                                  'Connection': 'keep-alive',
                                  'Access-Control-Allow-Origin': '*',
                                  'Access-Control-Allow-Headers': 'Cache-Control'
                              })
            else:
                # POST响应（兼容旧版本）
                return Response(generate(), 
                              mimetype='text/plain', 
                              headers={
                                  'Cache-Control': 'no-cache',
                                  'Connection': 'keep-alive',
                                  'Access-Control-Allow-Origin': '*',
                                  'Access-Control-Allow-Headers': 'Cache-Control',
                                  'Transfer-Encoding': 'chunked'
                              })
            
        except Exception as e:
            logger.error(f"流式获取邮件失败: {e}")
            logger.error(f"错误详情: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"错误堆栈: {traceback.format_exc()}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500

    @app.route('/api/emails/stream-status', methods=['GET'])
    @login_required
    def api_stream_status():
        """API: 获取当前用户是否存在正在运行的流式任务（用于断线重连/重新登录恢复显示）"""
        try:
            user_id = AuthManager.get_current_user_id()
            from .services.stream_manager import stream_manager
            status = stream_manager.get_status(user_id)
            return jsonify({"success": True, "status": status})
        except Exception as e:
            logger.error(f"获取流式状态失败: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route('/api/emails/stop-stream', methods=['POST'])
    @login_required
    def api_stop_stream():
        """API: 请求终止后台流式任务"""
        try:
            user_id = AuthManager.get_current_user_id()
            from .services.stream_manager import stream_manager
            result = stream_manager.stop(user_id)
            return jsonify({"success": True, **result})
        except Exception as e:
            logger.error(f"终止流式任务失败: {e}")
            return jsonify({"success": False, "error": str(e)}), 500
    
    @app.route('/api/emails/refetch_all', methods=['POST'])
    @login_required
    def api_refetch_all_emails():
        """API: 重新获取所有邮件"""
        try:
            # 获取新邮件（默认获取最近7天的邮件）
            user_id = AuthManager.get_current_user_id()
            days_back = request.json.get('days_back', 7) if request.json else 7
            max_count = request.json.get('max_count') if request.json else None
            new_emails = email_service.fetch_new_emails(user_id, days_back, max_count)
            
            new_count = 0
            updated_count = 0
            
            for email_data in new_emails:
                try:
                    # 检查邮件是否已存在
                    existing_email = email_service.get_email_by_message_id(email_data['message_id'], user_id)
                    
                    if existing_email:
                        # 更新现有邮件
                        email_service.email_model.update_email(existing_email['id'], user_id, email_data)
                        updated_count += 1
                    else:
                        # 保存新邮件
                        email_id = email_service.email_model.save_email(email_data, user_id)
                        new_count += 1
                        
                        # 异步处理AI分析（避免使用未定义的 executor）
                        Thread(target=_process_new_email, args=(email_data, user_id), daemon=True).start()
                        
                except Exception as e:
                    logger.error(f"处理邮件失败: {e}")
                    continue
            
            return jsonify({
                'success': True,
                'new_count': new_count,
                'updated_count': updated_count,
                'message': f'重新获取完成，新增: {new_count} 封，更新: {updated_count} 封'
            })
            
        except Exception as e:
            logger.error(f"重新获取所有邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/emails/refetch_selected', methods=['POST'])
    @login_required
    def api_refetch_selected_emails():
        """API: 重新获取选中的邮件"""
        try:
            data = request.get_json()
            email_ids = data.get('email_ids', [])
            
            if not email_ids:
                return jsonify({
                    'success': False,
                    'error': '未提供邮件ID列表'
                }), 400
            
            # 创建数据库管理器实例
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            success_count = 0
            fail_count = 0
            
            user_id = AuthManager.get_current_user_id()
            for email_id in email_ids:
                try:
                    # 获取邮件的message_id
                    email_query = "SELECT message_id FROM emails WHERE id = ? AND user_id = ?"
                    email_result = db.execute_query(email_query, (email_id, user_id))
                    
                    if not email_result:
                        fail_count += 1
                        continue
                    
                    message_id = email_result[0]['message_id']
                    
                    # 从邮件服务器重新获取这封邮件
                    imap = email_service.connect_imap()
                    imap.select('INBOX')
                    
                    # 获取邮件的基本信息用于搜索
                    email_info_query = "SELECT subject, sender, received_date FROM emails WHERE id = ? AND user_id = ?"
                    email_info_result = db.execute_query(email_info_query, (email_id, user_id))
                    
                    if not email_info_result:
                        logger.error(f"无法获取邮件 {email_id} 的基本信息")
                        fail_count += 1
                        continue
                    
                    email_info = email_info_result[0]
                    subject = email_info['subject']
                    sender = email_info['sender']
                    received_date = email_info['received_date']
                    
                    search_success = False
                    
                    # 方式1: 基于主题和发件人搜索（更可靠）
                    try:
                        # 解析发件人邮箱地址
                        import re
                        sender_email = re.search(r'[\w\.-]+@[\w\.-]+', sender)
                        sender_addr = sender_email.group() if sender_email else sender
                        
                        # 构建搜索条件：主题 + 发件人（处理中文编码）
                        try:
                            # 确保搜索条件使用UTF-8编码
                            subject_safe = subject[:50].encode('utf-8', errors='ignore').decode('utf-8')
                            search_criteria = f'FROM "{sender_addr}" SUBJECT "{subject_safe}"'
                            status, message_ids = imap.search('UTF-8', search_criteria)
                        except UnicodeError:
                            # 如果UTF-8失败，尝试ASCII安全的搜索
                            subject_ascii = subject[:50].encode('ascii', errors='ignore').decode('ascii')
                            search_criteria = f'FROM "{sender_addr}" SUBJECT "{subject_ascii}"'
                            status, message_ids = imap.search(None, search_criteria)
                        logger.info(f"搜索邮件 (主题+发件人): 状态={status}, 结果数量={len(message_ids[0].split()) if message_ids[0] else 0}")
                        
                        if status == 'OK' and message_ids[0]:
                            # 如果找到多个结果，选择最匹配的
                            msg_ids = message_ids[0].split()
                            
                            for msg_id in msg_ids:
                                try:
                                    status, msg_data = imap.fetch(msg_id, '(RFC822)')
                                    
                                    if status == 'OK':
                                        import email
                                        msg = email.message_from_bytes(msg_data[0][1])
                                        
                                        # 验证是否是同一封邮件（通过Message-ID）
                                        fetched_message_id = msg.get('Message-ID', '')
                                        if fetched_message_id == message_id:
                                            email_data = email_service.parse_email_message(msg)
                                            
                                            if email_data:
                                                # 更新邮件数据
                                                if email_service.email_model.update_email(email_id, user_id, email_data):
                                                    success_count += 1
                                                    search_success = True
                                                    logger.info(f"成功更新邮件 {email_id}")
                                                    break
                                                else:
                                                    logger.error(f"更新邮件 {email_id} 到数据库失败")
                                            else:
                                                logger.error(f"解析邮件 {email_id} 失败")
                                except Exception as fetch_e:
                                    logger.error(f"获取邮件内容异常: {fetch_e}")
                                    continue
                        
                        if not search_success:
                            logger.warning(f"未找到匹配的邮件: 主题={subject[:30]}..., 发件人={sender_addr}")
                            
                    except Exception as search_e:
                        logger.error(f"搜索邮件异常: {search_e}")
                    
                    if not search_success:
                        fail_count += 1
                    
                    try:
                        imap.close()
                        imap.logout()
                    except:
                        pass  # 忽略关闭连接时的错误
                    
                except Exception as e:
                    logger.error(f"重新获取邮件 {email_id} 失败: {e}")
                    fail_count += 1
                    # 确保IMAP连接被关闭
                    try:
                        if 'imap' in locals():
                            imap.close()
                            imap.logout()
                    except:
                        pass
            
            return jsonify({
                'success': True,
                'success_count': success_count,
                'fail_count': fail_count,
                'message': f'重新获取完成，成功: {success_count} 封，失败: {fail_count} 封'
            })
            
        except Exception as e:
            logger.error(f"重新获取选中邮件失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/system/delete_all_emails', methods=['POST'])
    @login_required
    def api_delete_all_emails():
        """API: 删除当前用户的所有邮件数据"""
        try:
            user_id = AuthManager.get_current_user_id()
            
            # 创建数据库管理器实例
            from .models.database import DatabaseManager
            import shutil
            import os
            
            db = DatabaseManager(config)
            
            # 获取删除前的邮件数量
            count_query = "SELECT COUNT(*) as count FROM emails WHERE user_id = ?"
            count_result = db.execute_query(count_query, (user_id,))
            deleted_count = count_result[0]['count'] if count_result else 0
            
            # 删除当前用户的所有相关数据
            delete_queries = [
                ("DELETE FROM keyword_matches WHERE email_id IN (SELECT id FROM emails WHERE user_id = ?)", (user_id,)),
                ("DELETE FROM notion_archive WHERE email_id IN (SELECT id FROM emails WHERE user_id = ?)", (user_id,)),
                ("DELETE FROM reminders WHERE email_id IN (SELECT id FROM emails WHERE user_id = ?)", (user_id,)),
                ("DELETE FROM events WHERE email_id IN (SELECT id FROM emails WHERE user_id = ?)", (user_id,)),
                ("DELETE FROM email_analysis WHERE email_id IN (SELECT id FROM emails WHERE user_id = ?)", (user_id,)),
                ("DELETE FROM emails WHERE user_id = ?", (user_id,))
            ]
            
            for query, params in delete_queries:
                try:
                    db.execute_update(query, params)
                except Exception as e:
                    logger.warning(f"删除数据时出现警告: {e}")

            # 同步重置当前用户的增量游标（否则删除数据后会误判“没有新邮件”）
            try:
                from .services.config_service import UserConfigService
                ucfg = UserConfigService()
                ucfg.set_user_config(user_id, 'email', 'last_seen_uid', 0)
                logger.info(f"用户 {user_id} 的 last_seen_uid 已重置为 0")
            except Exception as e:
                logger.warning(f"重置用户 {user_id} last_seen_uid 失败: {e}")
            
            # 删除当前用户的附件文件夹
            attachments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'attachments', str(user_id))
            if os.path.exists(attachments_dir):
                try:
                    shutil.rmtree(attachments_dir)
                    os.makedirs(attachments_dir, exist_ok=True)
                    logger.info(f"用户 {user_id} 的附件文件夹已清空")
                except Exception as e:
                    logger.warning(f"清空用户 {user_id} 附件文件夹时出现警告: {e}")
            
            logger.info(f"用户 {user_id} 已删除所有邮件数据，共 {deleted_count} 封邮件")
            
            # 清除邮件缓存，确保前端立即看到更新
            try:
                clear_email_cache()
            except Exception:
                pass
            
            return jsonify({
                'success': True,
                'deleted_count': deleted_count,
                'message': f'成功删除 {deleted_count} 封邮件及相关数据，并已重置同步游标'
            })
            
        except Exception as e:
            logger.error(f"删除邮件数据失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/api/system/stats', methods=['GET'])
    def api_get_system_stats():
        """API: 获取系统统计数据"""
        try:
            # 创建数据库管理器实例
            from .models.database import DatabaseManager
            import os
            
            db = DatabaseManager(config)
            
            # 获取邮件总数
            email_count_query = "SELECT COUNT(*) as count FROM emails"
            email_result = db.execute_query(email_count_query, ())
            total_emails = email_result[0]['count'] if email_result else 0
            
            # 获取事件总数
            event_count_query = "SELECT COUNT(*) as count FROM events"
            event_result = db.execute_query(event_count_query, ())
            total_events = event_result[0]['count'] if event_result else 0
            
            # 获取Notion归档数量
            notion_count_query = "SELECT COUNT(*) as count FROM notion_archive"
            notion_result = db.execute_query(notion_count_query, ())
            notion_archived = notion_result[0]['count'] if notion_result else 0
            
            # 获取附件数量
            attachments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'attachments')
            total_attachments = 0
            if os.path.exists(attachments_dir):
                try:
                    total_attachments = len([f for f in os.listdir(attachments_dir) if os.path.isfile(os.path.join(attachments_dir, f))])
                except Exception as e:
                    logger.warning(f"统计附件数量时出现警告: {e}")
            
            return jsonify({
                'success': True,
                'stats': {
                    'total_emails': total_emails,
                    'total_events': total_events,
                    'notion_archived': notion_archived,
                    'total_attachments': total_attachments
                }
            })
            
        except Exception as e:
            logger.error(f"获取系统统计失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
    @app.route('/attachments/<int:attachment_id>')
    @login_required
    def serve_attachment(attachment_id):
        """提供附件文件访问（从数据库）"""
        try:
            from .models.database import AttachmentModel
            import io
            from flask import send_file
            
            user_id = AuthManager.get_current_user_id()
            attachment_model = AttachmentModel(config)
            
            # 从数据库获取附件
            attachment = attachment_model.get_attachment(attachment_id, user_id)
            
            if not attachment:
                return jsonify({'error': '附件不存在或无权限访问'}), 404
            file_data = attachment.get('file_data')
            if isinstance(file_data, memoryview):
                file_data = file_data.tobytes()
            if file_data is None:
                return jsonify({'error': '附件数据为空'}), 404

            # 使用 send_file 更稳健（header 编码/Range 等）
            content_type = attachment.get('content_type') or 'application/octet-stream'
            download_name = attachment.get('filename') or f'attachment_{attachment_id}'
            return send_file(
                io.BytesIO(file_data),
                mimetype=content_type,
                as_attachment=False,
                download_name=download_name,
                max_age=3600,
            )
            
        except Exception as e:
            logger.exception(f"提供附件文件失败: attachment_id={attachment_id}, error={e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/attachments/filename/<path:filename>')
    @login_required
    def serve_attachment_by_filename(filename):
        """根据文件名提供附件文件访问（向后兼容）"""
        try:
            from .models.database import AttachmentModel
            import io
            from flask import send_file
            
            user_id = AuthManager.get_current_user_id()
            attachment_model = AttachmentModel(config)
            
            # 根据文件名查找附件
            query = """
            SELECT id, filename, content_type, file_size, file_data 
            FROM attachments 
            WHERE filename = ? AND user_id = ?
            """
            
            results = attachment_model.db.execute_query(query, (filename, user_id))
            
            if not results:
                return jsonify({'error': '附件不存在或无权限访问'}), 404
            
            attachment = results[0]
            file_data = attachment.get('file_data')
            if isinstance(file_data, memoryview):
                file_data = file_data.tobytes()
            if file_data is None:
                return jsonify({'error': '附件数据为空'}), 404

            content_type = attachment.get('content_type') or 'application/octet-stream'
            download_name = attachment.get('filename') or filename
            return send_file(
                io.BytesIO(file_data),
                mimetype=content_type,
                as_attachment=False,
                download_name=download_name,
                max_age=3600,
            )
            
        except Exception as e:
            logger.exception(f"提供附件文件失败: filename={filename}, error={e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/attachments/<path:unique_filename>')
    @login_required
    def serve_attachment_by_unique_filename(unique_filename):
        """根据 unique_filename（实际存库的 filename 字段）提供附件访问（推荐路径）"""
        try:
            # 兼容 /attachments/123 这种数字路径（优先走 id 路由，但这里再兜底一次）
            if unique_filename.isdigit():
                return serve_attachment(int(unique_filename))

            from .models.database import AttachmentModel
            import io
            from flask import send_file

            user_id = AuthManager.get_current_user_id()
            attachment_model = AttachmentModel(config)

            query = """
            SELECT id, filename, content_type, file_size, file_data 
            FROM attachments 
            WHERE filename = ? AND user_id = ?
            """
            results = attachment_model.db.execute_query(query, (unique_filename, user_id))
            if not results:
                return jsonify({'error': '附件不存在或无权限访问'}), 404

            attachment = results[0]
            file_data = attachment.get('file_data')
            if isinstance(file_data, memoryview):
                file_data = file_data.tobytes()
            if file_data is None:
                return jsonify({'error': '附件数据为空'}), 404

            content_type = attachment.get('content_type') or 'application/octet-stream'
            download_name = attachment.get('filename') or unique_filename
            return send_file(
                io.BytesIO(file_data),
                mimetype=content_type,
                as_attachment=False,
                download_name=download_name,
                max_age=3600,
            )
        except Exception as e:
            logger.exception(f"提供附件文件失败: unique_filename={unique_filename}, error={e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/attachments/remote')
    @login_required
    def serve_remote_image():
        """代理远程图片，避免混合内容与防盗链问题
        用法：/attachments/remote?u=https%3A%2F%2Fexample.com%2Fimg.png
        可选：w=, h= 简单下游裁剪（仅透传，不处理）
        """
        try:
            import requests
            from urllib.parse import urlparse
            target_url = request.args.get('u', '').strip()
            if not target_url:
                return jsonify({'error': '缺少参数u'}), 400
            # 仅允许 http/https
            parsed = urlparse(target_url)
            if parsed.scheme not in ('http', 'https'):
                return jsonify({'error': '不支持的协议'}), 400
            # 发起请求（不携带引用站点Referer，降低防盗链影响；可按需增加UA）
            headers = {
                'User-Agent': 'Mozilla/5.0 (MailScheduler/1.0)'
            }
            # 超时与流式
            resp = requests.get(target_url, headers=headers, timeout=10, stream=True, verify=True)
            # 仅允许图片MIME
            content_type = resp.headers.get('Content-Type', '').lower()
            if not content_type.startswith('image/'):
                # 尝试猜测
                if any(target_url.lower().endswith(ext) for ext in ['.png','.jpg','.jpeg','.gif','.webp','.bmp','svg']):
                    guessed = 'image/' + target_url.lower().split('.')[-1]
                    content_type = guessed
                else:
                    return jsonify({'error': '目标不是图片'}), 400
            from flask import Response
            return Response(resp.iter_content(chunk_size=64 * 1024), mimetype=content_type)
        except requests.exceptions.SSLError:
            return jsonify({'error': '远程站点SSL错误'}), 502
        except requests.exceptions.Timeout:
            return jsonify({'error': '远程站点超时'}), 504
        except Exception as e:
            logger.error(f"远程图片代理失败: {e}")
            return jsonify({'error': '获取远程图片失败'}), 502
    
    @app.errorhandler(404)
    def not_found(error):
        return _render_page('404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return _render_page('500.html'), 500
    
    return app