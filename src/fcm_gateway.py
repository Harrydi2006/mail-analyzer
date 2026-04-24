# -*- coding: utf-8 -*-
"""
独立 FCM 网关服务：
- 仅负责发送 FCM
- 适合单独挂代理，避免影响 AI/主业务请求
"""

from __future__ import annotations

import os
from typing import Any, Dict

from flask import Flask, jsonify, request

from .core.config import Config
from .core.logger import get_logger, setup_logger
from .services.fcm_service import FCMService

logger = get_logger(__name__)


def create_fcm_gateway_app() -> Flask:
    app = Flask(__name__)
    config = Config()
    setup_logger(config)
    svc = FCMService(config)
    internal_token = str(os.environ.get("FCM_GATEWAY_TOKEN", "") or "").strip()

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "healthy", "service": "fcm-gateway"}), 200

    @app.route("/send", methods=["POST"])
    def send():
        try:
            if internal_token:
                req_token = str(request.headers.get("X-Internal-Token", "") or "").strip()
                if req_token != internal_token:
                    return jsonify({"success": False, "error": "unauthorized"}), 401

            payload = request.get_json(silent=True) or {}
            token = str(payload.get("token") or "").strip()
            title = str(payload.get("title") or "新通知")
            body = str(payload.get("body") or "")
            data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
            credentials_path = str(payload.get("credentials_path") or "").strip()

            if not token:
                return jsonify({"success": False, "error": "token不能为空"}), 400

            ok, msg = svc.send_to_token(
                token=token,
                title=title,
                body=body,
                data={k: str(v) for k, v in data.items()},
                credentials_path=credentials_path,
            )
            if ok:
                return jsonify({"success": True, "message_id": msg or ""})
            return jsonify({"success": False, "error": msg or "FCM发送失败"}), 502
        except Exception as e:
            logger.error(f"FCM 网关发送异常: {e}")
            return jsonify({"success": False, "error": str(e)}), 500

    return app


if __name__ == "__main__":
    host = os.environ.get("FCM_GATEWAY_HOST", "0.0.0.0")
    try:
        port = int(os.environ.get("FCM_GATEWAY_PORT", "5051"))
    except Exception:
        port = 5051
    app = create_fcm_gateway_app()
    app.run(host=host, port=port, debug=False, threaded=True)

