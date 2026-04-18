"""
每日早安圖 — Nano Banana 生圖 + Claude 小語 + 去重推播
"""

import os
import re
import json
from datetime import datetime, timedelta

import anthropic
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    PushMessageRequest, ImageMessage, TextMessage,
)

from image_gen import generate_image, cleanup_old_images

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "morning_history.json")
HISTORY_DAYS = 30

PLAN_SYSTEM = """你是小亮，哥的智囊。現在要為今天的早安圖設計 3 組「圖像主題 + 搭配小語」。

要求：
1. 三組風格各異：一組激勵行動、一組平靜療癒、一組幽默輕鬆
2. 每組包含：
   - theme_en：英文圖像 prompt，20-40 字，具體畫面（場景、主體、光線、氛圍）
   - phrase_zh：繁體中文小語，15-30 字，正向、溫暖、積極，不空泛說教
3. 禁止 emoji、markdown、表情符號
4. 禁止與「近期歷史」中的小語重複或意思相近
5. 圖像主題要多樣：日出、自然風景、動物、咖啡、花、窗景、都市晨光、書、食物等等，不要每天都同一類

嚴格只輸出 JSON 陣列，格式：
[
  {"theme_en": "...", "phrase_zh": "..."},
  {"theme_en": "...", "phrase_zh": "..."},
  {"theme_en": "...", "phrase_zh": "..."}
]
不要任何解釋、不要 markdown code block、不要其他文字。"""


def _load_history() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(history: list[dict]):
    cutoff = datetime.now() - timedelta(days=HISTORY_DAYS)
    history = [h for h in history if datetime.fromisoformat(h["date"]) > cutoff]
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def plan_morning(api_key: str, recent: list[str]) -> list[dict]:
    """呼叫 Claude 產 3 組 {theme_en, phrase_zh}。"""
    client = anthropic.Anthropic(api_key=api_key)
    recent_text = "\n".join(f"- {g}" for g in recent[-30:]) if recent else "（無）"
    user_msg = f"近期歷史小語（避免重複或意思相近）：\n{recent_text}\n\n請產生今天的三組早安內容。"
    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=PLAN_SYSTEM,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = resp.content[0].text.strip()
    # 容錯：抽出第一個 [...] JSON 區塊
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"Claude 回覆非 JSON：{text[:200]}")
    data = json.loads(m.group(0))
    if not isinstance(data, list) or len(data) < 3:
        raise RuntimeError(f"Claude 回覆結構異常：{text[:200]}")
    for item in data[:3]:
        if "theme_en" not in item or "phrase_zh" not in item:
            raise RuntimeError(f"Claude 回覆缺欄位：{item}")
    return data[:3]


def send_morning(
    line_token: str,
    admin_id: str,
    gemini_key: str,
    anthropic_key: str,
    public_base_url: str,
) -> dict:
    """產生並推播三張早安圖 + 小語。回傳統計資訊。"""
    cleanup_old_images()
    history = _load_history()
    recent_phrases = [h["phrase"] for h in history]

    plan = plan_morning(anthropic_key, recent_phrases)

    base = public_base_url.rstrip("/")
    messages = []
    themes_used = []
    for item in plan:
        theme = item["theme_en"]
        phrase = item["phrase_zh"]
        orig_name, prev_name = generate_image(theme, gemini_key)
        messages.append(ImageMessage(
            original_content_url=f"{base}/img/{orig_name}",
            preview_image_url=f"{base}/img/{prev_name}",
        ))
        messages.append(TextMessage(text=phrase))
        themes_used.append(theme)

    configuration = Configuration(access_token=line_token)
    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        # LINE push 單次最多 5 則，6 則拆兩批
        api.push_message(PushMessageRequest(to=admin_id, messages=messages[:5]))
        if len(messages) > 5:
            api.push_message(PushMessageRequest(to=admin_id, messages=messages[5:]))

    today = datetime.now().isoformat()
    for item in plan:
        history.append({"date": today, "phrase": item["phrase_zh"], "theme": item["theme_en"]})
    _save_history(history)

    return {
        "sent": len(plan),
        "themes": themes_used,
        "greetings": [item["phrase_zh"] for item in plan],
    }
