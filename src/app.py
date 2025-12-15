# -*- coding: utf-8 -*-
"""
Flask应用主文件
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
from flask_cors import CORS
import os
import json
from pathlib import Path
from datetime import datetime
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


def create_app():
    """创建Flask应用实例"""
    app = Flask(__name__, 
                template_folder='../templates',
                static_folder='../static')
    
    # 启用CORS（受控来源，仅示例：允许本地与环境变量指定的域名）
    allowed_origins = os.environ.get('CORS_ALLOW_ORIGINS', 'http://localhost:5000,http://127.0.0.1:5000').split(',')
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
    
    # 初始化数据库
    init_database()
    
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
            expected = AM.get_csrf_token()
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
    
    @app.route('/')
    @login_required
    def index():
        """主页"""
        return render_template('index.html')
    
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
            return render_template('emails.html', emails=emails)
        except Exception as e:
            logger.error(f"获取邮件列表失败: {e}")
            return render_template('emails.html', emails=[], error=str(e))
    
    @app.route('/schedule')
    @login_required
    def schedule():
        """日程表页面"""
        try:
            # 获取当前用户ID
            user_id = AuthManager.get_current_user_id()
            # 获取日程事件
            events = scheduler_service.get_upcoming_events(user_id)
            return render_template('schedule.html', events=events)
        except Exception as e:
            logger.error(f"获取日程失败: {e}")
            return render_template('schedule.html', events=[], error=str(e))
    
    @app.route('/config')
    @login_required
    def config_page():
        """配置页面"""
        return render_template('config.html', config=config.get_safe_config())
    
    @app.route('/admin')
    @admin_required
    def admin_page():
        """管理员后台页面"""
        return render_template('admin.html')
    
    def _process_new_email(email_data, user_id=1):
        """处理新邮件的AI分析（多线程函数）"""
        try:
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
                        delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
                        db.execute_update(delete_query, (email_id,))
                        analysis_query = (
                            "INSERT INTO email_analysis (email_id, summary, importance_score, importance_reason, events_json, keywords_matched, ai_model, analysis_date)"
                            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                        )
                        db.execute_insert(analysis_query, (
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
                    email_id,
                    analysis_result.get('summary', ''),
                    analysis_result.get('importance_score', 5),
                    analysis_result.get('importance_reason', ''),
                    json.dumps(serializable_events, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),  # keywords_matched
                    analysis_result.get('ai_model', ''),
                    datetime.now()
                )
                
                db.execute_insert(analysis_query, analysis_params)
                logger.info(f"AI分析结果已保存，邮件ID: {email_id}")
                
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
    
    def _retry_email_analysis(email_data):
        """重试邮件AI分析（多线程函数）"""
        try:
            # 为每个线程创建独立的服务实例
            thread_ai_service = AIService(config)
            thread_scheduler_service = SchedulerService(config)
            thread_notion_service = NotionService(config)
            
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
                
                delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
                db.execute_update(delete_query, (email_id,))
                
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
                    email_id,
                    analysis_result.get('summary', ''),
                    analysis_result.get('importance_score', 5),
                    analysis_result.get('importance_reason', ''),
                    json.dumps(serializable_events, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),  # keywords_matched
                    analysis_result.get('ai_model', ''),
                    datetime.now()
                )
                
                db.execute_insert(analysis_query, analysis_params)
                logger.info(f"重试分析成功，邮件ID: {email_id}")
                
                # 删除旧的事件（如果有）
                delete_events_query = "DELETE FROM events WHERE email_id = ?"
                db.execute_update(delete_events_query, (email_id,))
                
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
    
    def _analyze_email_only(email_data, user_id=1, task_id: str = None):
        """仅进行AI分析的函数（多线程函数）"""
        try:
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
                
                delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
                db.execute_update(delete_query, (email_id,))
                
                # 保存新的分析结果
                analysis_query = """
                INSERT INTO email_analysis 
                (email_id, summary, importance_score, importance_reason, 
                 events_json, keywords_matched, ai_model, analysis_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    email_id,
                    analysis_result.get('summary', ''),
                    analysis_result.get('importance_score', 5),
                    analysis_result.get('importance_reason', ''),
                    json.dumps(serializable_events, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),  # keywords_matched
                    analysis_result.get('ai_model', ''),
                    datetime.now()
                )
                
                db.execute_insert(analysis_query, analysis_params)
                logger.info(f"AI分析结果已保存，邮件ID: {email_id}")
                
                # 删除旧的事件（如果有）
                delete_events_query = "DELETE FROM events WHERE email_id = ?"
                db.execute_update(delete_events_query, (email_id,))
                
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
                    delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
                    db.execute_update(delete_query, (email_id,))
                    analysis_query = (
                        "INSERT INTO email_analysis (email_id, summary, importance_score, importance_reason, events_json, keywords_matched, ai_model, analysis_date)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    )
                    db.execute_insert(analysis_query, (
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
                delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
                db.execute_update(delete_query, (email_id,))
                analysis_query = (
                    "INSERT INTO email_analysis (email_id, summary, importance_score, importance_reason, events_json, keywords_matched, ai_model, analysis_date)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                )
                db.execute_insert(analysis_query, (
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
            with api_check_email._lock:
                api_check_email._progress[task_id] = {
                    'status': 'starting',
                    'new_count': 0,
                    'total': 0,
                    'analyzed': 0,
                    'failed': 0,
                    'saved': 0,
                    'synced': 0,
                    'message': ''
                }

            def _job(uid: int, tid: str, max_count: int = None):
                try:
                    # 获取服务实例
                    local_email_service = EmailService(config)

                    # 使用新的流式处理：获取、保存、分析三步并行执行
                    with api_check_email._lock:
                        api_check_email._progress[tid]['status'] = 'fetching'
                    saved_count = 0
                    analyzed_count = 0
                    failed_count = 0
                    
                    for result in local_email_service.fetch_and_process_emails_stream(uid, max_count=max_count):
                        try:
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
                    
                    # 为了兼容性，创建emails_to_analyze列表（虽然已经处理过了）
                    emails_to_analyze = []

                    # 兼容：若没有新流，仍获取未分析/失败的旧邮件
                    from .models.database import DatabaseManager
                    db = DatabaseManager(config)
                    unanalyzed_query = """
                    SELECT e.* FROM emails e
                    LEFT JOIN email_analysis ea ON e.id = ea.email_id
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
                                    futures.append((executor.submit(_analyze_email_only, email, uid, tid), email))
                                for future, email_data in futures:
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

            Thread(target=_job, args=(user_id, task_id, limit_n), daemon=True).start()
            return jsonify({'success': True, 'task_id': task_id})

        except Exception as e:
            logger.error(f"检查邮件API错误: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500

    @app.route('/api/tasks/<task_id>/progress')
    @login_required
    def api_task_progress(task_id):
        try:
            if not hasattr(api_check_email, '_progress'):
                return jsonify({'success': False, 'error': '任务不存在'}), 404
            with api_check_email._lock:
                prog = api_check_email._progress.get(task_id)
            if not prog:
                return jsonify({'success': False, 'error': '任务不存在'}), 404
            return jsonify({'success': True, 'progress': prog})
        except Exception as e:
            logger.error(f"获取任务进度失败: {e}")
            return jsonify({'success': False, 'error': str(e)}), 500
    
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
                'reminder': config_service.get_reminder_config(user_id)
            }
            
            return jsonify(user_config)
        
        elif request.method == 'POST':
            try:
                from .services.config_service import UserConfigService
                config_service = UserConfigService()
                
                new_config = request.get_json()
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
                
                if success:
                    return jsonify({
                        'success': True,
                        'message': '配置更新成功'
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
            delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
            db.execute_update(delete_query, (email_id,))
            
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
                json.dumps([], ensure_ascii=False),  # keywords_matched
                analysis_result.get('ai_model', ''),
                datetime.now()
            )
            
            db.execute_insert(analysis_query, analysis_params)
            
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
            
            query = "SELECT * FROM emails WHERE id = ?"
            email_result = db.execute_query(query, (email_id,))
            
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
                email_data['subject']
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
            delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
            db.execute_update(delete_query, (email_id,))
            
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
                json.dumps([], ensure_ascii=False),  # keywords_matched
                analysis_result.get('ai_model', ''),
                datetime.now()
            )
            
            db.execute_insert(analysis_query, analysis_params)
            
            # 保存事件到日程表（用户隔离）
            if analysis_result.get('events'):
                scheduler_service = SchedulerService(config)
                for event in analysis_result['events']:
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
            with api_check_email._lock:
                api_check_email._progress[task_id] = {
                    'status': 'analyzing',
                    'new_count': 0,
                    'total': len(emails_to_analyze),
                    'analyzed': 0,
                    'failed': 0,
                    'saved': 0,
                    'synced': 0,
                    'message': ''
                }

            def _job(uid: int, tid: str, emails: list):
                analyzed_count = 0
                failed_count = 0
                try:
                    # 清空该用户的日程与提醒（在失败重分析前确保干净状态）
                    try:
                        db.execute_update("DELETE FROM reminders WHERE user_id = ?", (uid,))
                        db.execute_update("DELETE FROM events WHERE user_id = ?", (uid,))
                        logger.info(f"已清空用户 {uid} 的日程与提醒（失败重分析）")
                    except Exception as _e:
                        logger.warning(f"清空用户日程失败（失败重分析）: {_e}")
                    if emails:
                        max_workers = min(3, len(emails))
                        with ThreadPoolExecutor(max_workers=max_workers) as executor:
                            futures = []
                            for email in emails:
                                futures.append((executor.submit(_analyze_email_only, email, uid, tid), email))
                            for future, email_data in futures:
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
                        api_check_email._progress[tid]['message'] = (
                            f"失败邮件重分析完成，共 {len(emails)} 封，成功 {analyzed_count} 封"
                            + (f"，失败 {failed_count} 封" if failed_count > 0 else '')
                        )
                except Exception as e:
                    logger.error(f"批量重新分析失败邮件任务错误: {e}")
                    with api_check_email._lock:
                        api_check_email._progress[tid]['status'] = 'error'
                        api_check_email._progress[tid]['message'] = str(e)

            Thread(target=_job, args=(user_id, task_id, emails_to_analyze), daemon=True).start()
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
            
            data = request.get_json()
            test_content = data.get('content', '明天下午2点有一个重要的期末考试。')
            
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
            
            # 检查缓存（仅缓存第一页、无筛选条件）
            import time
            current_time = time.time()
            cache_key = f"{user_id}_{importance}_{status}_{search}"
            
            # 如果有筛选条件或缓存过期，重新查询
            use_cache = (
                page == 1 and not importance and not status and not search and
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
                
                if status:
                    if status == 'processed':
                        where_conditions.append("e.is_processed = 1")
                    elif status == 'unprocessed':
                        where_conditions.append("e.is_processed = 0")
                
                if search:
                    where_conditions.append("(e.subject LIKE ? OR e.sender LIKE ?)")
                    search_pattern = f"%{search}%"
                    params.extend([search_pattern, search_pattern])
                
                # 构建完整查询
                where_clause = " AND ".join(where_conditions) if where_conditions else "1=1"
                
                query = f"""
                SELECT e.*, ea.summary, ea.importance_score, ea.events_json,
                       CASE 
                           WHEN ea.importance_score >= 8 THEN 'important'
                           WHEN ea.importance_score >= 4 THEN 'normal'
                           ELSE 'unimportant'
                       END as importance_level
                FROM emails e
                LEFT JOIN email_analysis ea ON e.id = ea.email_id
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
                LEFT JOIN email_analysis ea ON e.id = ea.email_id
                WHERE {where_clause}
                """
                count_result = db.execute_query(count_query, tuple(params))
                total_count = count_result[0]['count'] if count_result else 0
                
                # 解析events_json
                import json
                for email in emails:
                    if email.get('events_json'):
                        try:
                            email['events'] = json.loads(email['events_json'])
                        except json.JSONDecodeError:
                            email['events'] = []
                    else:
                        email['events'] = []
                
                # 仅在第一页且无筛选条件时缓存当前页数据
                if page == 1 and not (importance or status or search):
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
            for k in ['api_key', 'provider', 'model', 'base_url', 'max_tokens', 'temperature']:
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
            # 检查是否是手动测试请求
            manual_test = request.args.get('manual', 'false').lower() == 'true'
            
            if not manual_test:
                # 非手动测试时，只返回配置状态，不进行实际连接测试
                status = {
                    'email': bool(config.email_config.get('username') and config.email_config.get('password')),
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
        """API: 获取基础系统状态（不包含AI测试）"""
        try:
            status = {
                'email': False,
                'ai': bool(config.ai_config.get('api_key')),  # 只检查是否配置了API密钥
                'notion': False
            }
            
            # 检查邮件服务状态
            try:
                if config.email_config.get('username') and config.email_config.get('password'):
                    status['email'] = email_service.test_connection()
            except:
                pass
            
            # 检查Notion服务状态
            try:
                if config.notion_config.get('token'):
                    test_result = notion_service.test_connection()
                    status['notion'] = test_result.get('success', False)
            except:
                pass
            
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
    def api_get_statistics():
        """API: 获取系统统计数据"""
        try:
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 获取邮件总数
            email_count_query = "SELECT COUNT(*) as count FROM emails"
            email_result = db.execute_query(email_count_query)
            total_emails = email_result[0]['count'] if email_result else 0
            
            # 获取事件总数
            event_count_query = "SELECT COUNT(*) as count FROM events"
            event_result = db.execute_query(event_count_query)
            total_events = event_result[0]['count'] if event_result else 0
            
            # 获取重要事件数量
            important_events_query = "SELECT COUNT(*) as count FROM events WHERE importance_level = 'important'"
            important_result = db.execute_query(important_events_query)
            important_events = important_result[0]['count'] if important_result else 0
            
            # 获取待提醒事件数量（未来7天内的事件）
            from datetime import datetime, timedelta
            future_date = datetime.now() + timedelta(days=7)
            pending_reminders_query = """
            SELECT COUNT(*) as count FROM events 
            WHERE start_time BETWEEN datetime('now') AND datetime('now', '+7 days')
            """
            pending_result = db.execute_query(pending_reminders_query)
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
            if not importance_level or importance_level not in ['important', 'normal', 'unimportant']:
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
            update_query = "UPDATE events SET importance_level = ? WHERE id = ?"
            db.execute_update(update_query, (importance_level, event_id))
            
            # 重新计算提醒时间
            event_query = "SELECT * FROM events WHERE id = ?"
            event_result = db.execute_query(event_query, (event_id,))
            if event_result:
                event_data = event_result[0]
                # 删除旧的提醒
                delete_reminders_query = "DELETE FROM reminders WHERE event_id = ?"
                db.execute_update(delete_reminders_query, (event_id,))
                
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
            delete_reminders_query = "DELETE FROM reminders WHERE event_id = ?"
            db.execute_update(delete_reminders_query, (event_id,))
            
            # 删除事件
            delete_event_query = "DELETE FROM events WHERE id = ?"
            db.execute_update(delete_event_query, (event_id,))
            
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
        """API: 获取通知"""
        try:
            # 获取待发送的提醒
            user_id = AuthManager.get_current_user_id()
            pending_reminders = scheduler_service.get_pending_reminders(user_id)
            
            notifications = []
            for reminder in pending_reminders:
                notifications.append({
                    'id': f"reminder-{reminder['id']}",
                    'title': '事件提醒',
                    'message': f"{reminder['title']} 即将开始",
                    'type': 'warning',
                    'url': f"/schedule#event-{reminder['event_id']}"
                })
            
            return jsonify({
                'success': True,
                'notifications': notifications
            })
            
        except Exception as e:
            logger.error(f"获取通知失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
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
            return jsonify({'importance_levels': ['important','normal','unimportant']}), 200

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
            valid = {'important','normal','unimportant'}
            clean_levels = [str(v) for v in levels if str(v) in valid]
            if not clean_levels:
                # 允许空则默认全选
                clean_levels = ['important','normal','unimportant']
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
                    allowed = set(['important','normal','unimportant'])
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
        # 复用单事件导出逻辑（身份已验证）
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
    def api_archive_to_notion(email_id):
        """API: 归档邮件到Notion"""
        try:
            # 创建数据库管理器实例
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 获取邮件数据
            email_query = "SELECT * FROM emails WHERE id = ?"
            email_results = db.execute_query(email_query, (email_id,))
            
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
            events_query = "SELECT * FROM events WHERE email_id = ?"
            events_results = db.execute_query(events_query, (email_id,))
            analysis_result['events'] = [dict(event) for event in events_results]
            
            # 归档到Notion
            page_id = notion_service.archive_email(email_data, analysis_result)
            
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
                    
                    # 获取相关事件
                    events_query = "SELECT * FROM events WHERE email_id = ?"
                    events_results = db.execute_query(events_query, (email_data['id'],))
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
                    existing_email = email_service.get_email_by_message_id(email_data['message_id'])
                    
                    if existing_email:
                        # 更新现有邮件
                        email_service.email_model.update_email(existing_email['id'], email_data)
                        updated_count += 1
                    else:
                        # 保存新邮件
                        email_id = email_service.email_model.save_email(email_data, user_id)
                        new_count += 1
                        
                        # 异步处理AI分析
                        executor.submit(_process_new_email, email_data, user_id)
                        
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
                                                if email_service.email_model.update_email(email_id, email_data):
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
        return render_template('404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return render_template('500.html'), 500
    
    return app