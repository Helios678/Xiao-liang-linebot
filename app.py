"""
小亮 LINE Bot — 主程式
功能：群組模式、股票查詢、家人記憶、Rate Limiting
"""

import os
import re
import time
import threading
from collections import defaultdict, deque
from dotenv import load_dotenv
from flask import Flask, request, abort

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, PushMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import (
    MessageEvent, TextMessageContent,
    ImageMessageContent, AudioMessageContent,
    StickerMessageContent, VideoMessageContent, FileMessageContent,
    JoinEvent,
)

from conversation import ConversationManager
from claude_client import ClaudeClient, COST_ALERT_USD
from stock_client import query_stock, query_news
from memory_manager import MemoryManager
from portfolio_client import get_portfolio_summary

# ── 載入環境變數 ────────────────────────────────────────────────────────────────
load_dotenv()

ANTHROPIC_API_KEY         = os.environ["ANTHROPIC_API_KEY"]
LINE_CHANNEL_SECRET       = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
ADMIN_USER_ID             = os.environ.get("ADMIN_USER_ID", "")

# ── 初始化 ──────────────────────────────────────────────────────────────────────
app           = Flask(__name__)
handler       = WebhookHandler(LINE_CHANNEL_SECRET)
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
conversations = ConversationManager()
claude        = ClaudeClient(ANTHROPIC_API_KEY)
memory        = MemoryManager()

# ── Rate Limiting（每人每60秒最多5則，哥豁免）────────────────────────────────
_rate: dict[str, deque] = defaultdict(deque)

def _rate_ok(user_id: str) -> bool:
    now = time.time()
    q = _rate[user_id]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= 5:
        return False
    q.append(now)
    return True

# ── 等待姓名輸入的用戶（user_id → 暫存的第一則問題）──────────────────────────────
_pending_intro: dict[str, str] = {}

# ── 高耗能請求待審佇列 ───────────────────────────────────────────────────────────
# [{user_id, source_id, text, name}]
_pending: list[dict] = []

# ── 常數 ────────────────────────────────────────────────────────────────────────
TRIGGER       = re.compile(r"^(@小亮|小亮)\s*", re.IGNORECASE)
STOCK_RE      = re.compile(r"^查\s*(\S+)")
NEWS_RE       = re.compile(r"^(?:查新聞|新聞)\s*(\d{4,6})")
PRIVACY_KW    = ["感情", "戀愛", "外遇", "病情", "診斷", "收入", "薪水", "存款", "債務"]
FINANCE_KW    = ["持倉", "損益", "投資組合", "我的股票", "我的持股"]
PORTFOLIO_KW  = ["持倉", "損益", "查持倉", "投資組合"]
SEARCH_KW     = ["搜尋", "上網查", "上網找", "查最新", "最新消息", "最新新聞", "幫我搜"]
TIMELINE_KW   = ["剛剛", "最近誰", "誰說過", "誰跟你說", "對話記錄", "發言記錄", "時間軸", "誰發言", "說了什麼"]

HELP_TEXT = (
    "我是小亮，以下是我能做的事：\n"
    "📌 一般問答：問什麼都可以\n"
    "📈 股票查詢：小亮 查2330 / 小亮 查台積電\n"
    "📰 公司新聞：小亮 查新聞 2330\n"
    "💼 持倉損益：小亮 持倉（僅哥）\n"
    "🧠 記住事情：小亮 記住：...（限 50 字）\n"
    "🔄 清除對話：小亮 重置\n"
    "📋 查看記憶：小亮 查看記憶（僅哥）\n"
    "✏️ 修改記憶：小亮 修改記憶 <編號> <新內容>（僅哥）\n"
    "🗑️ 刪除記憶：小亮 刪除記憶 <編號>（僅哥）\n\n"
    "私人問題建議私訊給我，群組裡不討論個人隱私。"
)

JOIN_TEXT = (
    "大家好！我是小亮，哥的智囊助手。\n"
    "叫我的方式：訊息開頭加 @小亮 或直接打「小亮」\n"
    "想知道我能做什麼，輸入：小亮 幫助"
)

# ── 工具函式 ────────────────────────────────────────────────────────────────────
def is_admin(user_id: str) -> bool:
    return bool(ADMIN_USER_ID) and user_id == ADMIN_USER_ID

def is_group(event) -> bool:
    return getattr(event.source, "type", "") in ("group", "room")

def split_reply(text: str) -> list[str]:
    if len(text) <= 4500:
        return [text]
    parts = []
    while len(text) > 4500:
        parts.append(text[:4500] + "（續）")
        text = text[4500:]
    parts.append(text)
    return parts

def send_reply(reply_token: str, text: str, fallback_to: str = ""):
    """發送 reply message；若 reply_token 已過期則改用 push message 補送。"""
    parts = split_reply(text)[:5]
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[TextMessage(text=p) for p in parts],
                )
            )
    except Exception as e:
        print(f"[WARN] reply_message failed ({e}), fallback push to {fallback_to!r}")
        if fallback_to:
            push_msg(fallback_to, text)

def push_msg(to: str, text: str):
    try:
        with ApiClient(configuration) as api_client:
            MessagingApi(api_client).push_message(
                PushMessageRequest(to=to, messages=[TextMessage(text=text)])
            )
        print(f"[INFO] push_msg OK → {to[:12]}")
    except Exception as e:
        print(f"[ERROR] push_msg FAILED → {to[:12]} | {e}")

# ── Webhook 端點 ────────────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

# ── Bot 加入群組 ─────────────────────────────────────────────────────────────────
@handler.add(JoinEvent)
def handle_join(event: JoinEvent):
    send_reply(event.reply_token, JOIN_TEXT)

# ── 非文字訊息 ───────────────────────────────────────────────────────────────────
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event: MessageEvent):
    if not is_group(event):
        user_id = event.source.user_id
        send_reply(event.reply_token, "小亮目前無法看圖，請用文字描述。", fallback_to=user_id)

@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio(event: MessageEvent):
    if not is_group(event):
        user_id = event.source.user_id
        send_reply(event.reply_token, "小亮無法處理語音，請打字。", fallback_to=user_id)

@handler.add(MessageEvent, message=StickerMessageContent)
def handle_sticker(event: MessageEvent):
    pass

@handler.add(MessageEvent, message=VideoMessageContent)
def handle_video(event: MessageEvent):
    pass

@handler.add(MessageEvent, message=FileMessageContent)
def handle_file(event: MessageEvent):
    pass

# ── 文字訊息（核心）────────────────────────────────────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event: MessageEvent):
    user_id   = event.source.user_id
    raw_text  = event.message.text.strip()
    in_group  = is_group(event)
    source_id = (getattr(event.source, "group_id", None)
                 or getattr(event.source, "room_id", None)
                 or user_id)

    # reply token 過期時的 fallback 目標（群組推給群組，私訊推給個人）
    def reply(text: str):
        send_reply(event.reply_token, text, fallback_to=source_id)

    # 群組模式：只回應觸發詞開頭；私訊模式：有觸發詞則剝離，沒有則全文使用
    # 例外：用戶正在等待輸入姓名時，允許不帶觸發詞的回覆
    if in_group:
        m = TRIGGER.match(raw_text)
        if not m:
            if user_id not in _pending_intro:
                return
            user_text = raw_text.strip()
            if not user_text:
                return
        else:
            user_text = raw_text[m.end():].strip()
            if not user_text:
                return
    else:
        m = TRIGGER.match(raw_text)
        user_text = raw_text[m.end():].strip() if m else raw_text
        if not user_text:
            user_text = raw_text

    # Rate Limiting（哥豁免）
    if not is_admin(user_id) and not _rate_ok(user_id):
        reply("小亮需要休息一下，請 60 秒後再試。")
        return

    # 群組時間軸自動記錄（rate limit 通過後才記）
    if in_group:
        _name, _ = memory.get_member_by_id(user_id)
        _display = _name or ("哥" if is_admin(user_id) else user_id[:8])
        memory.add_group_message(_display, user_text)

    # /myid — 查自己的 LINE userId（設定 ADMIN_USER_ID 用）
    if user_text == "/myid":
        reply(f"你的 LINE userId：\n{user_id}")
        return

    # /模擬 <訊息> — 哥限定，以一般用戶身份測試審核流程
    simulate_user = False
    if is_admin(user_id) and user_text.startswith("/模擬 "):
        user_text = user_text[4:].strip()
        simulate_user = True
        if not user_text:
            reply("用法：/模擬 <要測試的訊息>")
            return

    # 清除對話
    if user_text in ("/reset", "清除對話", "重置"):
        conversations.clear(user_id)
        reply("對話記憶已清除，我們重新開始。")
        return

    # 幫助
    if user_text in ("幫助", "help", "Help", "說明"):
        reply(HELP_TEXT)
        return

    # Token 用量（哥限定）
    if user_text == "token用量":
        if not is_admin(user_id):
            reply("這個指令只有哥可以使用。")
            return
        reply(claude.get_stats())
        return

    # 查看記憶（哥限定）
    if user_text == "查看記憶":
        if not is_admin(user_id):
            reply("這個指令只有哥可以使用。")
            return
        reply(memory.get_all_summary())
        return

    # 持倉損益（哥限定，群組中拒絕）
    if any(user_text.startswith(kw) for kw in PORTFOLIO_KW):
        if in_group:
            reply("個人財務資訊不在群組討論，請私訊給我。")
            return
        if not is_admin(user_id):
            reply("持倉資訊只有哥可以查詢。")
            return
        reply(get_portfolio_summary())
        return

    # 高耗能請求審核：哥同意/拒絕
    if is_admin(user_id) and user_text in ("同意", "執行"):
        if not _pending:
            reply("目前沒有待審請求。")
            return
        req = _pending.pop(0)
        reply(f"執行中，稍後回覆 {req['name']}。")
        history = conversations.get(req["user_id"])
        req_search = any(kw in req["text"] for kw in SEARCH_KW)
        result = claude.chat(history, req["text"], enable_search=req_search)
        conversations.add(req["user_id"], "user", req["text"])
        conversations.add(req["user_id"], "assistant", result)
        push_msg(req["user_id"], f"哥已同意，以下是回覆：\n\n{result}")
        return

    if is_admin(user_id) and user_text == "拒絕":
        if not _pending:
            reply("目前沒有待審請求。")
            return
        req = _pending.pop(0)
        reply(f"已拒絕 {req['name']} 的請求。")
        push_msg(req["user_id"], "哥考量後決定不處理這個請求，有其他問題歡迎再問。")
        return

    if is_admin(user_id) and user_text == "待審請求":
        if not _pending:
            reply("目前沒有待審請求。")
        else:
            lines = [f"待審請求（共 {len(_pending)} 筆）："]
            for i, r in enumerate(_pending, 1):
                lines.append(f"{i}. {r['name']}：{r['text'][:50]}")
            reply("\n".join(lines))
        return

    # 公司新聞查詢
    nm = NEWS_RE.match(user_text)
    if nm:
        reply(query_news(nm.group(1)))
        return

    # 記住指令（限 50 字）
    if user_text.startswith("記住：") or user_text.startswith("記住:"):
        content = user_text[3:].strip()
        if len(content) > 50:
            reply(f"內容太長（{len(content)} 字），請精簡在 50 字以內。")
            return
        name, _ = memory.get_member_by_id(user_id)
        recorder = "哥" if is_admin(user_id) else (name or user_id[:8])
        memory.add_event(content, recorder)
        reply(f"好的，已記住：{content}")
        return

    # 修改記憶（哥限定）：修改記憶 <編號> <新內容>
    if user_text.startswith("修改記憶") and is_admin(user_id):
        parts = user_text[4:].strip().split(None, 1)
        if len(parts) != 2 or not parts[0].isdigit():
            reply("格式：修改記憶 <編號> <新內容>\n例如：修改記憶 3 今天吃火鍋")
            return
        idx, new_content = int(parts[0]), parts[1].strip()
        if len(new_content) > 50:
            reply(f"新內容太長（{len(new_content)} 字），請精簡在 50 字以內。")
            return
        if memory.edit_event(idx, new_content):
            reply(f"第 {idx} 筆已更新為：{new_content}")
        else:
            reply(f"找不到第 {idx} 筆，請先用「查看記憶」確認編號。")
        return

    # 刪除記憶（哥限定）：刪除記憶 <編號>
    if user_text.startswith("刪除記憶") and is_admin(user_id):
        idx_str = user_text[4:].strip()
        if not idx_str.isdigit():
            reply("格式：刪除記憶 <編號>\n例如：刪除記憶 3")
            return
        idx = int(idx_str)
        if memory.delete_event(idx):
            reply(f"第 {idx} 筆已刪除。")
        else:
            reply(f"找不到第 {idx} 筆，請先用「查看記憶」確認編號。")
        return

    # ── 自介姓名解析工具 ────────────────────────────────────────────────────────────
    def _extract_name(text: str) -> str | None:
        """從文字中抽取姓名；支援「我叫XXX」「我是XXX」，否則回傳 None。"""
        for prefix in ("我叫", "我是"):
            if text.startswith(prefix):
                n = text[len(prefix):].strip()
                return n if n else None
        return None

    # 新成員姓名流程（優先檢查，避免被下方新用戶檢查覆蓋）
    if user_id in _pending_intro and not is_admin(user_id):
        name = _extract_name(user_text) or user_text.strip()
        memory.register_member(user_id, name)
        original_question = _pending_intro.pop(user_id)

        reply(f"好的，{name}！很高興認識你，我來回答你剛才的問題。")

        def _answer_pending():
            try:
                ctx = [f"【用戶背景】\n用戶稱呼：{name}（剛完成自我介紹）"]
                history = conversations.get(user_id)
                result = claude.chat(history, original_question, "\n\n".join(ctx))
                conversations.add(user_id, "user", original_question)
                conversations.add(user_id, "assistant", result)
                push_msg(source_id, result)
            except Exception as e:
                print(f"[ERROR] _answer_pending failed: {e}")
                push_msg(source_id, f"小亮處理時出錯了：{e}")

        threading.Thread(target=_answer_pending, daemon=True).start()
        return

    # 新用戶第一次發言（非哥）
    if not is_admin(user_id) and memory.is_new_user(user_id):
        # 若第一則訊息本身就是自介格式，直接登記不需暫存
        intro_name = _extract_name(user_text)
        if intro_name:
            memory.register_member(user_id, intro_name)
            reply(f"好的，{intro_name}！很高興認識你，有什麼需要幫忙的嗎？")
        else:
            _pending_intro[user_id] = user_text
            reply(
                "你好，我是小亮！\n"
                f"你的問題我記下了：「{user_text[:40]}{'...' if len(user_text) > 40 else ''}」\n\n"
                "請先告訴我你的稱呼，方便我更新記憶、提供更好的服務。\n"
                "格式：直接輸入名字，例如「我叫小明」或「Paul」"
            )
        return

    # 隱私引導（群組中）
    if in_group and not is_admin(user_id) and any(kw in user_text for kw in PRIVACY_KW):
        reply("這個問題比較私人，建議私訊給我，群組裡不方便討論。")
        return

    # 持倉查詢（群組中一律拒絕）
    if in_group and any(kw in user_text for kw in FINANCE_KW):
        reply("個人財務資訊不在群組討論，請私訊給我。")
        return

    # 高耗能請求審核（非哥，或模擬模式）：用 Haiku 預判意圖
    if (not is_admin(user_id) or simulate_user) and claude.is_high_cost_intent(user_text):
        member_name, _ = memory.get_member_by_id(user_id)
        display_name = member_name or user_id[:8]
        _pending.append({"user_id": user_id, "name": display_name, "text": user_text})
        reply("這個請求需要消耗較多資源，已通知哥確認，請稍候。")
        if ADMIN_USER_ID:
            push_msg(
                ADMIN_USER_ID,
                f"收到 {display_name} 的高耗能請求：\n\n{user_text}\n\n回覆「同意」執行，「拒絕」則婉拒。"
            )
        return

    # 股票查詢
    stock_info = ""
    sm = STOCK_RE.match(user_text)
    if sm:
        stock_info = query_stock(sm.group(1))

    # 組合 extra_context 給 Claude
    ctx_parts = []
    mem_ctx = memory.get_user_context(user_id)
    if mem_ctx:
        ctx_parts.append(f"【用戶背景】\n{mem_ctx}")
    if is_admin(user_id):
        ctx_parts.append("【注意】這位是哥（管理員），有所有功能權限。")
    if stock_info:
        ctx_parts.append(f"【股票即時資料】\n{stock_info}")
    # 只在被問及群組對話記錄時才附入時間軸
    if in_group and any(kw in user_text for kw in TIMELINE_KW):
        ctx_parts.append(memory.get_group_timeline())

    # 組合給 Claude 的訊息（股票資料直接附在訊息中）
    enhanced = user_text
    if stock_info:
        enhanced = f"{user_text}\n\n[即時股票資料]\n{stock_info}"

    # 判斷是否需要啟用網路搜尋
    enable_search = any(kw in user_text for kw in SEARCH_KW)

    # 費用估算審核（非哥，或模擬模式，預估超過 COST_ALERT_USD 需確認）
    if not is_admin(user_id) or simulate_user:
        est_cost = claude.estimate_cost_for_request(
            conversations.get(user_id), enhanced, "\n\n".join(ctx_parts)
        )
        if est_cost > COST_ALERT_USD:
            member_name, _ = memory.get_member_by_id(user_id)
            display_name = member_name or user_id[:8]
            _pending.append({"user_id": user_id, "name": display_name, "text": user_text})
            reply(f"這個請求預估費用較高（約 ${est_cost:.2f} USD），已通知哥確認，請稍候。")
            if ADMIN_USER_ID:
                push_msg(
                    ADMIN_USER_ID,
                    f"收到 {display_name} 的高費用請求（預估 ${est_cost:.2f} USD）：\n\n{user_text[:200]}\n\n回覆「同意」執行，「拒絕」則婉拒。"
                )
            return

    # Claude 呼叫在背景執行緒跑，避免佔住 Webhook 回應時間
    # reply_token 可能在 Claude 回來前過期，直接用 push_msg 送出
    def _call_claude():
        print(f"[INFO] _call_claude start | source={source_id[:12]} user={user_id[:12]}")
        try:
            history = conversations.get(user_id)
            result  = claude.chat(history, enhanced, "\n\n".join(ctx_parts), enable_search=enable_search)
            conversations.add(user_id, "user", user_text)
            conversations.add(user_id, "assistant", result)
            push_msg(source_id, result)
        except Exception as e:
            print(f"[ERROR] _call_claude failed: {e}")
            push_msg(source_id, f"小亮處理時出錯了：{e}")

    threading.Thread(target=_call_claude, daemon=True).start()

# ── 健康檢查 ────────────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return "小亮在線 ✓"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
