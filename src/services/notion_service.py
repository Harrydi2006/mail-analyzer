# -*- coding: utf-8 -*-
"""
Notion集成服务模块
"""

import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from notion_client import Client
from notion_client.errors import APIResponseError

from ..core.config import Config
from ..core.logger import get_logger
from ..models.database import DatabaseManager

logger = get_logger(__name__)


class NotionService:
    """Notion集成服务类"""
    
    def __init__(self, config: Config, user_id: int = None):
        """初始化Notion服务
        
        Args:
            config: 配置对象
            user_id: 用户ID，用于获取用户级别的配置
        """
        self.config = config
        self.user_id = user_id
        self.db = DatabaseManager(config)
        
        # 初始化Notion客户端
        self.client = None
        self._initialize_client()
    
    def _initialize_client(self):
        """初始化或重新初始化Notion客户端"""
        try:
            # 根据用户ID获取配置
            if self.user_id:
                from ..services.config_service import UserConfigService
                config_service = UserConfigService()
                self.notion_config = config_service.get_notion_config(self.user_id)
            else:
                # 使用全局配置作为后备
                full_config = self.config.get_full_config()
                self.notion_config = full_config.get('notion', {})
            
            token = self.notion_config.get('token')
            if token:
                self.client = Client(auth=token)
                logger.info(f"Notion客户端初始化成功 (用户ID: {self.user_id})")
            else:
                self.client = None
                logger.warning(f"Notion Token未配置，客户端未初始化 (用户ID: {self.user_id})")
        except Exception as e:
            logger.error(f"初始化Notion客户端失败: {e}")
            self.client = None
    
    def test_connection(self) -> Dict[str, Any]:
        """测试Notion连接
        
        Returns:
            测试结果
        """
        try:
            # 重新初始化客户端以获取最新配置
            self._initialize_client()
            
            if not self.client:
                return {
                    'success': False,
                    'error': 'Notion客户端未初始化，请检查token配置'
                }
            
            # 测试获取用户信息
            user_info = self.client.users.me()
            
            # 如果配置了数据库ID，测试数据库访问
            database_id = self.notion_config.get('database_id')
            if database_id:
                try:
                    database_info = self.client.databases.retrieve(database_id)
                    return {
                        'success': True,
                        'message': 'Notion连接正常',
                        'user': user_info.get('name', ''),
                        'database': database_info.get('title', [{}])[0].get('plain_text', '')
                    }
                except APIResponseError as e:
                    return {
                        'success': False,
                        'error': f'数据库访问失败: {e}'
                    }
            else:
                return {
                    'success': True,
                    'message': 'Notion连接正常，但未配置数据库ID',
                    'user': user_info.get('name', '')
                }
                
        except Exception as e:
            logger.error(f"Notion连接测试失败: {e}")
            return {
                'success': False,
                'error': str(e)
            }
    
    def create_database_if_not_exists(self, parent_page_id: str = None) -> Optional[str]:
        """创建邮件归档数据库（如果不存在）
        
        Args:
            parent_page_id: 父页面ID，如果为None则在根目录创建
        
        Returns:
            数据库ID
        """
        try:
            # 重新初始化客户端以获取最新配置
            self._initialize_client()
            
            if not self.client:
                logger.error("Notion客户端未初始化，请检查Token配置")
                return None
            
            # 数据库属性定义
            properties = {
                "邮件主题": {
                    "title": {}
                },
                "发件人": {
                    "email": {}
                },
                "收件时间": {
                    "date": {}
                },
                "重要性": {
                    "select": {
                        "options": [
                            {"name": "重要", "color": "red"},
                            {"name": "普通", "color": "blue"},
                            {"name": "不重要", "color": "gray"}
                        ]
                    }
                },
                "AI总结": {
                    "rich_text": {}
                },
                "事件数量": {
                    "number": {}
                },
                "处理状态": {
                    "select": {
                        "options": [
                            {"name": "已处理", "color": "green"},
                            {"name": "待处理", "color": "yellow"},
                            {"name": "已归档", "color": "blue"}
                        ]
                    }
                },
                "标签": {
                    "multi_select": {
                        "options": [
                            {"name": "考试", "color": "red"},
                            {"name": "作业", "color": "orange"},
                            {"name": "会议", "color": "blue"},
                            {"name": "讲座", "color": "green"},
                            {"name": "通知", "color": "purple"}
                        ]
                    }
                }
            }
            
            # 如果没有指定父页面，尝试获取用户的根页面
            if not parent_page_id:
                # 搜索用户的页面，找到一个可以作为父页面的
                search_result = self.client.search(
                    filter={"property": "object", "value": "page"},
                    page_size=1
                )
                
                if search_result.get('results'):
                    parent_page_id = search_result['results'][0]['id']
                    logger.info(f"使用找到的页面作为父页面: {parent_page_id}")
                else:
                    logger.error("未找到可用的父页面，无法创建数据库")
                    return None
            
            # 创建数据库
            database_data = {
                "parent": {
                    "type": "page_id",
                    "page_id": parent_page_id
                },
                "title": [
                    {
                        "type": "text",
                        "text": {
                            "content": "邮件智能归档数据库"
                        }
                    }
                ],
                "properties": properties
            }
            
            database = self.client.databases.create(**database_data)
            database_id = database['id']
            
            logger.info(f"成功创建Notion数据库: {database_id}")
            
            # 更新配置
            self.config.set('notion.database_id', database_id)
            self.config.save_config()
            
            return database_id
            
        except Exception as e:
            logger.error(f"创建Notion数据库失败: {e}")
            return None
    
    def archive_email(self, email_data: Dict[str, Any], analysis_result: Dict[str, Any]) -> Optional[str]:
        """归档邮件到Notion
        
        Args:
            email_data: 邮件数据
            analysis_result: AI分析结果
        
        Returns:
            Notion页面ID
        """
        try:
            # 重新初始化客户端以获取最新配置
            self._initialize_client()
            
            if not self.client:
                logger.warning("Notion客户端未初始化，跳过归档")
                return None
            
            database_id = self.notion_config.get('database_id')
            if not database_id:
                logger.warning("未配置Notion数据库ID，跳过归档")
                return None
            
            # 准备页面属性
            properties = {
                "邮件主题": {
                    "title": [
                        {
                            "text": {
                                "content": email_data.get('subject', '无主题')
                            }
                        }
                    ]
                },
                "发件人": {
                    "email": email_data.get('sender', '')
                },
                "收件时间": {
                    "date": {
                        "start": self._format_date_for_notion(email_data.get('received_date', datetime.now()))
                    }
                },
                "重要性": {
                    "select": {
                        "name": self._map_importance_level(email_data.get('importance_level', 'normal'))
                    }
                },
                "AI总结": {
                    "rich_text": [
                        {
                            "text": {
                                "content": analysis_result.get('summary', '')
                            }
                        }
                    ]
                },
                "事件数量": {
                    "number": len(analysis_result.get('events', []))
                },
                "处理状态": {
                    "select": {
                        "name": "已归档"
                    }
                }
            }
            
            # 添加标签
            tags = self._extract_tags(email_data, analysis_result)
            if tags:
                properties["标签"] = {
                    "multi_select": [{"name": tag} for tag in tags[:5]]  # 最多5个标签
                }
            
            # 准备页面内容
            children = self._create_page_content(email_data, analysis_result)
            
            # 创建页面
            page_data = {
                "parent": {
                    "database_id": database_id
                },
                "properties": properties,
                "children": children
            }
            
            page = self.client.pages.create(**page_data)
            page_id = page['id']
            
            # 保存归档记录
            self._save_archive_record(email_data.get('id'), page_id, page.get('url', ''))
            
            logger.info(f"成功归档邮件到Notion: {email_data.get('subject', '')} -> {page_id}")
            return page_id
            
        except Exception as e:
            logger.error(f"归档邮件到Notion失败: {e}")
            return None
    
    def _map_importance_level(self, importance_level: str) -> str:
        """映射重要性级别到Notion选项
        
        Args:
            importance_level: 重要性级别
        
        Returns:
            Notion选项名称
        """
        mapping = {
            'important': '重要',
            'normal': '普通',
            'unimportant': '不重要'
        }
        return mapping.get(importance_level, '普通')
    
    def _format_date_for_notion(self, date_value) -> str:
        """格式化日期为Notion可接受的ISO格式
        
        Args:
            date_value: 日期值，可能是datetime对象或字符串
        
        Returns:
            ISO格式的日期字符串
        """
        if isinstance(date_value, datetime):
            return date_value.isoformat()
        elif isinstance(date_value, str):
            try:
                # 尝试解析字符串日期
                from dateutil import parser
                parsed_date = parser.parse(date_value)
                return parsed_date.isoformat()
            except:
                # 如果解析失败，返回当前时间
                return datetime.now().isoformat()
        else:
            # 其他类型，返回当前时间
            return datetime.now().isoformat()
    
    def _extract_tags(self, email_data: Dict[str, Any], analysis_result: Dict[str, Any]) -> List[str]:
        """提取邮件标签
        
        Args:
            email_data: 邮件数据
            analysis_result: 分析结果
        
        Returns:
            标签列表
        """
        tags = []
        
        # 根据匹配的关键词添加标签
        matched_keywords = email_data.get('matched_keywords', [])
        for keyword_type, keyword in matched_keywords:
            if '考试' in keyword or 'exam' in keyword.lower():
                tags.append('考试')
            elif '作业' in keyword or 'assignment' in keyword.lower():
                tags.append('作业')
            elif '会议' in keyword or 'meeting' in keyword.lower():
                tags.append('会议')
            elif '讲座' in keyword or 'lecture' in keyword.lower():
                tags.append('讲座')
        
        # 根据事件类型添加标签
        events = analysis_result.get('events', [])
        for event in events:
            if event.get('importance_level') == 'important':
                if '考试' in event.get('title', '') or '截止' in event.get('title', ''):
                    tags.append('考试')
                elif '作业' in event.get('title', ''):
                    tags.append('作业')
        
        # 默认添加通知标签
        if not tags:
            tags.append('通知')
        
        return list(set(tags))  # 去重
    
    def _create_page_content(self, email_data: Dict[str, Any], analysis_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """创建Notion页面内容
        
        Args:
            email_data: 邮件数据
            analysis_result: 分析结果
        
        Returns:
            Notion页面内容块列表
        """
        children = []
        
        # 添加邮件基本信息
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": "📧 邮件信息"
                        }
                    }
                ]
            }
        })
        
        # 邮件详情
        email_info = f"""**发件人:** {email_data.get('sender', '')}
**收件人:** {email_data.get('recipient', '')}
**收件时间:** {email_data.get('received_date', '')}
**重要性:** {email_data.get('importance_level', 'normal')}"""
        
        children.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": email_info
                        }
                    }
                ]
            }
        })
        
        # AI分析结果
        if analysis_result.get('summary'):
            children.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "🤖 AI分析结果"
                            }
                        }
                    ]
                }
            })
            
            children.append({
                "object": "block",
                "type": "callout",
                "callout": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": analysis_result['summary']
                            }
                        }
                    ],
                    "icon": {
                        "emoji": "💡"
                    }
                }
            })
        
        # 提取的事件
        events = analysis_result.get('events', [])
        if events:
            children.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": "📅 提取的事件"
                            }
                        }
                    ]
                }
            })
            
            for i, event in enumerate(events, 1):
                event_text = f"""**事件 {i}:** {event.get('title', '')}
**时间:** {event.get('start_time', '')}
**描述:** {event.get('description', '')}
**重要性:** {event.get('importance_level', '')}"""
                
                if event.get('location'):
                    event_text += f"\n**地点:** {event['location']}"
                
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": event_text
                                }
                            }
                        ]
                    }
                })
        
        # 邮件图片附件
        images = email_data.get('images', [])
        if images:
            children.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"🖼️ 邮件图片 ({len(images)}张)"
                            }
                        }
                    ]
                }
            })
            
            for image in images:
                # 添加图片说明
                children.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {
                                "type": "text",
                                "text": {
                                    "content": f"📎 {image.get('filename', '未知文件')} ({self._format_file_size(image.get('size', 0))})"
                                }
                            }
                        ]
                    }
                })
                
                # 尝试上传图片到Notion
                image_block = self._create_image_block(image)
                if image_block:
                    children.append(image_block)
        
        # 邮件原文
        children.append({
            "object": "block",
            "type": "heading_2",
            "heading_2": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": "📄 邮件原文"
                        }
                    }
                ]
            }
        })
        
        # 邮件内容（完整内容）
        content = email_data.get('content', '')
        
        children.append({
            "object": "block",
            "type": "code",
            "code": {
                "rich_text": [
                    {
                        "type": "text",
                        "text": {
                            "content": content
                        }
                    }
                ],
                "language": "plain text"
            }
        })
        
        return children
    
    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小
        
        Args:
            size_bytes: 文件大小（字节）
        
        Returns:
            格式化的文件大小字符串
        """
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
    
    def _create_image_block(self, image_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """创建图片块
        
        Args:
            image_data: 图片数据
        
        Returns:
            Notion图片块或None
        """
        try:
            # Notion目前不支持直接上传图片，只能使用外部URL
            # 这里我们创建一个代码块来显示图片的base64数据（截断显示）
            base64_data = image_data.get('base64_data', '')
            if len(base64_data) > 100:
                display_data = base64_data[:100] + '...'
            else:
                display_data = base64_data
            
            return {
                "object": "block",
                "type": "code",
                "code": {
                    "rich_text": [
                        {
                            "type": "text",
                            "text": {
                                "content": f"图片数据 (Base64): {display_data}"
                            }
                        }
                    ],
                    "language": "plain text"
                }
            }
            
        except Exception as e:
            logger.error(f"创建图片块失败: {e}")
            return None
    
    def _save_archive_record(self, email_id: int, notion_page_id: str, notion_url: str):
        """保存归档记录
        
        Args:
            email_id: 邮件ID
            notion_page_id: Notion页面ID
            notion_url: Notion页面URL
        """
        try:
            query = """
            INSERT INTO notion_archive (email_id, notion_page_id, notion_url)
            VALUES (?, ?, ?)
            """
            
            self.db.execute_insert(query, (email_id, notion_page_id, notion_url))
            logger.info(f"保存归档记录: 邮件 {email_id} -> Notion {notion_page_id}")
            
        except Exception as e:
            logger.error(f"保存归档记录失败: {e}")
    
    def get_archived_emails(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取已归档的邮件列表
        
        Args:
            limit: 返回数量限制
        
        Returns:
            已归档邮件列表
        """
        try:
            query = """
            SELECT e.*, na.notion_page_id, na.notion_url, na.archived_at
            FROM emails e
            JOIN notion_archive na ON e.id = na.email_id
            ORDER BY na.archived_at DESC
            LIMIT ?
            """
            
            return self.db.execute_query(query, (limit,))
            
        except Exception as e:
            logger.error(f"获取已归档邮件失败: {e}")
            return []
    
    def get_notion_page_url(self, email_id: int) -> Optional[str]:
        """获取邮件对应的Notion页面URL
        
        Args:
            email_id: 邮件ID
        
        Returns:
            Notion页面URL
        """
        try:
            query = """
            SELECT notion_url FROM notion_archive 
            WHERE email_id = ?
            """
            
            results = self.db.execute_query(query, (email_id,))
            
            if results:
                return results[0]['notion_url']
            
            return None
            
        except Exception as e:
            logger.error(f"获取Notion页面URL失败: {e}")
            return None
    
    def update_page(self, page_id: str, properties: Dict[str, Any]) -> bool:
        """更新Notion页面属性
        
        Args:
            page_id: 页面ID
            properties: 要更新的属性
        
        Returns:
            是否更新成功
        """
        try:
            if not self.client:
                return False
            
            self.client.pages.update(
                page_id=page_id,
                properties=properties
            )
            
            logger.info(f"成功更新Notion页面: {page_id}")
            return True
            
        except Exception as e:
            logger.error(f"更新Notion页面失败: {e}")
            return False
    
    def search_pages(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """搜索Notion页面
        
        Args:
            query: 搜索关键词
            limit: 返回数量限制
        
        Returns:
            搜索结果列表
        """
        try:
            if not self.client:
                return []
            
            search_result = self.client.search(
                query=query,
                page_size=limit
            )
            
            return search_result.get('results', [])
            
        except Exception as e:
            logger.error(f"搜索Notion页面失败: {e}")
            return []