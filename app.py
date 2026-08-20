import base64
from datetime import datetime, timezone
import json
import os
import random
import re
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
import requests
import stripe
import streamlit as st
from streamlit_clickable_images import clickable_images
from supabase import create_client

# ページの設定
st.set_page_config(
    page_title="田中式 司法書士一問一答｜法律特化AI講師とチャットで会話！その場で疑問をスピード解決", 
    page_icon="📖", 
    layout="centered"
)

# セッション状態の初期化
if "user" not in st.session_state:
    st.session_state["user"] = None

# Supabaseクライアントの初期化（一般操作用）
@st.cache_resource
def init_connection():
    url = st.secrets["supabase"]["SUPABASE_URL"]
    key = st.secrets["supabase"]["SUPABASE_KEY"]
    return create_client(url, key)

supabase = init_connection()

# Supabase管理者クライアントの初期化（退会・アカウント削除用）
def init_admin_connection():
    url = st.secrets["supabase"]["SUPABASE_URL"]
    service_role_key = st.secrets["supabase"].get("SUPABASE_SERVICE_ROLE_KEY", "")
    if service_role_key:
        return create_client(url, service_role_key)
    return None

supabase_admin = init_admin_connection()

# Stripe APIキーのセットアップ
if "stripe" in st.secrets and "STRIPE_SECRET_KEY" in st.secrets["stripe"]:
    stripe.api_key = st.secrets["stripe"]["STRIPE_SECRET_KEY"]

# 1. 認証機能（Supabase Auth）
def login(email, password):
    try:
        res = supabase.auth.sign_in_with_password({
            "email": email,
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
        res = supabase.auth.sign_up({
            "email": email,
            "password": password
        })
        return res
    except Exception as e:
        st.error(f"登録エラー: {e}")
        return None

# パスワード更新処理（ログイン中用）
def update_password(new_password):
    try:
        res = supabase.auth.update_user({"password": new_password})
        if res.user:
            return True
        return False
    except Exception as e:
        st.error(f"パスワード変更エラー: {e}")
        return False

# パスワード再設定メール送信処理（ログイン前用）
def reset_password_request(email):
    try:
        res = supabase.auth.reset_password_for_email(email)
        return True
    except Exception as e:
        st.error(f"送信エラー: {e}")
        return False

# 契約初期レコード作成
def ensure_subscription_record(email, user_id):
    try:
        response = supabase.table("subscriptions").select("email").eq("email", email).execute()
        if not response.data:
            supabase.table("subscriptions").insert({
                "email": email,
                "user_id": user_id,
                "status": "inactive",
                "cancel_at_period_end": False,
                "current_period_end": "1970-01-01T00:00:00+00:00"
            }).execute()
    except Exception as e:
        print(f"ensure_subscription_record Error: {e}")

def sync_subscription_from_stripe(email):
    if not stripe.api_key or not email:
        return False, "APIキーまたはメールアドレスが設定されていません"
    try:
        clean_email = email.strip().lower()
        sub_id, sub_data = get_stripe_subscription_info(clean_email)
        
        # Stripe上にサブスク情報が見つからない場合はDBを上書きせず終了
        if not sub_data:
            return False, "Stripe上にサブスクデータが見つかりませんでした（既存データを維持します）"

        def get_field(obj, key, default=None):
            if isinstance(obj, dict):
                val = obj.get(key, default)
            else:
                val = getattr(obj, key, default)
            return val if val is not None else default

        status = get_field(sub_data, "status", "inactive")
        current_period_end = get_field(sub_data, "current_period_end", 0)

        raw_cancel_at_period_end = get_field(sub_data, "cancel_at_period_end", False)
        raw_cancel_at = get_field(sub_data, "cancel_at", None)
        cancel_at_period_end = bool(raw_cancel_at_period_end or (raw_cancel_at is not None))

        if current_period_end > 0:
            end_iso = datetime.fromtimestamp(current_period_end, tz=timezone.utc).isoformat()
        else:
            end_iso = "1970-01-01T00:00:00+00:00"

        # 常に有効な値を入れて NULL の混入を防ぐ
        res = supabase.table("subscriptions").update({
            "status": status,
            "cancel_at_period_end": cancel_at_period_end,
            "current_period_end": end_iso
        }).eq("email", clean_email).execute()

        if not res.data:
            return False, f"DB更新対象が見つかりません (検索email: {clean_email})"

        return True, f"同期成功: status={status}, current_period_end={end_iso}"

    except Exception as e:
        return False, f"DB更新エラー: {e}"

# 判定関数（アクセスの直前にStripeと同期を実行）
def check_access(email):
    try:
        # Stripeから最新情報を同期
        sync_subscription_from_stripe(email)

        response = supabase.table("subscriptions").select("*").eq("email", email).execute()
        data = response.data
        if data:
            subscription = data[0]
            period_end = subscription.get("current_period_end", "1970-01-01T00:00:00+00:00")
            
            # 初期値やNULLではない有効期限データが存在すること
            if period_end and period_end != "1970-01-01T00:00:00+00:00":
                try:
                    # Supabaseから取得した日時文字列をUTC付きでパース
                    end_date = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    
                    # サブスク解約済みであっても、現在時刻が有効期限内であればアクセス許可
                    if now <= end_date:
                        return True
                except Exception as parse_err:
                    print(f"日付パースエラー: {parse_err}")

        return False
    except Exception as e:
        print(f"check_access Error: {e}")
        return False

# Stripeのサブスクリプション状態を取得する関数（稼働中優先）
def get_stripe_subscription_info(email):
    if not stripe.api_key or not email:
        return None, None
    try:
        clean_email = email.strip().lower()
        customers = stripe.Customer.list(email=clean_email, limit=10)
        if not customers.data:
            return None, None
        
        all_subs = []
        for customer in customers.data:
            subs = stripe.Subscription.list(customer=customer.id, status="all", limit=10)
            all_subs.extend(subs.data)
            
        if all_subs:
            # active または trialing のサブスクを優先。無ければ全体から最新を取得
            active_subs = [s for s in all_subs if getattr(s, "status", None) in ["active", "trialing"]]
            target_sub = max(active_subs, key=lambda x: x.created) if active_subs else max(all_subs, key=lambda x: x.created)
            return target_sub.id, target_sub
            
        return None, None
    except Exception as e:
        print(f"Stripe Error: {e}")
        return None, None

# 退会実行処理
def execute_account_deletion(user_email, user_id):
    try:
        # 1. Stripe側サブスクリプションの即時キャンセル処理
        sub_id, sub_data = get_stripe_subscription_info(user_email)
        if sub_id and sub_data and sub_data.status in ["active", "trialing"]:
            stripe.Subscription.cancel(sub_id)

        # 2. Supabase DBの契約テーブルレコード削除
        supabase.table("subscriptions").delete().eq("email", user_email).execute()

        # 3. Supabase Authからユーザーアカウント完全削除
        if supabase_admin:
            supabase_admin.auth.admin.delete_user(user_id)
        else:
            st.error("管理者キー（SUPABASE_SERVICE_ROLE_KEY）が設定されていないため、アカウント削除を完了できませんでした。")
            return False

        return True
    except Exception as e:
        st.error(f"退会処理中にエラーが発生しました: {e}")
        return False

# 共通ユーティリティ関数
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

# 法的表記・モーダルダイアログ機能
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

# パスワード変更ダイアログ（ログイン中用）
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

# パスワード再設定ダイアログ（ログイン前用）
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

# 退会ダイアログ関数
@st.dialog("退会手続き（アカウント完全削除）", width="medium")
def show_delete_account_dialog():
    if not st.session_state.get("user"):
        st.warning("ログインしていません。")
        return

    curr_email = st.session_state["user"]["email"]
    curr_id = st.session_state["user"]["id"]

    # 1. Supabaseのsubscriptionsテーブルから契約情報を取得
    is_active_recurring = False
    try:
        response = supabase.table("subscriptions").select("status, cancel_at_period_end").eq("email", curr_email).execute()
        if response.data:
            sub_data = response.data[0]
            status = sub_data.get("status")
            cancel_at_period_end = sub_data.get("cancel_at_period_end", False)

            if status in ["active", "trialing"] and not cancel_at_period_end:
                is_active_recurring = True
    except Exception as e:
        print(f"DB取得エラー: {e}")

    # 2. 自動更新が継続中の場合は退会をブロックして解約へ誘導
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
        st.info("※Stripe画面で解約手続き（自動更新の停止）を完了後、再度この画面からアカウント削除を行ってください。")
        return

    # 3. 自動更新停止済みまたは未契約の場合は注意事項を経て退会を許可
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

# URLパラメータ判定
if st.query_params.get("page") == "tokusho":
    show_tokusho_dialog()

# 特商法ダイアログから遷移指示があった場合に退会ダイアログを開く
if st.session_state.get("show_delete_modal"):
    st.session_state["show_delete_modal"] = False
    show_delete_account_dialog()

# A. 未ログイン時の表示
if not st.session_state.get("user"):
    bg_pc_b64 = get_image_base64("images/1_background_PC.png")
    if not bg_pc_b64:
        bg_pc_b64 = get_image_base64("1_background_PC.png")
    
    bg_sp_b64 = get_image_base64("images/1_background_mobile.png")
    if not bg_sp_b64:
        bg_sp_b64 = get_image_base64("1_background_mobile.png")
    
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
                padding-top: 28vh !important;
                max-width: 92% !important;
                margin: 0 auto !important;
            }}
        }}

        [data-testid="stMainBlockContainer"] h1,
        [data-testid="stMainBlockContainer"] h2,
        [data-testid="stMainBlockContainer"] h3,
        [data-testid="stMainBlockContainer"] p,
        [data-testid="stMainBlockContainer"] label,
        [data-testid="stMainBlockContainer"] span,
        label[data-testid="stWidgetLabel"] p,
        div[data-testid="stWidgetLabel"] p,
        .stCheckbox label span {{
            color: #0F172A !important;
            font-weight: 700 !important;
        }}

        div[data-baseweb="tab-list"] {{
            background-color: transparent !important;
            border-bottom: 2px solid #cbd5e1 !important;
            margin-bottom: 12px !important;
        }}

        button[data-baseweb="tab"] {{
            padding-top: 2px !important;
            padding-bottom: 8px !important;
        }}

        button[data-baseweb="tab"] p {{
            color: #0F172A !important;
            font-weight: 700 !important;
            font-size: 1.05rem !important;
        }}

        div[data-baseweb="tab-panel"] {{
            background-color: transparent !important;
            padding: 0 !important;
            box-shadow: none !important;
        }}

        div[data-testid="stTextInput"] {{
            margin-bottom: -8px !important;
        }}

        [data-testid="stForm"] {{
            background-color: transparent !important;
            border: none !important;
            padding: 0 !important;
            box-shadow: none !important;
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

# B. 未契約・期限切れ時の案内画面表示
user_email = st.session_state["user"]["email"]
user_id = st.session_state["user"]["id"]

ensure_subscription_record(user_email, user_id)

if not check_access(user_email):
  
    st.title("契約のご案内")
    st.warning("有効なサブスクリプションが確認できませんでした。AI学習機能を利用するには有料プランへのご登録が必要です。")
    
    base_stripe_url = st.secrets["stripe"]["STRIPE_PAYMENT_LINK"]
    stripe_url = f"{base_stripe_url}?prefilled_email={user_email}&client_reference_id={user_id}"
    
    st.link_button("決済画面へ進む（Stripe Checkout）", stripe_url, type="primary", use_container_width=True)
    
    st.markdown("---")
    col_unsub_tokusho, col_unsub_delete = st.columns(2)
    with col_unsub_tokusho:
        if st.button("特定商取引法に基づく表記", key="btn_tokusho_unsubscribed", use_container_width=True):
            show_tokusho_dialog()
    with col_unsub_delete:
        if st.button("退会手続き（アカウント削除）", key="btn_delete_unsubscribed", use_container_width=True):
            show_delete_account_dialog()

    st.stop()

# C. メイン処理
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
        "user": st.session_state["user"].get("id", "windows-user"),
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
            return f"エラーが発生しました (ステータスコード: {response.status_code})", conversation_id
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

# 音声生成（VOICEVOX連携）
def get_voice_audio_base64(text, speaker_id=2):
    voicevox_url = st.secrets["voicevox"]["VOICEVOX_URL"]
    try:
        res1 = requests.post(f"{voicevox_url}/audio_query?text={text}&speaker={speaker_id}")
        query = res1.json()
        res2 = requests.post(
            f"{voicevox_url}/synthesis?speaker={speaker_id}",
            headers={"Content-Type": "application/json"},
            data=json.dumps(query),
        )
        audio_b64 = base64.b64encode(res2.content).decode("utf-8")
        return f"data:audio/wav;base64,{audio_b64}"
    except Exception:
        return ""

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

# -----------------------------------------------

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

menu = st.sidebar.radio("移動先を選択", ["年度別", "科目別", "AIに質問（チャット）"])

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

                col_audio_btn, col_audio_space = st.columns([1, 2])
                with col_audio_btn:
                    if st.button("🔊 問題文を読み上げる", key=f"btn_audio_y_{ptr}", use_container_width=True):
                        with st.spinner("音声を生成中..."):
                            audio_src = get_voice_audio_base64(row.get("文章", ""))
                            if audio_src:
                                st.audio(audio_src, format="audio/wav", autoplay=True)
                            else:
                                st.error("音声生成に失敗しました。VOICEVOXが起動しているか確認してください。")

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

                col_audio_btn_c, col_audio_space_c = st.columns([1, 2])
                with col_audio_btn_c:
                    if st.button("🔊 問題文を読み上げる", key=f"btn_audio_c_{ptr_c}", use_container_width=True):
                        with st.spinner("音声を生成中..."):
                            audio_src = get_voice_audio_base64(row.get("文章", ""))
                            if audio_src:
                                st.audio(audio_src, format="audio/wav", autoplay=True)
                            else:
                                st.error("音声生成に失敗しました。VOICEVOXが起動しているか確認してください。")

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

# ルート3：AIに質問（チャット）
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