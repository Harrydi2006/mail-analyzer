# -*- coding: utf-8 -*-
"""
邮件标签服务：
- 管理用户可编辑标签库与订阅规则
- 解析/规范化 AI 生成标签
- 从历史分析中提取可复用标签，供 AI 分类参考
"""

import json
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import Config
from ..models.database import DatabaseManager
from .config_service import UserConfigService


class TagService:
    LEVEL2_FIXED = ["课程", "活动", "事项", "其他"]

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()
        self.db = DatabaseManager(self.config)
        self.user_cfg = UserConfigService()

    @staticmethod
    def _score_to_level1(score: int) -> str:
        try:
            s = int(score)
        except Exception:
            s = 5
        if s >= 8:
            return "important"
        if s >= 4:
            return "normal"
        return "unimportant"

    @classmethod
    def _normalize_level2(cls, raw: Any) -> Tuple[str, str]:
        text = str(raw or "").strip()
        if not text:
            return "其他", ""
        for v in ["课程", "活动", "事项"]:
            if text.startswith(v):
                return v, ""
        if text.startswith("其他"):
            custom = ""
            if "[" in text and "]" in text:
                try:
                    custom = text[text.index("[") + 1:text.rindex("]")].strip()
                except Exception:
                    custom = ""
            return "其他", custom
        # 非法二级标签归入“其他”
        return "其他", text[:32]

    @staticmethod
    def _is_probably_garbled(text: Any) -> bool:
        """粗略判断乱码（如 UTF-8/GBK 误解码产生的异常字符序列）。"""
        s = str(text or "").strip()
        if not s:
            return False
        if "\ufffd" in s:  # replacement character
            return True
        # 中文标签场景下，出现较多非预期文字系统字符通常是编码错位
        weird_scripts = 0
        for ch in s:
            o = ord(ch)
            if (
                0x0370 <= o <= 0x03FF  # Greek
                or 0x0400 <= o <= 0x04FF  # Cyrillic
                or 0x0590 <= o <= 0x05FF  # Hebrew
                or 0x0600 <= o <= 0x06FF  # Arabic
                or 0x0900 <= o <= 0x097F  # Devanagari
            ):
                weird_scripts += 1

        cyr = sum(1 for ch in s if 0x0400 <= ord(ch) <= 0x04FF)
        cjk = sum(1 for ch in s if 0x4E00 <= ord(ch) <= 0x9FFF)
        # 如“֪ͨ”这类错码通常落在 Greek/Hebrew，并且不含中文
        if weird_scripts >= 2 and cjk == 0:
            return True
        return cyr >= 2 and cjk == 0

    @staticmethod
    def _try_recover_utf8_from_gbk_mojibake(text: str) -> str:
        """尝试修复“UTF-8字节被按GBK解码”造成的乱码（如 考试 -> 镰凭瘯）。"""
        s = str(text or "")
        if not s:
            return s
        try:
            recovered = s.encode("gb18030").decode("utf-8")
        except Exception:
            return s
        if not recovered or recovered == s:
            return s
        # 过滤不可见控制字符
        if any((ord(ch) < 32 and ch not in ("\t", "\n", "\r")) for ch in recovered):
            return s
        return recovered

    @staticmethod
    def _try_recover_utf8_from_latin1_mojibake(text: str) -> str:
        """尝试修复“UTF-8字节被按 latin1/cp1252 解码”的乱码。"""
        s = str(text or "")
        if not s:
            return s
        for codec in ("latin1", "cp1252"):
            try:
                recovered = s.encode(codec).decode("utf-8")
            except Exception:
                continue
            if recovered and recovered != s:
                if any((ord(ch) < 32 and ch not in ("\t", "\n", "\r")) for ch in recovered):
                    continue
                return recovered
        return s

    @classmethod
    def _sanitize_text(cls, text: Any, max_len: int) -> str:
        s = str(text or "").strip()
        # 先尝试常见错码修复，再做乱码过滤
        s = cls._try_recover_utf8_from_gbk_mojibake(s).strip()
        s = cls._try_recover_utf8_from_latin1_mojibake(s).strip()
        s = s[:max_len]
        if not s:
            return ""
        if cls._is_probably_garbled(s):
            return ""
        return s

    @classmethod
    def normalize_tags(cls, raw_tags: Any, importance_score: int = 5) -> Dict[str, str]:
        tags = raw_tags if isinstance(raw_tags, dict) else {}
        level1 = str(tags.get("level1") or cls._score_to_level1(importance_score)).strip().lower()
        if level1 not in ("important", "normal", "unimportant"):
            level1 = cls._score_to_level1(importance_score)

        level2, level2_custom = cls._normalize_level2(tags.get("level2"))
        level2_custom = cls._sanitize_text(level2_custom, 32)
        level3 = cls._sanitize_text(tags.get("level3"), 64)
        level4 = cls._sanitize_text(tags.get("level4"), 128)
        return {
            "level1": level1,
            "level2": level2,
            "level2_custom": level2_custom,
            "level3": level3,
            "level4": level4,
        }

    @staticmethod
    def _parse_keywords_payload(payload: Any) -> Dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, str):
            try:
                obj = json.loads(payload)
                return obj if isinstance(obj, dict) else {}
            except Exception:
                return {}
        return {}

    def get_user_tag_settings(self, user_id: int) -> Dict[str, Any]:
        library_default = {"level3": [], "level4": [], "other_level2": []}
        subs_default = []
        ignored_default = []
        manual_default = {"level3": [], "level4": [], "other_level2": []}
        retention_default = 30
        lib = self.user_cfg.get_user_config(user_id, "tags", "library", library_default) or library_default
        subs = self.user_cfg.get_user_config(user_id, "tags", "subscriptions", subs_default) or subs_default
        ignored = self.user_cfg.get_user_config(user_id, "tags", "history_ignored", ignored_default) or ignored_default
        manual = self.user_cfg.get_user_config(user_id, "tags", "history_manual", manual_default) or manual_default
        retention_days = self.user_cfg.get_user_config(user_id, "tags", "history_retention_days", retention_default)

        clean_lib = {
            "level3": [self._sanitize_text(x, 64) for x in (lib.get("level3") or [])],
            "level4": [self._sanitize_text(x, 128) for x in (lib.get("level4") or [])],
            "other_level2": [self._sanitize_text(x, 32) for x in (lib.get("other_level2") or [])],
        }
        clean_lib["level3"] = sorted({x for x in clean_lib["level3"] if x})
        clean_lib["level4"] = sorted({x for x in clean_lib["level4"] if x})
        clean_lib["other_level2"] = sorted({x for x in clean_lib["other_level2"] if x})

        clean_subs: List[Dict[str, Any]] = []
        for s in subs:
            if isinstance(s, dict):
                lv = int(s.get("level", 0) or 0)
                val = self._sanitize_text(s.get("value"), 128)
            else:
                lv = 3
                val = self._sanitize_text(s, 128)
            if lv in (2, 3, 4) and val:
                clean_subs.append({"level": lv, "value": val})

        clean_ignored: List[Dict[str, Any]] = []
        for s in ignored:
            if isinstance(s, dict):
                lv = int(s.get("level", 0) or 0)
                val = self._sanitize_text(s.get("value"), 128)
            else:
                lv = 3
                val = self._sanitize_text(s, 128)
            if lv in (2, 3, 4) and val:
                clean_ignored.append({"level": lv, "value": val})

        clean_manual = {
            "level3": [self._sanitize_text(x, 64) for x in (manual.get("level3") or [])],
            "level4": [self._sanitize_text(x, 128) for x in (manual.get("level4") or [])],
            "other_level2": [self._sanitize_text(x, 32) for x in (manual.get("other_level2") or [])],
        }
        clean_manual["level3"] = sorted({x for x in clean_manual["level3"] if x})
        clean_manual["level4"] = sorted({x for x in clean_manual["level4"] if x})
        clean_manual["other_level2"] = sorted({x for x in clean_manual["other_level2"] if x})

        try:
            clean_retention_days = int(retention_days)
        except Exception:
            clean_retention_days = retention_default
        if clean_retention_days < 1:
            clean_retention_days = 1
        if clean_retention_days > 365:
            clean_retention_days = 365

        # 若存在脏数据（乱码/空值），读取时自动修复并写回
        if clean_lib != lib:
            self.user_cfg.set_user_config(user_id, "tags", "library", clean_lib)
        if clean_subs != subs:
            self.user_cfg.set_user_config(user_id, "tags", "subscriptions", clean_subs)
        if clean_ignored != ignored:
            self.user_cfg.set_user_config(user_id, "tags", "history_ignored", clean_ignored)
        if clean_manual != manual:
            self.user_cfg.set_user_config(user_id, "tags", "history_manual", clean_manual)
        if clean_retention_days != retention_days:
            self.user_cfg.set_user_config(user_id, "tags", "history_retention_days", clean_retention_days)

        return {
            "library": clean_lib,
            "subscriptions": clean_subs,
            "history_ignored": clean_ignored,
            "history_manual": clean_manual,
            "history_retention_days": clean_retention_days,
        }

    def set_user_tag_settings(
        self,
        user_id: int,
        library: Dict[str, Any],
        subscriptions: List[Any],
        history_retention_days: Optional[int] = None,
    ) -> bool:
        # 写入前统一清洗，避免任何入口把脏数据写进库
        clean_library = {
            "level3": sorted({
                self._sanitize_text(x, 64)
                for x in (library or {}).get("level3", [])
                if self._sanitize_text(x, 64)
            }),
            "level4": sorted({
                self._sanitize_text(x, 128)
                for x in (library or {}).get("level4", [])
                if self._sanitize_text(x, 128)
            }),
            "other_level2": sorted({
                self._sanitize_text(x, 32)
                for x in (library or {}).get("other_level2", [])
                if self._sanitize_text(x, 32)
            }),
        }

        clean_subs: List[Dict[str, Any]] = []
        seen = set()
        for s in (subscriptions or []):
            if isinstance(s, dict):
                lv = int(s.get("level", 0) or 0)
                val = self._sanitize_text(s.get("value"), 128)
            else:
                lv = 3
                val = self._sanitize_text(s, 128)
            if lv not in (2, 3, 4) or not val:
                continue
            key = (lv, val)
            if key in seen:
                continue
            seen.add(key)
            clean_subs.append({"level": lv, "value": val})

        if history_retention_days is None:
            history_retention_days = self.get_user_tag_settings(int(user_id)).get("history_retention_days", 30)
        try:
            history_retention_days = int(history_retention_days)
        except Exception:
            history_retention_days = 30
        if history_retention_days < 1:
            history_retention_days = 1
        if history_retention_days > 365:
            history_retention_days = 365
        ok1 = self.user_cfg.set_user_config(user_id, "tags", "library", clean_library)
        ok2 = self.user_cfg.set_user_config(user_id, "tags", "subscriptions", clean_subs)
        ok3 = self.user_cfg.set_user_config(user_id, "tags", "history_retention_days", history_retention_days)
        return bool(ok1 and ok2 and ok3)

    def get_existing_tag_candidates(self, user_id: int, limit: int = 300, include_history: bool = True) -> Dict[str, List[str]]:
        lv3, lv4, other2 = set(), set(), set()

        if include_history:
            rows = self.db.execute_query(
                """
                SELECT keywords_matched
                FROM email_analysis
                WHERE user_id = ?
                ORDER BY analysis_date DESC
                LIMIT ?
                """,
                (user_id, int(limit)),
            )
            for r in rows:
                payload = self._parse_keywords_payload(r.get("keywords_matched"))
                tags = self.normalize_tags((payload or {}).get("tags") or {}, 5)
                if tags.get("level3"):
                    lv3.add(tags["level3"])
                if tags.get("level4"):
                    lv4.add(tags["level4"])
                if tags.get("level2") == "其他" and tags.get("level2_custom"):
                    other2.add(tags["level2_custom"])

        # 候选中始终保留用户手工维护的标签库
        settings = self.get_user_tag_settings(user_id)
        lib = settings.get("library") or {}
        manual = settings.get("history_manual") or {}
        lv3.update([str(x).strip() for x in (lib.get("level3") or []) if str(x).strip()])
        lv4.update([str(x).strip() for x in (lib.get("level4") or []) if str(x).strip()])
        other2.update([str(x).strip() for x in (lib.get("other_level2") or []) if str(x).strip()])
        lv3.update([str(x).strip() for x in (manual.get("level3") or []) if str(x).strip()])
        lv4.update([str(x).strip() for x in (manual.get("level4") or []) if str(x).strip()])
        other2.update([str(x).strip() for x in (manual.get("other_level2") or []) if str(x).strip()])
        return {
            "level3": sorted(lv3)[:200],
            "level4": sorted(lv4)[:300],
            "other_level2": sorted(other2)[:100],
        }

    def _is_subscribed_item(self, subs: List[Any], level: int, value: str) -> bool:
        for item in (subs or []):
            if not isinstance(item, dict):
                continue
            lv = int(item.get("level", 0) or 0)
            val = str(item.get("value") or "").strip()
            if lv == int(level) and val == str(value).strip():
                return True
        return False

    def get_history_tag_candidates(self, user_id: int, limit: int = 500) -> Dict[str, List[Dict[str, Any]]]:
        """历史候选（历史分析 + 手工添加），支持用户隐藏项。

        返回项格式：
        - {"value": "xxx", "manual": bool, "subscribed": bool}
        """
        settings = self.get_user_tag_settings(int(user_id))
        retention_days = int(settings.get("history_retention_days") or 30)
        since_expr = f"-{retention_days} days"
        rows = self.db.execute_query(
            """
            SELECT keywords_matched
            FROM email_analysis
            WHERE user_id = ?
              AND analysis_date >= datetime('now', ?)
            ORDER BY analysis_date DESC
            LIMIT ?
            """,
            (int(user_id), since_expr, int(limit)),
        )
        lv3, lv4, other2 = set(), set(), set()
        for r in rows:
            payload = self._parse_keywords_payload(r.get("keywords_matched"))
            tags = self.normalize_tags((payload or {}).get("tags") or {}, 5)
            if tags.get("level3"):
                lv3.add(tags["level3"])
            if tags.get("level4"):
                lv4.add(tags["level4"])
            if tags.get("level2") == "其他" and tags.get("level2_custom"):
                other2.add(tags["level2_custom"])

        settings = self.get_user_tag_settings(int(user_id))
        manual = settings.get("history_manual") or {}
        ignored = settings.get("history_ignored") or []
        subs = settings.get("subscriptions") or []
        ignored_set = {
            (int(x.get("level", 0) or 0), str(x.get("value") or "").strip())
            for x in ignored
            if isinstance(x, dict)
        }
        # 手工添加标签（不受历史清理周期影响）
        m3 = {str(x).strip() for x in (manual.get("level3") or []) if str(x).strip()}
        m4 = {str(x).strip() for x in (manual.get("level4") or []) if str(x).strip()}
        m2 = {str(x).strip() for x in (manual.get("other_level2") or []) if str(x).strip()}

        # 订阅标签不受历史清理周期影响：始终纳入候选展示
        sub2 = {str(s.get("value") or "").strip() for s in subs if isinstance(s, dict) and int(s.get("level", 0) or 0) == 2}
        sub3 = {str(s.get("value") or "").strip() for s in subs if isinstance(s, dict) and int(s.get("level", 0) or 0) == 3}
        sub4 = {str(s.get("value") or "").strip() for s in subs if isinstance(s, dict) and int(s.get("level", 0) or 0) == 4}

        all3 = sorted({x for x in lv3.union(m3).union(sub3) if x and (3, x) not in ignored_set})[:300]
        all4 = sorted({x for x in lv4.union(m4).union(sub4) if x and (4, x) not in ignored_set})[:400]
        all2 = sorted({x for x in other2.union(m2).union(sub2) if x and (2, x) not in ignored_set})[:150]

        out3 = [{"value": x, "manual": x in m3, "subscribed": self._is_subscribed_item(subs, 3, x)} for x in all3]
        out4 = [{"value": x, "manual": x in m4, "subscribed": self._is_subscribed_item(subs, 4, x)} for x in all4]
        out2 = [{"value": x, "manual": x in m2, "subscribed": self._is_subscribed_item(subs, 2, x)} for x in all2]
        return {"level3": out3, "level4": out4, "other_level2": out2}

    def ignore_history_candidate(self, user_id: int, level: int, value: str) -> bool:
        settings = self.get_user_tag_settings(int(user_id))
        ignored = settings.get("history_ignored") or []
        level = int(level or 0)
        value = self._sanitize_text(value, 128)
        if level not in (2, 3, 4) or not value:
            return False
        exists = any(
            isinstance(x, dict) and int(x.get("level", 0) or 0) == level and str(x.get("value") or "").strip() == value
            for x in ignored
        )
        if not exists:
            ignored.append({"level": level, "value": value})
        return bool(self.user_cfg.set_user_config(int(user_id), "tags", "history_ignored", ignored))

    def add_history_candidate_to_library(self, user_id: int, level: int, value: str) -> bool:
        settings = self.get_user_tag_settings(int(user_id))
        lib = settings.get("library") or {"level3": [], "level4": [], "other_level2": []}
        subs = settings.get("subscriptions") or []
        ignored = settings.get("history_ignored") or []

        level = int(level or 0)
        value = self._sanitize_text(value, 128)
        if level not in (2, 3, 4) or not value:
            return False

        if level == 2:
            cur = [str(x).strip() for x in (lib.get("other_level2") or []) if str(x).strip()]
            if value not in cur:
                cur.append(value)
            lib["other_level2"] = cur
        elif level == 3:
            cur = [str(x).strip() for x in (lib.get("level3") or []) if str(x).strip()]
            if value not in cur:
                cur.append(value)
            lib["level3"] = cur
        else:
            cur = [str(x).strip() for x in (lib.get("level4") or []) if str(x).strip()]
            if value not in cur:
                cur.append(value)
            lib["level4"] = cur

        ok = self.set_user_tag_settings(int(user_id), lib, subs)
        if not ok:
            return False

        # 已加入标签库后自动从“隐藏候选”中移除，避免状态混乱
        ignored = [
            x for x in ignored
            if not (
                isinstance(x, dict)
                and int(x.get("level", 0) or 0) == level
                and str(x.get("value") or "").strip() == value
            )
        ]
        self.user_cfg.set_user_config(int(user_id), "tags", "history_ignored", ignored)
        return True

    def add_manual_history_candidate(self, user_id: int, level: int, value: str) -> bool:
        settings = self.get_user_tag_settings(int(user_id))
        manual = settings.get("history_manual") or {"level3": [], "level4": [], "other_level2": []}
        level = int(level or 0)
        value = self._sanitize_text(value, 128)
        if level not in (2, 3, 4) or not value:
            return False
        if level == 2:
            key, max_len = "other_level2", 32
        elif level == 3:
            key, max_len = "level3", 64
        else:
            key, max_len = "level4", 128
        value = self._sanitize_text(value, max_len)
        cur = [str(x).strip() for x in (manual.get(key) or []) if str(x).strip()]
        if value not in cur:
            cur.append(value)
        manual[key] = sorted(set(cur))
        return bool(self.user_cfg.set_user_config(int(user_id), "tags", "history_manual", manual))

    def remove_history_candidate(self, user_id: int, level: int, value: str, manual: bool = False) -> bool:
        level = int(level or 0)
        value = self._sanitize_text(value, 128)
        if level not in (2, 3, 4) or not value:
            return False
        if manual:
            settings = self.get_user_tag_settings(int(user_id))
            manual_cfg = settings.get("history_manual") or {"level3": [], "level4": [], "other_level2": []}
            key = "other_level2" if level == 2 else ("level3" if level == 3 else "level4")
            cur = [str(x).strip() for x in (manual_cfg.get(key) or []) if str(x).strip()]
            manual_cfg[key] = [x for x in cur if x != value]
            return bool(self.user_cfg.set_user_config(int(user_id), "tags", "history_manual", manual_cfg))
        return self.ignore_history_candidate(int(user_id), level, value)

    def get_ai_tag_context(self, user_id: int) -> Dict[str, Any]:
        existing = self.get_existing_tag_candidates(user_id)
        return {
            "level2_fixed": list(self.LEVEL2_FIXED),
            "existing_level3": existing.get("level3", []),
            "existing_level4": existing.get("level4", []),
            "existing_other_level2": existing.get("other_level2", []),
        }

    def get_email_tags(self, user_id: int, email_id: int) -> List[Dict[str, str]]:
        rows = self.db.execute_query(
            """
            SELECT keywords_matched, importance_score
            FROM email_analysis
            WHERE user_id = ? AND email_id = ?
            ORDER BY analysis_date DESC
            LIMIT 1
            """,
            (user_id, email_id),
        )
        if not rows:
            return []
        payload = self._parse_keywords_payload(rows[0].get("keywords_matched"))
        score = rows[0].get("importance_score", 5)
        tags = self.normalize_tags((payload or {}).get("tags") or {}, score)
        return [tags]

    def get_email_tags_bulk(self, user_id: int, email_ids: List[int]) -> Dict[int, List[Dict[str, str]]]:
        if not email_ids:
            return {}
        placeholders = ",".join(["?"] * len(email_ids))
        rows = self.db.execute_query(
            f"""
            SELECT email_id, keywords_matched, importance_score
            FROM email_analysis
            WHERE user_id = ? AND email_id IN ({placeholders})
            ORDER BY analysis_date DESC
            """,
            tuple([user_id] + [int(x) for x in email_ids]),
        )
        out: Dict[int, List[Dict[str, str]]] = {}
        for r in rows:
            eid = int(r.get("email_id"))
            if eid in out:
                continue  # 仅取最新一条
            payload = self._parse_keywords_payload(r.get("keywords_matched"))
            score = r.get("importance_score", 5)
            out[eid] = [self.normalize_tags((payload or {}).get("tags") or {}, score)]
        return out

    def is_subscribed(self, user_id: int, tags: Dict[str, str]) -> Tuple[bool, str]:
        settings = self.get_user_tag_settings(user_id)
        subs = settings.get("subscriptions") or []
        l2 = str(tags.get("level2") or "").strip()
        l3 = str(tags.get("level3") or "").strip()
        l4 = str(tags.get("level4") or "").strip()
        l2_custom = str(tags.get("level2_custom") or "").strip()
        l2_ext = f"其他[{l2_custom}]" if l2 == "其他" and l2_custom else l2

        for item in subs:
            if isinstance(item, dict):
                lv = int(item.get("level", 0) or 0)
                val = str(item.get("value") or "").strip()
            else:
                # 兼容历史字符串配置：默认按三级标签匹配
                lv = 3
                val = str(item or "").strip()
            if not val:
                continue
            if lv == 2 and val in (l2, l2_ext):
                return True, f"L2:{val}"
            if lv == 3 and val == l3:
                return True, f"L3:{val}"
            if lv == 4 and val == l4:
                return True, f"L4:{val}"
        return False, ""

    def apply_subscriptions_to_events(self, user_id: int, include_revert: bool = True) -> Dict[str, int]:
        """将当前标签订阅规则应用到历史事件。

        - 命中订阅：升级为 subscribed（绿色）
        - 未命中且当前为 subscribed（include_revert=True）：回退到 AI 重要性映射等级
        """
        events = self.db.execute_query(
            "SELECT id, email_id, importance_level, color FROM events WHERE user_id = ?",
            (int(user_id),),
        )
        if not events:
            return {"total": 0, "upgraded": 0, "reverted": 0, "unchanged": 0}

        email_ids = sorted({int(e["email_id"]) for e in events if e.get("email_id")})
        tag_map = self.get_email_tags_bulk(int(user_id), email_ids)

        score_map: Dict[int, int] = {}
        if email_ids:
            placeholders = ",".join(["?"] * len(email_ids))
            rows = self.db.execute_query(
                f"""
                SELECT email_id, importance_score
                FROM email_analysis
                WHERE user_id = ? AND email_id IN ({placeholders})
                ORDER BY analysis_date DESC
                """,
                tuple([int(user_id)] + email_ids),
            )
            for r in rows:
                eid = int(r.get("email_id") or 0)
                if eid and eid not in score_map:
                    try:
                        score_map[eid] = int(r.get("importance_score") or 5)
                    except Exception:
                        score_map[eid] = 5

        colors = (self.config.reminder_config or {}).get("colors", {})
        upgraded = 0
        reverted = 0
        unchanged = 0

        for ev in events:
            ev_id = int(ev.get("id") or 0)
            email_id = int(ev.get("email_id") or 0)
            if not ev_id or not email_id:
                unchanged += 1
                continue

            current_level = str(ev.get("importance_level") or "normal")
            current_color = str(ev.get("color") or "")
            tags_list = tag_map.get(email_id) or []
            tags = tags_list[0] if tags_list else {}
            hit, _ = self.is_subscribed(int(user_id), tags)

            target_level = current_level
            target_color = current_color
            if hit:
                target_level = "subscribed"
                target_color = "#28a745"
            elif include_revert and current_level == "subscribed":
                score = int(score_map.get(email_id, 5))
                target_level = self._score_to_level1(score)
                target_color = colors.get(target_level, "#4444FF")

            if target_level != current_level or (target_color and target_color != current_color):
                self.db.execute_update(
                    "UPDATE events SET importance_level = ?, color = ? WHERE id = ? AND user_id = ?",
                    (target_level, target_color or current_color, ev_id, int(user_id)),
                )
                if target_level == "subscribed" and current_level != "subscribed":
                    upgraded += 1
                elif current_level == "subscribed" and target_level != "subscribed":
                    reverted += 1
            else:
                unchanged += 1

        return {
            "total": len(events),
            "upgraded": upgraded,
            "reverted": reverted,
            "unchanged": unchanged,
        }

