# -*- coding: utf-8 -*-
"""
后台流式任务管理器

目标：
- 流式“处理邮件”任务在后台线程中运行（即使前端刷新/重登也不中断）
- 前端通过 SSE 订阅任务输出（支持断线重连/重新订阅）

注意：
- 这是进程内实现：如果部署为多进程/多实例，状态不会跨进程共享。
"""

from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Deque, Dict, Generator, Optional

from ..core.logger import get_logger
from ..core.config import Config
from .task_lock import task_lock_manager

logger = get_logger(__name__)


@dataclass
class _UserStreamState:
    running: bool = False
    started_at: float = 0.0
    params: Dict[str, Any] = field(default_factory=dict)
    q: "queue.Queue[Dict[str, Any]]" = field(default_factory=queue.Queue)
    history: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=500))
    last_event: Optional[Dict[str, Any]] = None
    thread: Optional[threading.Thread] = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    seq: int = 0


class StreamManager:
    """进程内流式任务管理器（按 user_id 隔离）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._states: Dict[int, _UserStreamState] = {}

    def _state(self, user_id: int) -> _UserStreamState:
        with self._lock:
            if user_id not in self._states:
                self._states[user_id] = _UserStreamState()
            return self._states[user_id]

    def get_status(self, user_id: int) -> Dict[str, Any]:
        st = self._state(user_id)
        with self._lock:
            return {
                "running": bool(st.running),
                "started_at": st.started_at,
                "params": dict(st.params or {}),
                "last_event": st.last_event,
            }

    def _publish(self, user_id: int, event: Dict[str, Any]):
        st = self._state(user_id)
        event = self._make_json_safe(event)
        with self._lock:
            st.seq += 1
            event = dict(event)
            event["_seq"] = st.seq
            st.last_event = event
            st.history.append(event)
            try:
                st.q.put_nowait(event)
            except Exception:
                # queue 满/异常时也不要影响后台任务
                pass

    @staticmethod
    def _make_json_safe(obj: Any) -> Any:
        """递归将 datetime 等对象转换为可 JSON 序列化的结构。"""
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, dict):
            return {k: StreamManager._make_json_safe(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [StreamManager._make_json_safe(v) for v in obj]
        return obj

    def start_email_stream(
        self,
        user_id: int,
        days_back: int,
        max_count: Optional[int],
        analysis_workers: int,
        config: Config,
    ) -> Dict[str, Any]:
        """启动（或复用）后台邮件流式任务。"""
        st = self._state(user_id)
        with self._lock:
            if st.running and st.thread and st.thread.is_alive():
                return {"started": False, "message": "已有流式任务在运行", "status": self.get_status(user_id)}

            # 重置状态
            st.running = True
            st.started_at = time.time()
            st.params = {"days_back": days_back, "max_count": max_count, "analysis_workers": analysis_workers}
            st.q = queue.Queue()
            st.history.clear()
            st.last_event = None
            st.cancel_event = threading.Event()
            st.seq = 0

            t = threading.Thread(
                target=self._run_email_stream,
                args=(user_id, days_back, max_count, analysis_workers, config, st.cancel_event),
                daemon=True,
                name=f"email-stream-{user_id}",
            )
            st.thread = t
            t.start()

        return {"started": True, "message": "已启动流式任务", "status": self.get_status(user_id)}

    def stop(self, user_id: int) -> Dict[str, Any]:
        """请求终止后台流式任务（尽力而为，取决于 IMAP 阻塞点）。"""
        st = self._state(user_id)
        with self._lock:
            if not (st.running and st.thread and st.thread.is_alive()):
                return {"stopped": False, "message": "当前没有运行中的流式任务"}
            st.cancel_event.set()
        self._publish(user_id, {"status": "info", "message": "已收到终止请求，正在停止..."})
        return {"stopped": True, "message": "已发送终止请求"}

    def _run_email_stream(
        self,
        user_id: int,
        days_back: int,
        max_count: Optional[int],
        analysis_workers: int,
        config: Config,
        cancel_event: threading.Event,
    ):
        """后台线程：执行实际的邮件流式处理，并持续 publish 事件。"""
        from .email_service import EmailService

        try:
            self._publish(user_id, {"status": "started", "message": "开始流式处理邮件（后台任务）"})

            # 停止自动任务并获取锁
            task_lock_manager.force_release_all_locks(user_id)
            if not task_lock_manager.acquire_lock(user_id, "stream", timeout=10):
                self._publish(user_id, {"status": "error", "fatal": True, "message": "无法获取处理锁，可能有其他流式处理正在进行"})
                return

            self._publish(user_id, {"status": "info", "message": "已获取处理锁，开始流式处理"})

            # 为该后台任务创建独立的 EmailService，避免与其他请求线程共享内部可变状态
            svc = EmailService(config)
            for event in svc.fetch_and_process_emails_stream(
                user_id,
                days_back=days_back,
                max_count=max_count,
                analysis_workers=analysis_workers,
                cancel_event=cancel_event,
            ):
                # 事件必须是可 JSON 序列化的 dict
                if isinstance(event, dict):
                    self._publish(user_id, event)
                else:
                    self._publish(user_id, {"status": "info", "message": str(event)})

            # 如果子函数未显式发 completed，这里兜底
            last = self.get_status(user_id).get("last_event") or {}
            if last.get("status") not in ("completed", "cancelled"):
                self._publish(user_id, {"status": "completed", "message": "流式处理完成"})

        except Exception as e:
            logger.error(f"[stream_manager] 后台流式任务失败: {e}")
            self._publish(user_id, {"status": "error", "fatal": True, "message": str(e)})
        finally:
            try:
                task_lock_manager.release_lock(user_id, "stream")
            except Exception:
                pass
            with self._lock:
                st = self._states.get(user_id)
                if st:
                    st.running = False

    def subscribe(self, user_id: int) -> Generator[Dict[str, Any], None, None]:
        """订阅流式输出：先回放 history，再实时读取 queue。"""
        st = self._state(user_id)

        # 先回放历史
        with self._lock:
            history_snapshot = list(st.history)
            running = bool(st.running)
            last_event = st.last_event
            last_seq = history_snapshot[-1].get("_seq", 0) if history_snapshot else (last_event.get("_seq", 0) if last_event else 0)

        for ev in history_snapshot:
            yield ev

        # 如果没有历史但有 last_event，也回放一次（保险）
        if not history_snapshot and last_event:
            yield last_event

        # 再进入实时订阅
        # 若当前不在运行且没有任何输出，直接结束
        if not running and not history_snapshot and not last_event:
            return

        while True:
            # 如果任务已结束且队列空一段时间，则退出
            with self._lock:
                running = bool(st.running)
            try:
                ev = st.q.get(timeout=15)
                # 去重：避免订阅者先回放 history 又从队列再次拿到同一条事件
                if isinstance(ev, dict) and ev.get("_seq", 0) <= last_seq:
                    continue
                if isinstance(ev, dict):
                    last_seq = max(last_seq, ev.get("_seq", 0))
                yield ev
                if isinstance(ev, dict) and ev.get("status") in ("completed", "cancelled"):
                    return
                if isinstance(ev, dict) and ev.get("status") == "error" and ev.get("fatal"):
                    return
            except queue.Empty:
                # keepalive：让反向代理/浏览器保持连接
                yield {"status": "keepalive", "ts": time.time()}
                if not running:
                    return


# 全局单例
stream_manager = StreamManager()


