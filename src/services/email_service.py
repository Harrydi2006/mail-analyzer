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
from ..models.database import EmailModel, AttachmentModel

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
        self.attachment_model = AttachmentModel(config)
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
    
    def extract_email_content(self, msg: Message, user_id: int = None, email_id: int = None) -> Dict[str, Any]:
        """提取邮件内容和附件
        
        Args:
            msg: 邮件消息对象
            user_id: 用户ID（用于附件存储）
            email_id: 邮件ID（用于附件存储）
        
        Returns:
            包含文本、HTML内容和附件信息的字典
        """
        text_content = ''
        html_content = ''
        attachments = []
        images = []
        cid_to_filename = {}
        
        try:
            if msg.is_multipart():
                # 多部分邮件
                for part in msg.walk():
                    content_type = part.get_content_type()
                    content_disposition = str(part.get('Content-Disposition', ''))
                    content_id = part.get('Content-ID')
                    if content_id:
                        content_id = content_id.strip('<>')
                    
                    # 处理图片（包括附件与内联图片）
                    if content_type.startswith('image/'):
                        filename = part.get_filename()
                        # 优先使用原始文件名，否则用content-id或时间戳生成
                        if filename:
                            filename = self.decode_mime_words(filename)
                        else:
                            filename = (content_id or f"inline_{datetime.now().strftime('%H%M%S%f')}") + ".png"
                        image_data = self._extract_image_attachment(part, filename, user_id, email_id)
                        if image_data:
                            images.append(image_data)
                            if content_id:
                                cid_to_filename[content_id] = image_data['unique_filename']
                        continue
                    
                    # 其他附件
                    if 'attachment' in content_disposition or part.get_filename():
                        filename = part.get_filename()
                        if filename:
                            filename = self.decode_mime_words(filename)
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
                # 在转换为文本前先进行HTML内联图片替换与简单清理
                html_content = self._rewrite_html_inline_images(html_content, cid_to_filename)
                html_content = self._rewrite_remote_images(html_content)
                html_content = self._sanitize_html(html_content)
                text_content = self.html_converter.handle(html_content)
            else:
                # 仍需对HTML进行图片重写与清理
                if html_content:
                    html_content = self._rewrite_html_inline_images(html_content, cid_to_filename)
                    html_content = self._rewrite_remote_images(html_content)
                    html_content = self._sanitize_html(html_content)
            
            return {
                'text': text_content.strip(),
                'html': html_content.strip(),
                'attachments': attachments,
                'images': images
            }
            
        except Exception as e:
            logger.error(f"提取邮件内容失败: {e}")
            return {'text': '', 'html': '', 'attachments': [], 'images': []}
    
    def _extract_image_attachment(self, part: Message, filename: str, user_id: int = None, email_id: int = None) -> Optional[Dict[str, Any]]:
        """提取图片附件并存储到数据库
        
        Args:
            part: 邮件部分对象
            filename: 文件名
            user_id: 用户ID
            email_id: 邮件ID
        
        Returns:
            图片信息字典
        """
        try:
            payload = part.get_payload(decode=True)
            if not payload:
                return None
            
            # 生成唯一文件名（含微秒+随机短哈希，避免同秒多图覆盖）
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            file_ext = Path(filename).suffix or '.png'
            import os as _os
            uniq = _os.urandom(3).hex()
            base_name = Path(filename).name
            unique_filename = f"{timestamp}_{uniq}_{base_name}"
            
            # 获取图片尺寸（如果可能）
            image_width = None
            image_height = None
            try:
                from PIL import Image
                import io
                img = Image.open(io.BytesIO(payload))
                image_width, image_height = img.size
            except ImportError:
                logger.warning("PIL/Pillow未安装，无法获取图片尺寸")
            except Exception:
                pass  # 忽略图片尺寸获取失败
            
            # 存储到数据库
            # 保存附件到数据库（如果提供了user_id和email_id）
            attachment_id = None
            if user_id and email_id:
                try:
                    attachment_id = self.attachment_model.save_attachment(
                        user_id=user_id,
                        email_id=email_id,
                        filename=unique_filename,
                        content_type=part.get_content_type(),
                        file_data=payload,
                        is_image=True,
                        image_width=image_width,
                        image_height=image_height
                    )
                except Exception as e:
                    logger.error(f"保存图片附件到数据库失败: {e}")
            
            # 返回附件信息
            result = {
                'filename': filename,
                'unique_filename': unique_filename,
                'content_type': part.get_content_type(),
                'size': len(payload),
                'image_width': image_width,
                'image_height': image_height,
                'base64_data': base64.b64encode(payload).decode('utf-8')
            }
            
            # 如果有attachment_id，添加到结果中
            if attachment_id is not None:
                result['id'] = attachment_id
            
            return result
            
        except Exception as e:
            logger.error(f"提取图片附件失败: {e}")
            return None

    def _rewrite_html_inline_images(self, html: str, cid_map: Dict[str, str]) -> str:
        """将HTML中的cid内联图片替换为可访问的附件URL
        覆盖以下场景：
        - <img src="cid:...">、<img src='cid:...'>、<img src=cid:...>
        - 行内样式 background-image: url(cid:...)
        - srcset 中的 cid:... 项
        """
        try:
            if not html or not cid_map:
                return html
            import re
            # 规范化cid映射，支持大小写与带扩展名写法
            cid_map_l = {str(k).strip().lower(): v for k, v in cid_map.items()}
            common_exts = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp']
            def map_cid(cid_value: str) -> str:
                key = (cid_value or '').strip().strip('<>')
                kl = key.lower()
                # 直接命中
                if kl in cid_map_l:
                    return cid_map_l[kl]
                # 去掉常见扩展后再匹配
                for ext in common_exts:
                    if kl.endswith(ext):
                        base = kl[:-len(ext)]
                        if base in cid_map_l:
                            return cid_map_l[base]
                # 模糊匹配（部分客户端会拼接前后缀）
                for k2, v2 in cid_map_l.items():
                    if kl == k2 or kl in k2 or k2 in kl:
                        return v2
                return None
            # 1) <img src="cid:..."> 或 单引号
            def repl_src_quoted(m):
                cid = m.group(1)
                fname = map_cid(cid)
                return f'src="/attachments/{fname}"' if fname else m.group(0)
            html = re.sub(r'src\s*=\s*[\"\']cid:([^\"\'>]+)[\"\']', repl_src_quoted, html, flags=re.IGNORECASE)
            # 2) <img src=cid:...>（无引号）
            def repl_src_unquoted(m):
                cid = m.group(1)
                fname = map_cid(cid)
                return f'src="/attachments/{fname}"' if fname else m.group(0)
            html = re.sub(r'src\s*=\s*cid:([^\s>]+)', repl_src_unquoted, html, flags=re.IGNORECASE)
            # 3) CSS: url(cid:...)
            def repl_css_url(m):
                cid = m.group(1)
                fname = map_cid(cid)
                return f'url("/attachments/{fname}")' if fname else m.group(0)
            html = re.sub(r'url\(\s*[\"\']?cid:([^\"\')\s]+)[\"\']?\s*\)', repl_css_url, html, flags=re.IGNORECASE)
            # 4) srcset 属性中的 cid:...（可能有多项、逗号分隔）
            def repl_srcset(m):
                quote = m.group(1)
                content = m.group(2)
                def repl_item(mm):
                    cid = mm.group(1)
                    fname = map_cid(cid)
                    return f'/attachments/{fname}' if fname else mm.group(0)
                new_content = re.sub(r'cid:([^\s,]+)', repl_item, content, flags=re.IGNORECASE)
                return f'srcset={quote}{new_content}{quote}'
            html = re.sub(r'srcset\s*=\s*([\"\'])(.*?)(\1)', repl_srcset, html, flags=re.IGNORECASE|re.DOTALL)
            return html
        except Exception:
            return html

    def _sanitize_html(self, html: str) -> str:
        """简单清理HTML，移除<script>和<style>块，降低XSS风险"""
        try:
            import re
            # 移除<script>...</script>与<style>...</style>
            html = re.sub(r'<\s*script[^>]*>.*?<\s*/\s*script\s*>', '', html, flags=re.IGNORECASE|re.DOTALL)
            html = re.sub(r'<\s*style[^>]*>.*?<\s*/\s*style\s*>', '', html, flags=re.IGNORECASE|re.DOTALL)
            return html
        except Exception:
            return html

    def _embed_images_in_html(self, html: str, images_data: list) -> str:
        """将图片直接嵌入HTML中，避免路径访问问题
        
        Args:
            html: HTML内容
            images_data: 图片数据列表
        
        Returns:
            嵌入图片后的HTML内容
        """
        try:
            if not html or not images_data:
                return html
            
            import re
            import base64
            
            # 为每个图片创建base64数据URL
            for img in images_data:
                if not img.get('unique_filename') or not img.get('base64_data'):
                    continue
                
                unique_filename = img['unique_filename']
                base64_data = img['base64_data']
                content_type = img.get('content_type', 'image/jpeg')
                
                # 创建data URL
                data_url = f"data:{content_type};base64,{base64_data}"
                
                # 替换HTML中的图片路径
                # 替换 /attachments/filename 为 data URL
                pattern = f'/attachments/{re.escape(unique_filename)}'
                html = re.sub(pattern, data_url, html, flags=re.IGNORECASE)
            
            return html
            
        except Exception as e:
            logger.warning(f"嵌入图片到HTML失败: {e}")
            return html

    def _rewrite_remote_images(self, html: str) -> str:
        """将HTML中的 http/https 外链图片改写为站内代理，避免https下的混合内容与防盗链
        支持：
        - <img src="http(s)://...">
        - CSS url(http/https)
        - srcset 中的 http/https 项
        """
        try:
            if not html:
                return html
            import re
            from urllib.parse import quote
            # 1) <img src="http(s)://...">
            def repl_img_src(m):
                url = m.group(1)
                return f'src="/attachments/remote?u={quote(url, safe="")}"'
            html = re.sub(r'src\s*=\s*\"(https?://[^\"]+)\"', repl_img_src, html, flags=re.IGNORECASE)
            html = re.sub(r"src\s*=\s*\'(https?://[^\']+)\'", repl_img_src, html, flags=re.IGNORECASE)
            # 无引号
            html = re.sub(r'src\s*=\s*(https?://[^\s>]+)', lambda m: f'src="/attachments/remote?u={quote(m.group(1), safe="")}"', html, flags=re.IGNORECASE)
            # 2) CSS url(http/https)
            def repl_css_url(m):
                url = m.group(1)
                return f'url("/attachments/remote?u={quote(url, safe="")}")'
            html = re.sub(r'url\(\s*[\"\']?(https?://[^\"\')\s]+)[\"\']?\s*\)', repl_css_url, html, flags=re.IGNORECASE)
            # 3) srcset 属性
            def repl_srcset(m):
                quote_ch = m.group(1)
                content = m.group(2)
                def repl_item(mm):
                    url = mm.group(1)
                    return f'/attachments/remote?u={quote(url, safe="")}'
                new_content = re.sub(r'(https?://[^\s,]+)', lambda mm: repl_item(mm), content, flags=re.IGNORECASE)
                return f'srcset={quote_ch}{new_content}{quote_ch}'
            html = re.sub(r'srcset\s*=\s*([\"\'])(.*?)(\1)', repl_srcset, html, flags=re.IGNORECASE|re.DOTALL)
            return html
        except Exception:
            return html
    
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
    
    def parse_email_message(
        self,
        msg: email.message.Message,
        user_id: int = None,
        email_id: int = None,
        imap_uid: str = None,
    ) -> Dict[str, Any]:
        """解析邮件消息
        
        Args:
            msg: 邮件消息对象
            user_id: 用户ID（用于附件存储）
            email_id: 邮件ID（用于附件存储）
        
        Returns:
            解析后的邮件数据
        """
        try:
            # 提取基本信息
            subject = self.decode_mime_words(msg.get('Subject', ''))
            sender = self.decode_mime_words(msg.get('From', ''))
            recipient = self.decode_mime_words(msg.get('To', ''))
            message_id = msg.get('Message-ID', '') or ''
            # 部分邮件可能缺失 Message-ID，但数据库字段要求非空且唯一：用 IMAP UID 做兜底
            if not message_id and imap_uid:
                message_id = f"imap-uid:{user_id}:{imap_uid}"
            
            # 解析日期
            date_str = msg.get('Date', '')
            received_date = datetime.now()
            if date_str:
                try:
                    received_date = parsedate_to_datetime(date_str)
                except Exception as e:
                    logger.warning(f"解析邮件日期失败: {e}")
            
            # 提取内容
            content_data = self.extract_email_content(msg, user_id, email_id)
            
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
    
    def fetch_new_emails(self, user_id: int, days_back: int = 1, max_count: int = None) -> List[Dict[str, Any]]:
        """获取新邮件
        
        Args:
            user_id: 用户ID
            days_back: 获取多少天前的邮件
            max_count: 仅同步前N封（最新）
        
        Returns:
            新邮件列表
        """
        try:
            # 获取用户级邮件配置
            from .config_service import UserConfigService
            config_service = UserConfigService()
            user_email_config = config_service.get_email_config(user_id)
            
            # 临时覆盖邮件配置
            original_config = self.email_config.copy()
            self.email_config.update(user_email_config)
            
            try:
                imap = self.connect_imap()
            finally:
                # 恢复原始配置
                self.email_config = original_config
            
            # 选择收件箱
            imap.select('INBOX')
            
            # 以 IMAP UID 为准做增量同步（稳定且不会因删除/移动导致编号变化）
            last_seen_uid = 0
            try:
                last_seen_uid = int(config_service.get_user_config(user_id, 'email', 'last_seen_uid', 0) or 0)
            except Exception:
                last_seen_uid = 0

            # 当 last_seen_uid 缺失但本地已有邮件时，用 UIDNEXT 与本地数量估算“可能的新邮件窗口”，避免全量扫描过慢
            estimate_buffer = 200
            try:
                estimate_buffer = int(user_email_config.get('uid_estimate_buffer', 200))
            except Exception:
                estimate_buffer = 200

            if last_seen_uid > 0:
                status, uids = imap.uid('search', None, f'UID {last_seen_uid + 1}:*')
            else:
                existing_count = 0
                try:
                    existing_count = int(self.email_model.db.execute_query(
                        "SELECT COUNT(*) as count FROM emails WHERE user_id = ?",
                        (user_id,)
                    )[0]['count'])
                except Exception:
                    existing_count = 0

                uidnext = None
                try:
                    st, data = imap.status('INBOX', '(UIDNEXT)')
                    if st == 'OK' and data and isinstance(data[0], (bytes, bytearray)):
                        import re
                        m = re.search(rb'UIDNEXT\s+(\d+)', data[0])
                        if m:
                            uidnext = int(m.group(1))
                except Exception:
                    uidnext = None

                if uidnext and existing_count > 0:
                    start_uid = max(1, uidnext - (existing_count + estimate_buffer))
                    status, uids = imap.uid('search', None, f'UID {start_uid}:*')
                else:
                    since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
                    status, uids = imap.uid('search', None, f'(SINCE "{since_date}")')
            
            if status != 'OK':
                logger.error("搜索邮件失败")
                return []
            
            uid_list = (uids[0] or b'').split()
            uid_list = sorted(uid_list, key=lambda x: int(x))
            # 仅保留最新N封
            if max_count and isinstance(max_count, int) and max_count > 0:
                uid_list = uid_list[-max_count:]
            logger.info(f"找到 {len(uid_list)} 封邮件（UID增量）")
            
            new_emails = []
            max_uid_seen = last_seen_uid
            
            for uid in uid_list:
                try:
                    uid_int = int(uid)
                    if uid_int > max_uid_seen:
                        max_uid_seen = uid_int

                    # 先取 Header 做存在性判断（比直接取 RFC822 快很多）
                    status, hdr_data = imap.uid('fetch', uid, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID SUBJECT FROM DATE)])')
                    if status != 'OK' or not hdr_data or not hdr_data[0]:
                        continue
                    raw_hdr = hdr_data[0][1] if isinstance(hdr_data[0], (list, tuple)) and len(hdr_data[0]) > 1 else None
                    msg_id = ''
                    if raw_hdr:
                        try:
                            hdr_msg = email.message_from_bytes(raw_hdr)
                            msg_id = hdr_msg.get('Message-ID', '') or ''
                        except Exception:
                            msg_id = ''

                    # Message-ID 缺失时，用 uid 兜底（与 parse_email_message 的策略保持一致）
                    if not msg_id:
                        msg_id = f"imap-uid:{user_id}:{uid.decode('utf-8', errors='ignore')}"

                    existing_email = self.email_model.get_email_by_message_id(msg_id, user_id)
                    if existing_email:
                        continue

                    # 获取邮件正文（按 UID）
                    status, msg_data = imap.uid('fetch', uid, '(RFC822)')
                    
                    if status != 'OK':
                        continue
                    
                    # 解析邮件
                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)
                    
                    # 解析邮件数据（先不处理附件，等邮件保存后再处理）
                    email_data = self.parse_email_message(msg, user_id, None, imap_uid=uid.decode('utf-8', errors='ignore'))
                    
                    if email_data:
                        # 检查是否已存在
                        existing_email = self.email_model.get_email_by_message_id(
                            email_data['message_id'], user_id
                        )
                        
                        if not existing_email:
                            new_emails.append(email_data)
                            logger.info(f"发现新邮件: {email_data['subject']}")
                
                except Exception as e:
                    logger.error(f"处理邮件 UID={uid} 失败: {e}")
                    continue

            # 持久化 last_seen_uid（即使有重复/已存在，也应推进游标避免反复扫描）
            try:
                if max_uid_seen > last_seen_uid:
                    config_service.set_user_config(user_id, 'email', 'last_seen_uid', int(max_uid_seen))
            except Exception:
                pass
            
            # 关闭连接
            imap.close()
            imap.logout()
            
            logger.info(f"获取到 {len(new_emails)} 封新邮件")
            return new_emails
            
        except Exception as e:
            logger.error(f"获取邮件失败: {e}")
            return []

    def _decode_header(self, header_value: str) -> str:
        """解码邮件头信息
        
        Args:
            header_value: 邮件头值
            
        Returns:
            解码后的字符串
        """
        if not header_value:
            return ''
        
        try:
            from email.header import decode_header
            decoded_parts = decode_header(header_value)
            decoded_string = ''
            
            for part, encoding in decoded_parts:
                if isinstance(part, bytes):
                    if encoding:
                        decoded_string += part.decode(encoding)
                    else:
                        decoded_string += part.decode('utf-8', errors='ignore')
                else:
                    decoded_string += part
            
            return decoded_string.strip()
            
        except Exception as e:
            logger.warning(f"解码邮件头失败: {e}")
            return str(header_value)

    def get_emails_by_subject_and_sender(self, user_id: int, message_ids: List[str]) -> List[Dict[str, Any]]:
        """根据主题和发件人获取邮件（用于检查重复）
        
        Args:
            user_id: 用户ID
            message_ids: 邮件ID列表
            
        Returns:
            邮件数据列表
        """
        try:
            if not message_ids:
                return []
            
            # 构建查询条件
            placeholders = ','.join(['?' for _ in message_ids])
            query = f"""
            SELECT id, subject, sender, message_id
            FROM emails 
            WHERE user_id = ? AND message_id IN ({placeholders})
            """
            
            params = [user_id] + message_ids
            return self.email_model.db.execute_query(query, params)
            
        except Exception as e:
            logger.error(f"根据主题和发件人获取邮件失败: {e}")
            return []

    def _find_new_emails_binary_search(self, imap, message_ids, latest_email, existing_count):
        """使用精确的二分法查找新邮件
        
        算法步骤：
        1. 检查倒数第n个邮件的标题是否与最新邮件匹配
        2. 如果不匹配，使用二分法基于时间查找
        3. 找到时间相同的邮件后，比对标题
        4. 如果标题不一致，前后查找比对标题
        5. 如果找不到匹配的，回退到数据库去重
        
        Args:
            imap: IMAP连接
            message_ids: 邮件ID列表（按时间排序，最新的在最后）
            latest_email: 数据库中最新邮件的信息
            existing_count: 数据库中已有邮件数量
            
        Returns:
            新邮件的ID列表
        """
        try:
            total_messages = len(message_ids)
            logger.info(f"开始精确二分法查找，总共 {total_messages} 封邮件，已有 {existing_count} 封")
            
            # 如果获取的邮件数量小于等于已有数量，说明没有新邮件
            if total_messages <= existing_count:
                logger.info("获取的邮件数量小于等于已有数量，没有新邮件")
                return []
            
            # 1. 检查倒数第n个邮件的标题是否与最新邮件匹配
            check_index = total_messages - existing_count
            if check_index < 0:
                check_index = 0
            
            logger.info(f"检查索引 {check_index} 的邮件（倒数第 {existing_count} 个）")
            
            # 获取要检查的邮件信息
            msg_id = message_ids[check_index]
            email_info = self._get_email_basic_info(imap, msg_id)
            
            if email_info is None:
                logger.warning(f"无法获取邮件 {msg_id} 的基本信息，回退到数据库去重")
                return message_ids
            
            logger.info(f"检查邮件: {email_info['subject']} from {email_info['sender']}")
            logger.info(f"最新邮件: {latest_email['subject']} from {latest_email['sender']}")
            
            # 比较标题和发件人
            if (email_info['subject'] == latest_email['subject'] and 
                email_info['sender'] == latest_email['sender']):
                # 标题匹配，说明从这个位置开始都是新邮件
                new_messages = message_ids[check_index + 1:]
                logger.info(f"标题匹配，从索引 {check_index + 1} 开始有 {len(new_messages)} 个新邮件")
                return new_messages
            else:
                # 标题不匹配，使用二分法基于时间查找
                logger.info("标题不匹配，使用二分法基于时间查找")
                return self._binary_search_by_time_and_title(imap, message_ids, latest_email, 0, total_messages - 1)
                
        except Exception as e:
            logger.error(f"精确二分法查找失败: {e}")
            # 出错时回退到数据库去重
            return message_ids

    def _binary_search_by_time_and_title(self, imap, message_ids, latest_email, left, right):
        """基于时间和标题的二分法查找
        
        Args:
            imap: IMAP连接
            message_ids: 邮件ID列表
            latest_email: 数据库中最新邮件的信息
            left: 左边界
            right: 右边界
            
        Returns:
            新邮件的ID列表
        """
        try:
            if left > right:
                logger.info("二分法未找到匹配的邮件，回退到数据库去重")
                return message_ids
            
            if left == right:
                # 只剩一个邮件，检查是否匹配
                msg_id = message_ids[left]
                email_info = self._get_email_basic_info(imap, msg_id)
                if email_info and self._is_same_email(email_info, latest_email):
                    return message_ids[left + 1:]  # 从这个邮件之后都是新邮件
                else:
                    return message_ids[left:]  # 从这个邮件开始都是新邮件
            
            mid = (left + right) // 2
            msg_id = message_ids[mid]
            
            # 获取中间邮件的基本信息
            email_info = self._get_email_basic_info(imap, msg_id)
            
            if email_info is None:
                logger.warning(f"无法获取邮件 {msg_id} 的基本信息")
                return message_ids[mid:]
            
            logger.info(f"二分法检查索引 {mid}: {email_info['subject']} ({email_info['received_date']})")
            
            # 比较时间
            if email_info['received_date'] > latest_email['received_date']:
                # 当前邮件比最新邮件新，新邮件在左边（包含当前）
                return self._binary_search_by_time_and_title(imap, message_ids, latest_email, left, mid)
            elif email_info['received_date'] < latest_email['received_date']:
                # 当前邮件比最新邮件旧，新邮件在右边
                return self._binary_search_by_time_and_title(imap, message_ids, latest_email, mid + 1, right)
            else:
                # 时间相同，需要比对标题
                logger.info(f"时间相同，比对标题: {email_info['subject']} vs {latest_email['subject']}")
                if self._is_same_email(email_info, latest_email):
                    # 标题也相同，找到了分界点
                    logger.info(f"找到分界点 {mid}，从索引 {mid + 1} 开始是新邮件")
                    return message_ids[mid + 1:]
                else:
                    # 标题不同，需要前后查找
                    logger.info("标题不同，开始前后查找")
                    return self._search_around_same_time(imap, message_ids, latest_email, mid, left, right)
                
        except Exception as e:
            logger.error(f"二分法时间和标题查找失败: {e}")
            return message_ids[left:]

    def _search_around_same_time(self, imap, message_ids, latest_email, center_index, left_bound, right_bound):
        """在相同时间附近查找匹配的邮件
        
        Args:
            imap: IMAP连接
            message_ids: 邮件ID列表
            latest_email: 数据库中最新邮件的信息
            center_index: 中心索引
            left_bound: 左边界
            right_bound: 右边界
            
        Returns:
            新邮件的ID列表
        """
        try:
            logger.info(f"在索引 {center_index} 附近查找匹配邮件")
            
            # 向前查找
            for i in range(center_index - 1, left_bound - 1, -1):
                msg_id = message_ids[i]
                email_info = self._get_email_basic_info(imap, msg_id)
                if email_info is None:
                    continue
                
                logger.info(f"向前查找索引 {i}: {email_info['subject']} ({email_info['received_date']})")
                
                if email_info['received_date'] != latest_email['received_date']:
                    # 时间不同了，停止查找
                    logger.info(f"时间不同，停止向前查找")
                    break
                
                if self._is_same_email(email_info, latest_email):
                    # 找到匹配的邮件
                    logger.info(f"向前查找到匹配邮件，从索引 {i + 1} 开始是新邮件")
                    return message_ids[i + 1:]
            
            # 向后查找
            for i in range(center_index + 1, right_bound + 1):
                msg_id = message_ids[i]
                email_info = self._get_email_basic_info(imap, msg_id)
                if email_info is None:
                    continue
                
                logger.info(f"向后查找索引 {i}: {email_info['subject']} ({email_info['received_date']})")
                
                if email_info['received_date'] != latest_email['received_date']:
                    # 时间不同了，停止查找
                    logger.info(f"时间不同，停止向后查找")
                    break
                
                if self._is_same_email(email_info, latest_email):
                    # 找到匹配的邮件
                    logger.info(f"向后查找到匹配邮件，从索引 {i + 1} 开始是新邮件")
                    return message_ids[i + 1:]
            
            # 没找到匹配的邮件，回退到数据库去重
            logger.info("前后查找都未找到匹配邮件，回退到数据库去重")
            return message_ids
            
        except Exception as e:
            logger.error(f"前后查找失败: {e}")
            return message_ids

    def _get_email_basic_info(self, imap, msg_id):
        """获取邮件的基本信息（主题、发件人、时间）
        
        Args:
            imap: IMAP连接
            msg_id: 邮件ID
            
        Returns:
            邮件基本信息字典，如果失败返回None
        """
        try:
            # 只获取邮件头信息
            status, msg_data = imap.fetch(msg_id, '(RFC822.HEADER)')
            if status != 'OK':
                return None
            
            raw_email = msg_data[0][1]
            msg = email.message_from_bytes(raw_email)
            
            # 获取邮件基本信息
            subject = self._decode_header(msg.get('Subject', ''))
            sender = self._decode_header(msg.get('From', ''))
            
            # 获取邮件时间
            date_header = msg.get('Date', '')
            received_date = None
            if date_header:
                from email.utils import parsedate_to_datetime
                try:
                    email_date = parsedate_to_datetime(date_header)
                    if email_date:
                        received_date = email_date.isoformat()
                except Exception as e:
                    logger.warning(f"解析邮件时间失败: {e}")
            
            return {
                'subject': subject,
                'sender': sender,
                'received_date': received_date
            }
            
        except Exception as e:
            logger.warning(f"获取邮件基本信息失败: {e}")
            return None

    def _is_same_email(self, email_info, latest_email):
        """判断两个邮件是否是同一封邮件
        
        Args:
            email_info: 邮件基本信息
            latest_email: 数据库中的最新邮件信息
            
        Returns:
            是否是同一封邮件
        """
        return (email_info['subject'] == latest_email['subject'] and 
                email_info['sender'] == latest_email['sender'])

    def _get_unprocessed_emails(self, user_id: int):
        """获取未处理的邮件
        
        Args:
            user_id: 用户ID
            
        Returns:
            未处理邮件的列表
        """
        try:
            # 查找没有AI分析结果的邮件
            query = """
            SELECT e.id, e.subject, e.content, e.received_date, e.sender, e.message_id
            FROM emails e
            LEFT JOIN email_analysis ea ON e.id = ea.email_id
            WHERE e.user_id = ? AND (ea.id IS NULL OR ea.summary = '' OR ea.summary = '邮件内容分析失败' OR ea.summary = 'AI分析失败')
            ORDER BY e.received_date DESC
            LIMIT 50
            """
            
            result = self.email_model.db.execute_query(query, (user_id,))
            logger.info(f"找到 {len(result)} 封未处理的邮件")
            return result
            
        except Exception as e:
            logger.error(f"获取未处理邮件失败: {e}")
            return []

    def fetch_and_process_emails_stream(
        self,
        user_id: int,
        days_back: int = 1,
        max_count: int = None,
        analysis_workers: int = 3,
        cancel_event=None,
    ):
        """流式获取、保存、解析邮件（真正的流式处理）
        
        Args:
            user_id: 用户ID
            days_back: 获取多少天前的邮件
            max_count: 仅同步前N封（最新）
        
        Yields:
            每封邮件的处理状态和结果
        """
        from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
        
        try:
            # 导入AI服务（并行分析时每个任务使用独立实例）
            from .ai_service import AIService
            
            # 获取用户级邮件配置
            from .config_service import UserConfigService
            config_service = UserConfigService()
            user_email_config = config_service.get_email_config(user_id)
            
            # 临时覆盖邮件配置
            original_config = self.email_config.copy()
            self.email_config.update(user_email_config)
            imap = None
            
            try:
                imap = self.connect_imap()
                imap.select('INBOX')

                # 使用 UID 做增量拉取，替代不稳定的“编号 + 二分标题匹配”算法
                last_seen_uid = 0
                try:
                    last_seen_uid = int(config_service.get_user_config(user_id, 'email', 'last_seen_uid', 0) or 0)
                except Exception:
                    last_seen_uid = 0

                # 估算“新邮件窗口”：当 last_seen_uid 缺失但本地已有邮件时，避免从很早的 UID 扫到现在导致扫描过慢
                estimate_buffer = 200
                try:
                    estimate_buffer = int(user_email_config.get('uid_estimate_buffer', 200))
                except Exception:
                    estimate_buffer = 200

                if last_seen_uid > 0:
                    status, uids = imap.uid('search', None, f'UID {last_seen_uid + 1}:*')
                else:
                    existing_count = 0
                    try:
                        existing_count = int(self.email_model.db.execute_query(
                            "SELECT COUNT(*) as count FROM emails WHERE user_id = ?",
                            (user_id,)
                        )[0]['count'])
                    except Exception:
                        existing_count = 0

                    uidnext = None
                    try:
                        st, data = imap.status('INBOX', '(UIDNEXT)')
                        if st == 'OK' and data and isinstance(data[0], (bytes, bytearray)):
                            import re
                            m = re.search(rb'UIDNEXT\s+(\d+)', data[0])
                            if m:
                                uidnext = int(m.group(1))
                    except Exception:
                        uidnext = None

                    if uidnext and existing_count > 0:
                        start_uid = max(1, uidnext - (existing_count + estimate_buffer))
                        status, uids = imap.uid('search', None, f'UID {start_uid}:*')
                    else:
                since_date = (datetime.now() - timedelta(days=days_back)).strftime('%d-%b-%Y')
                        status, uids = imap.uid('search', None, f'(SINCE "{since_date}")')
                
                if status != 'OK':
                    logger.error("搜索邮件失败")
                    yield {
                        'status': 'error',
                        'message': '搜索邮件失败'
                    }
                    return
                
                message_ids = (uids[0] or b'').split()
                # 按 UID 数值排序，截取最新 N 封
                message_ids = sorted(message_ids, key=lambda x: int(x))
                if max_count and isinstance(max_count, int) and max_count > 0:
                    message_ids = message_ids[-max_count:]
                
                logger.info(f"找到 {len(message_ids)} 封邮件（流式处理，UID增量）")
                
                # 如果没有邮件，直接返回完成状态
                if not message_ids:
                    yield {
                        'status': 'completed',
                        'message': '没有找到新邮件',
                        'total_emails': 0,
                        'new_emails': 0
                    }
                    return
                
                logger.info(f"用户 last_seen_uid={last_seen_uid}")
                
                # 先处理未处理的邮件：放入线程池并行分析（主线程继续 fetch/save）
                unprocessed_emails = self._get_unprocessed_emails(user_id)
                
                logger.info(f"最终处理 {len(message_ids)} 封邮件（流式处理）")
                logger.info(f"message_ids类型: {type(message_ids)}, 长度: {len(message_ids) if message_ids else 0}")
                if message_ids:
                    logger.info(f"前5个message_ids: {message_ids[:5]}")
                
                # 发送统计信息（注意：message_ids 是 UID 增量的“候选集合”，其中一部分可能已存在于数据库，会被跳过）
                yield {
                    'status': 'stats',
                    'message': f'扫描到 {len(message_ids)} 封候选邮件（UID增量）。已存在的会跳过；真正保存/分析时会逐封输出详情。',
                    'total_emails': len(message_ids),
                    'new_emails': len(message_ids)
                }
                
                if not message_ids:
                    yield {
                        'status': 'completed',
                        'message': '没有新邮件需要处理',
                        'total_emails': len(message_ids),
                        'new_emails': 0
                    }
                    return
                
                # 统计变量
                total_emails = len(message_ids)
                processed_count = 0
                saved_count = 0
                analyzed_count = 0
                max_uid_seen = last_seen_uid
                
                # 并行分析：主循环只负责 fetch/save；AI 分析丢到线程池里跑
                try:
                    aw = int(analysis_workers or 3)
                except Exception:
                    aw = 3
                if aw < 1:
                    aw = 1
                if aw > 8:
                    aw = 8

                logger.info(f"开始处理邮件循环，total_emails: {total_emails}, analysis_workers={aw}")

                in_flight = {}  # future -> meta(email_id, subject, kind)

                def _submit_analysis(executor, email_id: int, email_data: Dict[str, Any], kind: str):
                    """提交分析任务（线程池仅做 AI 分析，落库/建日程由主线程处理）"""
                    # 复制必要字段，避免 email_data 在主线程后续被修改影响任务
                    payload = {
                        "subject": email_data.get("subject", ""),
                        "content": email_data.get("content", ""),
                        "received_date": email_data.get("received_date"),
                        "matched_keywords": email_data.get("matched_keywords", []),
                        "sender": email_data.get("sender", ""),
                    }

                    def _task():
                        svc = AIService(self.config)
                        return svc.analyze_email_content(
                            payload["content"],
                            payload["subject"],
                            user_id=user_id,
                            reference_time=payload["received_date"],
                        )

                    fut = executor.submit(_task)
                    in_flight[fut] = {"email_id": email_id, "email_data": payload, "kind": kind}

                def _drain_done(nonblock: bool = True):
                    """吐出已完成的分析结果（尽量不阻塞 fetch/save）。"""
                    nonlocal analyzed_count
                    if not in_flight:
                        return
                    timeout = 0 if nonblock else 60
                    done, _ = wait(list(in_flight.keys()), timeout=timeout, return_when=FIRST_COMPLETED if nonblock else None)
                    for fut in list(done):
                        meta = in_flight.pop(fut, None) or {}
                        email_id = meta.get("email_id")
                        email_data = meta.get("email_data") or {}
                        kind = meta.get("kind") or "new"
                                try:
                            analysis_result = fut.result()
                        except Exception as e:
                            yield {"status": "error", "email_id": email_id, "subject": email_data.get("subject", ""), "message": f"分析线程异常: {e}"}
                            continue
                                    
                                    if analysis_result:
                            # 主线程落库 + 建日程，避免 SQLite 并发写锁/重复连接问题
                            try:
                                self.save_email_analysis(email_data, analysis_result, user_id, email_id=email_id)
                                if analysis_result.get("events"):
                                            from .scheduler_service import SchedulerService
                                            scheduler_service = SchedulerService(self.config)
                                    for ev in analysis_result["events"]:
                                        ev["email_id"] = email_id
                                        scheduler_service.add_event(ev, user_id)
                            except Exception as e:
                                logger.error(f"保存分析/建日程失败(email_id={email_id}): {e}")

                            analyzed_count += 1
                                        yield {
                                "status": "reanalyzed" if kind == "existing" else "analyzed",
                                "email_id": email_id,
                                "subject": email_data.get("subject", ""),
                                "sender": email_data.get("sender", ""),
                                "received_date": email_data.get("received_date"),
                                "analysis": analysis_result,
                                "message": f"分析完成: {email_data.get('subject', '')}",
                                        }
                                    else:
                                        yield {
                                "status": "error",
                                "email_id": email_id,
                                "subject": email_data.get("subject", ""),
                                "sender": email_data.get("sender", ""),
                                "received_date": email_data.get("received_date"),
                                "message": "分析失败: 无法获取分析结果",
                            }

                with ThreadPoolExecutor(max_workers=aw) as executor:
                    # 先把“未处理邮件”放进分析队列（并行跑）
                    if unprocessed_emails:
                                    yield {
                            "status": "stats",
                            "message": f"发现 {len(unprocessed_emails)} 封未处理邮件，优先并行分析",
                            "total_emails": total_emails,
                            "new_emails": len(message_ids),
                            "unprocessed_emails": len(unprocessed_emails),
                        }
                        yield {
                            "status": "info",
                            "message": f"发现 {len(unprocessed_emails)} 封未处理邮件，已加入并行分析队列"
                        }
                        for row in unprocessed_emails:
                            if cancel_event is not None and getattr(cancel_event, "is_set", None) and cancel_event.is_set():
                                yield {"status": "cancelled", "message": "已终止流式处理"}
                                return
                        yield {
                                "status": "reanalyzing",
                                "email_id": row["id"],
                                "subject": row.get("subject", ""),
                                "sender": row.get("sender", ""),
                                "received_date": row.get("received_date"),
                                "message": f"重新分析: {row.get('subject', '')}",
                            }
                            yield {
                                "status": "analyzing",
                                "email_id": row["id"],
                                "subject": row.get("subject", ""),
                                "sender": row.get("sender", ""),
                                "received_date": row.get("received_date"),
                                "message": f"开始分析: {row.get('subject', '')}",
                            }
                            _submit_analysis(executor, row["id"], row, kind="existing")

                    skipped_total = 0
                    skipped_emitted = 0

                    for i, msg_id in enumerate(message_ids):
                        # 支持外部终止（后台任务停止按钮）
                        if cancel_event is not None and getattr(cancel_event, "is_set", None) and cancel_event.is_set():
                            yield {"status": "cancelled", "message": "已终止流式处理"}
                            for fut in list(in_flight.keys()):
                                fut.cancel()
                            return

                        # 尽量先吐出已完成的分析结果（不阻塞）
                        for ev in _drain_done(nonblock=True):
                            yield ev

                        logger.info(f"处理邮件 {i+1}/{total_emails}: msg_id={msg_id}")

                        # 发送进度更新（每10封邮件更新一次：扫描进度）
                        if i % 10 == 0:
                        yield {
                                "status": "progress",
                                "processed": i,
                                "total": total_emails,
                                "saved": saved_count,
                                "analyzed": analyzed_count,
                                "message": f"扫描进度：第 {i+1}/{total_emails} 封；已保存 {saved_count}；已分析 {analyzed_count}",
                            }

                        # 1. 获取邮件（按 UID）
                        try:
                            uid_int = int(msg_id)
                            if uid_int > max_uid_seen:
                                max_uid_seen = uid_int
                        except Exception:
                            uid_int = None

                        status, msg_data = imap.uid("fetch", msg_id, "(RFC822)")
                        if status != "OK":
                            logger.warning(f"获取邮件 {msg_id} 失败: {status}")
                            continue

                        raw_email = msg_data[0][1]
                        msg = email.message_from_bytes(raw_email)

                        # 2. 解析邮件数据
                        email_data = self.parse_email_message(
                            msg, user_id, None, imap_uid=str(uid_int) if uid_int is not None else None
                        )
                        if not email_data:
                            logger.warning(f"解析邮件 {msg_id} 失败")
                            continue

                        # 检查是否已存在
                        existing_email = self.email_model.get_email_by_message_id(email_data["message_id"], user_id)
                        if existing_email:
                            if not existing_email.get("is_processed", False):
                                yield {
                                    "status": "reanalyzing",
                                    "email_id": existing_email["id"],
                                    "subject": email_data.get("subject", ""),
                                    "sender": email_data.get("sender", ""),
                                    "message": f"重新分析邮件: {email_data.get('subject', '')}",
                                }
                                yield {
                                    "status": "analyzing",
                                    "email_id": existing_email["id"],
                                    "subject": email_data.get("subject", ""),
                                    "message": f"开始分析: {email_data.get('subject', '')}",
                                }
                                _submit_analysis(executor, existing_email["id"], email_data, kind="existing")
                                continue
                            else:
                                # 已存在且已处理：节流输出 skipped，避免用户误以为“卡住/进入别的代码”
                                skipped_total += 1
                                if skipped_emitted < 10 or (skipped_total % 200 == 0):
                                    skipped_emitted += 1
                                yield {
                                        "status": "skipped",
                                        "email_id": existing_email.get("id"),
                                        "subject": email_data.get("subject", ""),
                                        "sender": email_data.get("sender", ""),
                                        "received_date": email_data.get("received_date"),
                                        "message": f"已存在且已处理，跳过: {email_data.get('subject', '')}",
                                    }
                                continue

                        # 3. 保存新邮件
                            yield {
                            "status": "saving",
                            "subject": email_data.get("subject", ""),
                            "sender": email_data.get("sender", ""),
                            "message": f"保存邮件: {email_data.get('subject', '')}",
                        }

                        email_id = self.email_model.save_email(email_data, user_id)
                        yield {
                            "status": "saved",
                            "email_id": email_id,
                            "subject": email_data.get("subject", ""),
                            "sender": email_data.get("sender", ""),
                            "received_date": email_data.get("received_date"),
                            "message": f"邮件已保存: {email_data.get('subject', '')}",
                        }

                        # 4) 并行分析（上一封分析时继续获取下一封）
                        yield {
                            "status": "analyzing",
                            "email_id": email_id,
                            "subject": email_data.get("subject", ""),
                            "message": f"开始分析: {email_data.get('subject', '')}",
                        }
                        _submit_analysis(executor, email_id, email_data, kind="new")

                        processed_count += 1
                        saved_count += 1
                        
                        yield {
                            "status": "progress",
                            "processed": processed_count,
                            "total": total_emails,
                            "saved": saved_count,
                            "analyzed": analyzed_count,
                            "message": f"已处理 {processed_count} 个邮件，共 {total_emails} 个邮件",
                        }

                    # fetch 完成后，等待所有分析任务完成（同时支持取消）
                    while in_flight:
                        if cancel_event is not None and getattr(cancel_event, "is_set", None) and cancel_event.is_set():
                            yield {"status": "cancelled", "message": "已终止流式处理"}
                            for fut in list(in_flight.keys()):
                                fut.cancel()
                            return
                        for ev in _drain_done(nonblock=False):
                            yield ev

                # （已在 ThreadPoolExecutor 块内等待 in_flight 清空）
                
                # 发送最终完成状态
                yield {
                    'status': 'completed',
                    'message': f'流式处理完成，共处理 {processed_count} 封邮件',
                    'total_emails': total_emails,
                    'saved': saved_count,
                    'analyzed': analyzed_count
                }

                # 推进 UID 游标，避免下次重复扫描
                try:
                    if max_uid_seen > last_seen_uid:
                        config_service.set_user_config(user_id, 'email', 'last_seen_uid', int(max_uid_seen))
                except Exception:
                    pass
                        
            finally:
                # 恢复原始配置并关闭连接
                self.email_config = original_config
                try:
                    if imap is not None:
                        try:
                            imap.close()
                        except Exception:
                            pass
                        imap.logout()
                except Exception:
                    pass
                
                # 清除邮件缓存，确保前端能看到最新数据
                try:
                    from ..app import clear_email_cache
                    clear_email_cache()
                except Exception:
                    pass
                    
        except Exception as e:
            logger.error(f"流式处理邮件失败: {e}")
            yield {
                'status': 'error',
                'message': f"流式处理失败: {str(e)}"
            }
    
    def save_analysis_only(self, email_id: int, email_data: Dict[str, Any], analysis_result: Dict[str, Any]) -> int:
        """只保存分析结果（用于重新分析已有邮件）
        
        Args:
            email_id: 邮件ID
            email_data: 邮件数据（用于获取matched_keywords等）
            analysis_result: AI分析结果
        
        Returns:
            邮件ID
        """
        try:
            from ..models.database import DatabaseManager
            import json
            from datetime import datetime
            
            db = DatabaseManager(self.config)
            
            # 先删除旧的分析结果
            delete_query = "DELETE FROM email_analysis WHERE email_id = ?"
            db.execute_update(delete_query, (email_id,))
            
            # 插入新的分析结果（注意：email_analysis 表包含 user_id，用于数据隔离）
            analysis_query = """
            INSERT INTO email_analysis 
            (user_id, email_id, summary, importance_score, importance_reason, 
             events_json, keywords_matched, ai_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """
            
            def convert_datetime_to_string(obj):
                """递归地将对象中的所有datetime对象转换为ISO格式字符串"""
                if isinstance(obj, datetime):
                    return obj.isoformat()
                elif isinstance(obj, dict):
                    return {key: convert_datetime_to_string(value) for key, value in obj.items()}
                elif isinstance(obj, list):
                    return [convert_datetime_to_string(item) for item in obj]
                else:
                    return obj
            
            # 处理events中的datetime对象
            events = analysis_result.get('events', [])
            events_json = convert_datetime_to_string(events)
            
            # 处理matched_keywords中的datetime对象
            matched_keywords = email_data.get('matched_keywords', [])
            keywords_json = convert_datetime_to_string(matched_keywords)
            
            # user_id 必须存在（否则会落到默认用户 1）
            user_id = email_data.get('user_id', 1)
            analysis_params = (
                user_id,
                email_id,
                analysis_result.get('summary', ''),
                analysis_result.get('importance_score', 5),
                analysis_result.get('importance_reason', ''),
                json.dumps(events_json, ensure_ascii=False),
                json.dumps(keywords_json, ensure_ascii=False),
                analysis_result.get('ai_model', '')
            )
            
            db.execute_insert(analysis_query, analysis_params)
            
            # 标记为已处理
            self.email_model.mark_email_processed(email_id)
            
            logger.info(f"保存分析结果完成: {email_data.get('subject', '未知')}")
            return email_id
            
        except Exception as e:
            logger.error(f"保存分析结果失败: {e}")
            raise
    
    def save_email_analysis(self, email_data: Dict[str, Any], analysis_result: Dict[str, Any], user_id: int, email_id: int = None) -> int:
        """保存分析结果（可选：如果 email_id 为空则先保存邮件）
        
        Args:
            email_data: 邮件数据
            analysis_result: AI分析结果
            user_id: 用户ID
        
        Returns:
            邮件ID
        """
        try:
            # 如果未提供 email_id，先保存邮件；否则严禁重复 save_email（避免 REPLACE 导致 email_id 变化）
            if email_id is None:
            email_id = self.email_model.save_email(email_data, user_id)
            
            # 复用 “只保存分析结果” 的逻辑
            # 确保 user_id 写入分析表
            email_data = dict(email_data or {})
            email_data['user_id'] = user_id
            self.save_analysis_only(email_id, email_data, analysis_result)
            
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
                
                # 提供 html 字段给前端富文本展示
                if email.get('html_content'):
                    html_content = email['html_content']
                    # 重新处理HTML中的图片路径，确保图片能正确显示
                    try:
                        import json
                        # 获取该邮件的图片附件信息
                        images_query = """
                        SELECT images FROM emails 
                        WHERE id = ? AND user_id = ?
                        """
                        images_result = db.execute_query(images_query, (email_id, user_id))
                        
                        cid_to_filename = {}
                        if images_result and images_result[0].get('images'):
                            try:
                                images_data = json.loads(images_result[0]['images'])
                                # 构建CID到文件名的映射
                                for img in images_data:
                                    if img.get('filename') and img.get('unique_filename'):
                                        # 尝试从文件名中提取可能的CID
                                        filename = img['filename']
                                        unique_filename = img['unique_filename']
                                        # 将文件名作为可能的CID进行映射
                                        cid_to_filename[filename] = unique_filename
                                        # 也尝试去掉扩展名
                                        if '.' in filename:
                                            base_name = filename.rsplit('.', 1)[0]
                                            cid_to_filename[base_name] = unique_filename
                            except json.JSONDecodeError:
                                pass
                        
                        # 重新进行图片路径重写
                        html_content = self._rewrite_html_inline_images(html_content, cid_to_filename)
                        html_content = self._rewrite_remote_images(html_content)
                        html_content = self._sanitize_html(html_content)
                        
                        # 将图片直接嵌入HTML中，避免路径访问问题
                        html_content = self._embed_images_in_html(html_content, images_data)
                    except Exception as e:
                        logger.warning(f"重新处理HTML图片路径失败: {e}")
                    
                    email['html'] = html_content
                
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