import os
import json
import re
import requests
import pandas as pd

VOICEVOX_URL = "http://localhost:50021"
SPEAKER_ID = 2

os.makedirs("audio_output", exist_ok=True)

try:
    df = pd.read_csv("司法書士過去問集CSV.csv", encoding="utf-8", header=1)
except UnicodeDecodeError:
    df = pd.read_csv("司法書士過去問集CSV.csv", encoding="cp932", header=1)

def save_voicevox_audio(text, speaker_id, file_path):
    """VOICEVOXから音声を取得して保存する関数"""
    # 1. 音声合成クエリ作成
    res1 = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": speaker_id}
    )
    if res1.status_code != 200:
        print(f"エラー: クエリ作成失敗 {text[:10]}...")
        return False
    
    query = res1.json()
    
    # 2. 音声波形生成
    res2 = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        headers={"Content-Type": "application/json"},
        params={"speaker": speaker_id},
        data=json.dumps(query)
    )
    
    # 3. 保存
    with open(file_path, "wb") as f:
        f.write(res2.content)
    return True

for index, row in df.iterrows():
    q_no = str(row.get("問題番号", "")).strip()
    limb = str(row.get("肢", "")).strip()
    text = str(row.get("文章", "")).strip()
    correctness = str(row.get("正誤", "")).strip()
    explanation = str(row.get("簡単な解説", "")).strip()
    
    if not text or pd.isna(row.get("文章")):
        continue

    # 正誤の変換
    if correctness == "○":
        correctness_read = "まる"
    elif correctness == "×":
        correctness_read = "ばつ"
    else:
        correctness_read = correctness

    # ファイル名用ID（記号置換）
    clean_q_no = re.sub(r'[\\/:*?"<>|]', '_', q_no)
    clean_limb = re.sub(r'[\\/:*?"<>|]', '_', limb)
    file_id = f"{clean_q_no}_{clean_limb}"
    
    # 1. 問題ファイルの生成
    q_path = f"audio_output/Q_{file_id}.wav"
    if not os.path.exists(q_path):
        question_text = f"問題。{text}。"
        if save_voicevox_audio(question_text, SPEAKER_ID, q_path):
            print(f"生成完了: {q_path}")

    # 2. 解答・解説ファイルの生成
    a_path = f"audio_output/A_{file_id}.wav"
    if not os.path.exists(a_path):
        answer_text = f"答えは、{correctness_read}。"
        if explanation and not pd.isna(row.get("簡単な解説")):
            answer_text += f"解説。{explanation}"
        if save_voicevox_audio(answer_text, SPEAKER_ID, a_path):
            print(f"生成完了: {a_path}")

print("すべての処理が完了しました。")