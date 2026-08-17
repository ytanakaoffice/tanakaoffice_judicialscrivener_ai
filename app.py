import base64
import json
import os
import random
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import requests
import streamlit as st
from streamlit_clickable_images import clickable_images
from supabase import create_client

# ==========================================
# ページの設定（※必ず他のstコマンドより先に書く！）
# ==========================================
st.set_page_config(
    page_title="田中式 司法書士一問一答｜法律特化AI講師への24時間質問サポート付", 
    page_icon="📖", 
    layout="centered"
)

# ==========================================
# Supabaseクライアントの初期化
# ==========================================
@st.cache_resource
def init_connection():
    url = st.secrets["supabase"]["SUPABASE_URL"]
    key = st.secrets["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# ==========================================
# パスワード認証機能
# ==========================================
def login(email, password):
    try:
        # Supabaseでログインを試行
        user = supabase.auth.sign_in_with_password({
            "email": email,
            "password": password
        })
        return user
    except Exception as e:
        st.error("メールアドレスまたはパスワードが正しくありません。")
        return None

# ログイン画面のUI
if "user" not in st.session_state:
    st.title("ログイン")
    email = st.text_input("メールアドレス")
    password = st.text_input("パスワード", type="password")
    
    if st.button("ログイン"):
        user_info = login(email, password)
        if user_info:
            st.session_state["user"] = user_info
            st.rerun() # 再読み込みしてアプリを表示
            
    # ログインしていない場合はここで処理を止め、メインアプリを表示しない
    st.stop() 

# ---------------------------------------------------------
# ここから下がログイン成功後のメインアプリ処理
# ---------------------------------------------------------

# 画像をBase64形式に変換する関数
def get_image_base64(path):
    try:
        with open(path, "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
    except FileNotFoundError:
        return ""

# ==========================================
# UIブラッシュアップ用カスタムCSS
# ==========================================
st.markdown("""
<style>
    .stApp {
        background-color: #f8f9fa;
    }
    /* カスタム問題文カード */
    .custom-question-card {
        border-radius: 16px;
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        box-shadow: 0 4px 12px rgba(0,0,0,0.05);
        padding: 24px;
        font-size: 1.4rem !important;
        font-weight: 500 !important;
        line-height: 1.8 !important;
        letter-spacing: 0.1em !important;
        color: #1a1a1a !important;
        word-break: break-all;
        margin-bottom: 20px;
    }
    /* スマホ向け調整 */
    @media (max-width: 768px) {
        .custom-question-card {
            font-size: 1.2rem !important;
            padding: 16px;
            line-height: 1.6 !important;
        }
    }
    .streamlit-expanderHeader {
        border-radius: 8px;
        background-color: #ffffff;
    }
</style>
""", unsafe_allow_html=True)

# ==========================================
# API・メール設定
# ==========================================
DIFY_ENDPOINT = "https://api.dify.ai/v1/chat-messages"
DIFY_API_KEY = st.secrets["dify_api_key"]

def call_dify(query, conversation_id=""):
    headers = {
        "Authorization": f"Bearer {DIFY_API_KEY}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",
        "user": "windows-user",
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    try:
        response = requests.post(
            DIFY_ENDPOINT,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        if response.status_code == 200:
            res_data = response.json()
            answer = res_data.get("answer", "回答を取得できませんでした。")
            conv_id = res_data.get("conversation_id", "")
            return answer, conv_id
        else:
            return (
                f"エラーが発生しました (ステータスコード: {response.status_code})"
                f" - {response.text}",
                conversation_id,
            )
    except Exception as e:
        return f"通信エラー: {e}", conversation_id

# メール送信機能
def send_report_email(q_no, reason):
    sender_email = st.secrets["gmail_sender"]
    app_password = st.secrets["gmail_app_password"]
    receiver_email = st.secrets["gmail_receiver"]

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = f"過去問アプリ 法改正・問題修正の報告（{q_no}）"

    body = f"対象の問題番号: {q_no}\n\n【報告内容・根拠】\n{reason}"
    msg.attach(MIMEText(body, 'plain'))

    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(sender_email, app_password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Mail Error: {e}")
        return False

# CSVデータの安全な読み込み
@st.cache_data
def load_data():
    csv_file = "司法書士過去問集CSV.csv"
    df_temp = pd.DataFrame()
    for enc in ["utf-8", "cp932", "shift_jis"]:
        try:
            df_temp = pd.read_csv(csv_file, encoding=enc)
            if "問題番号" not in df_temp.columns and len(df_temp.columns) >= 6:
                df_temp.columns = [
                    "問題番号",
                    "分野",
                    "肢",
                    "文章",
                    "正誤",
                    "簡単な解説",
                    "col7",
                    "col8",
                    "col9",
                ][: len(df_temp.columns)]
            break
        except Exception:
            continue

    if df_temp.empty:
        try:
            df_temp = pd.read_csv(csv_file, header=None, encoding="utf-8")
            df_temp.columns = [
                "問題番号",
                "分野",
                "肢",
                "文章",
                "正誤",
                "簡単な解説",
                "c7",
                "c8",
                "c9",
            ][: len(df_temp.columns)]
        except:
            return pd.DataFrame()

    if "問題番号" in df_temp.columns:
        df_temp = df_temp[
            df_temp["問題番号"].astype(str).str.contains("令和|平成")
        ]

    return df_temp

df = load_data()

# ---------------------------------------------------------
# 状態管理・初期化
# ---------------------------------------------------------
if "inline_messages" not in st.session_state:
    st.session_state.inline_messages = []
if "inline_conv_id" not in st.session_state:
    st.session_state.inline_conv_id = ""
if "chat_count" not in st.session_state:
    st.session_state.chat_count = 0
if "teacher_state" not in st.session_state:
    st.session_state.teacher_state = "normal"
if "inline_waiting" not in st.session_state:
    st.session_state.inline_waiting = False
if "main_waiting" not in st.session_state:
    st.session_state.main_waiting = False

# 年度別の正答率用変数
if "y_correct_count" not in st.session_state:
    st.session_state.y_correct_count = 0
if "y_total_count" not in st.session_state:
    st.session_state.y_total_count = 0

# 科目別の正答率用変数
if "c_correct_count" not in st.session_state:
    st.session_state.c_correct_count = 0
if "c_total_count" not in st.session_state:
    st.session_state.c_total_count = 0

def reset_inline_chat():
    st.session_state.inline_messages = []
    st.session_state.inline_conv_id = ""
    st.session_state.inline_waiting = False

# ---------------------------------------------------------
# AIたなかっち1号先生アバター表示関数
# ---------------------------------------------------------
def render_ai_teacher():
    image_map = {
        "normal": "images/1_teacher_normal.png",
        "thinking": "images/1_teacher_thinking.png",
        "happy": "images/1_teacher_happy.png",
        "sad": "images/1_teacher_sad.png",
    }
    current_state = st.session_state.get("teacher_state", "normal")
    img_path = image_map.get(current_state, image_map["normal"])
    
    with st.sidebar:
        st.markdown("### AIたなかっち1号先生")
        if os.path.exists(img_path):
            st.image(img_path, use_container_width=True)
        else:
            st.info(f"画像が見つかりません: {img_path}")
        st.markdown("---")

# ---------------------------------------------------------
# 問題ごとのインラインチャットUI
# ---------------------------------------------------------
def render_inline_chat(row):
    st.markdown("---")
    st.markdown("### この問題についてAIに質問する")
    
    if st.session_state.chat_count >= 30:
        st.warning("本日のラリー制限（30回）に達しました。明日またお越しください！")
        return

    for msg in st.session_state.inline_messages:
        avatar_img = "images/1_teacher_normal.png" if msg["role"] == "assistant" else None
        with st.chat_message(msg["role"], avatar=avatar_img):
            st.markdown(msg["content"])
            
    prompt = st.chat_input("この問題の解説で分からない部分を聞いてみましょう", key="inline_chat_input_field")
    if prompt:
        st.session_state.chat_count += 1
        st.session_state.inline_messages.append({"role": "user", "content": prompt})
        st.session_state.teacher_state = "thinking"
        st.session_state.inline_waiting = True
        st.rerun()

    if st.session_state.inline_waiting:
        st.session_state.inline_waiting = False
        with st.chat_message("assistant", avatar="images/1_teacher_normal.png"):
            status_text = "AIたなかっち1号先生が考えています..."
            last_prompt = st.session_state.inline_messages[-1]["content"]
            if len(last_prompt) < 10:
                status_text = "（軽量モードで処理中...）"
                
            with st.spinner(status_text):
                if not st.session_state.inline_conv_id:
                    q_text = row.get('文章', '')
                    a_text = row.get('簡単な解説', '解説がありません')
                    api_prompt = f"以下の問題と解説について質問です。\n\n【問題】\n{q_text}\n\n【解説】\n{a_text}\n\n【ユーザーの質問】\n{last_prompt}"
                else:
                    api_prompt = last_prompt
                    
                response_text, new_conv_id = call_dify(api_prompt, st.session_state.inline_conv_id)
                if new_conv_id:
                    st.session_state.inline_conv_id = new_conv_id
                
                st.session_state.teacher_state = "normal"
                st.markdown(response_text)
                st.caption(f"（本日の残り: {30 - st.session_state.chat_count}回）")
                
        st.session_state.inline_messages.append({"role": "assistant", "content": response_text})
        st.rerun()

# ---------------------------------------------------------
# サイドメニュー
# ---------------------------------------------------------
render_ai_teacher()

st.sidebar.title("メニュー")
menu = st.sidebar.radio(
    "移動先を選択", ["年度別", "科目別", "AIに質問（チャット）"]
)

# ---------------------------------------------------------
# ルート1：年度別
# ---------------------------------------------------------
if menu == "年度別":
    st.title("田中式 司法書士一問一答｜法律特化AI講師への24時間質問サポート付")

    if not df.empty and "問題番号" in df.columns:
        all_questions = df["問題番号"].dropna().unique()

        def extract_session(q_no):
            return str(q_no).split("第")[0] if "第" in str(q_no) else str(q_no)

        def session_sort_key(s):
            era_val = 2 if "令和" in s else (1 if "平成" in s else 0)
            if "元" in s:
                year_val = 1
            else:
                import re
                match = re.search(r'(\d+)年', s)
                year_val = int(match.group(1)) if match else 0
            return (era_val, year_val, s)

        sessions = sorted(list(set([extract_session(q) for q in all_questions])), key=session_sort_key)
        selected_session = st.selectbox(
            "演習する年度・回を選んでください", sessions, key="y_session"
        )

        session_rows = df[
            df["問題番号"].astype(str).str.startswith(selected_session)
        ].reset_index(drop=True)

        if not session_rows.empty:
            mode = st.radio(
                "出題モード:",
                ["順番通り", "ランダム"],
                horizontal=True,
                key="y_mode",
            )

            if (
                st.session_state.get("y_current_session") != selected_session
                or st.session_state.get("y_current_mode") != mode
            ):
                st.session_state.y_current_session = selected_session
                st.session_state.y_current_mode = mode
                st.session_state.y_ptr = 0
                st.session_state.y_answered = False
                st.session_state.y_user_ans = None
                st.session_state.y_correct_count = 0
                st.session_state.y_total_count = 0
                st.session_state.teacher_state = "normal"
                reset_inline_chat()

                indices = list(range(len(session_rows)))
                if mode == "ランダム":
                    random.shuffle(indices)
                st.session_state.y_order = indices

            ptr = st.session_state.y_ptr
            order = st.session_state.y_order
            
            if ptr < len(order):
                current_target_idx = order[ptr]
                q_options = [f"第 {i+1} 問" for i in range(len(session_rows))]

                selected_q = st.selectbox(
                    "現在の問題（選択して移動も可能）:",
                    q_options,
                    index=current_target_idx,
                )
                
                target_start_idx = int(selected_q.replace("第 ", "").replace(" 問", "")) - 1
                if target_start_idx != current_target_idx:
                    st.session_state.y_ptr = order.index(target_start_idx)
                    st.session_state.y_answered = False
                    st.session_state.y_user_ans = None
                    st.session_state.teacher_state = "normal"
                    reset_inline_chat()
                    st.rerun()

                row = session_rows.iloc[current_target_idx]

                # 正答率の計算
                acc_rate = (st.session_state.y_correct_count / st.session_state.y_total_count * 100) if st.session_state.y_total_count > 0 else 0

                st.markdown(
                    f"### 【 {selected_session} 】 ( {ptr + 1} / {len(session_rows)} 問目 ) ｜ 正答率: {acc_rate:.1f}% ({st.session_state.y_total_count}問中 {st.session_state.y_correct_count}問正解)"
                )
                st.markdown(
                    f"分野: {row.get('分野', '')} ｜ 肢: {row.get('肢', '')}"
                )
                
                st.markdown(
                    f'<div class="custom-question-card">{row.get("文章", "")}</div>',
                    unsafe_allow_html=True
                )

                if not st.session_state.y_answered:
                    clicked = clickable_images(
                        [
                            get_image_base64("images/btn_o.png"),
                            get_image_base64("images/btn_x.png")
                        ] if os.path.exists("images/btn_o.png") else [
                            get_image_base64("images/0_btn_o.png"),
                            get_image_base64("images/0_btn_x.png")
                        ],
                        titles=["正解", "不正解"],
                        div_style={"display": "flex", "justify-content": "center", "gap": "20px"},
                        img_style={"width": "120px", "cursor": "pointer"},
                        key=f"img_btn_y_{ptr}"
                    )

                    if clicked > -1:
                        st.session_state.y_answered = True
                        correct = str(row.get("正誤", "")).strip()
                        if clicked == 0:
                            st.session_state.y_user_ans = "○"
                        else:
                            st.session_state.y_user_ans = "×"
                        
                        st.session_state.y_total_count += 1
                        if st.session_state.y_user_ans == correct:
                            st.session_state.y_correct_count += 1
                            st.session_state.teacher_state = "happy"
                        else:
                            st.session_state.teacher_state = "sad"
                        st.rerun()

                if st.session_state.y_answered:
                    correct = str(row.get("正誤", "")).strip()
                    if st.session_state.y_user_ans == correct:
                        col_ok, col_img = st.columns([5, 1])
                        with col_ok:
                            st.success("正解です！")
                        with col_img:
                            happy_img_path = "images/1_teacher_happy_o.png"
                            if os.path.exists(happy_img_path):
                                st.image(happy_img_path, width=45)
                    else:
                        col_err, col_img = st.columns([5, 1])
                        with col_err:
                            st.error(f"不正解... （正解は {correct} です）")
                        with col_img:
                            sad_img_path = "images/1_teacher_sad_x.png"
                            if os.path.exists(sad_img_path):
                                st.image(sad_img_path, width=45)
                        
                    st.write(f"解説: {row.get('簡単な解説', '解説がありません')}")

                    if st.button("次の問題へ ➡", key="y_btn_next"):
                        st.session_state.y_ptr += 1
                        st.session_state.y_answered = False
                        st.session_state.y_user_ans = None
                        st.session_state.teacher_state = "normal"
                        reset_inline_chat()
                        st.rerun()
                        
                    with st.expander("この問題の誤りや法改正を報告する"):
                        st.write("法改正による影響や、問題・解説の誤りがあれば根拠を添えてお知らせください。")
                        report_text = st.text_area("報告内容・根拠を記載", key=f"report_area_y_{ptr}")
                        
                        if st.button("報告を送信", key=f"btn_send_report_y_{ptr}"):
                            if report_text:
                                q_no_for_report = row.get('問題番号', '不明')
                                with st.spinner("送信中..."):
                                    success = send_report_email(q_no_for_report, report_text)
                                if success:
                                    st.success("報告を送信しました。田中事務所へ通知されました！")
                                else:
                                    st.error("送信に失敗しました。Secretsの設定をご確認ください。")
                            else:
                                st.warning("報告内容を入力してください。")

                    render_inline_chat(row)
                    
            else:
                st.balloons()
                final_acc = (st.session_state.y_correct_count / st.session_state.y_total_count * 100) if st.session_state.y_total_count > 0 else 0
                st.success(f"全ての問題を完了しました！ 最終正答率: {final_acc:.1f}% ({st.session_state.y_total_count}問中 {st.session_state.y_correct_count}問正解)")
                if st.button("最初からやり直す", key="y_btn_reset"):
                    st.session_state.y_ptr = 0
                    st.session_state.y_answered = False
                    st.session_state.y_correct_count = 0
                    st.session_state.y_total_count = 0
                    st.session_state.teacher_state = "normal"
                    reset_inline_chat()
                    st.rerun()

# ---------------------------------------------------------
# ルート2：科目別
# ---------------------------------------------------------
elif menu == "科目別":
    st.title("田中式 司法書士一問一答｜法律特化AI講師への24時間質問サポート付")

    if not df.empty and "分野" in df.columns:
        categories = sorted(df["分野"].dropna().unique())
        selected_cat = st.selectbox(
            "科目を選択してください", categories, key="c_cat"
        )

        cat_rows = df[df["分野"] == selected_cat].reset_index(drop=True)

        if not cat_rows.empty:
            mode_cat = st.radio(
                "出題モード:",
                ["順番通り", "ランダム"],
                horizontal=True,
                key="c_mode",
            )

            if (
                st.session_state.get("c_current_cat") != selected_cat
                or st.session_state.get("c_current_mode") != mode_cat
            ):
                st.session_state.c_current_cat = selected_cat
                st.session_state.c_current_mode = mode_cat
                st.session_state.c_ptr = 0
                st.session_state.c_answered = False
                st.session_state.c_user_ans = None
                st.session_state.c_correct_count = 0
                st.session_state.c_total_count = 0
                st.session_state.teacher_state = "normal"
                reset_inline_chat()

                indices_c = list(range(len(cat_rows)))
                if mode_cat == "ランダム":
                    random.shuffle(indices_c)
                st.session_state.c_order = indices_c

            ptr_c = st.session_state.c_ptr
            order_c = st.session_state.c_order

            if ptr_c < len(order_c):
                current_target_idx_c = order_c[ptr_c]
                q_options_c = [f"第 {i+1} 問" for i in range(len(cat_rows))]

                selected_q_c = st.selectbox(
                    "現在の問題（選択して移動も可能）:",
                    q_options_c,
                    index=current_target_idx_c,
                )
                
                target_start_idx_c = int(selected_q_c.replace("第 ", "").replace(" 問", "")) - 1
                if target_start_idx_c != current_target_idx_c:
                    st.session_state.c_ptr = order_c.index(target_start_idx_c)
                    st.session_state.c_answered = False
                    st.session_state.c_user_ans = None
                    st.session_state.teacher_state = "normal"
                    reset_inline_chat()
                    st.rerun()

                row = cat_rows.iloc[current_target_idx_c]

                # 正答率の計算
                acc_rate_c = (st.session_state.c_correct_count / st.session_state.c_total_count * 100) if st.session_state.c_total_count > 0 else 0

                st.markdown(
                    f"### 【 科目: {selected_cat} 】 ( {ptr_c + 1} / {len(cat_rows)} 問目 ) ｜ 正答率: {acc_rate_c:.1f}% ({st.session_state.c_total_count}問中 {st.session_state.c_correct_count}問正解)"
                )
                st.markdown(
                    f"問題番号: {row.get('問題番号', '')} ｜ 肢: {row.get('肢', '')}"
                )
                
                st.markdown(
                    f'<div class="custom-question-card">{row.get("文章", "")}</div>',
                    unsafe_allow_html=True
                )

                if not st.session_state.c_answered:
                    clicked_c = clickable_images(
                        [
                            get_image_base64("images/btn_o.png"),
                            get_image_base64("images/btn_x.png")
                        ] if os.path.exists("images/btn_o.png") else [
                            get_image_base64("images/0_btn_o.png"),
                            get_image_base64("images/0_btn_x.png")
                        ],
                        titles=["正解", "不正解"],
                        div_style={"display": "flex", "justify-content": "center", "gap": "20px"},
                        img_style={"width": "120px", "cursor": "pointer"},
                        key=f"img_btn_c_{ptr_c}"
                    )

                    if clicked_c > -1:
                        st.session_state.c_answered = True
                        correct = str(row.get("正誤", "")).strip()
                        if clicked_c == 0:
                            st.session_state.c_user_ans = "○"
                        else:
                            st.session_state.c_user_ans = "×"
                        
                        st.session_state.c_total_count += 1
                        if st.session_state.c_user_ans == correct:
                            st.session_state.c_correct_count += 1
                            st.session_state.teacher_state = "happy"
                        else:
                            st.session_state.teacher_state = "sad"
                        st.rerun()

                if st.session_state.c_answered:
                    correct = str(row.get("正誤", "")).strip()
                    if st.session_state.c_user_ans == correct:
                        col_ok, col_img = st.columns([5, 1])
                        with col_ok:
                            st.success("正解です！")
                        with col_img:
                            happy_img_path = "images/1_teacher_happy_o.png"
                            if os.path.exists(happy_img_path):
                                st.image(happy_img_path, width=45)
                    else:
                        col_err, col_img = st.columns([5, 1])
                        with col_err:
                            st.error(f"不正解... （正解は {correct} です）")
                        with col_img:
                            sad_img_path = "images/1_teacher_sad_x.png"
                            if os.path.exists(sad_img_path):
                                st.image(sad_img_path, width=45)
                        
                    st.write(f"解説: {row.get('簡単な解説', '解説がありません')}")

                    if st.button("次の問題へ ➡", key="c_btn_next"):
                        st.session_state.c_ptr += 1
                        st.session_state.c_answered = False
                        st.session_state.c_user_ans = None
                        st.session_state.teacher_state = "normal"
                        reset_inline_chat()
                        st.rerun()
                        
                    with st.expander("この問題の誤りや法改正を報告する"):
                        st.write("法改正による影響や、問題・解説の誤りがあれば根拠を添えてお知らせください。")
                        report_text = st.text_area("報告内容・根拠を記載", key=f"report_area_c_{ptr_c}")
                        
                        if st.button("報告を送信", key=f"btn_send_report_c_{ptr_c}"):
                            if report_text:
                                q_no_for_report = row.get('問題番号', '不明')
                                with st.spinner("送信中..."):
                                    success = send_report_email(q_no_for_report, report_text)
                                if success:
                                    st.success("報告を送信しました。田中事務所へ通知されました！")
                                else:
                                    st.error("送信に失敗しました。Secretsの設定をご確認ください。")
                            else:
                                st.warning("報告内容を入力してください。")

                    render_inline_chat(row)
                    
            else:
                st.balloons()
                final_acc_c = (st.session_state.c_correct_count / st.session_state.c_total_count * 100) if st.session_state.c_total_count > 0 else 0
                st.success(f"全ての問題を完了しました！ 最終正答率: {final_acc_c:.1f}% ({st.session_state.c_total_count}問中 {st.session_state.c_correct_count}問正解)")
                if st.button("最初からやり直す", key="c_btn_reset"):
                    st.session_state.c_ptr = 0
                    st.session_state.c_answered = False
                    st.session_state.c_correct_count = 0
                    st.session_state.c_total_count = 0
                    st.session_state.teacher_state = "normal"
                    reset_inline_chat()
                    st.rerun()

# ---------------------------------------------------------
# ルート3：AIに質問（チャット）
# ---------------------------------------------------------
elif menu == "AIに質問（チャット）":
    st.title("AIたなかっち1号先生へ質問")
    st.write("過去問に関する疑問や、試験勉強の悩みをなんでも聞いてください！")

    if "main_messages" not in st.session_state:
        st.session_state.main_messages = []
    if "main_conv_id" not in st.session_state:
        st.session_state.main_conv_id = ""
        
    if st.session_state.chat_count >= 30:
        st.warning("本日のラリー制限（30回）に達しました。明日またお越しください！")
    else:
        # メッセージ履歴の表示
        for msg in st.session_state.main_messages:
            avatar_img = "images/1_teacher_normal.png" if msg["role"] == "assistant" else None
            with st.chat_message(msg["role"], avatar=avatar_img):
                st.markdown(msg["content"])

        # 入力フォーム
        prompt = st.chat_input("質問を入力してください")
        if prompt:
            st.session_state.chat_count += 1
            st.session_state.main_messages.append({"role": "user", "content": prompt})
            st.session_state.teacher_state = "thinking"
            st.session_state.main_waiting = True
            st.rerun()

        # AIからの応答待機処理
        if st.session_state.main_waiting:
            st.session_state.main_waiting = False
            with st.chat_message("assistant", avatar="images/1_teacher_normal.png"):
                with st.spinner("AIたなかっち1号先生が考えています..."):
                    response_text, new_conv_id = call_dify(st.session_state.main_messages[-1]["content"], st.session_state.main_conv_id)
                    if new_conv_id:
                        st.session_state.main_conv_id = new_conv_id

                    st.session_state.teacher_state = "normal"
                    st.markdown(response_text)
                    st.caption(f"（本日の残り: {30 - st.session_state.chat_count}回）")

            st.session_state.main_messages.append({"role": "assistant", "content": response_text})
            st.rerun()