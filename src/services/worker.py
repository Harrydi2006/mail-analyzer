# -*- coding: utf-8 -*-
"""
后台多用户轮询Worker：无需HTTP凭证，自动为所有激活用户执行：
1) 流式拉取新邮件 -> 立即保存
2) 并行AI分析（包含未分析/失败的历史邮件兜底）
3) 处理提醒发送
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import time
from typing import List, Dict

from ..core.config import Config
from ..core.logger import get_logger, setup_logger
from ..models.database import DatabaseManager
from .config_service import UserConfigService
from .email_service import EmailService
from .ai_service import AIService
from .scheduler_service import SchedulerService
from .notion_service import NotionService


logger = get_logger(__name__)


def _analyze_batch_for_user(config: Config, emails_to_analyze: List[Dict], user_id: int, max_workers: int = 3, timeout_seconds: int = 30):
    if not emails_to_analyze:
        return 0, 0
    analyzed_count = 0
    failed_count = 0
    ai_service = AIService(config)
    scheduler_service = SchedulerService(config)
    notion_service = NotionService(config, user_id)
    db = DatabaseManager(config)

    max_workers = min(max_workers, len(emails_to_analyze))
    if max_workers <= 0:
        return 0, len(emails_to_analyze)

    def _analyze(email_row: Dict) -> bool:
        try:
            email_id = email_row['id']
            content = email_row.get('content') or ''
            subject = email_row.get('subject') or ''
            result = ai_service.analyze_email_content(content, subject, user_id=user_id,
                                                     reference_time=email_row.get('received_date'))
            # 删除旧分析（按 user_id 隔离）
            db.execute_update("DELETE FROM email_analysis WHERE email_id = ? AND user_id = ?", (email_id, user_id))
            # 规范化事件中的datetime
            events = (result or {}).get('events', [])
            serializable_events = []
            for ev in events:
                e2 = ev.copy()
                st = e2.get('start_time')
                et = e2.get('end_time')
                if hasattr(st, 'isoformat') and not isinstance(st, str):
                    e2['start_time'] = st.isoformat()
                if hasattr(et, 'isoformat') and not isinstance(et, str):
                    e2['end_time'] = et.isoformat()
                if 'reminder_times' in e2 and e2['reminder_times']:
                    e2['reminder_times'] = [rt.isoformat() if hasattr(rt, 'isoformat') and not isinstance(rt, str) else rt for rt in e2['reminder_times']]
                serializable_events.append(e2)

            import json
            db.execute_insert(
                """
                INSERT INTO email_analysis 
                (user_id, email_id, summary, importance_score, importance_reason, events_json, keywords_matched, ai_model, analysis_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    email_id,
                    (result or {}).get('summary', ''),
                    (result or {}).get('importance_score', 5),
                    (result or {}).get('importance_reason', ''),
                    json.dumps(serializable_events, ensure_ascii=False),
                    json.dumps([], ensure_ascii=False),
                    (result or {}).get('ai_model', ''),
                    datetime.now()
                )
            )
            # 创建事件与提醒
            if result and result.get('events'):
                for ev in result['events']:
                    ev['email_id'] = email_id
                    scheduler_service.add_event(ev, user_id)
            # 归档到Notion（按需）
            try:
                if result:
                    # 非强制，失败不影响主流程
                    notion_service.archive_email(email_row, result)
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"AI分析失败（worker）: {email_row.get('subject')} - {e}")
            return False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_analyze, row) for row in emails_to_analyze]
        for fut in futures:
            try:
                ok = fut.result(timeout=timeout_seconds)
                if ok:
                    analyzed_count += 1
                else:
                    failed_count += 1
            except Exception:
                failed_count += 1

    return analyzed_count, failed_count


def run_once(config: Config):
    setup_logger(config)
    db = DatabaseManager(config)
    email_service = EmailService(config)
    scheduler_service = SchedulerService(config)
    user_cfg_svc = UserConfigService()

    # 动态读取激活用户（无激活用户则不执行，避免误用默认 user_id=1）
    users = db.execute_query("SELECT id FROM users WHERE is_active = 1")
    user_ids = [u['id'] for u in users]
    if not user_ids:
        logger.info("[worker] 未找到激活用户，跳过本轮")
        return

    # 读超时配置
    try:
        ai_cfg = config._config.get('ai', {}) or {}
        analysis_timeout = int(ai_cfg.get('analysis_timeout_seconds', 30))
    except Exception:
        analysis_timeout = 30

    from datetime import timedelta

    for uid in user_ids:
        try:
            logger.info(f"[worker] 处理用户 {uid}")
            # 按用户自动获取设置与间隔判断
            email_cfg = user_cfg_svc.get_email_config(uid)
            if not email_cfg.get('auto_fetch', True):
                logger.info(f"[worker] 用户 {uid} 已关闭自动获取，跳过")
                continue
            # 自动获取限制：避免一次拉取过多
            try:
                max_count = int(email_cfg.get('max_emails_per_fetch', 50))
            except Exception:
                max_count = 50
            if max_count <= 0:
                max_count = 50
            # 自动获取时间窗口：默认仅取近 1 天；可通过用户配置 email.auto_days_back 调整
            try:
                days_back = int(email_cfg.get('auto_days_back', 1))
            except Exception:
                days_back = 1
            if days_back <= 0:
                days_back = 1
            try:
                fetch_interval = int(email_cfg.get('fetch_interval', 1800))  # 秒
            except Exception:
                fetch_interval = 1800
            # 读取上次执行时间（用户级持久化）
            last_fetch_at = user_cfg_svc.get_user_config(uid, 'email', 'last_fetch_at', None)
            should_run = True
            if last_fetch_at:
                try:
                    last_dt = datetime.fromisoformat(last_fetch_at)
                    should_run = (datetime.now() - last_dt).total_seconds() >= fetch_interval
                except Exception:
                    should_run = True
            if not should_run:
                logger.info(f"[worker] 用户 {uid} 未到执行间隔（间隔 {fetch_interval}s），跳过")
                continue
            # 检查任务锁，避免与手动流式处理冲突
            from .task_lock import task_lock_manager
            if not task_lock_manager.acquire_lock(uid, 'auto', timeout=2):
                logger.info(f"[worker] 用户 {uid} 正在进行手动流式处理，跳过自动获取")
                continue
            
            try:
                # 1) 批量获取新邮件（不使用流式处理）
                new_emails = email_service.fetch_new_emails(uid, days_back=days_back, max_count=max_count)
                logger.info(f"[worker] 用户 {uid} 获取到 {len(new_emails)} 封新邮件")
                
                # 2) 批量保存邮件
                saved_count = 0
                for email_data in new_emails:
                    try:
                        email_id = email_service.email_model.save_email(email_data, uid)
                        saved_count += 1
                        logger.info(f"[worker] 用户 {uid} 保存邮件: {email_data['subject']}")
                    except Exception as e:
                        logger.error(f"[worker] 保存邮件失败: {e}")
                
                logger.info(f"[worker] 用户 {uid} 保存了 {saved_count} 封新邮件")
                
                # 3) 获取未分析的邮件（包括刚保存的和之前失败的）
                emails_to_analyze = db.execute_query(
                    """
                    SELECT e.id, e.subject, e.content, e.received_date
                    FROM emails e
                    LEFT JOIN email_analysis ea ON e.id = ea.email_id
                    WHERE e.user_id = ? AND (ea.id IS NULL OR ea.summary = '' OR ea.summary = '邮件内容分析失败' OR ea.summary = 'AI分析失败')
                    ORDER BY e.received_date DESC
                    LIMIT 50
                    """,
                    (uid,)
                )
                
                # 4) 批量并行分析
                analyzed_count, failed_count = _analyze_batch_for_user(
                    config, emails_to_analyze, uid, max_workers=3, timeout_seconds=analysis_timeout
                )
                
                logger.info(f"[worker] 用户 {uid} 批量处理完成: 获取 {len(new_emails)} 封，保存 {saved_count} 封，分析 {analyzed_count} 封，失败 {failed_count} 封")
            
            finally:
                # 释放任务锁
                task_lock_manager.release_lock(uid, 'auto')

            # 4) 处理提醒
            try:
                processed = scheduler_service.process_reminders(uid)
                logger.info(f"[worker] 用户 {uid}: 处理提醒 {processed}")
            except Exception as e:
                logger.warning(f"处理提醒失败（worker）: {e}")

            # 更新上次执行时间
            try:
                user_cfg_svc.set_user_config(uid, 'email', 'last_fetch_at', datetime.now().isoformat())
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[worker] 用户 {uid} 处理发生错误: {e}")


def main():
    """Worker主循环 - 持续运行，自动重启"""
    config = Config()
    setup_logger(config)
    
    # 后台轮询主间隔：默认30分钟，可在 config.yaml -> scheduler.interval_seconds 设置
    try:
        interval_seconds = int((config._config.get('scheduler') or {}).get('interval_seconds', 1800))
    except Exception:
        interval_seconds = 1800
    
    logger.info(f"Worker启动，轮询间隔: {interval_seconds}秒 ({interval_seconds//60}分钟)")
    
    consecutive_errors = 0
    max_consecutive_errors = 5
    
    while True:
        try:
            logger.debug("开始执行Worker任务...")
            run_once(config)
            consecutive_errors = 0  # 成功后重置错误计数
            logger.debug(f"Worker任务完成，等待 {interval_seconds} 秒...")
            
        except KeyboardInterrupt:
            logger.info("收到中断信号，Worker正在退出...")
            break
            
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Worker执行失败 ({consecutive_errors}/{max_consecutive_errors}): {e}", exc_info=True)
            
            # 连续失败太多次，增加等待时间
            if consecutive_errors >= max_consecutive_errors:
                wait_time = interval_seconds * 2
                logger.warning(f"连续失败 {consecutive_errors} 次，等待 {wait_time} 秒后重试...")
                time.sleep(wait_time)
                consecutive_errors = 0  # 重置计数
                continue
        
        # 正常等待
        time.sleep(interval_seconds)


if __name__ == '__main__':
    main()


