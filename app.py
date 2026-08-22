import base64
from datetime import datetime, timezone
import json
import os
import random
import re
import smtplib
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import requests
import stripe
import streamlit as st
import streamlit.components.v1 as components
from streamlit_clickable_images import clickable_images
from supabase import create_client

# ==========================================
# ページ設定
# ==========================================
st.set_page_config(
    page_title="田中式 司法書士一問一答｜法律特化AI講師とチャットで会話！その場で疑問をスピード解決", 
    page_icon="📖", 
    layout="centered"
)

# セッション状態の初期化
if "user" not in st.session_state:
    st.session_state["user"] = None

# ==========================================
# Supabase & Stripe クライアント初期化
# ==========================================
@st.cache_resource
def init_connection():
    url = st.secrets["supabase"]["SUPABASE_URL"]
    key = st.secrets["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

def init_admin_connection():
    url = st.secrets["supabase"]["SUPABASE_URL"]
    service_role_key = st.secrets["supabase"].get("SUPABASE_SERVICE_ROLE_KEY", "")
    if service_role_key:
        return create_client(url, service_role_key)
    return None

supabase_admin = init_admin_connection()

if "stripe" in st.secrets and "STRIPE_SECRET_KEY" in st.secrets["stripe"]:
    stripe.api_key = st.secrets["stripe"]["STRIPE_SECRET_KEY"]

# ==========================================
# 音声ファイル検索・読み込み処理
# ==========================================

def get_audio_data_uri(audio_bytes):
    """
    音声のバイトデータを HTML5 audio で再生可能な Data URI に変換
    """
    b64 = base64.b64encode(audio_bytes).decode('utf-8')
    return f"data:audio/mp3;base64,{b64}"

def get_audio_file_path(prefix, q_num, limb):
    """
    static/audio_output 内のサブフォルダも含めて音声ファイルを探す
    """
    if not q_num or not limb:
        return None
    
    q_str = str(q_num).strip()
    limb_str = str(limb).strip()
    target_name = f"{prefix}_{q_str}_{limb_str}"
    
    dir_path = os.path.join(os.path.dirname(__file__), "static", "audio_output")
    if not os.path.exists(dir_path):
        dir_path = os.path.join("static", "audio_output")
        
    if not os.path.exists(dir_path):
        return None
        
    try:
        # os.walk を使ってサブフォルダ（令和元年度など）の中身も全探索する
        for root, _, files in os.walk(dir_path):
            for fname in files:
                name_without_ext, _ = os.path.splitext(fname)
                if name_without_ext.strip() == target_name:
                    return os.path.join(root, fname)
    except Exception:
        pass
        
    return None

def get_audio_url(file_path):
    """音声ファイルのURLパスを安全に生成（パス崩れ・日本語URL対応）"""
    if not file_path or not os.path.exists(file_path):
        return ""
    
    # パス区切り文字をすべて '/' に統一
    norm_path = file_path.replace("\\", "/")
    
    # "static/" 以降のパス（audio_output/令和元年度/...）だけを確実に抽出
    if "/static/" in norm_path:
        sub_path = norm_path.split("/static/", 1)[1]
    elif norm_path.startswith("static/"):
        sub_path = norm_path.split("static/", 1)[1]
    else:
        sub_path = os.path.basename(norm_path)
    
    # 日本語フォルダ・ファイル名をURLエンコード
    encoded_path = urllib.parse.quote(sub_path, safe='/')
    
    return f"/app/static/{encoded_path}"

def render_continuous_player(playlist, current_batch, total_batches, auto_start=False):
    if not playlist:
        st.info("再生できる音声ファイルが見つかりませんでした。")
        return

    json_data = json.dumps(playlist, ensure_ascii=False)
    auto_start_js = "true" if auto_start else "false"
    
    html_code = f"""
    <div style="background-color: #ffffff; padding: 16px; border-radius: 12px; border: 1px solid #e0e0e0; font-family: sans-serif; box-sizing: border-box;">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span id="batch-info" style="font-size: 0.85rem; font-weight: bold; color: #4f46e5; background: #eeeefd; padding: 4px 10px; border-radius: 6px;">
                グループ {current_batch + 1} / {total_batches} （10問単位）
            </span>
            <span id="playlist-status" style="font-size: 0.85rem; color: #64748b;"></span>
        </div>
        
        <div id="track-info" style="font-size: 1.05rem; font-weight: bold; color: #1e293b; margin-bottom: 8px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">再生準備中...</div>
        <div id="track-detail" style="font-size: 0.9rem; color: #475569; margin-bottom: 12px; height: 90px; overflow-y: auto; background: #f8fafc; padding: 10px; border-radius: 6px; box-sizing: border-box; line-height: 1.4;"></div>
        
        <audio id="audio-player" controls style="width: 100%; margin-bottom: 12px;"></audio>
        
        <div style="display: flex; gap: 8px; align-items: center; justify-content: space-between; flex-wrap: wrap;">
            <button onclick="prevTrack()" style="padding: 8px 14px; border-radius: 6px; border: 1px solid #cbd5e1; background: #f1f5f9; cursor: pointer; font-weight: bold;">⏮ 前の音声</button>
            <button onclick="nextTrack()" style="padding: 8px 14px; border-radius: 6px; border: none; background: #4f46e5; color: white; cursor: pointer; font-weight: bold;">次の音声 ⏭</button>
        </div>
    </div>

    <script>
        const playlist = {json_data};
        const autoStart = {auto_start_js};
        let currentIndex = 0;

        const player = document.getElementById('audio-player');
        const infoEl = document.getElementById('track-info');
        const detailEl = document.getElementById('track-detail');
        const statusEl = document.getElementById('playlist-status');

        function loadTrack(index) {{
            if (index < 0 || index >= playlist.length) return;
            currentIndex = index;
            const track = playlist[currentIndex];
            infoEl.innerText = track.title;
            detailEl.innerText = track.text;
            
            if (track.url) {{
                player.src = track.url;
                player.load();
            }} else {{
                infoEl.innerText = track.title + " (音声データなし)";
            }}
            
            statusEl.innerText = (currentIndex + 1) + ' / ' + playlist.length;
        }}

        function prevTrack() {{
            if (currentIndex > 0) {{
                loadTrack(currentIndex - 1);
                player.play().catch(e => console.log(e));
            }}
        }}

        function nextTrack() {{
            if (currentIndex < playlist.length - 1) {{
                loadTrack(currentIndex + 1);
                player.play().catch(e => console.log(e));
            }} else {{
                autoClickNextBatch();
            }}
        }}

        function autoClickNextBatch() {{
            infoEl.innerText = "次の10問を自動読み込み中...";
            setTimeout(function() {{
                try {{
                    const buttons = Array.from(window.parent.document.querySelectorAll('button'));
                    const nextBtn = buttons.find(b => b.innerText && b.innerText.includes('次の10問へ'));
                    if (nextBtn) {{
                        nextBtn.click();
                    }}
                }} catch (e) {{
                    console.error("自動遷移エラー:", e);
                }}
            }}, 500);
        }}

        player.addEventListener('ended', function() {{
            if (currentIndex < playlist.length - 1) {{
                nextTrack();
            }} else {{
                autoClickNextBatch();
            }}
        }});

        if (playlist.length > 0) {{
            loadTrack(0);
            if (autoStart) {{
                player.play().catch(e => console.log("自動再生ブロック:", e));
            }}
        }}
    </script>
    """
    components.html(html_code, height=360)

# ==========================================
# 1. 認証機能（Supabase Auth）
# ==========================================
def login(email, password):
    try:
        clean_email = email.strip().lower()
        res = supabase.auth.sign_in_with_password({
            "email": clean_email,
            "password": password
        })
        if res.user:
            if res.user.email_confirmed_at is None:
                st.error("メール認証が完了していません。届いたメール内の確認リンクをクリックしてください。")
                return None
            return res.user
        return None
    except Exception:
        st.error("メールアドレスまたはパスワードが正しくありません。")
        return None

def signup(email, password):
    try:
        clean_email = email.strip().lower()
        res = supabase.auth.sign_up({
            "email": clean_email,
            "password": password
        })
        return res
    except Exception as e:
        st.error(f"登録エラー: {e}")
        return None

def update_password(new_password):
    try:
        res = supabase.auth.update_user({"password": new_password})
        if res.user:
            return True
        return False
    except Exception as e:
        st.error(f"パスワード変更エラー: {e}")
        return False

def reset_password_request(email):
    try:
        clean_email = email.strip().lower()
        supabase.auth.reset_password_for_email(clean_email)
        return True
    except Exception as e:
        st.error(f"送信エラー: {e}")
        return False

# ==========================================
# 2. 決済・サブスクリプション連携
# ==========================================

def ensure_subscription_record(email, user_id):
    try:
        clean_email = email.strip().lower()
        response = supabase.table("subscriptions").select("email").eq("email", clean_email).execute()
        if not response.data:
             supabase.table("subscriptions").insert({
                "email": clean_email,
                "user_id": user_id,
                "status": "inactive",
                "cancel_at_period_end": False,
                "current_period_end": "1970-01-01T00:00:00+00:00"
            }).execute()
    except Exception as e:
        print(f"ensure_subscription_record Error: {e}")

def check_access(email):
    try:
        clean_email = email.strip().lower()
        response = supabase.table("subscriptions").select("*").eq("email", clean_email).execute()
        
        if response.data:
            sub = response.data[0]
            status = sub.get("status")
            period_end = sub.get("current_period_end")

            if status in ["active", "trialing"]:
                return True

            if period_end and period_end != "1970-01-01T00:00:00+00:00":
                try:
                    end_date = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    if now <= end_date:
                        return True
                except Exception as parse_err:
                    print(f"日付パースエラー: {parse_err}")

        return False
    except Exception as e:
        print(f"check_access Error: {e}")
        return False

def execute_account_deletion(user_email, user_id):
    try:
        clean_email = user_email.strip().lower()
        
        if stripe.api_key:
            try:
                customers = stripe.Customer.list(email=clean_email, limit=1)
                if customers.data:
                    subs = stripe.Subscription.list(customer=customers.data[0].id, status="active")
                    for s in subs.data:
                        stripe.Subscription.cancel(s.id)
            except Exception as e:
                print(f"Stripe cancel warning: {e}")

        if supabase_admin:
            supabase_admin.table("subscriptions").delete().eq("email", clean_email).execute()
        else:
            supabase.table("subscriptions").delete().eq("email", clean_email).execute()

        if supabase_admin:
            supabase_admin.auth.admin.delete_user(user_id)
        else:
            st.error("管理者キー（SUPABASE_SERVICE_ROLE_KEY）が未設定のため、Authアカウント削除を完了できませんでした。")
            return False

        return True
    except Exception as e:
        st.error(f"退会処理中にエラーが発生しました: {e}")
        return False

# ==========================================
# 3. ユーティリティ・ダイアログ
# ==========================================
def get_image_base64(path):
    try:
        with open(path, "rb") as f:
            return f"data:image/png;base64,{base64.b64encode(f.read()).decode()}"
    except FileNotFoundError:
        return ""

def render_header_image():
    if os.path.exists("images/1_title.png"):
        st.image("images/1_title.png", use_container_width=True)
    elif os.path.exists("1_title.png"):
        st.image("1_title.png", use_container_width=True)
    else:
        st.title("田中式 司法書士一問一答")

@st.dialog("利用規約", width="large")
def show_terms_dialog():
    if os.path.exists("TERMS.md"):
        with open("TERMS.md", "r", encoding="utf-8") as f:
            terms_text = f.read()
        st.markdown(terms_text)
    else:
        st.error("TERMS.md ファイルが見つかりません。")
        
    if st.button("閉じる", key="btn_close_terms"):
        st.rerun()

@st.dialog("パスワードの変更", width="medium")
def show_change_password_dialog():
    st.write("新しいパスワードを入力してください。")
    new_pw = st.text_input("新しいパスワード", type="password", key="dialog_new_pw_input")
    confirm_pw = st.text_input("新しいパスワード（確認用）", type="password", key="dialog_confirm_pw_input")
    
    if st.button("パスワードを変更する", type="primary", use_container_width=True, key="btn_execute_change_pw"):
        if not new_pw or not confirm_pw:
            st.warning("すべての項目を入力してください。")
        elif new_pw != confirm_pw:
            st.error("パスワードが一致しません。")
        elif len(new_pw) < 6:
            st.error("パスワードは6文字以上で設定してください。")
        else:
            if update_password(new_pw):
                st.success("パスワードを変更しました。")
                st.rerun()

@st.dialog("パスワードの再設定", width="medium")
def show_reset_password_dialog():
    st.write("ご登録済みのメールアドレスを入力してください。パスワード再設定用の案内を送信します。")
    reset_email = st.text_input("メールアドレス", key="dialog_reset_email_input")
    if st.button("再設定メールを送信", type="primary", use_container_width=True, key="btn_execute_reset_pw"):
        if not reset_email:
            st.warning("メールアドレスを入力してください。")
        else:
            if reset_password_request(reset_email):
                st.success("パスワード再設定用のメールを送信しました。メールボックスをご確認ください。")

@st.dialog("退会手続き（アカウント完全削除）", width="medium")
def show_delete_account_dialog():
    if not st.session_state.get("user"):
        st.warning("ログインしていません。")
        return

    curr_email = st.session_state["user"]["email"]
    curr_id = st.session_state["user"]["id"]

    is_active_recurring = False
    try:
        response = supabase.table("subscriptions").select("status, cancel_at_period_end").eq("email", curr_email.strip().lower()).execute()
        if response.data:
            sub_data = response.data[0]
            status = sub_data.get("status")
            cancel_at_period_end = sub_data.get("cancel_at_period_end", False)

            if status in ["active", "trialing"] and not cancel_at_period_end:
                is_active_recurring = True
    except Exception as e:
        print(f"DB取得エラー: {e}")

    if is_active_recurring:
        st.error("【解約が必要です】サブスクリプションの自動更新が有効です。")
        st.write(
            "アカウントを削除する前に、先に『契約管理・解約』からサブスクリプションの解約（自動更新停止）を行ってください。"
            "解約を行わずにアカウントを削除すると、次回以降の自動請求が継続してしまう恐れがあります。"
        )
        
        stripe_portal_url = st.secrets.get("stripe", {}).get("STRIPE_PORTAL_URL", "#")
        st.markdown(
            f'<a href="{stripe_portal_url}" target="_blank">'
            f'<button style="width:100%; padding:10px; border-radius:6px; background-color:#4F46E5; color:white; border:none; cursor:pointer; font-weight:bold;">'
            f'契約管理画面（Stripe）で解約手続きをする'
            f'</button></a>',
            unsafe_allow_html=True
        )
        
        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        if st.button("🔄 Stripeで解約後、状態を再確認する", key="btn_recheck_in_dialog", use_container_width=True):
            st.rerun()
        return

    st.warning("アカウントを削除すると、これまでの学習履歴や登録情報が完全に消去され、復元できなくなります。")

    st.markdown("""
    ・注意事項および同意事項:
    1. 解約済みサブスクリプションの残りの契約有効期間がある場合でも、退会完了と同時にサービスの利用権限は即時失効します。
    2. 日割り計算等による返金・決済のキャンセル対応は理由を問わず一切行われません。
    3. アカウント削除後に同じメールアドレスで再登録しても、過去のデータは引き継げません。
    """)

    agree = st.checkbox("上記注意事項（残期間の放棄・返金不可・データ全削除）に同意します", key="chk_agree_delete")

    if st.button("アカウントを完全に削除して退会する", type="primary", disabled=not agree, use_container_width=True):
        with st.spinner("退会処理を実行中..."):
            success = execute_account_deletion(curr_email, curr_id)
            if success:
                st.success("退会手続きが完了しました。ご利用ありがとうございました。")
                supabase.auth.sign_out()
                st.session_state.clear()
                st.rerun()

@st.dialog("特定商取引法に基づく表記・退会案内", width="large")
def show_tokusho_dialog():
    contact_email = "お問い合わせ用メールアドレス未設定"
    try:
        if "gmail_receiver" in st.secrets:
            contact_email = st.secrets["gmail_receiver"]
        elif "app" in st.secrets and "contact_email" in st.secrets["app"]:
            contact_email = st.secrets["app"]["contact_email"]
        elif "contact_email" in st.secrets:
            contact_email = st.secrets["contact_email"]
    except Exception:
        pass

    st.markdown(f"""
    ### 特定商取引法に基づく表記

    ・事業者名・代表運営者：
    請求があった場合、遅滞なく開示いたします（下記お問い合わせ先までご連絡ください）。

    ・所在地・電話番号：
    請求があった場合、遅滞なく開示いたします（下記お問い合わせ先までご連絡ください）。

    ・お問い合わせ先：
    {contact_email}（またはアプリ内のお問い合わせフォーム）

    ・販売価格：
    月額 3,980円（税込）

    ・お支払い方法：
    クレジットカード決済（Stripe）

    ・サービス提供時期：
    決済手続き完了後、すぐにご利用いただけます。

    ・返品・キャンセル：
    商品の性質上、購入後の返金やキャンセルには応じかねます（解約後は現在の有効期限まで利用可能です）。

    ---

    ### 解約およびアカウント削除（退会）について

    ・解約方法（サブスクリプション停止）：
    サイドバーの「契約管理・解約」ボタンからいつでも自動更新の停止が可能です。解約手続き後も、現在の契約有効期限まではサービスをご利用いただけます。

    ・アカウント完全削除（退会）：
    「契約管理・解約」にて自動更新を停止後、アプリ内の退会ボタンから即時アカウントを削除可能です。または上記お問い合わせ先メールアドレスまで「退会希望」と記載してご連絡いただくことでも対応いたします。
    """)

    col_dialog_close, col_dialog_delete = st.columns(2)
    with col_dialog_close:
        if st.button("閉じる", key="btn_close_tokusho", use_container_width=True):
            if "page" in st.query_params:
                del st.query_params["page"]
            st.rerun()
            
    with col_dialog_delete:
        if st.session_state.get("user"):
            if st.button("退会手続きへ進む", key="btn_goto_delete_from_tokusho", use_container_width=True):
                st.session_state["show_delete_modal"] = True
                st.rerun()

if st.query_params.get("page") == "tokusho":
    show_tokusho_dialog()

if st.session_state.get("show_delete_modal"):
    st.session_state["show_delete_modal"] = False
    show_delete_account_dialog()

# ==========================================
# A. 未ログイン時の表示
# ==========================================
if not st.session_state.get("user"):
    bg_pc_b64 = get_image_base64("images/1_background_PC.png") or get_image_base64("1_background_PC.png")
    bg_sp_b64 = get_image_base64("images/1_background_mobile.png") or get_image_base64("1_background_mobile.png")
    
    st.markdown(f"""
    <style>
        .stApp {{
            background-image: linear-gradient(rgba(0, 0, 0, 0.12), rgba(0, 0, 0, 0.12)), url("{bg_pc_b64}");
            background-size: cover;
            background-position: center center;
            background-repeat: no-repeat;
            background-attachment: fixed;
        }}

        #MainMenu, footer, header {{
            visibility: hidden;
        }}
        
        [data-testid="stMainBlockContainer"] {{
            padding-top: 44vh !important;
            padding-bottom: 20px !important;
            max-width: 450px !important;
            margin-left: 0.8% !important;
            margin-right: auto !important;
        }}

        [data-testid="stMainBlockContainer"] > div:first-child {{
            background-color: rgba(255, 255, 255, 0.97) !important;
            padding: 18px 24px 20px 24px !important;
            border-radius: 16px !important;
            box-shadow: 0 10px 28px rgba(0, 0, 0, 0.28) !important;
            border: 1px solid #e2e8f0 !important;
            backdrop-filter: blur(6px) !important;
        }}

        @media (max-width: 768px) {{
            .stApp {{
                background-image: linear-gradient(rgba(0, 0, 0, 0.12), rgba(0, 0, 0, 0.12)), url("{bg_sp_b64}");
                background-position: top center;
            }}
            
            [data-testid="stMainBlockContainer"] {{
                padding-top: 35vh !important;
                max-width: 85% !important;
                margin: 0 auto !important;
            }}

            [data-testid="stMainBlockContainer"] > div:first-child {{
                padding: 10px !important; 
            }}

            h2, h3 {{
                font-size: 1.1rem !important;
                margin-bottom: 5px !important;
            }}
            
            div[data-testid="stTextInput"] {{
                margin-bottom: -10px !important;
            }}
            
            input {{
                padding: 6px !important;
                font-size: 0.9rem !important;
            }}
            
            button {{
                padding: 4px 8px !important;
                font-size: 0.9rem !important;
            }}
            
            button[data-baseweb="tab"] p {{
                font-size: 0.9rem !important;
            }}
        }}

        [data-testid="stMainBlockContainer"] h1,
        [data-testid="stMainBlockContainer"] h2,
        [data-testid="stMainBlockContainer"] p,
        [data-testid="stMainBlockContainer"] label {{
            color: #0F172A !important;
            font-weight: 700 !important;
        }}
    </style>
    """, unsafe_allow_html=True)

    tab_login, tab_signup = st.tabs(["ログイン", "新規会員登録"])
    
    with tab_login:
        st.markdown("<h2 style='margin-top:0; margin-bottom:12px; color:#0F172A; font-size:1.4rem;'>ログイン</h2>", unsafe_allow_html=True)
        
        email = st.text_input("メールアドレス", key="login_email")
        password = st.text_input("パスワード", type="password", key="login_password")
        
        st.markdown("<div style='margin-top: 14px;'></div>", unsafe_allow_html=True)
        if st.button("ログイン", key="btn_login", use_container_width=True, type="primary"):
            if email and password:
                user_info = login(email, password)
                if user_info:
                    st.session_state["user"] = {
                        "email": user_info.email,
                        "id": user_info.id
                    }
                    st.rerun()
            else:
                st.warning("メールアドレスとパスワードを入力してください。")

        st.markdown("<div style='margin-top: 8px;'></div>", unsafe_allow_html=True)
        if st.button("パスワードをお忘れの方はこちら", key="btn_forgot_password", use_container_width=True):
            show_reset_password_dialog()
                
    with tab_signup:
        st.markdown("<h2 style='margin-top:0; margin-bottom:12px; color:#0F172A; font-size:1.4rem;'>新規会員登録</h2>", unsafe_allow_html=True)
        
        if st.button("利用規約を確認する", key="btn_show_terms", use_container_width=True):
            show_terms_dialog()
        
        st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
        with st.form("signup_form"):
            new_email = st.text_input("メールアドレス", key="signup_email")
            new_password = st.text_input("パスワード (6文字以上)", type="password", key="signup_password")
            confirm_password = st.text_input("パスワード (確認用)", type="password", key="signup_confirm_password")
            agree_terms = st.checkbox("利用規約に同意する", key="chk_agree_terms")
            submit_signup = st.form_submit_button("アカウントを作成する", use_container_width=True, type="primary")
            
            if submit_signup:
                if not new_email or not new_password or not confirm_password:
                    st.warning("すべての項目を入力してください。")
                elif new_password != confirm_password:
                    st.error("パスワードが一致しません。")
                elif len(new_password) < 6:
                    st.error("パスワードは6文字以上で設定してください。")
                elif not agree_terms:
                    st.error("利用規約への同意が必要です。")
                else:
                    res = signup(new_email, new_password)
                    if res and res.user:
                        st.success("仮登録が完了しました！")
                        st.info("入力されたメールアドレスに確認メールを送信しました。メール内のリンクをクリックして認証を完了させてからログインしてください。")

    st.markdown("<div style='margin-top: 10px;'></div>", unsafe_allow_html=True)
    if st.button("特定商取引法に基づく表記・退会案内", key="btn_tokusho_unlogin", use_container_width=True):
        show_tokusho_dialog()

    st.stop()


# ==========================================
# ユーザー検証
# ==========================================
current_user = st.session_state["user"]
user_email = current_user["email"]
user_id = current_user["id"]

ensure_subscription_record(user_email, user_id)

# ==========================================
# B. 未契約・期限切れ時の案内画面表示
# ==========================================
if not check_access(user_email):
    st.title("契約のご案内")
    st.warning("有効なサブスクリプションが確認できませんでした。AI学習機能を利用するには有料プランへのご登録が必要です。")
    
    base_stripe_url = st.secrets["stripe"]["STRIPE_PAYMENT_LINK"]
    stripe_url = f"{base_stripe_url}?prefilled_email={user_email}&client_reference_id={user_id}"
    
    st.link_button("決済画面へ進む（Stripe Checkout）", stripe_url, type="primary", use_container_width=True)
    
    st.markdown("<div style='margin-top: 15px;'></div>", unsafe_allow_html=True)
    if st.button("🔄 決済完了後の状態を再確認する", key="btn_recheck_subscription", use_container_width=True):
        st.rerun()

    st.markdown("---")
    col_unsub_tokusho, col_unsub_delete = st.columns(2)
    with col_unsub_tokusho:
        if st.button("特定商取引法に基づく表記", key="btn_tokusho_unsubscribed", use_container_width=True):
            show_tokusho_dialog()
    with col_unsub_delete:
        if st.button("退会手続き（アカウント削除）", key="btn_delete_unsubscribed", use_container_width=True):
            show_delete_account_dialog()

    st.stop()

# ==========================================
# C. メイン処理（問題演習・AIチャット）
# ==========================================
st.markdown("""
<style>
    .stApp {
        background-color: #f8f9fa;
    }
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
        margin-bottom: 12px;
    }
    @media (max-width: 768px) {
        .custom-question-card {
            font-size: 1.2rem !important;
            padding: 16px;
            line-height: 1.6 !important;
        }
    }
</style>
""", unsafe_allow_html=True)

def call_dify(query, conversation_id=""):
    dify_endpoint = st.secrets["dify"]["DIFY_ENDPOINT"]
    dify_api_key = st.secrets["dify"]["DIFY_API_KEY"]
    
    headers = {
        "Authorization": f"Bearer {dify_api_key}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "inputs": {},
        "query": query,
        "response_mode": "blocking",
        "user": user_id,
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id

    try:
        response = requests.post(
            dify_endpoint,
            headers=headers,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=30,
        )
        if response.status_code == 200:
            res_data = response.json()
            return res_data.get("answer", "回答を取得できませんでした。"), res_data.get("conversation_id", "")
        else:
            return f"エラー詳細: {response.status_code} - {response.text}", conversation_id
    except Exception as e:
        return f"通信エラー: {e}", conversation_id

def send_report_email(q_no, reason):
    sender_email = st.secrets.get("gmail_sender", st.secrets.get("gmail", {}).get("sender", ""))
    app_password = st.secrets.get("gmail_app_password", st.secrets.get("gmail", {}).get("app_password", ""))
    receiver_email = st.secrets.get("gmail_receiver", st.secrets.get("gmail", {}).get("receiver", ""))

    msg = MIMEMultipart()
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg['Subject'] = f"過去問アプリ 法改正・問題修正の報告（{q_no}）"

    body = f"送信ユーザー: {user_email}\n対象の問題番号: {q_no}\n\n【報告内容・根拠】\n{reason}"
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

@st.cache_data
def load_data():
    csv_file = "司法書士過去問集CSV.csv"
    df_temp = pd.DataFrame()
    for enc in ["utf-8", "cp932", "shift_jis"]:
        try:
            df_temp = pd.read_csv(csv_file, encoding=enc)
            if "問題番号" not in df_temp.columns and len(df_temp.columns) >= 6:
                df_temp.columns = ["問題番号", "分野", "肢", "文章", "正誤", "簡単な解説", "col7", "col8", "col9"][: len(df_temp.columns)]
            break
        except Exception:
            continue

    if df_temp.empty:
        try:
            df_temp = pd.read_csv(csv_file, header=None, encoding="utf-8")
            df_temp.columns = ["問題番号", "分野", "肢", "文章", "正誤", "簡単な解説", "c7", "c8", "c9"][: len(df_temp.columns)]
        except:
            return pd.DataFrame()

    if "問題番号" in df_temp.columns:
        df_temp = df_temp[df_temp["問題番号"].astype(str).str.contains("令和|平成")]

    return df_temp

df = load_data()

# 状態初期化
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

if "y_correct_count" not in st.session_state:
    st.session_state.y_correct_count = 0
if "y_total_count" not in st.session_state:
    st.session_state.y_total_count = 0

if "c_correct_count" not in st.session_state:
    st.session_state.c_correct_count = 0
if "c_total_count" not in st.session_state:
    st.session_state.c_total_count = 0

def reset_inline_chat():
    st.session_state.inline_messages = []
    st.session_state.inline_conv_id = ""
    st.session_state.inline_waiting = False

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

render_ai_teacher()

st.sidebar.title("メニュー")
st.sidebar.write(f"ログイン中: {user_email}")

col_out, col_stripe = st.sidebar.columns(2)
with col_out:
    if st.button("ログアウト", key="sidebar_logout", use_container_width=True):
        supabase.auth.sign_out()
        st.session_state.clear()
        st.rerun()

stripe_portal_url = st.secrets.get("stripe", {}).get("STRIPE_PORTAL_URL", "#")

with col_stripe:
    st.markdown(
        f'<a href="{stripe_portal_url}" target="_blank">'
        f'<button style="width:100%; padding:6px; border-radius:4px; background-color:#4F46E5; color:white; border:none; cursor:pointer; font-size:12px;">'
        f'契約管理・解約'
        f'</button></a>',
        unsafe_allow_html=True
    )

col_pw_side, col_delete_side = st.sidebar.columns(2)
with col_pw_side:
    if st.button("パスワード変更", key="btn_sidebar_change_pw", use_container_width=True):
        show_change_password_dialog()

with col_delete_side:
    if st.button("退会手続き", key="btn_sidebar_delete_account", use_container_width=True):
        show_delete_account_dialog()

if st.button("特商法表記", key="btn_sidebar_tokusho", use_container_width=True):
    show_tokusho_dialog()

st.sidebar.markdown("---")

menu = st.sidebar.radio("移動先を選択", ["年度別", "科目別", "過去問聞き流し", "AIに質問（チャット）"])

# ルート1：年度別
if menu == "年度別":
    render_header_image()

    if not df.empty and "問題番号" in df.columns:
        all_questions = df["問題番号"].dropna().unique()

        def extract_session(q_no):
            return str(q_no).split("第")[0] if "第" in str(q_no) else str(q_no)

        def session_sort_key(s):
            era_val = 2 if "令和" in s else (1 if "平成" in s else 0)
            year_val = 1 if "元" in s else (int(re.search(r'(\d+)年', s).group(1)) if re.search(r'(\d+)年', s) else 0)
            return (era_val, year_val, s)

        sessions = sorted(list(set([extract_session(q) for q in all_questions])), key=session_sort_key)
        selected_session = st.selectbox("演習する年度・回を選んでください", sessions, key="y_session")

        session_rows = df[df["問題番号"].astype(str).str.startswith(selected_session)].reset_index(drop=True)

        if not session_rows.empty:
            mode = st.radio("出題モード:", ["順番通り", "ランダム"], horizontal=True, key="y_mode")

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

                selected_q = st.selectbox("現在の問題（選択して移動も可能）:", q_options, index=current_target_idx)
                
                target_start_idx = int(selected_q.replace("第 ", "").replace(" 問", "")) - 1
                if target_start_idx != current_target_idx:
                    st.session_state.y_ptr = order.index(target_start_idx)
                    st.session_state.y_answered = False
                    st.session_state.y_user_ans = None
                    st.session_state.teacher_state = "normal"
                    reset_inline_chat()
                    st.rerun()

                row = session_rows.iloc[current_target_idx]
                acc_rate = (st.session_state.y_correct_count / st.session_state.y_total_count * 100) if st.session_state.y_total_count > 0 else 0

                st.markdown(
                    f'<div style="font-size: 1.25rem; font-weight: 700; color: #0F172A; display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px; margin-top: 8px; margin-bottom: 12px;">'
                    f'<span>【 {selected_session} 】 ( {ptr + 1} / {len(session_rows)} 問目 )</span>'
                    f'<span style="font-size: 0.9rem; font-weight: 500; color: #475569; margin-left: 6px;"> ｜ 正答率: {acc_rate:.1f}% ({st.session_state.y_total_count}問中 {st.session_state.y_correct_count}問正解)</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
                st.markdown(f"分野: {row.get('分野', '')} ｜ 肢: {row.get('肢', '')}")
                st.markdown(f'<div class="custom-question-card">{row.get("文章", "")}</div>', unsafe_allow_html=True)

                q_num_val = row.get("問題番号", "")
                limb_val = row.get("肢", "")
                
                col_audio_btn, col_audio_space = st.columns([1, 2])
                with col_audio_btn:
                    if st.button("🔊 問題文を読み上げる", key=f"btn_audio_y_{ptr}", use_container_width=True):
                        q_file = get_audio_file_path("Q", q_num_val, limb_val)
                        if q_file:
                            st.audio(q_file, autoplay=True)
                        else:
                            st.error(f"問題音声（Q_{q_num_val}_{limb_val}）が見つかりません。")

                st.markdown("<br>", unsafe_allow_html=True)

                if not st.session_state.y_answered:
                    clicked = clickable_images(
                        [get_image_base64("images/btn_o.png"), get_image_base64("images/btn_x.png")]
                        if os.path.exists("images/btn_o.png") else
                        [get_image_base64("images/0_btn_o.png"), get_image_base64("images/0_btn_x.png")],
                        titles=["正解", "不正解"],
                        div_style={"display": "flex", "justify-content": "center", "gap": "20px"},
                        img_style={"width": "120px", "cursor": "pointer"},
                        key=f"img_btn_y_{ptr}"
                    )

                    if clicked > -1:
                        st.session_state.y_answered = True
                        correct = str(row.get("正誤", "")).strip()
                        st.session_state.y_user_ans = "○" if clicked == 0 else "×"
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
                            if os.path.exists("images/1_teacher_happy_o.png"):
                                st.image("images/1_teacher_happy_o.png", width=45)
                    else:
                        col_err, col_img = st.columns([5, 1])
                        with col_err:
                            st.error(f"不正解... （正解は {correct} です）")
                        with col_img:
                            if os.path.exists("images/1_teacher_sad_x.png"):
                                st.image("images/1_teacher_sad_x.png", width=45)
                        
                    st.write(f"解説: {row.get('簡単な解説', '解説がありません')}")

                    if st.button("🔊 解説を読み上げる", key=f"btn_audio_ans_y_{ptr}"):
                        a_file = get_audio_file_path("A", q_num_val, limb_val)
                        if a_file:
                            st.audio(a_file, autoplay=True)
                        else:
                            st.error(f"解説音声（A_{q_num_val}_{limb_val}）が見つかりません。")

                    st.markdown("<br>", unsafe_allow_html=True)

                    if st.button("次の問題へ ➡", key="y_btn_next"):
                        st.session_state.y_ptr += 1
                        st.session_state.y_answered = False
                        st.session_state.y_user_ans = None
                        st.session_state.teacher_state = "normal"
                        reset_inline_chat()
                        st.rerun()
                        
                    with st.expander("この問題の誤りや法改正を報告する"):
                        report_text = st.text_area("報告内容・根拠を記載", key=f"report_area_y_{ptr}")
                        if st.button("報告を送信", key=f"btn_send_report_y_{ptr}"):
                            if report_text:
                                q_no_for_report = row.get('問題番号', '不明')
                                with st.spinner("送信中..."):
                                    success = send_report_email(q_no_for_report, report_text)
                                if success:
                                    st.success("報告を送信しました。")
                                else:
                                    st.error("送信に失敗しました。")

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

# ルート2：科目別
elif menu == "科目別":
    render_header_image()

    if not df.empty and "分野" in df.columns:
        categories = sorted(df["分野"].dropna().unique())
        selected_cat = st.selectbox("科目を選択してください", categories, key="c_cat")

        cat_rows = df[df["分野"] == selected_cat].reset_index(drop=True)

        if not cat_rows.empty:
            mode_cat = st.radio("出題モード:", ["順番通り", "ランダム"], horizontal=True, key="c_mode")

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

                selected_q_c = st.selectbox("現在の問題（選択して移動も可能）:", q_options_c, index=current_target_idx_c)
                
                target_start_idx_c = int(selected_q_c.replace("第 ", "").replace(" 問", "")) - 1
                if target_start_idx_c != current_target_idx_c:
                    st.session_state.c_ptr = order_c.index(target_start_idx_c)
                    st.session_state.c_answered = False
                    st.session_state.c_user_ans = None
                    st.session_state.teacher_state = "normal"
                    reset_inline_chat()
                    st.rerun()

                row = cat_rows.iloc[current_target_idx_c]
                acc_rate_c = (st.session_state.c_correct_count / st.session_state.c_total_count * 100) if st.session_state.c_total_count > 0 else 0

                st.markdown(
                    f'<div style="font-size: 1.25rem; font-weight: 700; color: #0F172A; display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px; margin-top: 8px; margin-bottom: 12px;">'
                    f'<span>【 科目: {selected_cat} 】 ( {ptr_c + 1} / {len(cat_rows)} 問目 )</span>'
                    f'<span style="font-size: 0.9rem; font-weight: 500; color: #475569; margin-left: 6px;"> ｜ 正答率: {acc_rate_c:.1f}% ({st.session_state.c_total_count}問中 {st.session_state.c_correct_count}問正解)</span>'
                    f'</div>',
                    unsafe_allow_html=True
                )
                st.markdown(f"問題番号: {row.get('問題番号', '')} ｜ 肢: {row.get('肢', '')}")
                st.markdown(f'<div class="custom-question-card">{row.get("文章", "")}</div>', unsafe_allow_html=True)

                q_num_val = row.get("問題番号", "")
                limb_val = row.get("肢", "")

                col_audio_btn_c, col_audio_space_c = st.columns([1, 2])
                with col_audio_btn_c:
                    if st.button("🔊 問題文を読み上げる", key=f"btn_audio_c_{ptr_c}", use_container_width=True):
                        q_file = get_audio_file_path("Q", q_num_val, limb_val)
                        if q_file:
                            st.audio(q_file, autoplay=True)
                        else:
                            st.error(f"問題音声（Q_{q_num_val}_{limb_val}）が見つかりません。")

                st.markdown("<br>", unsafe_allow_html=True)

                if not st.session_state.c_answered:
                    clicked_c = clickable_images(
                        [get_image_base64("images/btn_o.png"), get_image_base64("images/btn_x.png")]
                        if os.path.exists("images/btn_o.png") else
                        [get_image_base64("images/0_btn_o.png"), get_image_base64("images/0_btn_x.png")],
                        titles=["正解", "不正解"],
                        div_style={"display": "flex", "justify-content": "center", "gap": "20px"},
                        img_style={"width": "120px", "cursor": "pointer"},
                        key=f"img_btn_c_{ptr_c}"
                    )

                    if clicked_c > -1:
                        st.session_state.c_answered = True
                        correct = str(row.get("正誤", "")).strip()
                        st.session_state.c_user_ans = "○" if clicked_c == 0 else "×"
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
                            if os.path.exists("images/1_teacher_happy_o.png"):
                                st.image("images/1_teacher_happy_o.png", width=45)
                    else:
                        col_err, col_img = st.columns([5, 1])
                        with col_err:
                            st.error(f"不正解... （正解は {correct} です）")
                        with col_img:
                            if os.path.exists("images/1_teacher_sad_x.png"):
                                st.image("images/1_teacher_sad_x.png", width=45)
                        
                    st.write(f"解説: {row.get('簡単な解説', '解説がありません')}")

                    if st.button("🔊 解説を読み上げる", key=f"btn_audio_ans_c_{ptr_c}"):
                        a_file = get_audio_file_path("A", q_num_val, limb_val)
                        if a_file:
                            st.audio(a_file, autoplay=True)
                        else:
                            st.error(f"解説音声（A_{q_num_val}_{limb_val}）が見つかりません。")

                    st.markdown("<br>", unsafe_allow_html=True)

                    if st.button("次の問題へ ➡", key="c_btn_next"):
                        st.session_state.c_ptr += 1
                        st.session_state.c_answered = False
                        st.session_state.c_user_ans = None
                        st.session_state.teacher_state = "normal"
                        reset_inline_chat()
                        st.rerun()
                        
                    with st.expander("この問題の誤りや法改正を報告する"):
                        report_text = st.text_area("報告内容・根拠を記載", key=f"report_area_c_{ptr_c}")
                        if st.button("報告を送信", key=f"btn_send_report_c_{ptr_c}"):
                            if report_text:
                                q_no_for_report = row.get('問題番号', '不明')
                                with st.spinner("送信中..."):
                                    success = send_report_email(q_no_for_report, report_text)
                                if success:
                                    st.success("報告を送信しました。")
                                else:
                                    st.error("送信に失敗しました。")

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

# ルート3：過去問聞き流し
elif menu == "過去問聞き流し":
    render_header_image()
    st.subheader("🎧 過去問連続聞き流しモード")

    listen_type = st.radio("絞り込み方法を選択", ["年度別", "科目別"], horizontal=True, key="listen_type_radio")

    target_rows = pd.DataFrame()

    if listen_type == "年度別":
        if not df.empty and "問題番号" in df.columns:
            all_questions = df["問題番号"].dropna().unique()

            def extract_session(q_no):
                return str(q_no).split("第")[0] if "第" in str(q_no) else str(q_no)

            def session_sort_key(s):
                era_val = 2 if "令和" in s else (1 if "平成" in s else 0)
                year_val = 1 if "元" in s else (int(re.search(r'(\d+)年', s).group(1)) if re.search(r'(\d+)年', s) else 0)
                return (era_val, year_val, s)

            sessions = sorted(list(set([extract_session(q) for q in all_questions])), key=session_sort_key)
            sel_session = st.selectbox("聞き流す年度・回を選んでください", sessions, key="listen_session_select")
            target_rows = df[df["問題番号"].astype(str).str.startswith(sel_session)].reset_index(drop=True)

    else:
        if not df.empty and "分野" in df.columns:
            categories = sorted(df["分野"].dropna().unique())
            sel_cat = st.selectbox("聞き流す科目を選んでください", categories, key="listen_cat_select")
            target_rows = df[df["分野"] == sel_cat].reset_index(drop=True)

    if not target_rows.empty:
        batch_size = 10
        total_questions = len(target_rows)
        total_batches = (total_questions + batch_size - 1) // batch_size

        if "listen_batch_page" not in st.session_state:
            st.session_state.listen_batch_page = 0

        if st.session_state.listen_batch_page >= total_batches:
            st.session_state.listen_batch_page = 0

        batch_options = [f"第 {i*10 + 1} 〜 {min((i+1)*10, total_questions)} 問目 (グループ {i+1}/{total_batches})" for i in range(total_batches)]

        def sync_batch_selection():
            st.session_state.listen_batch_page = batch_options.index(st.session_state.selected_batch_str)

        st.selectbox(
            "再生する問題範囲を選択:", 
            batch_options, 
            index=st.session_state.listen_batch_page, 
            key="selected_batch_str",
            on_change=sync_batch_selection
        )

        current_batch_page = st.session_state.listen_batch_page

        start_q = current_batch_page * batch_size
        end_q = min(start_q + batch_size, total_questions)
        current_batch_rows = target_rows.iloc[start_q:end_q]

        playlist = []
        found_audio_count = 0

        with st.spinner(f"第 {start_q + 1} 〜 {end_q} 問の音声をセット中..."):
            for _, row in current_batch_rows.iterrows():
                q_num_val = row.get("問題番号", "")
                limb_val = row.get("肢", "")
                q_text = str(row.get("文章", ""))
                a_text = f"正解: {row.get('正誤', '')} ｜ 解説: {row.get('簡単な解説', '')}"

                q_file = get_audio_file_path("Q", q_num_val, limb_val)
                a_file = get_audio_file_path("A", q_num_val, limb_val)

                if q_file and os.path.exists(q_file):
                    try:
                        with open(q_file, "rb") as f:
                            b64_str = base64.b64encode(f.read()).decode("utf-8")
                        playlist.append({
                            "title": f"【問題】{q_num_val} 肢{limb_val}",
                            "text": q_text,
                            "url": f"data:audio/mp3;base64,{b64_str}"
                        })
                        found_audio_count += 1
                    except Exception:
                        pass

                if a_file and os.path.exists(a_file):
                    try:
                        with open(a_file, "rb") as f:
                            b64_str = base64.b64encode(f.read()).decode("utf-8")
                        playlist.append({
                            "title": f"【解説】{q_num_val} 肢{limb_val}",
                            "text": a_text,
                            "url": f"data:audio/mp3;base64,{b64_str}"
                        })
                        found_audio_count += 1
                    except Exception:
                        pass

        if playlist:
            st.success(f"全 {total_questions} 問中、第 {start_q + 1} 〜 {end_q} 問（{found_audio_count} 件）をセットしました。")
            
            auto_start_flag = st.session_state.get("auto_play_next", False)
            st.session_state.auto_play_next = False

            render_continuous_player(playlist, current_batch_page, total_batches, auto_start=auto_start_flag)

            col1, col2 = st.columns(2)
            with col1:
                if current_batch_page > 0:
                    if st.button("⏮ 前の10問へ", use_container_width=True, key="btn_prev_batch"):
                        st.session_state.listen_batch_page -= 1
                        st.session_state.auto_play_next = True
                        st.rerun()
            with col2:
                if current_batch_page + 1 < total_batches:
                    if st.button("次の10問へ ⏩", use_container_width=True, key="btn_next_batch"):
                        st.session_state.listen_batch_page += 1
                        st.session_state.auto_play_next = True
                        st.rerun()
        else:
            st.error("選択された区間の対応音声ファイルが見つかりませんでした。")


# ルート4：AIに質問（チャット）
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
        for msg in st.session_state.main_messages:
            avatar_img = "images/1_teacher_normal.png" if msg["role"] == "assistant" else None
            with st.chat_message(msg["role"], avatar=avatar_img):
                st.markdown(msg["content"])

        prompt = st.chat_input("質問を入力してください")
        if prompt:
            st.session_state.chat_count += 1
            st.session_state.main_messages.append({"role": "user", "content": prompt})
            st.session_state.teacher_state = "thinking"
            st.session_state.main_waiting = True
            st.rerun()

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