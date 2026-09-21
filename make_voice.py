import os
import json
import re
import requests
import pandas as pd

# 実行ディレクトリをスクリプトのある場所へ移動
os.chdir(os.path.dirname(os.path.abspath(__file__)))

VOICEVOX_URL = "http://localhost:50021"
SPEAKER_ID = 2

# ベースとなる出力先ディレクトリ
BASE_DIR = os.path.join("static", "audio_output")

try:
    df = pd.read_csv("司法書士過去問集CSV.csv", encoding="utf-8", header=1)
except UnicodeDecodeError:
    df = pd.read_csv("司法書士過去問集CSV.csv", encoding="cp932", header=1)

def save_voicevox_audio(text, speaker_id, file_path):
    """VOICEVOXから音声を取得して保存する関数"""
    res1 = requests.post(
        f"{VOICEVOX_URL}/audio_query",
        params={"text": text, "speaker": speaker_id}
    )
    if res1.status_code != 200:
        print(f"エラー: クエリ作成失敗 {text[:10]}...")
        return False
    
    query = res1.json()
    
    res2 = requests.post(
        f"{VOICEVOX_URL}/synthesis",
        headers={"Content-Type": "application/json"},
        params={"speaker": speaker_id},
        data=json.dumps(query)
    )
    
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

    # 1. 問題番号から「平成◯年度」「令和◯年度」などを抽出
    match = re.search(r'((?:平成|令和)[0-9一-九元]+年度)', q_no)
    if match:
        nendo = match.group(1)
    else:
        # CSV内に別途「年度」列がある場合のフォールバック
        nendo = str(row.get("年度", "")).strip() or "その他"

    # 出力先フォルダパスの設定
    output_dir = os.path.join(BASE_DIR, nendo)

    # 2. 正誤の変換
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
    
    q_path = os.path.join(output_dir, f"Q_{file_id}.wav")
    a_path = os.path.join(output_dir, f"A_{file_id}.wav")

    # 3. すでに問題ファイルと解答ファイルが両方存在する場合はスキップ
    if os.path.exists(q_path) and os.path.exists(a_path):
        print(f"スキップ（作成済み）: {nendo} / {file_id}")
        continue

    # 存在しない場合のみフォルダを作成して音声生成
    os.makedirs(output_dir, exist_ok=True)

    # 問題ファイルの生成
    if not os.path.exists(q_path):
        question_text = f"問題。{text}。"
        if save_voicevox_audio(question_text, SPEAKER_ID, q_path):
            print(f"生成完了: {q_path}")

    # 解答・解説ファイルの生成
    if not os.path.exists(a_path):
        answer_text = f"答えは、{correctness_read}。"
        if explanation and not pd.isna(row.get("簡単な解説")):
            answer_text += f"解説。{explanation}"
        if save_voicevox_audio(answer_text, SPEAKER_ID, a_path):
            print(f"生成完了: {a_path}")

print("すべての処理が完了しました。")