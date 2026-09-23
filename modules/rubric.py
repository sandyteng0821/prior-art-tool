# modules/rubric.py
# Rubric 檔載入 + {TARGET_*} 佔位展開（main.py 與 analyze_jsonl 共用單一來源）。

from pathlib import Path


def load_rubric(path: str, cfg) -> str:
    """讀 rubric 檔並展開 {TARGET_*} 佔位。

    用純字串 .replace（非 str.format）：rubric 內其他 {} 不應被當佔位。
    cfg 需有 TARGET_DRUG / TARGET_ROUTE / TARGET_INDICATION 屬性
    （config module 物件或動態載入的 config 皆可）。
    """
    text = Path(path).read_text(encoding="utf-8")
    for key in ("TARGET_DRUG", "TARGET_ROUTE", "TARGET_INDICATION"):
        text = text.replace("{" + key + "}", getattr(cfg, key))
    return text
    