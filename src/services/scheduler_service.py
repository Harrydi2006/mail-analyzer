# -*- coding: utf-8 -*-
"""
日程管理服务模块
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from icalendar import Calendar, Event as ICalEvent
import pytz

from ..core.config import Config
from ..core.logger import get_logger
from ..models.database import EventModel, DatabaseManager

logger = get_logger(__name__)


class SchedulerService:
    """日程管理服务类"""
    
    def __init__(self, config: Config):
        """初始化日程管理服务
        
        Args:
            config: 配置对象
        """
        self.config = config
        self.event_model = EventModel(config)
        self.db = DatabaseManager(config)
        self.reminder_config = config.reminder_config
    
    def add_event(self, event_data: Dict[str, Any], user_id: int = None) -> int:
        """添加事件到日程
        
        Args:
            event_data: 事件数据
            user_id: 用户ID
        
        Returns:
            事件ID
        """
        try:
            # 验证必要字段
            if not event_data.get('title'):
                raise ValueError("事件标题不能为空")
            
            if not event_data.get('start_time'):
                raise ValueError("事件开始时间不能为空")
            
            # 确保时间是datetime对象
            if isinstance(event_data['start_time'], str):
                event_data['start_time'] = datetime.fromisoformat(event_data['start_time'])
            
            if event_data.get('end_time') and isinstance(event_data['end_time'], str):
                event_data['end_time'] = datetime.fromisoformat(event_data['end_time'])
            
            # 设置默认值
            event_data.setdefault('importance_level', 'normal')
            event_data.setdefault('color', self._get_color_by_importance(event_data['importance_level']))
            
            # 设置用户ID（强制要求）
            if user_id is None:
                raise ValueError("缺少用户ID，无法添加事件")
            event_data['user_id'] = user_id
            
            # 计算提醒时间
            if 'reminder_times' not in event_data:
                event_data['reminder_times'] = self._calculate_reminder_times(
                    event_data['start_time'],
                    event_data['importance_level']
                )
            
            # 保存事件
            event_id = self.event_model.save_event(event_data)
            
            # 创建提醒
            self._create_reminders(event_id, event_data['reminder_times'], user_id)
            
            logger.info(f"成功添加事件: {event_data['title']} (ID: {event_id})")
            return event_id
            
        except Exception as e:
            logger.error(f"添加事件失败: {e}")
            raise
    
    def _get_color_by_importance(self, importance_level: str) -> str:
        """根据重要性获取颜色
        
        Args:
            importance_level: 重要性级别
        
        Returns:
            颜色代码
        """
        colors = self.reminder_config.get('colors', {})
        return colors.get(importance_level, '#4444FF')
    
    def _calculate_reminder_times(self, event_time: datetime, importance_level: str) -> List[datetime]:
        """计算提醒时间
        
        Args:
            event_time: 事件时间
            importance_level: 重要性级别
        
        Returns:
            提醒时间列表
        """
        reminder_times = []
        
        if importance_level == 'important':
            # 重要事件的提醒
            days_before = self.reminder_config.get('important_days_before', [3, 1])
            hours_before = self.reminder_config.get('important_hours_before', [1])
            
            # 天数提醒
            for days in days_before:
                reminder_time = event_time - timedelta(days=days)
                # 设置为当天上午9点提醒
                reminder_time = reminder_time.replace(hour=9, minute=0, second=0, microsecond=0)
                if reminder_time > datetime.now():
                    reminder_times.append(reminder_time)
            
            # 小时提醒
            for hours in hours_before:
                reminder_time = event_time - timedelta(hours=hours)
                if reminder_time > datetime.now():
                    reminder_times.append(reminder_time)
        
        elif importance_level == 'normal':
            # 普通事件提前1天提醒
            reminder_time = event_time - timedelta(days=1)
            reminder_time = reminder_time.replace(hour=9, minute=0, second=0, microsecond=0)
            if reminder_time > datetime.now():
                reminder_times.append(reminder_time)
        
        # 不重要事件不设置提醒
        
        return sorted(reminder_times)
    
    def _create_reminders(self, event_id: int, reminder_times: List[datetime], user_id: int):
        """创建提醒记录
        
        Args:
            event_id: 事件ID
            reminder_times: 提醒时间列表
        """
        try:
            for reminder_time in reminder_times:
                # 确定提醒类型
                reminder_type = 'exact_time'
                
                query = """
                INSERT INTO reminders (user_id, event_id, reminder_time, reminder_type)
                VALUES (?, ?, ?, ?)
                """
                
                self.db.execute_insert(query, (user_id, event_id, reminder_time, reminder_type))
            
            logger.info(f"为事件 {event_id} 创建了 {len(reminder_times)} 个提醒")
            
        except Exception as e:
            logger.error(f"创建提醒失败: {e}")
    
    def get_upcoming_events(self, user_id: int, days: int = 30) -> List[Dict[str, Any]]:
        """获取即将到来的事件
        
        Args:
            user_id: 用户ID
            days: 获取多少天内的事件
        
        Returns:
            事件列表
        """
        try:
            events = self.event_model.get_upcoming_events(days, user_id)
            
            # 添加额外信息
            for event in events:
                # 计算距离事件的时间
                if event.get('start_time'):
                    if isinstance(event['start_time'], str):
                        start_time = datetime.fromisoformat(event['start_time'])
                    else:
                        start_time = event['start_time']
                    
                    time_diff = start_time - datetime.now()
                    event['days_until'] = time_diff.days
                    event['hours_until'] = time_diff.total_seconds() / 3600
                
                # 获取相关的提醒
                event['reminders'] = self._get_event_reminders(event['id'])
            
            return events
            
        except Exception as e:
            logger.error(f"获取即将到来的事件失败: {e}")
            return []
    
    def _get_event_reminders(self, event_id: int) -> List[Dict[str, Any]]:
        """获取事件的提醒列表
        
        Args:
            event_id: 事件ID
        
        Returns:
            提醒列表
        """
        try:
            query = """
            SELECT * FROM reminders 
            WHERE event_id = ? 
            ORDER BY reminder_time ASC
            """
            
            return self.db.execute_query(query, (event_id,))
            
        except Exception as e:
            logger.error(f"获取事件提醒失败: {e}")
            return []
    
    def get_events_by_date_range(self, start_date: datetime, end_date: datetime) -> List[Dict[str, Any]]:
        """根据日期范围获取事件
        
        Args:
            start_date: 开始日期
            end_date: 结束日期
        
        Returns:
            事件列表
        """
        try:
            query = """
            SELECT * FROM events 
            WHERE start_time >= ? AND start_time <= ?
            ORDER BY start_time ASC
            """
            
            events = self.db.execute_query(query, (start_date, end_date))
            
            # 解析reminder_times JSON
            for event in events:
                if event.get('reminder_times'):
                    try:
                        event['reminder_times'] = json.loads(event['reminder_times'])
                    except json.JSONDecodeError:
                        event['reminder_times'] = []
            
            return events
            
        except Exception as e:
            logger.error(f"根据日期范围获取事件失败: {e}")
            return []
    
    def get_pending_reminders(self, user_id: int) -> List[Dict[str, Any]]:
        """获取待发送的提醒
        
        Returns:
            待发送提醒列表
        """
        try:
            query = """
            SELECT r.*, e.title, e.description, e.start_time, e.location, e.importance_level
            FROM reminders r
            JOIN events e ON r.event_id = e.id
            WHERE r.user_id = ?
            AND r.is_sent = FALSE 
            AND r.reminder_time <= ?
            ORDER BY r.reminder_time ASC
            """
            
            return self.db.execute_query(query, (user_id, datetime.now(),))
            
        except Exception as e:
            logger.error(f"获取待发送提醒失败: {e}")
            return []
    
    def mark_reminder_sent(self, reminder_id: int):
        """标记提醒为已发送
        
        Args:
            reminder_id: 提醒ID
        """
        try:
            query = """
            UPDATE reminders 
            SET is_sent = TRUE, sent_at = ?
            WHERE id = ?
            """
            
            self.db.execute_update(query, (datetime.now(), reminder_id))
            logger.info(f"标记提醒 {reminder_id} 为已发送")
            
        except Exception as e:
            logger.error(f"标记提醒已发送失败: {e}")
    
    def update_event(self, event_id: int, update_data: Dict[str, Any]) -> bool:
        """更新事件
        
        Args:
            event_id: 事件ID
            update_data: 更新数据
        
        Returns:
            是否更新成功
        """
        try:
            # 构建更新SQL
            set_clauses = []
            params = []
            
            for key, value in update_data.items():
                if key in ['title', 'description', 'start_time', 'end_time', 
                          'location', 'importance_level', 'color']:
                    set_clauses.append(f"{key} = ?")
                    params.append(value)
            
            if not set_clauses:
                return False
            
            set_clauses.append("updated_at = ?")
            params.append(datetime.now())
            params.append(event_id)
            
            query = f"""
            UPDATE events 
            SET {', '.join(set_clauses)}
            WHERE id = ?
            """
            
            rows_affected = self.db.execute_update(query, tuple(params))
            
            if rows_affected > 0:
                logger.info(f"成功更新事件 {event_id}")
                return True
            else:
                logger.warning(f"事件 {event_id} 不存在或未更新")
                return False
                
        except Exception as e:
            logger.error(f"更新事件失败: {e}")
            return False
    
    def delete_event(self, event_id: int) -> bool:
        """删除事件
        
        Args:
            event_id: 事件ID
        
        Returns:
            是否删除成功
        """
        try:
            # 先删除相关提醒
            self.db.execute_update("DELETE FROM reminders WHERE event_id = ?", (event_id,))
            
            # 删除事件
            rows_affected = self.db.execute_update("DELETE FROM events WHERE id = ?", (event_id,))
            
            if rows_affected > 0:
                logger.info(f"成功删除事件 {event_id}")
                return True
            else:
                logger.warning(f"事件 {event_id} 不存在")
                return False
                
        except Exception as e:
            logger.error(f"删除事件失败: {e}")
            return False
    
    def export_to_ical(self, events: List[Dict[str, Any]] = None, user_id: int = None) -> str:
        """导出事件到iCal格式
        
        Args:
            events: 事件列表，如果为None则导出所有即将到来的事件
        
        Returns:
            iCal格式字符串
        """
        try:
            if events is None:
                events = self.get_upcoming_events(365)  # 获取一年内的事件
            
            # 创建日历
            cal = Calendar()
            cal.add('prodid', '-//邮件智能日程管理系统//mxm.dk//')
            cal.add('version', '2.0')
            cal.add('calscale', 'GREGORIAN')
            cal.add('method', 'PUBLISH')
            
            # 读取用户订阅偏好（是否将持续性任务转为仅标记开始/结束）
            duration_as_markers = False
            try:
                if user_id is not None:
                    from .config_service import UserConfigService
                    _svc = UserConfigService()
                    sub_cfg = _svc.get_subscription_config(user_id)
                    duration_as_markers = bool(sub_cfg.get('duration_as_markers', False))
            except Exception:
                duration_as_markers = False

            # 添加事件
            for event_data in events:
                event = ICalEvent()
                
                # 基本信息 - 在标题中添加重要程度标识
                importance_level = event_data.get('importance_level', 'normal')
                title = event_data.get('title', '未命名事件')
                
                # 根据重要程度添加前缀标识
                if importance_level == 'important':
                    title = f"🔴 [重要] {title}"
                    category = "重要事件"
                    priority = 1
                elif importance_level == 'normal':
                    title = f"🟡 [普通] {title}"
                    category = "普通事件"
                    priority = 5
                else:
                    title = f"🔵 [一般] {title}"
                    category = "一般事件"
                    priority = 9
                
                event.add('summary', title)
                
                # 描述中也添加重要程度信息
                description = event_data.get('description', '')
                importance_text = {
                    'important': '重要程度：🔴 重要',
                    'normal': '重要程度：🟡 普通',
                    'unimportant': '重要程度：🔵 一般'
                }.get(importance_level, '重要程度：🟡 普通')
                
                if description:
                    description = f"{importance_text}\n\n{description}"
                else:
                    description = importance_text
                
                event.add('description', description)
                
                # 添加分类标识
                event.add('categories', category)
                
                # 时间信息
                start_time = event_data.get('start_time')
                if isinstance(start_time, str):
                    start_time = datetime.fromisoformat(start_time)
                event.add('dtstart', start_time)
                
                end_time = event_data.get('end_time')
                if duration_as_markers:
                    # 仅标记开始与结束为两个独立事件
                    # 为“开始”事件补齐信息（location/priority/uid/dtstamp）
                    if event_data.get('location'):
                        event.add('location', event_data['location'])
                    event.add('priority', priority)
                    event.add('uid', f"event-start-{event_data.get('id', 0)}@mail-scheduler")
                    event.add('dtstamp', datetime.now())
                    # 生成“开始”事件（dtstart==dtend 为时间点）
                    event.add('dtend', start_time)
                    cal.add_component(event)
                    # 若存在结束时间，追加一个结束标记事件
                    if end_time:
                        if isinstance(end_time, str):
                            end_time = datetime.fromisoformat(end_time)
                        end_ev = ICalEvent()
                        end_ev.add('summary', f"🔚 结束: {title}")
                        end_ev.add('description', description)
                        end_ev.add('categories', category)
                        end_ev.add('dtstart', end_time)
                        end_ev.add('dtend', end_time)
                        end_ev.add('priority', priority)
                        end_ev.add('uid', f"event-end-{event_data.get('id', 0)}@mail-scheduler")
                        end_ev.add('dtstamp', datetime.now())
                        if event_data.get('location'):
                            end_ev.add('location', event_data['location'])
                        cal.add_component(end_ev)
                    # 已手动添加，继续下一个
                    continue
                else:
                    if end_time:
                        if isinstance(end_time, str):
                            end_time = datetime.fromisoformat(end_time)
                        event.add('dtend', end_time)
                    else:
                        # 如果没有结束时间，设置为开始时间后1小时
                        event.add('dtend', start_time + timedelta(hours=1))
                
                # 其他信息
                if event_data.get('location'):
                    event.add('location', event_data['location'])
                
                # 设置优先级
                event.add('priority', priority)
                
                # 添加唯一ID
                event.add('uid', f"event-{event_data.get('id', 0)}@mail-scheduler")
                event.add('dtstamp', datetime.now())
                
                cal.add_component(event)
            
            return cal.to_ical().decode('utf-8')
            
        except Exception as e:
            logger.error(f"导出iCal失败: {e}")
            return ''
    
    def get_event_statistics(self) -> Dict[str, Any]:
        """获取事件统计信息
        
        Returns:
            统计信息字典
        """
        try:
            stats = {}
            
            # 总事件数
            total_query = "SELECT COUNT(*) as count FROM events"
            total_result = self.db.execute_query(total_query)
            stats['total_events'] = total_result[0]['count'] if total_result else 0
            
            # 按重要性分组统计
            importance_query = """
            SELECT importance_level, COUNT(*) as count 
            FROM events 
            GROUP BY importance_level
            """
            importance_results = self.db.execute_query(importance_query)
            stats['by_importance'] = {row['importance_level']: row['count'] for row in importance_results}
            
            # 即将到来的事件数（7天内）
            upcoming_query = """
            SELECT COUNT(*) as count 
            FROM events 
            WHERE start_time >= datetime('now') 
            AND start_time <= datetime('now', '+7 days')
            """
            upcoming_result = self.db.execute_query(upcoming_query)
            stats['upcoming_7_days'] = upcoming_result[0]['count'] if upcoming_result else 0
            
            # 待发送提醒数
            pending_reminders_query = """
            SELECT COUNT(*) as count 
            FROM reminders 
            WHERE is_sent = FALSE AND reminder_time <= datetime('now')
            """
            pending_result = self.db.execute_query(pending_reminders_query)
            stats['pending_reminders'] = pending_result[0]['count'] if pending_result else 0
            
            return stats
            
        except Exception as e:
            logger.error(f"获取事件统计失败: {e}")
            return {}
    
    def process_reminders(self, user_id: int) -> int:
        """处理待发送的提醒
        
        Returns:
            处理的提醒数量
        """
        try:
            pending_reminders = self.get_pending_reminders(user_id)
            processed_count = 0
            
            for reminder in pending_reminders:
                try:
                    # 这里可以集成实际的提醒发送逻辑
                    # 比如发送邮件、桌面通知、微信消息等
                    
                    # 记录提醒信息
                    logger.info(
                        f"提醒: {reminder['title']} - "
                        f"时间: {reminder['start_time']} - "
                        f"重要性: {reminder['importance_level']}"
                    )
                    
                    # 标记为已发送
                    self.mark_reminder_sent(reminder['id'])
                    processed_count += 1
                    
                except Exception as e:
                    logger.error(f"处理提醒 {reminder['id']} 失败: {e}")
            
            if processed_count > 0:
                logger.info(f"成功处理 {processed_count} 个提醒")
            
            return processed_count
            
        except Exception as e:
            logger.error(f"处理提醒失败: {e}")
            return 0
    
    def create_reminders_for_event(self, event_data: Dict[str, Any]):
        """为事件创建提醒
        
        Args:
            event_data: 事件数据
        """
        try:
            from ..services.ai_service import AIService
            
            # 创建AI服务实例来计算提醒时间
            ai_service = AIService(self.config)
            
            # 获取事件信息
            event_id = event_data.get('id')
            start_time = event_data.get('start_time')
            importance_level = event_data.get('importance_level', 'normal')
            
            if not event_id or not start_time:
                logger.warning("事件ID或开始时间缺失，无法创建提醒")
                return
            
            # 确保start_time是datetime对象
            if isinstance(start_time, str):
                start_time = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            
            # 计算提醒时间
            reminder_times = ai_service._calculate_reminder_times(start_time, importance_level)
            
            # 创建提醒记录
            for reminder_time in reminder_times:
                reminder_query = """
                INSERT INTO reminders (event_id, reminder_time, is_sent, created_at)
                VALUES (?, ?, 0, ?)
                """
                
                self.db.execute_insert(reminder_query, (
                    event_id,
                    reminder_time,
                    datetime.now()
                ))
            
            logger.info(f"为事件 {event_id} 创建了 {len(reminder_times)} 个提醒")
            
        except Exception as e:
            logger.error(f"创建事件提醒失败: {e}")