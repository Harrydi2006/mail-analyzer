# -*- coding: utf-8 -*-
"""
JPush（极光）服务端主动推送封装。
"""

from __future__ import annotations

import base64
from typing import Any, Dict, Optional, Tuple

import requests

from ..core.config import Config


class JPushService:
    """JPush REST v3 服务。"""

    def __init__(self, config: Config):
        self.config = config
        self._base = "https://api.jpush.cn/v3"

    def _credentials(self, notify_cfg: Dict[str, Any]) -> Tuple[str, str]:
        app_key = str(notify_cfg.get("jpush_app_key") or "").strip()
        master_secret = str(notify_cfg.get("jpush_master_secret") or "").strip()
        if not app_key:
            app_key = str(self.config.get("notification.jpush_app_key", "") or "").strip()
        if not master_secret:
            master_secret = str(self.config.get("notification.jpush_master_secret", "") or "").strip()
        return app_key, master_secret

    def send_to_registration_id(
        self,
        notify_cfg: Dict[str, Any],
        registration_id: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        registration_id = str(registration_id or "").strip()
        if not registration_id:
            return False, "JPush RegistrationID 为空"

        app_key, master_secret = self._credentials(notify_cfg)
        if not app_key or not master_secret:
            return False, "JPush 凭据不完整（缺少 app_key/master_secret）"

        auth_raw = f"{app_key}:{master_secret}".encode("utf-8")
        auth = base64.b64encode(auth_raw).decode("utf-8")
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/json",
        }
        payload = {
            "platform": ["android"],
            "audience": {"registration_id": [registration_id]},
            "notification": {
                "android": {
                    "alert": str(body or ""),
                    "title": str(title or "新通知"),
                    "builder_id": 1,
                    "extras": {k: str(v) for k, v in (data or {}).items() if v is not None},
                }
            },
            "options": {
                "time_to_live": 86400,
                "apns_production": False,
            },
        }
        try:
            resp = requests.post(
                f"{self._base}/push",
                json=payload,
                headers=headers,
                timeout=12,
            )
        except Exception as e:
            return False, f"JPush 请求失败: {e}"

        if resp.status_code in (200, 201):
            return True, "ok"
        try:
            body_obj = resp.json()
        except Exception:
            body_obj = resp.text[:500]
        return False, f"JPush 推送失败 HTTP {resp.status_code}: {body_obj}"

