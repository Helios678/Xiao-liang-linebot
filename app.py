"""
小亮 LINE Bot — 主程式
"""

import os
import logging
from dotenv import load_dotenv
from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from conversation import ConversationManager
from claude_client import ClaudeClient

# ── 載入環境變數 ────────────────────────────────────────────────────────────────
load_dotenv()

ANTHROPIC_API_KEY      = os.environ["ANTHROPIC_API_KEY"]
LINE_CHANNEL_SECRET    = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]

# ── 初始化 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
app = Flask(__name__)
app.logger.info(f"[STARTUP] SECRET len={len(LINE_CHANNEL_SECRET)} first4={LINE_CHANNEL_SECRET[:4]}")

handler        = WebhookHandler(LINE_CHANNEL_SECRET)
configuration  = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
conversations  = ConversationManager()
claude         = ClaudeClient(ANTHROPIC_API_KEY)


# ── Webhook 端點 ────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        app.logger.error(f"[SIG FAIL] body_len={len(body)} sig={signature[:20]}...")
        abort(400)

    return "OK"


# ── 處理文字訊息 ────────────────────────────────────────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event: MessageEvent):
    user_id = event.source.user_id
    user_text = event.message.text.strip()

    # 清除對話指令
    if user_text in ("/reset", "清除對話", "重置"):
        conversations.clear(user_id)
        reply = "對話記憶已清除，我們重新開始。"
    else:
        history = conversations.get(user_id)
        reply = claude.chat(history, user_text)
        conversations.add(user_id, "user", user_text)
        conversations.add(user_id, "assistant", reply)

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)],
            )
        )


# ── 健康檢查 ────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return "小亮在線 ✓"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
