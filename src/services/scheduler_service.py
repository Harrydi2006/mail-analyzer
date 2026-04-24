# -*- coding: utf-8 -*-
"""
日程管理服务模块
"""

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from difflib import SequenceMatcher
from icalendar import Calendar, Event as ICalEvent
import pytz
import smtplib
import ssl
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from ..core.config import Config
from ..core.logger import get_logger
from ..models.database import EventModel, DatabaseManager
from .tag_service import TagService
from .fcm_service import FCMService
from .jpush_service import JPushService

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
        self.tag_service = TagService(config)

    def _safe_datetime(self, value: Any) -> Optional[datetime]:
        if not value:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00'))
            except Exception:
                return None
        return None

    def _normalize_text(self, text: Any) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip().lower())

    def _normalize_title(self, title: Any) -> str:
        t = self._normalize_text(title)
        t = re.sub(r"^(re|fw|fwd)\s*:\s*", "", t, flags=re.IGNORECASE)
        t = t.replace("[合并事件]", "").strip()
        return t

    def _normalize_weights(self, raw: Dict[str, Any]) -> Dict[str, float]:
        keys = ("title", "time", "tags", "sender", "location")
        out = {}
        total = 0.0
        for k in keys:
            v = float((raw or {}).get(k, 0.0) or 0.0)
            if v < 0:
                v = 0.0
            out[k] = v
            total += v
        if total <= 0:
            return {'title': 0.35, 'time': 0.30, 'tags': 0.20, 'sender': 0.10, 'location': 0.05}
        return {k: (out[k] / total) for k in keys}

    def _get_dedup_beta_config(self, user_id: int) -> Dict[str, Any]:
        try:
            from .config_service import UserConfigService
            cfg = UserConfigService().get_dedup_beta_config(user_id) or {}
        except Exception:
            cfg = {}
        return {
            'enabled': bool(cfg.get('enabled', True)),
            'time_window_hours': max(1, int(cfg.get('time_window_hours', 72) or 72)),
            'auto_merge_threshold': min(0.99, max(0.5, float(cfg.get('auto_merge_threshold', 0.85) or 0.85))),
            'weights': self._normalize_weights(cfg.get('weights') or {}),
        }

    def _get_sender_domain_by_email_id(self, user_id: int, email_id: Optional[int]) -> str:
        if not email_id:
            return ""
        try:
            rows = self.db.execute_query("SELECT sender FROM emails WHERE id = ? AND user_id = ?", (int(email_id), int(user_id)))
            if not rows:
                return ""
            sender = str(rows[0].get("sender") or "")
            m = re.search(r"@([A-Za-z0-9.\-_]+)", sender)
            return (m.group(1).lower().strip() if m else "")
        except Exception:
            return ""

    def _calc_tags_score(self, new_tags: Dict[str, str], old_tags: Dict[str, str]) -> float:
        if not new_tags or not old_tags:
            return 0.0
        score = 0.0
        if (new_tags.get('level2') or '') == (old_tags.get('level2') or ''):
            score += 0.35
        if (new_tags.get('level2_custom') or '') and (new_tags.get('level2_custom') or '') == (old_tags.get('level2_custom') or ''):
            score += 0.15
        if (new_tags.get('level3') or '') and (new_tags.get('level3') or '') == (old_tags.get('level3') or ''):
            score += 0.30
        if (new_tags.get('level4') or '') and (new_tags.get('level4') or '') == (old_tags.get('level4') or ''):
            score += 0.20
        return min(1.0, score)

    def _find_duplicate_candidate(self, event_data: Dict[str, Any], user_id: int, dedup_cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        start_time = self._safe_datetime(event_data.get('start_time'))
        if not start_time:
            return None
        window_hours = int(dedup_cfg.get('time_window_hours', 72))
        start_min = start_time - timedelta(hours=window_hours)
        start_max = start_time + timedelta(hours=window_hours)
        candidates = self.db.execute_query(
            """
            SELECT id, email_id, title, description, start_time, end_time, location, importance_level
            FROM events
            WHERE user_id = ? AND start_time BETWEEN ? AND ?
            ORDER BY start_time DESC
            LIMIT 80
            """,
            (int(user_id), start_min, start_max),
        )
        if not candidates:
            return None

        weights = dedup_cfg.get('weights') or {}
        new_title = self._normalize_title(event_data.get('title'))
        new_location = self._normalize_text(event_data.get('location'))
        new_email_id = int(event_data.get('email_id') or 0)
        new_sender_domain = self._get_sender_domain_by_email_id(user_id, new_email_id)
        new_tags_list = self.tag_service.get_email_tags(user_id, new_email_id) if new_email_id else []
        new_tags = new_tags_list[0] if new_tags_list else {}
        max_time_diff_sec = max(3600, window_hours * 3600)

        best = None
        for c in candidates:
            c_start = self._safe_datetime(c.get('start_time'))
            if not c_start:
                continue
            old_title = self._normalize_title(c.get('title'))
            old_location = self._normalize_text(c.get('location'))
            old_email_id = int(c.get('email_id') or 0)
            old_sender_domain = self._get_sender_domain_by_email_id(user_id, old_email_id)
            old_tags_list = self.tag_service.get_email_tags(user_id, old_email_id) if old_email_id else []
            old_tags = old_tags_list[0] if old_tags_list else {}

            title_score = SequenceMatcher(None, new_title, old_title).ratio() if (new_title and old_title) else 0.0
            diff_sec = abs((start_time - c_start).total_seconds())
            time_score = max(0.0, 1.0 - (diff_sec / max_time_diff_sec))
            tags_score = self._calc_tags_score(new_tags, old_tags)
            sender_score = 1.0 if (new_sender_domain and old_sender_domain and new_sender_domain == old_sender_domain) else 0.0
            location_score = 1.0 if (new_location and old_location and new_location == old_location) else 0.0

            total = (
                weights.get('title', 0.0) * title_score +
                weights.get('time', 0.0) * time_score +
                weights.get('tags', 0.0) * tags_score +
                weights.get('sender', 0.0) * sender_score +
                weights.get('location', 0.0) * location_score
            )
            if best is None or total > best['score']:
                best = {
                    'event_id': int(c.get('id')),
                    'score': float(total),
                    'title_score': title_score,
                    'time_score': time_score,
                    'tags_score': tags_score,
                    'sender_score': sender_score,
                    'location_score': location_score,
                    'row': c,
                }
        return best

    def _entry_key(self, entry: Dict[str, Any]) -> tuple:
        st = self._safe_datetime(entry.get('start_time'))
        et = self._safe_datetime(entry.get('end_time'))
        return (
            self._normalize_title(entry.get('title')),
            st.isoformat() if st else '',
            et.isoformat() if et else '',
            self._normalize_text(entry.get('location')),
        )

    def _entry_time_text(self, entry: Dict[str, Any]) -> str:
        st = self._safe_datetime(entry.get('start_time'))
        et = self._safe_datetime(entry.get('end_time'))
        if st and et:
            return f"{st.strftime('%Y-%m-%d %H:%M')} ~ {et.strftime('%Y-%m-%d %H:%M')}"
        if st:
            return st.strftime('%Y-%m-%d %H:%M')
        return '未知时间'

    def _extract_merged_entries(self, desc: str) -> tuple[str, List[Dict[str, Any]]]:
        text = str(desc or '').strip()
        marker = '[合并日程明细]'
        json_start = '[MERGED_EVENTS_JSON]'
        json_end = '[/MERGED_EVENTS_JSON]'
        entries: List[Dict[str, Any]] = []

        # 提取机器可读块
        js = text.find(json_start)
        je = text.find(json_end)
        if js >= 0 and je > js:
            payload = text[js + len(json_start):je].strip()
            try:
                obj = json.loads(payload)
                if isinstance(obj, list):
                    for item in obj:
                        if isinstance(item, dict):
                            entries.append({
                                'title': str(item.get('title') or '').strip(),
                                'start_time': item.get('start_time'),
                                'end_time': item.get('end_time'),
                                'location': str(item.get('location') or '').strip(),
                            })
            except Exception:
                pass
            text = (text[:js] + text[je + len(json_end):]).strip()

        # 移除旧的人类可读合并块（后续会重建）
        mk = text.find(marker)
        if mk >= 0:
            text = text[:mk].strip()

        return text, entries

    def _build_merged_description(self, base_desc: str, entries: List[Dict[str, Any]], source_email_id: int, score: float) -> str:
        marker = '[合并日程明细]'
        human_lines = [marker]
        base_entry = entries[0] if entries else {}
        base_time = self._entry_time_text(base_entry) if base_entry else ''
        base_loc = self._normalize_text(base_entry.get('location')) if base_entry else ''
        for idx, e in enumerate(entries, start=1):
            title = str(e.get('title') or '未命名事件').strip()
            loc = str(e.get('location') or '').strip() or '地点未填'
            cur_time = self._entry_time_text(e)
            cur_loc_norm = self._normalize_text(e.get('location'))
            notes = []
            if idx > 1 and cur_time != base_time:
                notes.append('时间不同')
            if idx > 1 and cur_loc_norm != base_loc:
                notes.append('地点不同')
            note_text = f"（{'，'.join(notes)}）" if notes else ''
            human_lines.append(f"{idx}. {title}｜{cur_time}｜{loc}{note_text}")
        human_lines.append(
            f"合并来源邮件: {source_email_id}，合并时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}，匹配分数: {score:.2f}"
        )
        json_block = f"[MERGED_EVENTS_JSON]{json.dumps(entries, ensure_ascii=False)}[/MERGED_EVENTS_JSON]"
        body = "\n".join(human_lines + [json_block]).strip()
        if base_desc:
            return f"{base_desc}\n\n{body}".strip()
        return body

    def _merge_into_existing_event(self, existing: Dict[str, Any], event_data: Dict[str, Any], user_id: int, score: float) -> int:
        prefix = "[合并事件]"
        old_title = str(existing.get("title") or "").strip()
        if old_title.startswith(prefix):
            old_title = old_title[len(prefix):].strip()
        old_desc = str(existing.get("description") or "").strip()
        base_desc, history_entries = self._extract_merged_entries(old_desc)

        existing_entry = {
            'title': old_title or str(existing.get("title") or '').strip(),
            'start_time': existing.get('start_time'),
            'end_time': existing.get('end_time'),
            'location': str(existing.get('location') or '').strip(),
        }
        incoming_entry = {
            'title': str(event_data.get('title') or '').strip(),
            'start_time': event_data.get('start_time'),
            'end_time': event_data.get('end_time'),
            'location': str(event_data.get('location') or '').strip(),
        }

        merged_entries: List[Dict[str, Any]] = []
        seen = set()
        for e in history_entries + [existing_entry, incoming_entry]:
            k = self._entry_key(e)
            if k in seen:
                continue
            seen.add(k)
            merged_entries.append({
                'title': str(e.get('title') or '').strip(),
                'start_time': self._safe_datetime(e.get('start_time')).isoformat() if self._safe_datetime(e.get('start_time')) else None,
                'end_time': self._safe_datetime(e.get('end_time')).isoformat() if self._safe_datetime(e.get('end_time')) else None,
                'location': str(e.get('location') or '').strip(),
            })

        source_email_id = int(event_data.get("email_id") or 0)

        # 若所有关键字段一致（或去重后只有1条），则维持单事件，不展示分行明细
        if len(merged_entries) <= 1:
            final_title = existing_entry.get('title') or old_title
            new_title = final_title if str(final_title).startswith(prefix) else f"{prefix} {final_title}".strip()
            new_desc = base_desc
            final_start = self._safe_datetime(existing_entry.get('start_time')) or self._safe_datetime(event_data.get('start_time'))
            final_end = self._safe_datetime(existing_entry.get('end_time')) or self._safe_datetime(event_data.get('end_time'))
            final_location = str(existing_entry.get('location') or '').strip() or str(event_data.get('location') or '').strip()
        else:
            # 任一字段不同：分行并列显示所有日程；时间取并集；地点列举所有
            new_title = f"{prefix} 多日程合并（{len(merged_entries)}）"
            new_desc = self._build_merged_description(base_desc, merged_entries, source_email_id, score)

            starts = [self._safe_datetime(e.get('start_time')) for e in merged_entries if self._safe_datetime(e.get('start_time'))]
            ends = [self._safe_datetime(e.get('end_time')) for e in merged_entries if self._safe_datetime(e.get('end_time'))]
            final_start = min(starts) if starts else self._safe_datetime(existing.get('start_time'))
            # 结束时间取并集上界；若都无结束时间则为空
            union_ends = ends if ends else starts
            final_end = max(union_ends) if union_ends else self._safe_datetime(existing.get('end_time'))
            locs = []
            for e in merged_entries:
                v = str(e.get('location') or '').strip()
                if v and v not in locs:
                    locs.append(v)
            final_location = "；".join(locs)

        level_rank = {'unimportant': 0, 'normal': 1, 'important': 2, 'subscribed': 3}
        old_level = str(existing.get('importance_level') or 'normal')
        new_level = str(event_data.get('importance_level') or 'normal')
        final_level = old_level if level_rank.get(old_level, 1) >= level_rank.get(new_level, 1) else new_level
        final_color = self._get_color_by_importance(final_level)

        self.db.execute_update(
            """
            UPDATE events
            SET title = ?, description = ?, start_time = ?, end_time = ?, location = ?,
                importance_level = ?, color = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """,
            (
                new_title,
                new_desc,
                final_start,
                final_end,
                final_location,
                final_level,
                final_color,
                datetime.now(),
                int(existing.get('id')),
                int(user_id),
            )
        )
        # 合并后按并集起始时间重建提醒，避免提醒时间落后于最终事件窗口
        try:
            self.db.execute_update(
                "DELETE FROM reminders WHERE event_id = ? AND user_id = ?",
                (int(existing.get('id')), int(user_id))
            )
            if final_start:
                reminder_times = self._calculate_reminder_times(final_start, final_level)
                self._create_reminders(int(existing.get('id')), reminder_times, int(user_id))
        except Exception as _e:
            logger.warning(f"合并后重建提醒失败: {_e}")
        return int(existing.get('id'))

    # ===== 通知/提醒投递（按渠道）=====
    def _get_notification_config(self, user_id: int) -> Dict[str, Any]:
        try:
            from .config_service import UserConfigService
            svc = UserConfigService()
            return svc.get_notification_config(user_id) or {}
        except Exception:
            return {}

    def _get_reminder_user_config(self, user_id: int) -> Dict[str, Any]:
        """读取“提醒设置”里的全局约束（时间段/周末），用于发送渠道过滤。"""
        try:
            from .config_service import UserConfigService
            svc = UserConfigService()
            return svc.get_reminder_config(user_id) or {}
        except Exception:
            return {}

    def _is_within_reminder_window(self, now: datetime, reminder_cfg: Dict[str, Any]) -> bool:
        """是否允许在当前时间发送提醒（时间段 + 周末开关）。"""
        try:
            weekend_ok = bool(reminder_cfg.get('weekend_reminder', True))
            if not weekend_ok and now.weekday() >= 5:
                return False
            start_s = str(reminder_cfg.get('start_time', '08:00') or '08:00')
            end_s = str(reminder_cfg.get('end_time', '22:00') or '22:00')
            sh, sm = [int(x) for x in start_s.split(':', 1)]
            eh, em = [int(x) for x in end_s.split(':', 1)]
            start_m = sh * 60 + sm
            end_m = eh * 60 + em
            cur_m = now.hour * 60 + now.minute
            # 支持跨天时间段：例如 22:00-08:00
            if start_m <= end_m:
                return start_m <= cur_m <= end_m
            return cur_m >= start_m or cur_m <= end_m
        except Exception:
            return True

    def _ensure_delivery(self, user_id: int, reminder_id: int, channel: str):
        """幂等创建投递明细。"""
        try:
            q = """
            INSERT OR IGNORE INTO reminder_deliveries (user_id, reminder_id, channel, is_sent)
            VALUES (?, ?, ?, FALSE)
            """
            self.db.execute_insert(q, (user_id, reminder_id, channel))
        except Exception as e:
            logger.warning(f"创建投递明细失败(reminder_id={reminder_id}, channel={channel}): {e}")

    def _mark_delivery_sent(self, user_id: int, delivery_id: int):
        try:
            q = """
            UPDATE reminder_deliveries
            SET is_sent = TRUE, sent_at = ?, last_error = NULL, updated_at = ?
            WHERE id = ? AND user_id = ?
            """
            now = datetime.now()
            self.db.execute_update(q, (now, now, delivery_id, user_id))
        except Exception as e:
            logger.warning(f"标记投递已发送失败(delivery_id={delivery_id}): {e}")

    def _set_delivery_error(self, user_id: int, delivery_id: int, err: str):
        try:
            q = """
            UPDATE reminder_deliveries
            SET last_error = ?, updated_at = ?
            WHERE id = ? AND user_id = ?
            """
            self.db.execute_update(q, (str(err)[:2000], datetime.now(), delivery_id, user_id))
        except Exception:
            pass

    def _finalize_reminder_if_done(self, user_id: int, reminder_id: int, enabled_channels: List[str]) -> bool:
        """若该提醒的所有启用渠道都已发送，则标记 reminders.is_sent=true。"""
        try:
            if not enabled_channels:
                return False
            q = f"""
            SELECT COUNT(*) as cnt
            FROM reminder_deliveries
            WHERE user_id = ? AND reminder_id = ? AND channel IN ({','.join(['?'] * len(enabled_channels))}) AND is_sent = FALSE
            """
            params = tuple([user_id, reminder_id] + enabled_channels)
            res = self.db.execute_query(q, params)
            pending = int(res[0]['cnt']) if res else 0
            if pending == 0:
                self.mark_reminder_sent(reminder_id)
                return True
            return False
        except Exception:
            return False

    def _send_email(self, reminder: Dict[str, Any], notify_cfg: Dict[str, Any]) -> str:
        """发送邮件提醒；成功返回空串，失败返回错误信息。"""
        smtp_host = (notify_cfg.get('smtp_host') or '').strip()
        smtp_port = int(notify_cfg.get('smtp_port') or 587)
        smtp_user = (notify_cfg.get('smtp_user') or '').strip()
        smtp_password = (notify_cfg.get('smtp_password') or '').strip()
        smtp_use_tls = bool(notify_cfg.get('smtp_use_tls', True))
        smtp_use_ssl = bool(notify_cfg.get('smtp_use_ssl', False))
        mail_to = (notify_cfg.get('notification_email') or '').strip()
        mail_from = (notify_cfg.get('smtp_from') or smtp_user or mail_to).strip()
        if not (smtp_host and mail_to and mail_from):
            return "邮件通知未配置完整（smtp_host / notification_email / smtp_from）"
        if not smtp_password and smtp_user:
            return "邮件通知未配置 smtp_password"

        title = str(reminder.get('title') or '事件提醒')
        start_time = reminder.get('start_time')
        reminder_time = reminder.get('reminder_time')
        body_lines = [
            f"事件：{title}",
            f"开始时间：{start_time}",
            f"提醒时间：{reminder_time}",
        ]
        if reminder.get('location'):
            body_lines.append(f"地点：{reminder.get('location')}")
        if reminder.get('description'):
            body_lines.append("")
            body_lines.append("描述：")
            body_lines.append(str(reminder.get('description')))
        body_lines.append("")
        body_lines.append("—— 邮件智能日程管理系统")

        msg = MIMEMultipart()
        msg['From'] = mail_from
        msg['To'] = mail_to
        msg['Subject'] = f"事件提醒：{title}"
        msg.attach(MIMEText("\n".join(body_lines), 'plain', 'utf-8'))

        try:
            if smtp_use_ssl:
                context = ssl.create_default_context()
                with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=15) as server:
                    if smtp_user:
                        server.login(smtp_user, smtp_password)
                    server.sendmail(mail_from, [mail_to], msg.as_string())
            else:
                with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
                    server.ehlo()
                    if smtp_use_tls:
                        server.starttls(context=ssl.create_default_context())
                        server.ehlo()
                    if smtp_user:
                        server.login(smtp_user, smtp_password)
                    server.sendmail(mail_from, [mail_to], msg.as_string())
            return ""
        except Exception as e:
            return f"邮件发送失败: {e}"

    def _send_serverchan(self, reminder: Dict[str, Any], notify_cfg: Dict[str, Any]) -> str:
        """Server酱微信提醒；成功返回空串，失败返回错误信息。"""
        # 兼容字段：serverchan_sendkey / sendkey
        sendkey = (notify_cfg.get('serverchan_sendkey') or notify_cfg.get('sendkey') or '').strip()
        if not sendkey:
            return "Server酱未配置 sendkey"
        try:
            import requests
            import re

            # 清理 sendkey：用户可能误粘贴整段URL/带空格换行，尽量提取 SCTxxxx
            m = re.search(r"(SCT[0-9A-Za-z]+)", sendkey)
            if m:
                sendkey = m.group(1)

            title = str(reminder.get('title') or '事件提醒')
            start_time = reminder.get('start_time')
            reminder_time = reminder.get('reminder_time')
            prefix = str(notify_cfg.get('serverchan_title_prefix') or '事件提醒').strip()
            text = f"{prefix}：{title}"
            # Server酱 title 最大 32 字符，超出会触发 data format error
            if len(text) > 32:
                text = text[:32]

            desp = f"开始时间：{start_time}\n提醒时间：{reminder_time}"
            if reminder.get('location'):
                desp += f"\n地点：{reminder.get('location')}"
            if reminder.get('description'):
                desp += f"\n\n{reminder.get('description')}"
            url = f"https://sctapi.ftqq.com/{sendkey}.send"
            resp = requests.post(
                url,
                data={"title": text, "desp": str(desp)},
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                timeout=10,
            )
            if resp.status_code != 200:
                return f"Server酱HTTP错误: {resp.status_code}, body={resp.text[:200]}"
            try:
                j = resp.json()
                if int(j.get('code', -1)) != 0:
                    # 尽量把 message/rid 原样透出，便于定位
                    return f"Server酱返回错误: {j}"
            except Exception:
                # 非JSON也视为失败
                return f"Server酱返回非JSON: {resp.text[:200]}"
            return ""
        except Exception as e:
            return f"Server酱发送失败: {e}"

    def _send_serverchan_meta(self, reminder: Dict[str, Any], notify_cfg: Dict[str, Any]) -> Dict[str, Any]:
        """Server酱发送（带返回信息）。

        Returns:
            { ok: bool, error?: str, pushid?: str, readkey?: str, raw?: Any }
        """
        # 兼容字段：serverchan_sendkey / sendkey
        sendkey = (notify_cfg.get('serverchan_sendkey') or notify_cfg.get('sendkey') or '').strip()
        if not sendkey:
            return {'ok': False, 'error': 'Server酱未配置 sendkey'}
        try:
            import requests
            import re

            m = re.search(r"(SCT[0-9A-Za-z]+)", sendkey)
            if m:
                sendkey = m.group(1)

            title = str(reminder.get('title') or '事件提醒')
            start_time = reminder.get('start_time')
            reminder_time = reminder.get('reminder_time')
            prefix = str(notify_cfg.get('serverchan_title_prefix') or '事件提醒').strip()
            text = f"{prefix}：{title}"
            if len(text) > 32:
                text = text[:32]

            desp = f"开始时间：{start_time}\n提醒时间：{reminder_time}"
            if reminder.get('location'):
                desp += f"\n地点：{reminder.get('location')}"
            if reminder.get('description'):
                desp += f"\n\n{reminder.get('description')}"
            url = f"https://sctapi.ftqq.com/{sendkey}.send"
            resp = requests.post(
                url,
                data={"title": text, "desp": str(desp)},
                headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"},
                timeout=10,
            )
            if resp.status_code != 200:
                return {'ok': False, 'error': f"Server酱HTTP错误: {resp.status_code}", 'raw': resp.text[:500]}
            try:
                j = resp.json()
            except Exception:
                return {'ok': False, 'error': "Server酱返回非JSON", 'raw': resp.text[:500]}
            if int(j.get('code', -1)) != 0:
                # 保留原始结构，里面通常会含 message/rid
                return {'ok': False, 'error': "Server酱返回错误", 'raw': j}

            data = j.get('data') or {}
            pushid = str(data.get('pushid') or j.get('pushid') or '')
            readkey = str(data.get('readkey') or j.get('readkey') or '')
            out = {'ok': True, 'raw': j}
            if pushid:
                out['pushid'] = pushid
            if readkey:
                out['readkey'] = readkey
            return out
        except Exception as e:
            return {'ok': False, 'error': f"Server酱发送失败: {e}"}
    
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

            # 标签订阅优先：命中订阅标签时，事件升级为“订阅”级（绿色）
            try:
                email_id = event_data.get('email_id')
                if email_id:
                    email_tags = self.tag_service.get_email_tags(user_id, int(email_id))
                    if email_tags:
                        hit, hit_label = self.tag_service.is_subscribed(user_id, email_tags[0])
                        if hit:
                            event_data['importance_level'] = 'subscribed'
                            event_data['color'] = '#28a745'
                            event_data['subscription_tag'] = hit_label
            except Exception as _e:
                logger.warning(f"应用订阅标签升级失败: {_e}")

            # 智能去重（Beta）：命中阈值后不新增事件，合并到既有事件并打上 [合并事件] 标记
            try:
                dedup_cfg = self._get_dedup_beta_config(user_id)
                if dedup_cfg.get('enabled', True):
                    candidate = self._find_duplicate_candidate(event_data, user_id, dedup_cfg)
                    if candidate and candidate.get('score', 0.0) >= float(dedup_cfg.get('auto_merge_threshold', 0.85)):
                        merged_id = self._merge_into_existing_event(candidate.get('row') or {}, event_data, user_id, float(candidate.get('score', 0.0)))
                        logger.info(
                            "事件命中去重并合并: event_id=%s score=%.3f (title=%.3f time=%.3f tags=%.3f sender=%.3f location=%.3f)",
                            merged_id,
                            candidate.get('score', 0.0),
                            candidate.get('title_score', 0.0),
                            candidate.get('time_score', 0.0),
                            candidate.get('tags_score', 0.0),
                            candidate.get('sender_score', 0.0),
                            candidate.get('location_score', 0.0),
                        )
                        return merged_id
            except Exception as _e:
                logger.warning(f"事件去重失败，按新事件写入: {_e}")
            
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
        if importance_level == 'subscribed':
            return '#28a745'
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
        
        if importance_level in ('important', 'subscribed'):
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

            # 批量补充邮件标签，便于日程视图展示
            try:
                email_ids = [int(e['email_id']) for e in events if e.get('email_id')]
                tag_map = self.tag_service.get_email_tags_bulk(user_id, email_ids)
            except Exception:
                tag_map = {}
            
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
                event['email_tags'] = tag_map.get(int(event.get('email_id') or 0), [])
                # 运行时应用“标签订阅升级”，确保历史事件在订阅后也立即生效
                try:
                    if event['email_tags']:
                        hit, hit_label = self.tag_service.is_subscribed(user_id, event['email_tags'][0])
                        if hit:
                            event['importance_level'] = 'subscribed'
                            event['color'] = '#28a745'
                            event['subscription_tag'] = hit_label
                except Exception as _e:
                    logger.warning(f"运行时应用订阅升级失败: {_e}")
            
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
    
    def update_event(self, event_id: int, user_id: int, update_data: Dict[str, Any]) -> bool:
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
            params.append(user_id)
            
            query = f"""
            UPDATE events 
            SET {', '.join(set_clauses)}
            WHERE id = ? AND user_id = ?
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
    
    def delete_event(self, event_id: int, user_id: int) -> bool:
        """删除事件
        
        Args:
            event_id: 事件ID
        
        Returns:
            是否删除成功
        """
        try:
            # 先删除相关提醒（按 user_id 隔离）
            self.db.execute_update("DELETE FROM reminders WHERE event_id = ? AND user_id = ?", (event_id, user_id))
            
            # 删除事件（按 user_id 隔离）
            rows_affected = self.db.execute_update("DELETE FROM events WHERE id = ? AND user_id = ?", (event_id, user_id))
            
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
    
    def get_event_statistics(self, user_id: int = None) -> Dict[str, Any]:
        """获取事件统计信息
        
        Returns:
            统计信息字典
        """
        try:
            stats = {}
            
            # 总事件数（可选按 user_id 过滤）
            if user_id is not None:
                total_query = "SELECT COUNT(*) as count FROM events WHERE user_id = ?"
                total_result = self.db.execute_query(total_query, (user_id,))
            else:
                total_query = "SELECT COUNT(*) as count FROM events"
                total_result = self.db.execute_query(total_query)
            stats['total_events'] = total_result[0]['count'] if total_result else 0
            
            # 按重要性分组统计
            importance_query = """
            SELECT importance_level, COUNT(*) as count 
            FROM events 
            GROUP BY importance_level
            """
            if user_id is not None:
                importance_query = """
                SELECT importance_level, COUNT(*) as count 
                FROM events 
                WHERE user_id = ?
                GROUP BY importance_level
                """
                importance_results = self.db.execute_query(importance_query, (user_id,))
            else:
                importance_results = self.db.execute_query(importance_query)
            stats['by_importance'] = {row['importance_level']: row['count'] for row in importance_results}
            
            # 即将到来的事件数（7天内）
            upcoming_query = """
            SELECT COUNT(*) as count 
            FROM events 
            WHERE start_time >= datetime('now') 
            AND start_time <= datetime('now', '+7 days')
            """
            if user_id is not None:
                upcoming_query = """
                SELECT COUNT(*) as count 
                FROM events 
                WHERE user_id = ?
                  AND start_time >= datetime('now') 
                  AND start_time <= datetime('now', '+7 days')
                """
                upcoming_result = self.db.execute_query(upcoming_query, (user_id,))
            else:
                upcoming_result = self.db.execute_query(upcoming_query)
            stats['upcoming_7_days'] = upcoming_result[0]['count'] if upcoming_result else 0
            
            # 待发送提醒数
            pending_reminders_query = """
            SELECT COUNT(*) as count 
            FROM reminders 
            WHERE is_sent = FALSE AND reminder_time <= datetime('now')
            """
            if user_id is not None:
                pending_reminders_query = """
                SELECT COUNT(*) as count 
                FROM reminders 
                WHERE user_id = ? AND is_sent = FALSE AND reminder_time <= datetime('now')
                """
                pending_result = self.db.execute_query(pending_reminders_query, (user_id,))
            else:
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
            notify_cfg = self._get_notification_config(user_id)
            reminder_cfg = self._get_reminder_user_config(user_id)

            enabled_channels: List[str] = []
            if bool(notify_cfg.get('enable_email_notifications', False)):
                enabled_channels.append('email')
            if bool(notify_cfg.get('enable_serverchan_notifications', False)):
                enabled_channels.append('serverchan')
            if bool(notify_cfg.get('enable_browser_notifications', False)):
                enabled_channels.append('browser')
            has_fcm = bool(notify_cfg.get('enable_fcm_notifications', False)) and bool(notify_cfg.get('mobile_fcm_token'))
            has_jpush = bool(
                notify_cfg.get('enable_jpush_notifications', False) or
                notify_cfg.get('enable_getui_notifications', False)
            ) and bool(
                notify_cfg.get('mobile_jpush_registration_id') or
                notify_cfg.get('mobile_getui_client_id')
            )
            if (has_fcm or has_jpush) and bool(notify_cfg.get('fcm_push_reminder', True)):
                enabled_channels.append('mobile_push')

            if not enabled_channels:
                return 0

            now = datetime.now()
            if not self._is_within_reminder_window(now, reminder_cfg):
                # 不在允许时间段内：不发送，不标记（等待下次进入时间段再处理）
                return 0

            pending_reminders = self.get_pending_reminders(user_id)
            processed_count = 0

            for reminder in pending_reminders:
                reminder_id = reminder.get('id')
                if not reminder_id:
                    continue

                # 为启用渠道创建投递明细（幂等）
                for ch in enabled_channels:
                    self._ensure_delivery(user_id, int(reminder_id), ch)

                # 取出未发送的投递
                q = """
                SELECT id, channel
                FROM reminder_deliveries
                WHERE user_id = ? AND reminder_id = ? AND is_sent = FALSE
                """
                deliveries = self.db.execute_query(q, (user_id, int(reminder_id)))

                for d in deliveries:
                    ch = d.get('channel')
                    delivery_id = int(d.get('id'))
                    if ch not in enabled_channels:
                        continue
                    # browser 渠道由前端“拉取 + Notification API + 回执”完成，这里不发送
                    if ch == 'browser':
                        continue

                    err = ""
                    if ch == 'email':
                        err = self._send_email(reminder, notify_cfg)
                    elif ch == 'serverchan':
                        err = self._send_serverchan(reminder, notify_cfg)
                    elif ch == 'mobile_push':
                        title = f"日程提醒：{str(reminder.get('title') or '未命名事件')}"
                        body = f"开始时间：{str(reminder.get('start_time') or '-')}"
                        if reminder.get('location'):
                            body += f"｜地点：{str(reminder.get('location') or '')}"
                        err = self.send_mobile_push(
                            user_id=user_id,
                            title=title,
                            body=body,
                            push_type='reminder',
                            data={
                                'event_id': reminder.get('event_id', ''),
                                'reminder_id': reminder.get('id', ''),
                            },
                        )

                    if err:
                        self._set_delivery_error(user_id, delivery_id, err)
                        logger.warning(f"提醒投递失败(reminder_id={reminder_id}, channel={ch}): {err}")
                    else:
                        self._mark_delivery_sent(user_id, delivery_id)
                        logger.info(f"提醒投递成功(reminder_id={reminder_id}, channel={ch})")

                # 如果所有启用渠道都已完成，则标记 reminders.is_sent
                if self._finalize_reminder_if_done(user_id, int(reminder_id), enabled_channels):
                    processed_count += 1

            if processed_count > 0:
                logger.info(f"成功完成 {processed_count} 个提醒（所有启用渠道均已发送）")

            return processed_count
            
        except Exception as e:
            logger.error(f"处理提醒失败: {e}")
            return 0

    def get_pending_browser_deliveries(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        """给前端（浏览器通知）提供待投递列表：只返回 browser 渠道未发送的项。"""
        try:
            notify_cfg = self._get_notification_config(user_id)
            if not bool(notify_cfg.get('enable_browser_notifications', False)):
                return []

            # 确保对到期提醒创建 browser 明细（防止 scheduler 未运行时浏览器收不到）
            due_reminders = self.get_pending_reminders(user_id)
            for r in due_reminders:
                rid = r.get('id')
                if rid:
                    self._ensure_delivery(user_id, int(rid), 'browser')

            q = """
            SELECT
                rd.id as delivery_id,
                r.id as reminder_id,
                r.reminder_time,
                e.id as event_id,
                e.title,
                e.start_time,
                e.location,
                e.importance_level
            FROM reminder_deliveries rd
            JOIN reminders r ON rd.reminder_id = r.id
            JOIN events e ON r.event_id = e.id
            WHERE rd.user_id = ?
              AND rd.channel = 'browser'
              AND rd.is_sent = FALSE
              AND r.is_sent = FALSE
              AND r.reminder_time <= ?
            ORDER BY r.reminder_time ASC
            LIMIT ?
            """
            return self.db.execute_query(q, (user_id, datetime.now(), int(limit)))
        except Exception as e:
            logger.error(f"获取浏览器待投递提醒失败: {e}")
            return []

    def ack_browser_delivery(self, user_id: int, delivery_id: int) -> bool:
        """浏览器通知回执：标记 delivery 已发送，并在需要时 finalize reminder。"""
        try:
            q = """
            SELECT rd.id, rd.reminder_id
            FROM reminder_deliveries rd
            WHERE rd.id = ? AND rd.user_id = ? AND rd.channel = 'browser'
            """
            rows = self.db.execute_query(q, (int(delivery_id), int(user_id)))
            if not rows:
                return False
            reminder_id = int(rows[0]['reminder_id'])
            self._mark_delivery_sent(user_id, int(delivery_id))

            # 按当前启用渠道判定是否应 finalize
            notify_cfg = self._get_notification_config(user_id)
            enabled_channels: List[str] = []
            if bool(notify_cfg.get('enable_email_notifications', False)):
                enabled_channels.append('email')
            if bool(notify_cfg.get('enable_serverchan_notifications', False)):
                enabled_channels.append('serverchan')
            if bool(notify_cfg.get('enable_browser_notifications', False)):
                enabled_channels.append('browser')
            has_fcm = bool(notify_cfg.get('enable_fcm_notifications', False)) and bool(notify_cfg.get('mobile_fcm_token'))
            has_jpush = bool(
                notify_cfg.get('enable_jpush_notifications', False) or
                notify_cfg.get('enable_getui_notifications', False)
            ) and bool(
                notify_cfg.get('mobile_jpush_registration_id') or
                notify_cfg.get('mobile_getui_client_id')
            )
            if (has_fcm or has_jpush) and bool(notify_cfg.get('fcm_push_reminder', True)):
                enabled_channels.append('mobile_push')
            self._finalize_reminder_if_done(user_id, reminder_id, enabled_channels)
            return True
        except Exception as e:
            logger.error(f"浏览器回执失败(delivery_id={delivery_id}): {e}")
            return False

    def send_test_notification(self, user_id: int, channel: str, config_override: Dict[str, Any]) -> str:
        """发送测试通知（不写入 reminders/reminder_deliveries）

        Args:
            user_id: 用户ID（目前用于保持调用签名一致，便于未来扩展审计）
            channel: email | serverchan
            config_override: 前端临时填写的通知配置（不落库）

        Returns:
            空串表示成功，否则返回错误信息
        """
        channel = (channel or '').strip().lower()
        if channel not in ('email', 'serverchan', 'fcm', 'jpush', 'auto'):
            return "不支持的测试渠道"

        fake = {
            'title': '测试通知（邮件智能日程管理系统）',
            'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'reminder_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'location': '',
            'description': '这是一条测试通知，用于验证通知渠道配置是否正确。',
        }

        if channel == 'email':
            return self._send_email(fake, config_override or {})
        if channel == 'serverchan':
            # 保持旧签名：成功返回空串
            return self._send_serverchan(fake, config_override or {})
        if channel == 'fcm':
            title = str((config_override or {}).get('title') or '测试通知（邮件智能日程管理系统）')
            body = str((config_override or {}).get('body') or '这是一条测试通知，用于验证 FCM 推送配置是否正确。')
            return self.send_fcm_push(
                user_id=user_id,
                title=title,
                body=body,
                push_type='system',
                data={'channel': 'fcm', 'is_test': '1'},
                force=True,
            )
        if channel == 'jpush':
            title = str((config_override or {}).get('title') or '测试通知（邮件智能日程管理系统）')
            body = str((config_override or {}).get('body') or '这是一条测试通知，用于验证 JPush 推送配置是否正确。')
            return self.send_jpush_push(
                user_id=user_id,
                title=title,
                body=body,
                push_type='system',
                data={'channel': 'jpush', 'is_test': '1'},
                force=True,
            )
        if channel == 'auto':
            title = str((config_override or {}).get('title') or '测试通知（邮件智能日程管理系统）')
            body = str((config_override or {}).get('body') or '这是一条测试通知，用于验证移动推送回退链路是否正确。')
            return self.send_mobile_push(
                user_id=user_id,
                title=title,
                body=body,
                push_type='system',
                data={'channel': 'auto', 'is_test': '1'},
                force=True,
            )
        return "不支持的测试渠道"

    def send_test_notification_detail(self, user_id: int, channel: str, config_override: Dict[str, Any]) -> Dict[str, Any]:
        """发送测试通知（返回详细信息，用于排查 Server酱“入队但未送达”等问题）"""
        channel = (channel or '').strip().lower()
        if channel not in ('email', 'serverchan', 'fcm', 'jpush', 'auto'):
            return {'ok': False, 'error': '不支持的测试渠道'}
        fake = {
            'title': '测试通知（邮件智能日程管理系统）',
            'start_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'reminder_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'location': '',
            'description': '这是一条测试通知，用于验证通知渠道配置是否正确。',
        }
        if channel == 'email':
            err = self._send_email(fake, config_override or {})
            if err:
                return {'ok': False, 'error': err}
            return {'ok': True}
        if channel == 'serverchan':
            meta = self._send_serverchan_meta(fake, config_override or {})
            return meta
        if channel == 'fcm':
            title = str((config_override or {}).get('title') or fake['title'])
            body = str((config_override or {}).get('body') or fake['description'])
            err = self.send_fcm_push(
                user_id=user_id,
                title=title,
                body=body,
                push_type='system',
                data={'channel': 'fcm', 'is_test': '1'},
                force=True,
            )
            if err:
                return {'ok': False, 'error': err}
            return {'ok': True}
        if channel == 'jpush':
            title = str((config_override or {}).get('title') or fake['title'])
            body = str((config_override or {}).get('body') or fake['description'])
            err = self.send_jpush_push(
                user_id=user_id,
                title=title,
                body=body,
                push_type='system',
                data={'channel': 'jpush', 'is_test': '1'},
                force=True,
            )
            if err:
                return {'ok': False, 'error': err}
            return {'ok': True}
        if channel == 'auto':
            title = str((config_override or {}).get('title') or fake['title'])
            body = str((config_override or {}).get('body') or fake['description'])
            err = self.send_mobile_push(
                user_id=user_id,
                title=title,
                body=body,
                push_type='system',
                data={'channel': 'auto', 'is_test': '1'},
                force=True,
            )
            if err:
                return {'ok': False, 'error': err}
            return {'ok': True}
        return {'ok': False, 'error': '不支持的测试渠道'}

    def _is_push_type_enabled(self, notify_cfg: Dict[str, Any], push_type: str) -> bool:
        push_type = str(push_type or 'system').strip().lower()
        mapping = {
            'reminder': 'fcm_push_reminder',
            'task': 'fcm_push_task',
            'system': 'fcm_push_system',
            'email_new': 'fcm_push_email_new',
            'email_analysis': 'fcm_push_email_analysis',
            'event': 'fcm_push_event',
            'digest': 'fcm_push_digest',
        }
        key = mapping.get(push_type, 'fcm_push_system')
        return bool(notify_cfg.get(key, True))

    def _is_within_push_window(self, now: datetime, notify_cfg: Dict[str, Any]) -> bool:
        try:
            if not bool(notify_cfg.get('fcm_push_on_weekend', True)) and now.weekday() >= 5:
                return False
            if not bool(notify_cfg.get('fcm_push_quiet_hours_enabled', False)):
                return True
            start_s = str(notify_cfg.get('fcm_push_start_time', '08:00') or '08:00')
            end_s = str(notify_cfg.get('fcm_push_end_time', '22:00') or '22:00')
            sh, sm = [int(x) for x in start_s.split(':', 1)]
            eh, em = [int(x) for x in end_s.split(':', 1)]
            start_m = sh * 60 + sm
            end_m = eh * 60 + em
            cur_m = now.hour * 60 + now.minute
            if start_m <= end_m:
                return start_m <= cur_m <= end_m
            return cur_m >= start_m or cur_m <= end_m
        except Exception:
            return True

    def send_fcm_push(
        self,
        user_id: int,
        title: str,
        body: str,
        push_type: str = 'system',
        data: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> str:
        """主动发送 FCM 推送。成功返回空串，失败返回错误信息。"""
        notify_cfg = self._get_notification_config(user_id)
        if not bool(notify_cfg.get('enable_fcm_notifications', False)):
            return 'FCM 推送总开关未启用'
        token = str(notify_cfg.get('mobile_fcm_token') or '').strip()
        if not token:
            return '未找到移动端 FCM Token，请在手机端通知设置里刷新 Token'
        if not force:
            if not self._is_push_type_enabled(notify_cfg, push_type):
                return f'FCM 推送类型已关闭: {push_type}'
            if not self._is_within_push_window(datetime.now(), notify_cfg):
                return '当前时间不在 FCM 推送允许时段'

        # 推荐部署：FCM 独立网关容器（仅网关走代理），主服务和AI保持直连。
        gateway_url = str(
            os.environ.get('FCM_GATEWAY_URL')
            or self.config.get('notification.fcm_gateway_url', '')
            or ''
        ).strip()
        if gateway_url:
            try:
                send_url = gateway_url.rstrip('/')
                if not send_url.endswith('/send'):
                    send_url = f"{send_url}/send"
                headers = {'Content-Type': 'application/json'}
                internal_token = str(os.environ.get('FCM_GATEWAY_TOKEN') or '').strip()
                if internal_token:
                    headers['X-Internal-Token'] = internal_token
                resp = requests.post(
                    send_url,
                    headers=headers,
                    json={
                        'token': token,
                        'title': title,
                        'body': body,
                        'data': {
                            'push_type': str(push_type or 'system'),
                            **(data or {}),
                        },
                        'credentials_path': str(notify_cfg.get('fcm_service_account_path') or ''),
                    },
                    timeout=12,
                )
                body_obj = resp.json() if 'application/json' in str(resp.headers.get('content-type', '')) else {}
                if resp.status_code == 200 and body_obj.get('success') is True:
                    return ''
                err = str(body_obj.get('error') or body_obj.get('message') or f'HTTP {resp.status_code}')
                return f'FCM 网关发送失败: {err}'
            except requests.exceptions.Timeout:
                return 'FCM 网关发送超时（12秒）'
            except Exception as e:
                return f'FCM 网关调用异常: {e}'

        svc = FCMService(self.config)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(
                    svc.send_to_token,
                    token=token,
                    title=title,
                    body=body,
                    data={
                        'push_type': str(push_type or 'system'),
                        **(data or {}),
                    },
                    credentials_path=str(notify_cfg.get('fcm_service_account_path') or ''),
                )
                ok, msg = fut.result(timeout=12)
        except FutureTimeoutError:
            return 'FCM 发送超时（12秒）'
        except Exception as e:
            return f'FCM 调用异常: {e}'
        return '' if ok else (msg or 'FCM 推送失败')

    def send_jpush_push(
        self,
        user_id: int,
        title: str,
        body: str,
        push_type: str = 'system',
        data: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> str:
        """主动发送 JPush 推送。成功返回空串，失败返回错误信息。"""
        notify_cfg = self._get_notification_config(user_id)
        if not bool(
            notify_cfg.get('enable_jpush_notifications', False) or
            notify_cfg.get('enable_getui_notifications', False)
        ):
            return 'JPush 推送总开关未启用'
        registration_id = str(
            notify_cfg.get('mobile_jpush_registration_id') or
            notify_cfg.get('mobile_getui_client_id') or
            ''
        ).strip()
        if not registration_id:
            return '未找到移动端 JPush RegistrationID，请在手机端通知设置里刷新'
        if not force:
            if not self._is_push_type_enabled(notify_cfg, push_type):
                return f'JPush 推送类型已关闭: {push_type}'
            if not self._is_within_push_window(datetime.now(), notify_cfg):
                return '当前时间不在 JPush 推送允许时段'

        svc = JPushService(self.config)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(
                    svc.send_to_registration_id,
                    notify_cfg=notify_cfg,
                    registration_id=registration_id,
                    title=title,
                    body=body,
                    data={
                        'push_type': str(push_type or 'system'),
                        **(data or {}),
                    },
                )
                ok, msg = fut.result(timeout=15)
        except FutureTimeoutError:
            return 'JPush 发送超时（15秒）'
        except Exception as e:
            return f'JPush 调用异常: {e}'
        return '' if ok else (msg or 'JPush 推送失败')

    def send_mobile_push(
        self,
        user_id: int,
        title: str,
        body: str,
        push_type: str = 'system',
        data: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> str:
        """按优先级发送移动推送：支持 FCM/JPush 自动回退。"""
        notify_cfg = self._get_notification_config(user_id)
        priority = str(notify_cfg.get('mobile_push_priority') or 'fcm_first').strip().lower()
        if priority == 'getui_first':
            priority = 'jpush_first'
        if priority not in ('fcm_first', 'jpush_first'):
            priority = 'fcm_first'

        providers = ['fcm', 'jpush'] if priority == 'fcm_first' else ['jpush', 'fcm']
        errors: List[str] = []
        for provider in providers:
            if provider == 'fcm':
                err = self.send_fcm_push(
                    user_id=user_id,
                    title=title,
                    body=body,
                    push_type=push_type,
                    data=data,
                    force=force,
                )
            else:
                err = self.send_jpush_push(
                    user_id=user_id,
                    title=title,
                    body=body,
                    push_type=push_type,
                    data=data,
                    force=force,
                )
            if not err:
                return ''
            errors.append(f'{provider}: {err}')
        return '；'.join(errors) if errors else '无可用推送通道'
    
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
            
            # 计算提醒时间（统一使用 SchedulerService 的计算逻辑）
            reminder_times = self._calculate_reminder_times(start_time, importance_level)
            
            # 创建提醒记录（与表结构保持一致，包含 user_id 与 reminder_type）
            for reminder_time in reminder_times:
                reminder_query = """
                INSERT INTO reminders (user_id, event_id, reminder_time, reminder_type)
                VALUES (?, ?, ?, ?)
                """
                self.db.execute_insert(reminder_query, (
                    event_data.get('user_id', 1),
                    event_id,
                    reminder_time,
                    'exact_time'
                ))
            
            logger.info(f"为事件 {event_id} 创建了 {len(reminder_times)} 个提醒")
            
        except Exception as e:
            logger.error(f"创建事件提醒失败: {e}")