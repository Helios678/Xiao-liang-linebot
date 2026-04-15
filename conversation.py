"""
對話歷史管理 — 每個 LINE userId 維護獨立的對話紀錄
"""

MAX_HISTORY = 10  # 每個用戶保留最近幾則


class ConversationManager:
    def __init__(self):
        self._histories: dict[str, list[dict]] = {}

    def get(self, user_id: str) -> list[dict]:
        return self._histories.get(user_id, [])

    def add(self, user_id: str, role: str, content: str):
        history = self._histories.setdefault(user_id, [])
        history.append({"role": role, "content": content})
        # 超過上限時，從頭刪（保留最近的）
        if len(history) > MAX_HISTORY:
            self._histories[user_id] = history[-MAX_HISTORY:]

    def clear(self, user_id: str):
        self._histories.pop(user_id, None)
