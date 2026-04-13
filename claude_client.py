"""
Claude API 封裝
"""

import anthropic

BASE_SYSTEM = """你是小亮，用戶的智囊與私人助手，典故出自諸葛亮。

個性直接、務實，不廢話。回答精準，有把握才說，不確定就說不確定。
回覆使用繁體中文，長度視問題而定，不刻意拉長也不過度精簡。
若收到即時股票資料，根據資料回答，不要憑空捏造數字。

【格式規定】
- 回覆在 LINE 顯示，不支援 Markdown
- 禁止使用表格（|---|）、# 標題、**粗體**、`程式碼`
- 條列用「・」或數字，分隔用空行
- 股票資訊直接用文字列出，例如：
  成交價：990 ▼10（-1%）
  昨收：1,000 ｜ 最高：995 ｜ 最低：975"""

MODEL = "claude-sonnet-4-6"


class ClaudeClient:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)

    def chat(self, history: list[dict], user_message: str, extra_context: str = "") -> str:
        system = BASE_SYSTEM
        if extra_context:
            system = f"{BASE_SYSTEM}\n\n{extra_context}"
        messages = history + [{"role": "user", "content": user_message}]
        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system,
                messages=messages,
            )
            return response.content[0].text
        except anthropic.APIError as e:
            return f"小亮暫時無法回應：{e}"
