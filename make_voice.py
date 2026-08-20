import os
import json
import re
import requests
import pandas as pd

# ポート番号 5021 を追加
VOICEVOX_URL = "http://localhost:5021"
SPEAKER_ID = 2

os.makedirs("audio_output", exist_ok=True)

# 文字コードエラーを防ぐための読み込み処理
try:
    df = pd.read_csv("司法書士過去問集CSV.csv", encoding="utf-8")
except UnicodeDecodeError:
    df = pd.read_csv("司法書士過去問集CSV.csv", encoding="cp932")

for index, row in df.iterrows():
    q_no = str(row.get("問題番号", "")).strip()
    limb = str(row.get("肢", "")).strip()
    text = str(row.get("文章", "")).strip()
    
    if not text or pd.isna(row.get("文章")):
        continue

    # ファイル名に使えない記号を置換してIDを作成（例: R05_1_ア）
    clean_q_no = re.sub(r'[\\/:*?"<>|]', '_', q_no)
    clean_limb = re.sub(r'[\\/:*?"<>|]', '_', limb)
    file_id = f"{clean_q_no}_{clean_limb}"
    
    file_path = f"audio_output/{file_id}.wav"
    
    # すでに音声ファイルが存在する場合はスキップ（新規追加分のみ処理）
    if os.path.exists(file_path):
        print(f"スキップ（生成済み）: {file_id}")
        continue

    # 1. 音声合成クエリ作成
    res1 = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": SPEAKER_ID}
    )
    
    if res1.status_code == 200:
        query = res1.json()
        
        # 2. 音声波形生成
        res2 = requests.post(
            f"{VOICEVOX_URL}/synthesis",
            headers={"Content-Type": "application/json"},
            params={"speaker": SPEAKER_ID},
            data=json.dumps(query)
        )
        
        # 3. 保存
        with open(file_path, "wb") as f:
            f.write(res2.content)
            
        print(f"新規生成完了: {file_id}")