"""
家人記憶管理 — 優先存 Redis，無 REDIS_URL 時退回 family_memory.json
"""

import json
import os
from datetime import date

MEMORY_FILE = os.path.join(os.path.dirname(__file__), "family_memory.json")
REDIS_KEY   = "xiao_liang:family_memory"


class MemoryManager:
    def __init__(self):
        self._redis = self._connect_redis()
        self._data  = self._load()

    def _connect_redis(self):
        url = os.environ.get("REDIS_URL")
        if not url:
            return None
        try:
            import redis
            r = redis.from_url(url, decode_responses=True)
            r.ping()
            print("[INFO] Redis connected")
            return r
        except Exception as e:
            print(f"[WARN] Redis connection failed: {e}, fallback to file")
            return None

    def _load(self) -> dict:
        if self._redis:
            try:
                raw = self._redis.get(REDIS_KEY)
                if raw:
                    return json.loads(raw)
            except Exception as e:
                print(f"[WARN] Redis load failed: {e}")
        # 退回本地檔案
        if os.path.exists(MEMORY_FILE):
            try:
                with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"成員": {}, "群組事件": []}

    def _save(self):
        if self._redis:
            try:
                self._redis.set(REDIS_KEY, json.dumps(self._data, ensure_ascii=False))
                return
            except Exception as e:
                print(f"[WARN] Redis save failed: {e}")
        # 退回本地檔案
        try:
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ── 成員 ────────────────────────────────────────────────────────────────────

    def get_member_by_id(self, user_id: str) -> tuple[str | None, dict | None]:
        for name, info in self._data["成員"].items():
            if info.get("userId") == user_id:
                return name, info
        return None, None

    def is_new_user(self, user_id: str) -> bool:
        name, _ = self.get_member_by_id(user_id)
        return name is None

    def register_member(self, user_id: str, name: str):
        if name not in self._data["成員"]:
            self._data["成員"][name] = {}
        self._data["成員"][name]["userId"] = user_id
        self._data["成員"][name]["最後更新"] = str(date.today())
        self._save()

    def add_note(self, target_name: str, note: str) -> bool:
        if target_name in self._data["成員"]:
            self._data["成員"][target_name]["備註"] = note
            self._data["成員"][target_name]["最後更新"] = str(date.today())
            self._save()
            return True
        return False

    def get_user_context(self, user_id: str) -> str:
        name, info = self.get_member_by_id(user_id)
        if not name or not info:
            return ""
        parts = [f"用戶稱呼：{name}"]
        interests = info.get("關注")
        if interests:
            if isinstance(interests, list):
                parts.append(f"關注話題：{', '.join(interests)}")
            else:
                parts.append(f"關注話題：{interests}")
        note = info.get("備註")
        if note:
            parts.append(f"備註：{note}")
        return "\n".join(parts)

    # ── 群組事件 ─────────────────────────────────────────────────────────────────

    def add_event(self, content: str, recorder: str):
        self._data["群組事件"].append({
            "日期": str(date.today()),
            "內容": content,
            "記錄者": recorder,
        })
        # 只保留最近 50 筆
        if len(self._data["群組事件"]) > 50:
            self._data["群組事件"] = self._data["群組事件"][-50:]
        self._save()

    # ── 摘要（查看記憶指令）──────────────────────────────────────────────────────

    def edit_event(self, index: int, new_content: str) -> bool:
        """修改群組事件（index 從 1 開始，對應 get_all_summary 的編號）。"""
        events = self._data["群組事件"]
        if index < 1 or index > len(events):
            return False
        events[index - 1]["內容"] = new_content
        events[index - 1]["最後更新"] = str(date.today())
        self._save()
        return True

    def delete_event(self, index: int) -> bool:
        """刪除群組事件（index 從 1 開始）。"""
        events = self._data["群組事件"]
        if index < 1 or index > len(events):
            return False
        events.pop(index - 1)
        self._save()
        return True

    def get_all_summary(self) -> str:
        lines = ["【成員記憶】"]
        members = self._data["成員"]
        if members:
            for name, info in members.items():
                note = info.get("備註", "無備註")
                lines.append(f"・{name}：{note}")
        else:
            lines.append("（目前無成員記錄）")

        lines.append("\n【群組事件（全部，附編號）】")
        events = self._data["群組事件"]
        if events:
            for i, e in enumerate(events, 1):
                lines.append(f"{i}. {e['日期']}  {e['內容']}  ─ {e['記錄者']}")
        else:
            lines.append("（目前無事件記錄）")

        return "\n".join(lines)
