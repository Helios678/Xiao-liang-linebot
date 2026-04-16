"""
Claude API 封裝 - 含 Web Search 工具（可選啟用）
"""

import anthropic

BASE_SYSTEM = """你是小亮，用戶的智囊與私人助手，典故出自諸葛亮。

個性直接、務實，不廢話。回答精準，有把握才說，不確定就說不確定。
回覆使用繁體中文，長度視問題而定，不刻意拉長也不過度精簡。
若收到即時股票資料，根據資料回答，不要憑空捏造數字。
只有在用戶明確要求搜尋或查詢時，才使用網路搜尋工具，不要自行判斷是否需要搜尋。

【運作環境】
- 你部署在 LINE 群組與私訊中
- 群組中每位成員的對話是完全獨立的執行緒，你無法得知其他成員「剛剛」說了什麼
- 你有長期成員記憶（姓名、備註），但不記錄成員間的發言順序
- 若有人問「剛剛跟你對話的是誰」，如實說明你只知道目前對話的這位是誰，無法追蹤群組中其他人的發言順序

【格式規定】
- 回覆在 LINE 顯示，不支援 Markdown
- 禁止使用表格（|---|）、# 標題、**粗體**、`程式碼`
- 條列用「・」或數字，分隔用空行
- 股票資訊直接用文字列出，例如：
  成交價：990 ▼10（-1%）
  昨收：1,000 ｜ 最高：995 ｜ 最低：975"""

MODEL = "claude-sonnet-4-6"

WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
}

# claude-sonnet-4-6 定價（USD per million tokens）
INPUT_PRICE_PER_M  = 3.0
OUTPUT_PRICE_PER_M = 15.0
COST_ALERT_USD     = 1.0  # 單次請求預估超過此金額時需哥審核


class ClaudeClient:
    def __init__(self, api_key: str):
        self.client = anthropic.Anthropic(api_key=api_key)
        self._calls = 0
        self._input_tokens = 0
        self._output_tokens = 0

    def chat(self, history: list[dict], user_message: str, extra_context: str = "", enable_search: bool = False) -> str:
        system = BASE_SYSTEM
        if extra_context:
            system = f"{BASE_SYSTEM}\n\n{extra_context}"
        messages = history + [{"role": "user", "content": user_message}]
        tools = [WEB_SEARCH_TOOL] if enable_search else []

        try:
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=2048,
                system=system,
                messages=messages,
                **({"tools": tools} if tools else {}),
            )
            self._log_usage(response.usage)

            # 伺服器端搜尋迴圈若超過上限會回傳 pause_turn，需要繼續送
            for _ in range(3):
                if response.stop_reason != "pause_turn":
                    break
                messages = messages + [{"role": "assistant", "content": response.content}]
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=2048,
                    system=system,
                    messages=messages,
                    **({"tools": tools} if tools else {}),
                )
                self._log_usage(response.usage)

            # 取出所有 text block（過濾掉 server_tool_use 等非文字 block）
            texts = [b.text for b in response.content if b.type == "text"]
            return "\n".join(texts) if texts else "小亮暫時無法回應"

        except Exception as e:
            print(f"[ERROR] claude.chat failed: {e}")
            return f"小亮暫時無法回應：{e}"

    def _log_usage(self, usage):
        self._calls += 1
        self._input_tokens += usage.input_tokens
        self._output_tokens += usage.output_tokens
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        print(
            f"[TOKEN] call={self._calls} "
            f"in={usage.input_tokens} out={usage.output_tokens} "
            f"cache_create={cache_create} cache_read={cache_read} "
            f"| 累計 in={self._input_tokens:,} out={self._output_tokens:,}"
        )

    def is_high_cost_intent(self, user_message: str) -> bool:
        """用 Haiku 預判請求是否會產生高 token 消耗，失敗時放行。"""
        try:
            resp = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=5,
                system=(
                    "你是 LINE 機器人的請求分類器。"
                    "判斷以下用戶請求是否屬於「高 token 消耗」類型，"
                    "包括：生成長篇文章、大量翻譯、詳細分析或報告、多步驟研究、需要大量搜尋整理。"
                    "簡單問答、股票查詢、日常閒聊、單句翻譯不屬於高消耗。"
                    "只回答 yes 或 no。"
                ),
                messages=[{"role": "user", "content": user_message}],
            )
            answer = resp.content[0].text.strip().lower()
            return answer.startswith("y")
        except Exception as e:
            print(f"[WARN] is_high_cost_intent failed: {e}")
            return False  # 判斷失敗時放行，不影響正常使用

    def estimate_cost_for_request(self, history: list[dict], user_message: str, extra_context: str = "") -> float:
        """估算一次 API 呼叫的費用上限（USD）。用字元數近似 token，中英混合約 3 字元 = 1 token。"""
        system = BASE_SYSTEM
        if extra_context:
            system = f"{BASE_SYSTEM}\n\n{extra_context}"
        messages = history + [{"role": "user", "content": user_message}]
        total_chars = len(system) + sum(
            len(m["content"]) if isinstance(m.get("content"), str) else 0
            for m in messages
        )
        input_tokens = total_chars / 3
        input_cost   = input_tokens * INPUT_PRICE_PER_M / 1_000_000
        output_cost  = 2048 * OUTPUT_PRICE_PER_M / 1_000_000
        return input_cost + output_cost

    def get_stats(self) -> str:
        return (
            f"自服務啟動以來\n"
            f"API 呼叫次數：{self._calls}\n"
            f"Input tokens 累計：{self._input_tokens:,}\n"
            f"Output tokens 累計：{self._output_tokens:,}"
        )
