"""
Nano Banana（Gemini 2.5 Flash Image）圖像生成 + 本地暫存
"""

import os
import io
import time
import uuid

from google import genai
from PIL import Image

IMAGE_DIR = os.path.join(os.path.dirname(__file__), "morning_images")
os.makedirs(IMAGE_DIR, exist_ok=True)

MODEL = "gemini-2.5-flash-image"

STYLE_SUFFIX = (
    ", warm soft morning light, uplifting positive mood, "
    "cinematic composition, photorealistic, high detail, "
    "peaceful atmosphere, vibrant but gentle colors, "
    "landscape orientation, no text, no watermark"
)


def generate_image(prompt_en: str, api_key: str) -> tuple[str, str]:
    """呼叫 Nano Banana 產圖，同時產一張 preview 縮圖。
    回傳 (original_filename, preview_filename)。"""
    client = genai.Client(api_key=api_key)
    full_prompt = f"{prompt_en}{STYLE_SUFFIX}"
    resp = client.models.generate_content(
        model=MODEL,
        contents=full_prompt,
    )
    for part in resp.candidates[0].content.parts:
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            stem = uuid.uuid4().hex
            # 原圖：轉 JPEG 壓縮，避免超過 LINE 10MB 上限
            img = Image.open(io.BytesIO(inline.data)).convert("RGB")
            orig_name = f"{stem}.jpg"
            orig_path = os.path.join(IMAGE_DIR, orig_name)
            img.save(orig_path, "JPEG", quality=88, optimize=True)
            # 預覽圖：縮到 240px 寬，低畫質
            prev = img.copy()
            prev.thumbnail((240, 240))
            prev_name = f"{stem}_p.jpg"
            prev_path = os.path.join(IMAGE_DIR, prev_name)
            prev.save(prev_path, "JPEG", quality=70, optimize=True)
            return orig_name, prev_name
    raise RuntimeError("Gemini 未回傳圖片資料")


def cleanup_old_images(keep_hours: int = 48):
    """清掉超過 keep_hours 的舊圖，避免容器磁碟累積。"""
    now = time.time()
    cutoff = keep_hours * 3600
    for name in os.listdir(IMAGE_DIR):
        path = os.path.join(IMAGE_DIR, name)
        try:
            if os.path.isfile(path) and now - os.path.getmtime(path) > cutoff:
                os.remove(path)
        except Exception:
            pass
