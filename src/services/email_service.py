# -*- coding: utf-8 -*-
"""
邮件服务模块
"""

import imaplib
import email
from email.header import decode_header
from email.utils import parsedate_to_datetime
from email.message import Message
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import re
import html2text
import os
import base64
from pathlib import Path

from ..core.config import Config
from ..core.logger import get_logger
from ..models.database import EmailModel

logger = get_logger(__name__)


class EmailService:
    """邮件服务类"""
    
    def __init__(self, config: Config):
        """初始化邮件服务
        
        Args:
            config: 配置对象
        """
        self.config = config
        # 使用完整配置获取真实的敏感信息
        full_config = config.get_full_config()
        self.email_config = full_config.get('email', {})
        self.email_model = EmailModel(config)
        self.keywords_config = config.get_keywords()
        
        # HTML转文本转换器
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = True
        self.html_converter.ignore_images = True
    
    def connect_imap(self) -> imaplib.IMAP4_SSL:
        """连接到IMAP服务器
        
        Returns:
            IMAP连接对象
        
        Raises:
            Exception: 连接失败时抛出异常
        """
        try:
            # 创建IMAP连接
            if self.email_config.get('use_ssl', True):
                imap = imaplib.IMAP4_SSL(
                    self.email_config['imap_server'],
                    self.email_config.get('imap_port', 993)
                )
            else:
                imap = imaplib.IMAP4(
                    self.email_config['imap_server'],
                    self.email_config.get('imap_port', 143)
                )
            
            # 登录
            imap.login(
                self.email_config['username'],
                self.email_config['password']
            )
            
            logger.info(f"成功连接到邮件服务器: {self.email_config['imap_server']}")
            return imap
            
        except Exception as e:
            logger.error(f"连接邮件服务器失败: {e}")
            raise
    
    def decode_mime_words(self, s: str) -> str:
        """解码MIME编码的字符串
        
        Args:
            s: 待解码的字符串
        
        Returns:
            解码后的字符串
        """
        if not s:
            return ''
        
        try:
            decoded_fragments = decode_header(s)
            decoded_string = ''
            
            for fragment, encoding in decoded_fragments:
                if isinstance(fragment, bytes):
                    if encoding:
                        decoded_string += fragment.decode(encoding)
                    else:
                        # 尝试常见编码
                        for enc in ['utf-8', 'gbk', 'gb2312', 'latin-1']:
                            try:
                                decoded_string += fragment.decode(enc)
                                break
                            except UnicodeDecodeError:
                                continue
                        else:
                            decoded_string += fragment.decode('utf-8', errors='ignore')
                else:
                    decoded_string += fragment
            
            return decoded_string
            
        except Exception as e:
            logger.warning(f"解码邮件头失败: {e}")
            return str(s)
    
    def extract_email_content(self, msg: Message) -> Dict[str, Any]:
        """提取邮件内容和附件
        
        Args:
            msg: 邮件消息对象
        
        Returns:
            包含文本、HTML内容和附件信息的字典
        """
        text_content = ''
        html_content = ''
        attachments = []
        images = []
        
        try:
            if msg.is_multipart():
                # 多部分邮件
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get('Content-Disposition', ''))
                    
                    # 处理附件
                    if 'attachment' in content_disposition or part.get_filename():
                        filename = part.get_filename()
                        if filename:
                            filename = self.decode_mime_words(filename)
                            
                            # 检查是否为图片
                            if content_type.startswith('image/'):
                                image_data = self._extract_image_attachment(part, filename)
                                if image_data:
                                    images.append(image_data)
                            else:
                                # 其他类型附件
                                attachments.append({
                                    'filename': filename,
                                    'content_type': content_type,
                                    'size': len(part.get_payload(decode=True) or b'')
                                })
                        continue
                    
                    if content_type == 'text/plain':
                        charset = part.get_content_charset() or 'utf-8'
                        payload = part.get_payload(decode=True)
                        if payload:
                            try:
                                text_content += payload.decode(charset, errors='ignore')
                            except (UnicodeDecodeError, LookupError):
                                text_content += payload.decode('utf-8', errors='ignore')
                    
                    elif content_type == 'text/html':
                        charset = part.get_content_charset() or 'utf-8'
                        payload = part.get_payload(decode=True)
                        if payload:
                            try:
                                html_content += payload.decode(charset, errors='ignore')
                            except (UnicodeDecodeError, LookupError):
                                html_content += payload.decode('utf-8', errors='ignore')
            else:
                # 单部分邮件
                content_type = msg.get_content_type()
                charset = msg.get_content_charset() or 'utf-8'
                payload = msg.get_payload(decode=True)
                
                if payload:
                    try:
                        content = payload.decode(charset, errors='ignore')
                    except (UnicodeDecodeError, LookupError):
                        content = payload.decode('utf-8', errors='ignore')
                    
                    if content_type == 'text/html':
                        html_content = content
                    else:
                        text_content = content
            
            # 如果只有HTML内容，转换为文本
            if html_content and not text_content:
                text_content = self.html_converter.handle(html_content)
            
            return {
                'text': text_content.strip(),
                'html': html_content.strip(),
                'attachments': attachments,
                'images': images
            }
            
        except Exception as e:
            logger.error(f"提取邮件内容失败: {e}")
            return {'text': '', 'html': '', 'attachments': [], 'images': []}
    
    def _extract_image_attachment(self, part: Message, filename: str) -> Optional[Dict[str, Any]]:
        """提取图片附件
        
        Args:
            part: 邮件部分对象
            filename: 文件名
        
        Returns:
            图片信息字典
        """
        try:
            payload = part.get_payload(decode=True)
            if not payload:
                return None
            
            # 创建附件保存目录
            attachments_dir = Path('data/attachments')
            attachments_dir.mkdir(parents=True, exist_ok=True)
            
            # 生成唯一文件名
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            file_ext = Path(filename).suffix
            unique_filename = f"{timestamp}_{filename}"
            file_path = attachments_dir / unique_filename
            
            # 保存文件
            with open(file_path, 'wb') as f:
                f.write(payload)
            
            return {
                'filename': filename,
                'unique_filename': unique_filename,
                'file_path': str(file_path),
                'content_type': part.get_content_type(),
                'size': len(payload),
                'base64_data': base64.b64encode(payload).decode('utf-8')
            }
            
        except Exception as e:
            logger.error(f"提取图片附件失败: {e}")
            return None
    
    def analyze_importance_by_keywords(self, subject: str, content: str) -> Dict[str, Any]:
        """根据关键词分析邮件重要性
        
        Args:
            subject: 邮件主题
            content: 邮件内容
        
        Returns:
            包含重要性级别和匹配关键词的字典
        """
        text_to_analyze = f"{subject} {content}".lower()
        matched_keywords = []
        importance_level = 'normal'
        
        # 检查重要关键词
        for keyword in self.keywords_config.get('important', []):
            if keyword.lower() in text_to_analyze:
                matched_keywords.append(('important', keyword))
                importance_level = 'important'
        
        # 如果没有重要关键词，检查不重要关键词
        if importance_level != 'important':
            for keyword in self.keywords_config.get('unimportant', []):
                if keyword.lower() in text_to_analyze:
                    matched_keywords.append(('unimportant', keyword))
                    importance_level = 'unimportant'
                    break
        
        # 检查普通关键词
        if importance_level == 'normal':
            for keyword in self.keywords_config.get('normal', []):
                if keyword.lower() in text_to_analyze:
                    matched_keywords.append(('normal', keyword))
        
        return {
            'importance_level': importance_level,
            'matched_keywords': matched_keywords
        }
    
    def parse_email_message(self, msg: email.message.Message) -> Dict[str, Any]:
        """解析邮件消息
        
        Args:
            msg: 邮件消息对象
        
        Returns:
            解析后的邮件数据
        """
        try:
            # 提取基本信息
            subject = self.decode_mime_words(msg.get('Subject', ''))
            sender = self.decode_mime_words(msg.get('From', ''))
            recipient = self.decode_mime_words(msg.get('To', ''))
            message_id = msg.get('Message-ID', '')
            
            # 解析日期
            date_str = msg.get('Date', '')
            received_date = datetime.now()
            if date_str:
                try:
                    received_date = parsedate_to_datetime(date_str)
                except Exception as e:
                    logger.warning(f"解析邮件日期失败: {e}")
            
            # 提取内容
            content_data = self.extract_email_content(msg)
            
            # 分析重要性
            importance_data = self.analyze_importance_by_keywords(
                subject, content_data['text']
            )
            
            return {
                'message_id': message_id,
                'subject': subject,
                'sender': sender,
                'recipient': recipient,
                'content': content_data['text'],
                'html_content': content_data['html'],
                'attachments': content_data['attachments'],
                'images': content_data['images'],
                'received_date': received_date,
                'importance_level': importance_data['importance_level'],
                'matched_keywords': importance_data['matched_keywords']
            }
            
        except Exception as e:
            logger.error(f"解析邮件失败: {e}")
            return None
    
    def fetch_new_emails(self, user_id: int, days_back: int = 1) -> List[Dict[str, Any]]:
        """获取新邮件
        
        Args:
            user_id: 用户ID
            days_back: 获取多少天前的邮件
        
        Returns:
            新邮件列表
        """
        try:
            imap = self.connect_imap()
            
            # 选择收件箱
            imap.select('INBOX')
            
            # 计算搜索日期
            since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
            
            # 搜索邮件
            search_criteria = f'(SINCE "{since_date}")'
            status, message_ids = imap.search(None, search_criteria)
            
            if status != 'OK':
                logger.error("搜索邮件失败")
                return []
            
            message_ids = message_ids[0].split()
            logger.info(f"找到 {len(message_ids)} 封邮件")
            
            new_emails = []
            
            for msg_id in message_ids:
                try:
                    # 获取邮件
                    status, msg_data = imap.fetch(msg_id, '(RFC822)')
                    
                    if status != 'OK':
                        continue
                    
                    # 解析邮件
                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    # 解析邮件数据
                    email_data = self.parse_email_message(msg)
                    
                    if email_data:
                        # 检查是否已存在
                        existing_email = self.email_model.get_email_by_message_id(
                            email_data['message_id'], user_id
                        )
                        
                        if not existing_email:
                            new_emails.append(email_data)
                            logger.info(f"发现新邮件: {email_data['subject']}")
                
                except Exception as e:
                    logger.error(f"处理邮件 {msg_id} 失败: {e}")
                    continue
            
            # 关闭连接
            imap.close()
            imap.logout()
            
            logger.info(f"获取到 {len(new_emails)} 封新邮件")
            return new_emails
            
        except Exception as e:
            logger.error(f"获取邮件失败: {e}")
            return []
    
    def save_email_analysis(self, email_data: Dict[str, Any], analysis_result: Dict[str, Any], user_id: int) -> int:
        """保存邮件和分析结果
        
        Args:
            email_data: 邮件数据
            analysis_result: AI分析结果
            user_id: 用户ID
        
        Returns:
            邮件ID
        """
        try:
            # 保存邮件
            email_id = self.email_model.save_email(email_data, user_id)
            
            # 保存分析结果
            from ..models.database import DatabaseManager
            db = DatabaseManager(self.config)
            
            analysis_query = """
            INSERT INTO email_analysis 
            (email_id, summary, importance_score, importance_reason, 
             events_json, keywords_matched, ai_model)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            
            import json
            analysis_params = (
                email_id,
                analysis_result.get('summary', ''),
                analysis_result.get('importance_score', 5),
                analysis_result.get('importance_reason', ''),
                json.dumps(analysis_result.get('events', []), ensure_ascii=False),
                json.dumps(email_data.get('matched_keywords', []), ensure_ascii=False),
                analysis_result.get('ai_model', '')
            )
            
            db.execute_insert(analysis_query, analysis_params)
            
            # 标记为已处理
            self.email_model.mark_email_processed(email_id)
            
            logger.info(f"保存邮件分析结果完成: {email_data['subject']}")
            return email_id
            
        except Exception as e:
            logger.error(f"保存邮件分析结果失败: {e}")
            raise
    
    def get_processed_emails(self, user_id: int, limit: int = 50) -> List[Dict[str, Any]]:
        """获取已处理的邮件列表
        
        Args:
            user_id: 用户ID
            limit: 返回数量限制
        
        Returns:
            邮件列表
        """
        try:
            from ..models.database import DatabaseManager
            db = DatabaseManager(self.config)
            
            query = """
            SELECT e.*, ea.summary, ea.importance_score, ea.events_json
            FROM emails e
            LEFT JOIN email_analysis ea ON e.id = ea.email_id
            WHERE e.user_id = ?
            ORDER BY e.received_date DESC
            LIMIT ?
            """
            
            emails = db.execute_query(query, (user_id, limit))
            
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
            
            return emails
            
        except Exception as e:
            logger.error(f"获取已处理邮件失败: {e}")
            return []
    
    def get_email_by_id(self, email_id: int, user_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取邮件详情
        
        Args:
            email_id: 邮件ID
            user_id: 用户ID
        
        Returns:
            邮件详情
        """
        try:
            from ..models.database import DatabaseManager
            db = DatabaseManager(self.config)
            
            query = """
            SELECT e.*, ea.summary, ea.importance_score, ea.importance_reason,
                   ea.events_json, ea.keywords_matched, ea.ai_model, ea.analysis_date
            FROM emails e
            LEFT JOIN email_analysis ea ON e.id = ea.email_id
            WHERE e.id = ? AND e.user_id = ?
            """
            
            results = db.execute_query(query, (email_id, user_id))
            
            if results:
                email = results[0]
                
                # 解析JSON字段
                import json
                if email.get('events_json'):
                    try:
                        email['events'] = json.loads(email['events_json'])
                    except json.JSONDecodeError:
                        email['events'] = []
                
                if email.get('keywords_matched'):
                    try:
                        email['matched_keywords'] = json.loads(email['keywords_matched'])
                    except json.JSONDecodeError:
                        email['matched_keywords'] = []
                
                return email
            
            return None
            
        except Exception as e:
            logger.error(f"获取邮件详情失败: {e}")
            return None
    
    def get_email_by_message_id(self, message_id: str, user_id: int) -> Optional[Dict[str, Any]]:
        """根据消息ID获取邮件
        
        Args:
            message_id: 邮件消息ID
            user_id: 用户ID
        
        Returns:
            邮件数据
        """
        try:
            from ..models.database import DatabaseManager
            db = DatabaseManager(self.config)
            
            query = "SELECT * FROM emails WHERE message_id = ? AND user_id = ?"
            results = db.execute_query(query, (message_id, user_id))
            
            return results[0] if results else None
            
        except Exception as e:
            logger.error(f"根据消息ID获取邮件失败: {e}")
            return None
    
    def get_email_stats(self, user_id: int) -> Dict[str, Any]:
        """获取邮件统计信息
        
        Args:
            user_id: 用户ID
            
        Returns:
            统计信息字典
        """
        try:
            from ..models.database import DatabaseManager
            db = DatabaseManager(self.config)
            
            # 总邮件数
            total_query = "SELECT COUNT(*) as count FROM emails WHERE user_id = ?"
            total_result = db.execute_query(total_query, (user_id,))
            total_emails = total_result[0]['count'] if total_result else 0
            
            # 已处理邮件数
            processed_query = "SELECT COUNT(*) as count FROM emails WHERE user_id = ? AND is_processed = 1"
            processed_result = db.execute_query(processed_query, (user_id,))
            processed_emails = processed_result[0]['count'] if processed_result else 0
            
            # 今日邮件数
            today_query = """
            SELECT COUNT(*) as count FROM emails 
            WHERE user_id = ? AND DATE(received_date) = DATE('now')
            """
            today_result = db.execute_query(today_query, (user_id,))
            today_emails = today_result[0]['count'] if today_result else 0
            
            return {
                'total_emails': total_emails,
                'processed_emails': processed_emails,
                'unprocessed_emails': total_emails - processed_emails,
                'today_emails': today_emails,
                'processing_rate': round((processed_emails / total_emails * 100), 2) if total_emails > 0 else 0
            }
            
        except Exception as e:
            logger.error(f"获取邮件统计失败: {e}")
            return {
                'total_emails': 0,
                'processed_emails': 0,
                'unprocessed_emails': 0,
                'today_emails': 0,
                'processing_rate': 0
            }
    
    def search_emails(self, user_id: int, keyword: str = '', importance_level: str = '', 
                     days_back: int = 30, limit: int = 50) -> List[Dict[str, Any]]:
        """搜索邮件
        
        Args:
            user_id: 用户ID
            keyword: 搜索关键词
            importance_level: 重要性级别过滤
            days_back: 搜索多少天内的邮件
            limit: 返回数量限制
        
        Returns:
            匹配的邮件列表
        """
        try:
            from ..models.database import DatabaseManager
            db = DatabaseManager(self.config)
            
            # 构建查询条件
            conditions = ["e.user_id = ?"]
            params = [user_id]
            
            # 添加关键词搜索
            if keyword:
                conditions.append("(e.subject LIKE ? OR e.content LIKE ?)")
                keyword_param = f"%{keyword}%"
                params.extend([keyword_param, keyword_param])
            
            # 添加重要性级别过滤
            if importance_level:
                conditions.append("e.importance_level = ?")
                params.append(importance_level)
            
            # 添加时间范围过滤
            if days_back > 0:
                conditions.append("e.received_date >= datetime('now', '-{} days')".format(days_back))
            
            where_clause = " AND ".join(conditions)
            
            query = f"""
            SELECT e.*, ea.summary, ea.importance_score, ea.events_json
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
            
            return emails
            
        except Exception as e:
            logger.error(f"搜索邮件失败: {e}")
            return []
    
    def test_connection(self) -> bool:
        """测试邮件服务器连接
        
        Returns:
            连接是否成功
        """
        try:
            imap = self.connect_imap()
            # 在AUTH状态下直接logout，不需要close
            imap.logout()
            return True
        except Exception as e:
            logger.error(f"邮件服务器连接测试失败: {e}")
            return False