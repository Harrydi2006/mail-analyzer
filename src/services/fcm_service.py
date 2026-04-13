# -*- coding: utf-8 -*-
"""
FCM 推送服务（服务端主动推送）。
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..core.config import Config
from ..core.logger import get_logger

logger = get_logger(__name__)


class FCMService:
    """Firebase Cloud Messaging 服务。"""

    _initialized = False
    _init_error = ""

    def __init__(self, config: Config):
        self.config = config

    def _resolve_credentials_path(self, override_path: str = "") -> str:
        raw = (override_path or "").strip()
        if not raw:
            raw = str(self.config.get('notification.fcm_service_account_path', '') or '').strip()
        if not raw:
            raw = str(self.config.get('fcm.service_account_path', '') or '').strip()
        if not raw:
            raw = str(self.config.get('firebase.service_account_path', '') or '').strip()
        if not raw:
            return ""
        p = Path(raw)
        if not p.is_absolute():
            p = (self.config.project_root / p).resolve()
        return str(p)

    def initialize(self, credentials_path: str = "") -> Tuple[bool, str]:
        if self.__class__._initialized:
            return True, ""
        if self.__class__._init_error:
            return False, self.__class__._init_error

        try:
            import firebase_admin
            from firebase_admin import credentials

            if firebase_admin._apps:
                self.__class__._initialized = True
                return True, ""

            cred_path = self._resolve_credentials_path(credentials_path)
            if cred_path:
                if not Path(cred_path).exists():
                    self.__class__._init_error = f'FCM 凭据文件不存在: {cred_path}'
                    return False, self.__class__._init_error
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
            else:
                # 回退：允许使用 GOOGLE_APPLICATION_CREDENTIALS 等默认凭据。
                firebase_admin.initialize_app()

            self.__class__._initialized = True
            return True, ""
        except Exception as e:
            self.__class__._init_error = f'初始化 FCM 失败: {e}'
            logger.error(self.__class__._init_error)
            return False, self.__class__._init_error

    def send_to_token(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        credentials_path: str = "",
    ) -> Tuple[bool, str]:
        token = str(token or "").strip()
        if not token:
            return False, "FCM token 为空"

        ok, err = self.initialize(credentials_path=credentials_path)
        if not ok:
            return False, err

        try:
            from firebase_admin import messaging

            payload = {k: str(v) for k, v in (data or {}).items() if v is not None}
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(
                    title=str(title or "新通知"),
                    body=str(body or ""),
                ),
                data=payload,
                android=messaging.AndroidConfig(priority="high"),
                apns=messaging.APNSConfig(
                    headers={"apns-priority": "10"},
                    payload=messaging.APNSPayload(aps=messaging.Aps(sound="default")),
                ),
            )
            message_id = messaging.send(message)
            return True, str(message_id or "")
        except Exception as e:
            err_msg = f'FCM 发送失败: {e}'
            logger.error(err_msg)
            return False, err_msg
