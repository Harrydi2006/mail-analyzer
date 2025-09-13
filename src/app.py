# -*- coding: utf-8 -*-
"""
Flask应用主文件
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
import os
import json
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
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
    
    # 启用CORS
    CORS(app)
    
    # 配置密钥
    app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-key-change-in-production')
    
    # 初始化配置和服务
    config = Config()
    logger = setup_logger()
    app_logger = get_logger(__name__)
    
    # 初始化数据库
    init_database()
    
    # 初始化身份验证
    auth_manager = AuthManager(app)
    
    # 添加请求日志记录
    @app.before_request
    def log_request_info():
        logger.info(f'HTTP请求: {request.method} {request.path} - 来自 {request.remote_addr}')
    
    @app.after_request
    def log_response_info(response):
        logger.info(f'HTTP响应: {request.method} {request.path} - 状态码 {response.status_code}')
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
            thread_notion_service = NotionService(config)
            
            # 先保存邮件到数据库（确保邮件不丢失）
            email_id = thread_email_service.email_model.save_email(email_data, user_id)
            logger.info(f"邮件已保存到数据库，ID: {email_id}, 主题: {email_data.get('subject', 'Unknown')}")
            
            # AI分析邮件内容
            analysis_result = thread_ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject']
            )
            
            # 如果AI分析成功，保存分析结果
            if analysis_result:
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
                        if isinstance(serializable_event['start_time'], datetime):
                            serializable_event['start_time'] = serializable_event['start_time'].isoformat()
                    if 'end_time' in serializable_event and serializable_event['end_time']:
                        if isinstance(serializable_event['end_time'], datetime):
                            serializable_event['end_time'] = serializable_event['end_time'].isoformat()
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
                email_data['subject']
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
                        thread_scheduler_service.add_event(event)
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
    
    def _analyze_email_only(email_data):
        """仅进行AI分析的函数（多线程函数）"""
        try:
            # 为每个线程创建独立的AI服务实例
            thread_ai_service = AIService(config)
            thread_scheduler_service = SchedulerService(config)
            thread_notion_service = NotionService(config)
            
            email_id = email_data['id']
            logger.info(f"开始AI分析邮件，ID: {email_id}, 主题: {email_data.get('subject', 'Unknown')}")
            
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
                
                # 删除旧的事件（如果有）
                delete_events_query = "DELETE FROM events WHERE email_id = ?"
                db.execute_update(delete_events_query, (email_id,))
                
                # 如果有事件，添加到日程
                if analysis_result.get('events'):
                    for event in analysis_result['events']:
                        event['email_id'] = email_id
                        thread_scheduler_service.add_event(event)
                    logger.info(f"已添加 {len(analysis_result['events'])} 个事件到日程")
                
                # 归档到Notion
                thread_notion_service.archive_email(email_data, analysis_result)
                
                return {'success': True, 'email_subject': email_data.get('subject', 'Unknown'), 'email_id': email_id}
            else:
                logger.warning(f"AI分析失败: {email_data.get('subject', 'Unknown')}")
                return {'success': False, 'error': 'AI分析失败', 'email_subject': email_data.get('subject', 'Unknown')}
                
        except Exception as e:
            logger.error(f"AI分析邮件失败: {e}")
            return {'success': False, 'error': str(e), 'email_subject': email_data.get('subject', 'Unknown')}
    
    @app.route('/api/check_email', methods=['POST'])
    @login_required
    def api_check_email():
        """API: 手动检查邮件（分阶段处理版本）"""
        try:
            # 获取当前用户ID
            user_id = AuthManager.get_current_user_id()
            
            # 第一阶段：获取所有邮件并保存到数据库
            logger.info("开始第一阶段：获取新邮件")
            new_emails = email_service.fetch_new_emails(user_id)
            
            # 保存新邮件到数据库（不进行AI分析）
            saved_email_ids = []
            for email_data in new_emails:
                try:
                    email_id = email_service.email_model.save_email(email_data, user_id)
                    saved_email_ids.append(email_id)
                    logger.info(f"邮件已保存，ID: {email_id}, 主题: {email_data.get('subject', 'Unknown')}")
                except Exception as e:
                    logger.error(f"保存邮件失败: {email_data.get('subject', 'Unknown')}, 错误: {e}")
            
            # 第二阶段：获取所有需要AI分析的邮件
            logger.info("开始第二阶段：准备AI分析")
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            # 查找所有未分析的邮件（包括新保存的和之前失败的）
            unanalyzed_query = """
            SELECT e.* FROM emails e
            LEFT JOIN email_analysis ea ON e.id = ea.email_id
            WHERE (ea.email_id IS NULL OR ea.summary IN ('AI分析失败', '邮件内容分析失败', ''))
            AND e.user_id = ?
            ORDER BY e.received_date DESC
            LIMIT 100
            """
            
            unanalyzed_result = db.execute_query(unanalyzed_query, (user_id,))
            emails_to_analyze = []
            
            for row in unanalyzed_result:
                email_data = {
                    'id': row['id'],
                    'message_id': row['message_id'],
                    'subject': row['subject'],
                    'sender': row['sender'],
                    'content': row['content'],
                    'received_date': row['received_date']
                }
                emails_to_analyze.append(email_data)
            
            if not emails_to_analyze:
                return jsonify({
                    'success': True,
                    'message': f'邮件获取完成: {len(new_emails)} 封新邮件，无需AI分析的邮件',
                    'new_count': len(new_emails),
                    'analyzed_count': 0,
                    'failed_count': 0
                })
            
            # 第三阶段：批量AI分析
            logger.info(f"开始第三阶段：AI分析 {len(emails_to_analyze)} 封邮件")
            
            analyzed_count = 0
            failed_count = 0
            
            # 使用线程池进行AI分析
            max_workers = min(3, len(emails_to_analyze))
            
            try:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = []
                    
                    # 提交所有分析任务
                    for email in emails_to_analyze:
                        try:
                            future = executor.submit(_analyze_email_only, email)
                            futures.append((future, email))
                        except RuntimeError as e:
                            if "cannot schedule new futures after interpreter shutdown" in str(e):
                                logger.warning(f"解释器正在关闭，跳过邮件分析: {email.get('subject', 'Unknown')}")
                                failed_count += 1
                            else:
                                raise e
                    
                    # 收集分析结果
                    for future, email_data in futures:
                        try:
                            result = future.result(timeout=30)  # 添加超时
                            if result['success']:
                                analyzed_count += 1
                            else:
                                failed_count += 1
                        except Exception as e:
                            logger.error(f"AI分析邮件失败: {email_data.get('subject', 'Unknown')}, 错误: {e}")
                            failed_count += 1
            except RuntimeError as e:
                if "cannot schedule new futures after interpreter shutdown" in str(e):
                    logger.warning("解释器正在关闭，无法创建线程池")
                    failed_count = len(emails_to_analyze)
                else:
                    raise e
            
            # 清除邮件缓存
            clear_email_cache()
            
            return jsonify({
                'success': True,
                'message': f'处理完成: {len(new_emails)} 封新邮件获取, {analyzed_count} 封AI分析成功' + (f', {failed_count} 封分析失败' if failed_count > 0 else ''),
                'new_count': len(new_emails),
                'analyzed_count': analyzed_count,
                'failed_count': failed_count
            })
            
        except Exception as e:
            logger.error(f"检查邮件API错误: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
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
                'notification': config_service.get_notification_config(user_id)
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
    def api_get_email(email_id):
        """API: 获取邮件详情"""
        try:
            email_data = email_service.get_email_by_id(email_id)
            if email_data:
                return jsonify(email_data)
            else:
                return jsonify({'error': '邮件不存在'}), 404
        except Exception as e:
            logger.error(f"获取邮件详情失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/email/<int:email_id>/reanalyze', methods=['POST'])
    def api_reanalyze_email(email_id):
        """API: 重新分析单个邮件"""
        try:
            # 获取邮件数据
            email_data = email_service.get_email_by_id(email_id)
            if not email_data:
                return jsonify({'error': '邮件不存在'}), 404
            
            # 重新进行AI分析
            analysis_result = ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject']
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
                    scheduler_service.add_event(event)
            
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
            
            # 保存事件到日程表
            if analysis_result.get('events'):
                scheduler_service = SchedulerService(config)
                for event in analysis_result['events']:
                    event['email_id'] = email_id
                    scheduler_service.add_event(event)
            
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
    
    def _process_single_email(email_data):
        """处理单个邮件的AI分析（多线程函数）"""
        try:
            from .models.database import DatabaseManager
            
            email_id = email_data['id']
            
            # 为每个线程创建独立的服务实例
            thread_ai_service = AIService(config)
            thread_db = DatabaseManager(config)
            
            # 重新进行AI分析
            analysis_result = thread_ai_service.analyze_email_content(
                email_data['content'],
                email_data['subject']
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
                    thread_scheduler_service.add_event(event)
            
            return {'success': True, 'email_id': email_id}
            
        except Exception as e:
            logger.error(f"重新分析邮件 {email_id} 失败: {e}")
            return {'success': False, 'email_id': email_id, 'error': str(e)}
    
    @app.route('/api/emails/reanalyze_all', methods=['POST'])
    def api_reanalyze_all_emails():
        """API: 重新分析所有邮件（多线程版本）"""
        try:
            # 获取所有邮件
            from .models.database import DatabaseManager
            db = DatabaseManager(config)
            
            query = "SELECT id, subject, content FROM emails ORDER BY received_date DESC"
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
                future_to_email = {executor.submit(_process_single_email, email): email for email in emails}
                
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
    
    @app.route('/api/test_email', methods=['POST'])
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
    _email_cache = {'data': None, 'timestamp': 0, 'ttl': 30}  # 30秒缓存
    
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
            
            # 检查缓存（按用户ID缓存）
            import time
            current_time = time.time()
            cache_key = f"{user_id}_{limit}_{importance}_{status}_{search}"
            
            # 如果有筛选条件或缓存过期，重新查询
            if (_email_cache['data'] is None or 
                current_time - _email_cache['timestamp'] > _email_cache['ttl'] or
                importance or status or search or
                _email_cache.get('user_id') != user_id):
                
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
                LIMIT ?
                """
                
                params.append(limit)
                emails = db.execute_query(query, params)
                
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
                
                # 如果没有筛选条件，更新缓存
                if not (importance or status or search):
                    _email_cache['data'] = emails
                    _email_cache['timestamp'] = current_time
                    _email_cache['user_id'] = user_id
            else:
                # 使用缓存数据
                emails = _email_cache['data']
            
            # 分页
            total = len(emails)
            start = (page - 1) * per_page
            end = start + per_page
            emails = emails[start:end]
            
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
    def api_get_notifications():
        """API: 获取通知"""
        try:
            # 获取待发送的提醒
            pending_reminders = scheduler_service.get_pending_reminders()
            
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
    
    @app.route('/api/calendar/export.ics')
    @login_required
    def api_export_ical():
        """API: 导出iCal格式日历"""
        try:
            user_id = AuthManager.get_current_user_id()
            days = int(request.args.get('days', 365))
            importance = request.args.get('importance', '')
            
            # 获取事件
            events = scheduler_service.get_upcoming_events(user_id, days)
            
            # 按重要性筛选
            if importance:
                events = [e for e in events if e.get('importance_level') == importance]
            
            # 导出iCal
            ical_content = scheduler_service.export_to_ical(events)
            
            if ical_content:
                response = app.response_class(
                    ical_content,
                    mimetype='text/calendar',
                    headers={
                        'Content-Disposition': 'attachment; filename=calendar.ics',
                        'Cache-Control': 'no-cache'
                    }
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
    
    @app.route('/api/calendar/subscribe')
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
            
            # 按重要性筛选
            if importance:
                events = [e for e in events if e.get('importance_level') == importance]
            
            # 导出iCal
            ical_content = scheduler_service.export_to_ical(events)
            
            if ical_content:
                response = app.response_class(
                    ical_content,
                    mimetype='text/calendar; charset=utf-8',
                    headers={
                        'Content-Type': 'text/calendar; charset=utf-8',
                        'Cache-Control': 'no-cache, no-store, must-revalidate',
                        'Pragma': 'no-cache',
                        'Expires': '0'
                    }
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
            
            if not all([username, password]):
                return jsonify({'success': False, 'error': '用户名和密码不能为空'}), 400
            
            # 用户登录
            result = user_service.login_user(username, password)
            
            if result['success']:
                # 设置session
                AuthManager.login_user(result['user'])
                return jsonify(result)
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
    
    @app.route('/api/calendar/caldav', methods=['GET', 'PROPFIND', 'REPORT'])
    def api_caldav():
        """API: CalDAV协议支持（基础实现）"""
        try:
            if request.method == 'GET':
                # 返回日历信息
                return jsonify({
                    'calendar_name': '邮件智能日程',
                    'description': '从邮件中提取的智能日程事件',
                    'subscribe_url': url_for('api_calendar_subscribe', _external=True),
                    'export_url': url_for('api_export_ical', _external=True)
                })
            
            elif request.method == 'PROPFIND':
                # CalDAV属性查询
                xml_response = '''<?xml version="1.0" encoding="utf-8" ?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
    <D:response>
        <D:href>/api/calendar/caldav</D:href>
        <D:propstat>
            <D:prop>
                <D:displayname>邮件智能日程</D:displayname>
                <C:calendar-description>从邮件中提取的智能日程事件</C:calendar-description>
                <D:resourcetype>
                    <D:collection/>
                    <C:calendar/>
                </D:resourcetype>
            </D:prop>
            <D:status>HTTP/1.1 200 OK</D:status>
        </D:propstat>
    </D:response>
</D:multistatus>'''
                return xml_response, 207, {'Content-Type': 'application/xml'}
            
            elif request.method == 'REPORT':
                # CalDAV报告查询
                events = scheduler_service.get_upcoming_events(365)
                ical_content = scheduler_service.export_to_ical(events)
                return ical_content, 200, {'Content-Type': 'text/calendar'}
                
        except Exception as e:
            logger.error(f"CalDAV请求失败: {e}")
            return jsonify({
                'success': False,
                'error': str(e)
            }), 500
    
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
    
    @app.route('/api/emails/refetch_all', methods=['POST'])
    def api_refetch_all_emails():
        """API: 重新获取所有邮件"""
        try:
            # 获取新邮件（默认获取最近7天的邮件）
            days_back = request.json.get('days_back', 7) if request.json else 7
            new_emails = email_service.fetch_new_emails(days_back)
            
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
                        email_id = email_service.email_model.save_email(email_data)
                        new_count += 1
                        
                        # 异步处理AI分析
                        executor.submit(_process_new_email, email_data)
                        
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
            
            for email_id in email_ids:
                try:
                    # 获取邮件的message_id
                    email_query = "SELECT message_id FROM emails WHERE id = ?"
                    email_result = db.execute_query(email_query, (email_id,))
                    
                    if not email_result:
                        fail_count += 1
                        continue
                    
                    message_id = email_result[0]['message_id']
                    
                    # 从邮件服务器重新获取这封邮件
                    imap = email_service.connect_imap()
                    imap.select('INBOX')
                    
                    # 获取邮件的基本信息用于搜索
                    email_info_query = "SELECT subject, sender, received_date FROM emails WHERE id = ?"
                    email_info_result = db.execute_query(email_info_query, (email_id,))
                    
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
    def api_delete_all_emails():
        """API: 删除所有邮件数据"""
        try:
            # 创建数据库管理器实例
            from .models.database import DatabaseManager
            import shutil
            import os
            
            db = DatabaseManager(config)
            
            # 获取删除前的邮件数量
            count_query = "SELECT COUNT(*) as count FROM emails"
            count_result = db.execute_query(count_query, ())
            deleted_count = count_result[0]['count'] if count_result else 0
            
            # 删除所有相关数据
            delete_queries = [
                "DELETE FROM keyword_matches",
                "DELETE FROM notion_archive", 
                "DELETE FROM reminders",
                "DELETE FROM events",
                "DELETE FROM email_analysis",
                "DELETE FROM emails"
            ]
            
            for query in delete_queries:
                try:
                    db.execute_update(query, ())
                except Exception as e:
                    logger.warning(f"删除数据时出现警告: {e}")
            
            # 删除附件文件夹
            attachments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'attachments')
            if os.path.exists(attachments_dir):
                try:
                    shutil.rmtree(attachments_dir)
                    os.makedirs(attachments_dir, exist_ok=True)
                    logger.info("附件文件夹已清空")
                except Exception as e:
                    logger.warning(f"清空附件文件夹时出现警告: {e}")
            
            logger.info(f"已删除所有邮件数据，共 {deleted_count} 封邮件")
            
            return jsonify({
                'success': True,
                'deleted_count': deleted_count,
                'message': f'成功删除 {deleted_count} 封邮件及相关数据'
            })
            
        except Exception as e:
            logger.error(f"删除所有邮件失败: {e}")
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
    
    @app.route('/attachments/<filename>')
    def serve_attachment(filename):
        """提供附件文件访问"""
        try:
            import os
            from flask import send_from_directory
            
            attachments_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'attachments')
            
            # 安全检查：确保文件名不包含路径遍历字符
            if '..' in filename or '/' in filename or '\\' in filename:
                return jsonify({'error': '非法文件名'}), 400
            
            # 检查文件是否存在
            file_path = os.path.join(attachments_dir, filename)
            if not os.path.exists(file_path):
                return jsonify({'error': '文件不存在'}), 404
            
            return send_from_directory(attachments_dir, filename)
            
        except Exception as e:
            logger.error(f"提供附件文件失败: {e}")
            return jsonify({'error': str(e)}), 500
    
    @app.errorhandler(404)
    def not_found(error):
        return render_template('404.html'), 404
    
    @app.errorhandler(500)
    def internal_error(error):
        return render_template('500.html'), 500
    
    return app