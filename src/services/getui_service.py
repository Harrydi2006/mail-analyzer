# -*- coding: utf-8 -*-
"""
Getui（个推）服务端主动推送封装。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

import requests

from ..core.config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class GetuiService:
    """Getui REST v2 服务。"""

    _token_cache: Dict[str, Dict[str, Any]] = {}

    def __init__(self, config: Config):
        self.config = config
        self._base = "https://restapi.getui.com"

    def _credentials(self, notify_cfg: Dict[str, Any]) -> Tuple[str, str, str]:
        app_id = str(notify_cfg.get("getui_app_id") or "").strip()
        app_key = str(notify_cfg.get("getui_app_key") or "").strip()
        master_secret = str(notify_cfg.get("getui_master_secret") or "").strip()
        if not app_id:
            app_id = str(self.config.get("notification.getui_app_id", "") or "").strip()
        if not app_key:
            app_key = str(self.config.get("notification.getui_app_key", "") or "").strip()
        if not master_secret:
            master_secret = str(self.config.get("notification.getui_master_secret", "") or "").strip()
        if not app_id:
            app_id = str(os.getenv("GETUI_APP_ID", "")).strip()
        if not app_key:
            app_key = str(os.getenv("GETUI_APP_KEY", "")).strip()
        if not master_secret:
            master_secret = str(os.getenv("GETUI_MASTER_SECRET", "")).strip()
        return app_id, app_key, master_secret

    def _get_token(self, app_id: str, app_key: str, master_secret: str) -> Tuple[bool, str]:
        if not (app_id and app_key and master_secret):
            return False, "Getui 凭据不完整（缺少 app_id/app_key/master_secret）"

        cached = self.__class__._token_cache.get(app_id) or {}
        now = int(time.time())
        if cached.get("token") and int(cached.get("expires_at", 0)) > now + 30:
            return True, str(cached["token"])

        timestamp = str(int(time.time() * 1000))
        sign = hashlib.sha256(f"{app_key}{timestamp}{master_secret}".encode("utf-8")).hexdigest()

        url = f"{self._base}/v2/{app_id}/auth"
        try:
            resp = requests.post(
                url,
                json={"sign": sign, "timestamp": timestamp, "appkey": app_key},
                timeout=10,
            )
        except Exception as e:
            return False, f"Getui 鉴权请求失败: {e}"

        try:
            body = resp.json()
        except Exception:
            body = {"msg": resp.text[:500]}

        if resp.status_code != 200:
            return False, f"Getui 鉴权失败 HTTP {resp.status_code}: {body}"
        if str(body.get("code", "")) != "0":
            return False, f"Getui 鉴权失败: {body}"

        data = body.get("data") or {}
        token = str(data.get("token") or "").strip()
        expire_ms = int(data.get("expire_time") or data.get("expireTime") or 0)
        if not token:
            return False, "Getui 鉴权失败：未返回 token"

        expires_at = now + 3600
        if expire_ms > 0:
            expires_at = max(now + 30, int(expire_ms / 1000))
        self.__class__._token_cache[app_id] = {"token": token, "expires_at": expires_at}
        return True, token

    def send_to_cid(
        self,
        notify_cfg: Dict[str, Any],
        cid: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        cid = str(cid or "").strip()
        if not cid:
            return False, "Getui CID 为空"

        app_id, app_key, master_secret = self._credentials(notify_cfg)
        ok, token_or_err = self._get_token(app_id, app_key, master_secret)
        if not ok:
            return False, token_or_err
        token = token_or_err

        payload = {
            "request_id": uuid.uuid4().hex[:32],
            "audience": {"cid": [cid]},
            "settings": {"ttl": 24 * 3600 * 1000},
            "push_message": {
                "notification": {
                    "title": str(title or "新通知"),
                    "body": str(body or ""),
                    "click_type": "startapp",
                    "payload": json.dumps(data or {}, ensure_ascii=False),
                }
            },
        }
        url = f"{self._base}/v2/{app_id}/push/single/cid"
        headers = {"token": token, "Content-Type": "application/json;charset=utf-8"}

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=12)
        except Exception as e:
            return False, f"Getui 推送请求失败: {e}"

        try:
            body_obj = resp.json()
        except Exception:
            body_obj = {"msg": resp.text[:500]}

        if resp.status_code != 200:
            return False, f"Getui 推送失败 HTTP {resp.status_code}: {body_obj}"
        if str(body_obj.get("code", "")) != "0":
            return False, f"Getui 推送失败: {body_obj}"
        return True, str(body_obj.get("msg") or body_obj.get("data") or "ok")
