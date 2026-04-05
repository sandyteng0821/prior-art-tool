import sys
from dotenv import load_dotenv  # 新增
load_dotenv()                 # 新增

import json
from modules.patent_store import get_by_id
from modules.llm_analyzer import analyze_patent, USE_LLM
from config import TARGET_PRODUCT

def test_specific_patent(patent_id):
    print(f"🔍 正在從本地 DB 讀取專利: {patent_id}")
    patent = get_by_id(patent_id)
    
    if not patent:
        print(f"❌ 找不到專利 {patent_id}，請確認該 ID 已在 patents.db 中。")
        return

    print(f"📄 標題: {patent['title']}")
    print(f"📏 Claims 長度: {len(patent['claims'])} 字元")
    print(f"⚙️ 分析模式: {'LLM' if USE_LLM else '規則評分'}")
    print("-" * 40)

    # 執行分析 (傳入 list 因為模組收 list)
    try:
        res = analyze_patent(patent) # 它回傳的是 dict
        # 直接檢查是不是 dict，如果不是才拿 [0]
        if isinstance(res, list):
            res = results[0]

        print("\n✅ 分析結果：")
        print(f"🚩 FTO Risk: {res.get('fto_risk')}")
        print(f"🎯 Claim Scope: {res.get('claim_scope')}")
        print(f"💡 Gap Opportunity: {res.get('gap_opportunity')}")
        print(f"📝 Reasoning: {res.get('reasoning')}")
        
        # 檢查是否欄位齊全
        if not res.get('claim_scope') or res.get('claim_scope') == "分析失敗":
            print("\n⚠️ 警訊：解析結果不完整，請檢查 llm_analyzer.py 的 max_tokens 與 Prompt。")

    except Exception as e:
        print(f"\n💥 分析過程發生錯誤: {e}")

if __name__ == "__main__":
    # 你可以從 CLI 傳入 ID，或是直接改下面的預設值
    target_id = sys.argv[1] if len(sys.argv) > 1 else "US2023235331A1"
    test_specific_patent(target_id)