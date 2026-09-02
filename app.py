from flask import Flask, request, jsonify, redirect, url_for, session, render_template
from flask import send_from_directory
from flask import render_template
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask import request
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import timedelta, datetime
from supabase import create_client, Client
import os
import requests
import stripe
import re
import smtplib
from coverclear_rag import chunk_policy, retrieve_chunks, build_context
from email.message import EmailMessage

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b\d{3}\s\d{2}\s\d{4}\b"), "[REDACTED-SSN]"),
    (re.compile(r"\b\d{9}\b"), "[REDACTED-ID]"),
    (re.compile(
        r"\b(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)\d{3}[-.\s]?\d{4}\b"
    ), "[REDACTED-PHONE]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[REDACTED-EMAIL]"),
    (re.compile(
        r"(?i)\b(?:dob|date of birth)\s*[:\-]\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}"
    ), "Date of Birth: [REDACTED-DOB]"),
    (re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b"), "[REDACTED-DATE]"),
]

def redact_pii(text: str):
    cleaned = text or ""
    count = 0
    for pattern, repl in _PII_PATTERNS:
        cleaned, n = pattern.subn(repl, cleaned)
        count += n
    return cleaned, count

load_dotenv()

base_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(base_dir, 'templates'))

app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

# ====================== SUPABASE SETUP ======================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ====================== STRIPE SETUP ========================
stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
STRIPE_PUBLISHABLE_KEY = os.getenv('STRIPE_PUBLISHABLE_KEY')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET')

# Your Price IDs
PRICE_PRO = "price_1Tx7CSL1DZpx4P0VMkzrbzBY"
PRICE_CREDITS = "price_1Tx7CRL1DZpx4P0V7yNjcgwD"

# ====================== Get / Update User Data ======================
def get_user_data(user_id, email=None, name=None):
    """Fetch user from Supabase. Create if doesn't exist."""
    response = supabase.table("user_usage").select("*").eq("user_id", user_id).execute()

    if response.data:
        return response.data[0]

    # Create new user
    new_user = {
        "user_id": user_id,
        "email": email or f"user_{user_id}",
        "tier": "free",
        "analyses_used": 0
    }
    insert_response = supabase.table("user_usage").insert(new_user).execute()
    return insert_response.data[0]


def update_usage(user_id):
    """Increment analyses_used by 1"""
    user = get_user_data(user_id)
    new_count = user["analyses_used"] + 1

    supabase.table("user_usage").update({
        "analyses_used": new_count,
        "updated_at": datetime.utcnow().isoformat()
    }).eq("user_id", user_id).execute()

    return new_count


# ====================== AUTH SETUP ======================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login_page'


class User(UserMixin):
    def __init__(self, user_id, email, name=None):
        self.id = user_id
        self.email = email
        self.name = name


@login_manager.user_loader
def load_user(user_id):
    if 'email' in session:
        return User(user_id, session.get('email'), session.get('name'))
    return None


oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

oauth.register(
    name='x',
    client_id=os.getenv('X_CLIENT_ID'),
    client_secret=os.getenv('X_CLIENT_SECRET'),
    authorize_url='https://twitter.com/i/oauth2/authorize',
    access_token_url='https://api.twitter.com/2/oauth2/token',
    api_base_url='https://api.twitter.com/2/',
    client_kwargs={
        'scope': 'users.read tweet.read offline.access',
        'token_endpoint_auth_method': 'client_secret_basic',
        'code_challenge_method': 'S256'      # ← This is the critical line
    }
)

GROK_API_KEY = os.getenv('GROK_API_KEY')


# ====================== PUBLIC PAGES ======================
@app.route('/')
def index():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('index.html', email=email)


@app.route('/termguard')   # or whatever the path is
@app.route('/termguard.html')
def termguard():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('termguard.html', email=email)

@app.route('/coverclear')
@app.route('/coverclear.html')
def coverclear():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('coverclear.html', email=email)

@app.route('/billclear')
@app.route('/billclear.html')
def billclear():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('billclear.html', email=email)

@app.route('/pricing.html')
def pricing():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('pricing.html', email=email)


@app.route('/about.html')  # use your real path if different
def about():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('about.html', email=email)


@app.route('/privacy')
@app.route('/privacy.html')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
@app.route('/terms.html')
def terms():
    return render_template('terms.html')

@app.route('/login')
@app.route('/login.html')
def login_page():
    next_action = request.args.get('next', 'home')
    session['login_next'] = next_action
    return render_template('login.html')

@app.route('/robots.txt')
def robots():
    return send_from_directory(app.root_path, 'robots.txt')

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory(app.root_path, 'sitemap.xml')

@app.route('/llms.txt')
def llms_txt():
    return send_from_directory('.', 'llms.txt', mimetype='text/plain')

@app.route('/d50792e898e5425db44c3fe3babe6f31.txt')
def indexnow_key_file():
    return send_from_directory('.', 'd50792e898e5425db44c3fe3babe6f31.txt', mimetype='text/plain')

@app.route('/tg_app')
@app.route('/tg_app.html')
@app.route('/app')
@login_required
def tg_app():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('tg_app.html', email=email)

@app.route('/cc_app')
@app.route('/cc_app.html')
@login_required
def cc_app():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)

    user = get_user_data(current_user.id)
    tier = user.get("tier", "free")
    analyses_used = user.get("analyses_used", 0)

    TIER_LIMITS = {
        "free": 3,
        "credits": 45,
        "pro": 99999
    }
    limit = TIER_LIMITS.get(tier, 3)
    remaining = max(0, limit - analyses_used)

    return render_template(
        'cc_app.html',
        email=email,
        tier=tier,
        analyses_used=analyses_used,
        limit=limit,
        remaining=remaining
    )

@app.route('/bc_app')
@app.route('/bc_app.html')
@login_required
def bc_app():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)

    user = get_user_data(current_user.id)
    tier = user.get("tier", "free")
    analyses_used = user.get("analyses_used", 0)
    TIER_LIMITS = {"free": 3, "credits": 45, "pro": 99999}
    limit = TIER_LIMITS.get(tier, 3)
    remaining = max(0, limit - analyses_used)

    return render_template(
        'bc_app.html',
        email=email,
        tier=tier,
        analyses_used=analyses_used,
        limit=limit,
        remaining=remaining
    )

def redirect_after_login():
    next_action = session.pop("login_next", None) or "home"

    if next_action == "app":
        return redirect("/tg_app")
    if next_action == "coverclear":
        return redirect("/cc_app")
    if next_action == "billclear":
        return redirect("/bc_app")
    if next_action == "credits":
        return redirect("/create-checkout?plan=credits")
    if next_action == "pro":
        return redirect("/create-checkout?plan=pro")
    if next_action == "billing":
        return redirect("/billing-portal")
    return redirect("/")

@app.route('/cl_app')
@app.route('/cl_app.html')
@login_required
def cl_app():
    return render_template('cl_app.html')

# ---------- Email ContactUs ----

@app.route('/contact')
@app.route('/contact.html')
def contact_page():
    email = session.get('email')
    if not email and current_user.is_authenticated:
        email = getattr(current_user, 'email', None)
    return render_template('contact.html', email=email)

@app.route('/contact', methods=['POST'])
def contact_submit():
    # Honeypot — bots fill this, humans never see it
    if (request.form.get('company_website') or '').strip():
        return redirect('/contact?sent=1')

    name = (request.form.get('name') or '').strip()[:120]
    reply_to = (request.form.get('email') or '').strip()[:200]
    reason = (request.form.get('reason') or 'other').strip()[:40]
    message = (request.form.get('message') or '').strip()[:4000]

    allowed = {'support', 'partnerships', 'press', 'other'}
    if reason not in allowed:
        reason = 'other'

    if not name or not reply_to or '@' not in reply_to or not message:
        return render_template(
            'contact.html',
            email=session.get('email'),
            error='Please fill in your name, email, and message.'
        ), 400

    smtp_user = os.environ.get('SMTP_USER')
    smtp_password = os.environ.get('SMTP_PASSWORD')
    contact_to = os.environ.get('CONTACT_TO', smtp_user)

    if not smtp_user or not smtp_password:
        return render_template(
            'contact.html',
            email=session.get('email'),
            error='Mail is not configured yet. Please try again later.'
        ), 500

    msg = EmailMessage()
    msg['Subject'] = f'[NexusDocs {reason}] {name}'
    msg['From'] = smtp_user
    msg['To'] = contact_to
    msg['Reply-To'] = reply_to
    msg.set_content(
        f'Name: {name}\n'
        f'Email: {reply_to}\n'
        f'Reason: {reason}\n\n'
        f'{message}'
    )

    try:
        with smtplib.SMTP(os.environ.get('SMTP_HOST', 'smtp.protonmail.ch'),
                          int(os.environ.get('SMTP_PORT', 587))) as server:
            server.starttls()
            server.login(smtp_user, smtp_password)
            server.send_message(msg)
    except Exception:
        return render_template(
            'contact.html',
            email=session.get('email'),
            error='Could not send the message. Please try again.'
        ), 502

    return redirect('/contact?sent=1')

# ---------- Insights ----------
@app.route('/insights/')
def insights_slash():
    return redirect('/insights', code=301)

@app.route('/insights')
def insights_index():
    return render_template('insights/index.html')

@app.route('/insights/burst-pipe-renters-coverage')
@app.route('/insights/burst-pipe-renters-coverage/')
def insights_burst_pipe_renters():
    return render_template('insights/burst-pipe-renters-coverage.html')

@app.route('/insights/nda-residual-unaided-memory')
@app.route('/insights/nda-residual-unaided-memory/')
def insights_nda_residual_unaided_memory():
    return render_template('insights/nda-residual-unaided-memory.html')

@app.route('/insights/how-we-read-long-documents')
@app.route('/insights/how-we-read-long-documents/')
def insights_how_we_read_long_documents():
    return render_template('insights/how-we-read-long-documents.html')

@app.route('/insights/arbitration-clauses')
@app.route('/insights/arbitration-clauses/')
def insights_arbitration_clauses():
    return render_template('insights/arbitration-clauses.html')

@app.route('/insights/why-medical-bills-feel-impossible')
@app.route('/insights/why-medical-bills-feel-impossible/')
def insights_medical_bills():
    return render_template('insights/why-medical-bills-feel-impossible.html')

@app.route('/insights/two-documents-that-cost-you')
@app.route('/insights/two-documents-that-cost-you/')
def insights_two_documents():
    return render_template('insights/two-documents-that-cost-you.html')

@app.route('/insights/nda-clauses-to-watch')
@app.route('/insights/nda-clauses-to-watch/')
def insights_nda_clauses():
    return render_template('insights/nda-clauses-to-watch.html')

@app.route('/insights/chat-vs-termsguard')
@app.route('/insights/chat-vs-termsguard/')
def insights_chat_vs_termsguard():
    return render_template('insights/chat-vs-termsguard.html')

@app.route('/insights/data-when-company-sold-or-shuts-down')
@app.route('/insights/data-when-company-sold-or-shuts-down/')
def insights_data_company_sold():
    return render_template('insights/data-when-company-sold-or-shuts-down.html')

@app.route('/insights/cancellation-auto-renewal-traps')
@app.route('/insights/cancellation-auto-renewal-traps/')
def insights_cancellation_traps():
    return render_template('insights/cancellation-auto-renewal-traps.html')

@app.route('/insights/how-companies-make-money-from-your-data')
@app.route('/insights/how-companies-make-money-from-your-data/')
def insights_data_monetization():
    return render_template('insights/data-monetization.html')

@app.route('/insights/scary-clauses')
@app.route('/insights/scary-clauses/')
def insights_scary_clauses():
    return render_template('insights/scary-clauses.html')

# ====================== AUTH ROUTES ======================
@app.route('/auth/google')
def google_login():
    redirect_uri = "https://nexusdocs.ai/auth/google/callback"
    return google.authorize_redirect(redirect_uri)


@app.route('/auth/google/callback')
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = google.parse_id_token(token, nonce=token.get('nonce'))

        email = user_info['email']
        name = user_info.get('name')

        user = User(email, email, name)
        login_user(user, remember=True)

        session['email'] = email
        session['name'] = name
        session.permanent = True

        # Ensure user exists in Supabase
        get_user_data(email, email, name)

        return redirect_after_login()

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Login failed: {str(e)}", 500


@app.route('/auth/x')
def x_login():
    redirect_uri = "https://nexusdocs.ai/auth/x/callback"
    return oauth.x.authorize_redirect(redirect_uri)


@app.route('/auth/x/callback')
def x_callback():
    try:
        token = oauth.x.authorize_access_token()
        print("TOKEN:", token)

        # Explicitly pass the token
        resp = oauth.x.get(
            'users/me',
            params={'user.fields': 'id,name,username'},
            token=token
        )

        print("STATUS:", resp.status_code)
        print("BODY:", resp.text)

        data = resp.json()
        user_info = data.get('data', {})

        if not user_info:
            return f"Failed to get user info<br>Status: {resp.status_code}<br>Response: {resp.text}", 400

        username = user_info.get('username', 'xuser')
        name = user_info.get('name') or username
        email = f"{username}@x.com"

        user = User(email, email, name)
        login_user(user, remember=True)

        session['email'] = email
        session['name'] = name
        session.permanent = True

        get_user_data(email, email, name)

        return redirect_after_login()

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"X Login failed: {str(e)}", 500

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    return redirect('/')

@app.route('/create-checkout')
def create_checkout():
    """After login: send user straight to Stripe for credits or pro."""
    user_email = session.get('email')
    if not user_email:
        plan = request.args.get('plan', 'credits')
        return redirect(f'/login?next={plan}')

    plan = request.args.get('plan', 'credits')

    if plan == 'pro':
        price_id = PRICE_PRO
        mode = 'subscription'
    else:
        price_id = PRICE_CREDITS
        mode = 'payment'

    try:
        checkout_session = stripe.checkout.Session.create(
            customer_email=user_email,
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode=mode,
            success_url='https://nexusdocs.ai/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://nexusdocs.ai/pricing.html',
            metadata={
                'user_email': user_email,
                'price_id': price_id
            }
        )
        return redirect(checkout_session.url)
    except Exception as e:
        return f"Checkout error: {str(e)}", 500

@app.route('/billing-portal')
def billing_portal():
    """Send logged-in user to Stripe Customer Portal to manage/cancel subscription."""
    user_email = session.get('email')
    if not user_email:
        return redirect('/login?next=billing')

    try:
        customers = stripe.Customer.list(email=user_email, limit=1)
        if not customers.data:
            return (
                "No billing account found for this email. "
                "Subscribe first, or contact support if you already paid.",
                404,
            )

        next_dest = request.args.get("next", "app")
        if next_dest == "coverclear":
            return_url = "https://nexusdocs.ai/cc_app"
        elif next_dest == "billclear":
            return_url = "https://nexusdocs.ai/bc_app"
        else:
            return_url = "https://nexusdocs.ai/tg_app"

        portal_session = stripe.billing_portal.Session.create(
            customer=customers.data[0].id,
            return_url=return_url,
        )

        return redirect(portal_session.url)
    except Exception as e:
        return f"Billing portal error: {str(e)}", 500

@app.route('/create-checkout-session', methods=['POST'])
def create_checkout_session():
    try:
        data = request.json
        price_id = data.get('price_id')
        user_email = session.get('email')

        if not user_email:
            return jsonify({"error": "Please log in first"}), 401

        # Determine mode (subscription or one-time)
        mode = 'subscription' if price_id == PRICE_PRO else 'payment'

        checkout_session = stripe.checkout.Session.create(
            customer_email=user_email,
            line_items=[{
                'price': price_id,
                'quantity': 1,
            }],
            mode=mode,
            success_url='https://nexusdocs.ai/success?session_id={CHECKOUT_SESSION_ID}',
            cancel_url='https://nexusdocs.ai/pricing.html',
            metadata={
                'user_email': user_email,
                'price_id': price_id
            }
        )
        return jsonify({'url': checkout_session.url})
    except Exception as e:
        return jsonify(error=str(e)), 500


@app.route('/success')
def success():
    return """
    <html>
    <head>
        <title>Payment Successful</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-50 flex items-center justify-center min-h-screen">
        <div class="bg-white p-10 rounded-2xl shadow-lg text-center max-w-md">
            <div class="text-5xl mb-4">🎉</div>
            <h1 class="text-2xl font-bold mb-2">Payment Successful!</h1>
            <p class="text-gray-600 mb-6">Your account has been upgraded. You can now enjoy the full features.</p>
            <a href="/tg_app" class="bg-blue-600 text-white px-6 py-3 rounded-xl font-medium">
                Go to TermsGuard
            </a>
        </div>
    </body>
    </html>
    """


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    print("=== WEBHOOK RECEIVED ===")

    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
        print("Signature verified successfully")
    except Exception as e:
        print(f"Webhook verification failed: {e}")
        return jsonify({"error": "verification failed"}), 400

    try:
        # ========== 1. First-time purchase ==========
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']

            try:
                session_dict = session.to_dict() if hasattr(session, 'to_dict') else dict(session)
            except Exception:
                session_dict = session

            customer_email = session_dict.get('customer_email')
            if not customer_email:
                customer_details = session_dict.get('customer_details') or {}
                customer_email = customer_details.get('email')

            metadata = session_dict.get('metadata') or {}
            price_id = metadata.get('price_id')

            print(f"Checkout completed | Email: {customer_email} | Price: {price_id}")

            if customer_email and price_id:
                if price_id == PRICE_PRO:
                    supabase.table('user_usage').update({
                        'tier': 'pro',
                        'analyses_used': 0
                    }).eq('email', customer_email).execute()
                    print("→ Set to Pro (first purchase)")
                elif price_id == PRICE_CREDITS:
                    supabase.table('user_usage').update({
                        'tier': 'credits',
                        'analyses_used': 0
                    }).eq('email', customer_email).execute()
                    print("→ Set to Credits (first purchase)")

        # ========== 2. Monthly renewal (Pro subscription) ==========
        elif event['type'] == 'invoice.paid':
            invoice = event['data']['object']

            try:
                invoice_dict = invoice.to_dict() if hasattr(invoice, 'to_dict') else dict(invoice)
            except Exception:
                invoice_dict = invoice

            customer_email = None
            customer_id = invoice_dict.get('customer')

            if customer_id:
                try:
                    customer = stripe.Customer.retrieve(customer_id)
                    customer_email = customer.email
                except Exception as e:
                    print(f"Could not retrieve customer: {e}")

            if customer_email:
                supabase.table('user_usage').update({
                    'analyses_used': 0
                }).eq('email', customer_email).execute()
                print(f"→ Monthly renewal: Reset analyses_used to 0 for {customer_email}")
            else:
                print("→ invoice.paid received but no customer email found")

    except Exception as e:
        print(f"Error processing webhook: {e}")

    print("=== WEBHOOK FINISHED SUCCESSFULLY ===")
    return jsonify({"status": "success"}), 200

# ====================== API ROUTES ======================

@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    try:
        data = request.json or {}
        user_id = current_user.id
        prompt_type = data.get('type', 'summary')
        raw_text = data.get('text', '') or ''
        question = (data.get('question') or '').strip()
        doc_type = (data.get('doc_type') or 'other').strip().lower()

        allowed_docs = {'tos', 'privacy', 'nda', 'msa', 'lease', 'other'}
        if doc_type not in allowed_docs:
            doc_type = 'other'

        user = get_user_data(user_id)
        tier = user.get("tier", "free")
        analyses_used = user.get("analyses_used", 0)

        print(
            f"DEBUG: TermsGuard {current_user.email} | Tier: {tier} | "
            f"Credits used: {analyses_used} | Doc: {doc_type} | Type: {prompt_type}"
        )

        TIER_LIMITS = {
            "free": 3,
            "credits": 45,
            "pro": 99999
        }
        TIER_CHAR_LIMITS = {
            "free": 5000,
            "credits": 50000,
            "pro": 50000
        }
        TIER_MODELS = {
            "free": "grok-4.3",
            "credits": "grok-4.5",
            "pro": "grok-4.5"
        }

        limit = TIER_LIMITS.get(tier, 3)
        char_limit = TIER_CHAR_LIMITS.get(tier, 5000)
        model = TIER_MODELS.get(tier, "grok-4.3")

        truncated = len(raw_text) > char_limit
        text, pii_redacted = redact_pii(raw_text[:char_limit])

        doc_label = {
            "tos": "terms of service / terms of use",
            "privacy": "privacy policy",
            "nda": "non-disclosure agreement (NDA)",
            "msa": "master service / freelance / vendor agreement",
            "lease": "lease or rental agreement",
            "other": "legal document",
        }[doc_type]

        system_prompt = (
            "You are TermsGuard, a plain-English document explainer for NexusDocs. "
            "You are not a lawyer and you do not give legal advice.\n\n"
            "LEVEL-A RULES:\n"
            "- Use ONLY the document text the user provides. Do not browse the web. "
            "Do not invent clauses, section numbers, parties, dates, or exhibits.\n"
            "- If something is missing, undefined, or depends on another document or governing law, say so.\n"
            "- Separate three things when relevant:\n"
            "  1) What the text says\n"
            "  2) What you cannot know from this text alone\n"
            "  3) One practical question the user could ask the other party\n"
            "- Use simple language. Quote short phrases from the document when you flag a risk.\n"
            "- If asked a question the document does not answer, say it is not in the provided text."
        )

        focus = {
            "tos": "cancellation, auto-renewal, liability, arbitration, and what the user gives up by clicking I Agree",
            "privacy": "collection, sharing, selling, processors, retention, and user rights",
            "nda": "definition of confidential information, term, ownership/assignment, residuals/unaided memory, mutual vs one-sided duties, and remedies",
            "msa": "payment, IP ownership, termination, liability, indemnities, and non-solicit",
            "lease": "term, rent, deposits, repairs, termination, and fees",
            "other": "user rights, obligations, money, privacy, and lock-in",
        }[doc_type]

        if prompt_type == 'question':
            user_prompt = (
                f"The user selected document type: {doc_label}.\n"
                f"Answer this question using only the document below.\n"
                f"If the answer is not in the text, say so.\n\n"
                f"Question: {question}\n\n"
                f"Document:\n{text}"
            )
            credit_cost = 0
        elif prompt_type == 'risks':
            user_prompt = (
                f"The user selected document type: {doc_label}.\n"
                f"Extract privacy, data, billing, liability, and lock-in risks as bullet points.\n"
                f"Focus on: {focus}.\n"
                f"For each flag include: the issue, a short quote or paraphrase from the text, "
                f"why it matters, and what is missing if the text is incomplete.\n"
                f"Do not invent risks that are not supported by the text.\n\n"
                f"Document:\n{text}"
            )
            credit_cost = 0
        elif prompt_type == 'nda_checklist':
            user_prompt = (
                "This is an NDA checklist. Use ONLY the document text.\n"
                "Return JSON only. No markdown fences. No extra commentary.\n"
                "{\n"
                '  "is_nda": true,\n'
                '  "items": [\n'
                '    {"key":"residuals","label":"Residuals / unaided memory","status":"has","note":"one sentence"},\n'
                '    {"key":"residuals_exception","label":"Residuals exception (skills vs secrets)","status":"missing","note":"one sentence"},\n'
                '    {"key":"term_length","label":"Confidentiality term length","status":"has","note":"one sentence"},\n'
                '    {"key":"remedies","label":"Remedies (injunction, fees)","status":"unclear","note":"one sentence"}\n'
                "  ]\n"
                "}\n"
                'status must be exactly one of: has, missing, unclear.\n'
                "If this is not an NDA, set is_nda to false and mark items missing or unclear.\n\n"
                f"Document:\n{text}"
            )
            credit_cost = 0
        else:
            user_prompt = (
                f"The user selected document type: {doc_label}.\n"
                f"Summarize this document in plain English.\n"
                f"Focus on: {focus}.\n"
                f"Cover what the user is agreeing to, key obligations, and important limits.\n"
                f"End with 2-4 things that are unclear or not in this text.\n\n"
                f"Document:\n{text}"
            )
            credit_cost = 1

        if credit_cost > 0 and analyses_used >= limit:
            return jsonify({
                "error": f"You have used all your free analyses ({analyses_used}/{limit}). Please upgrade your plan.",
                "limit_reached": True,
                "analyses_used": analyses_used,
                "limit": limit,
                "remaining": 0,
                "tier": tier
            }), 403

        if credit_cost > 0 and analyses_used + credit_cost > limit:
            return jsonify({
                "error": f"Not enough analyses remaining. You need {credit_cost}.",
                "limit_reached": True,
                "analyses_used": analyses_used,
                "limit": limit,
                "remaining": max(0, limit - analyses_used),
                "tier": tier
            }), 403

        try:
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.3
                },
                timeout=90
            )
            response.raise_for_status()

        except requests.exceptions.Timeout:
            return jsonify({
                "error": "Analysis took too long. Please try a shorter document or try again."
            }), 504

        except requests.exceptions.RequestException:
            return jsonify({
                "error": "Failed to reach AI service. Please try again."
            }), 502

        result = response.json()
        content = (
            result.get('choices', [{}])[0]
            .get('message', {})
            .get('content', str(result))
        )

        if prompt_type == 'summary':
            new_count = analyses_used + credit_cost
            supabase.table("user_usage").update({
                "analyses_used": new_count,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("user_id", user_id).execute()
        else:
            new_count = analyses_used

        remaining = max(0, limit - new_count)

        return jsonify({
            "result": content,
            "tier": tier,
            "analyses_used": new_count,
            "limit": limit,
            "remaining": remaining,
            "truncated": truncated,
            "char_limit": char_limit,
            "model": model,
            "doc_type": doc_type
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ====================== COVERCLEAR ======================
@app.route('/analyze_coverclear', methods=['POST'])
@login_required
def analyze_coverclear():
    try:
        data = request.json or {}
        user_id = current_user.id
        prompt_type = data.get('type', 'summary')
        raw_text = data.get('text', '') or ''
        policy_type = (data.get('policy_type') or 'other').strip().lower()

        allowed_types = {'homeowners', 'renters', 'auto', 'business', 'other'}
        if policy_type not in allowed_types:
            policy_type = 'other'

        user = get_user_data(user_id)
        tier = user.get("tier", "free")
        analyses_used = user.get("analyses_used", 0)

        print(f"DEBUG: CoverClear {current_user.email} | Tier: {tier} | Credits used: {analyses_used} | Policy: {policy_type}")

        TIER_LIMITS = {
            "free": 3,
            "credits": 45,
            "pro": 99999
        }
        TIER_CHAR_LIMITS = {
            "free": 5000,
            "credits": 50000,
            "pro": 50000
        }
        TIER_MODELS = {
            "free": "grok-4.3",
            "credits": "grok-4.5",
            "pro": "grok-4.5"
        }

        limit = TIER_LIMITS.get(tier, 3)
        char_limit = TIER_CHAR_LIMITS.get(tier, 5000)
        model = TIER_MODELS.get(tier, "grok-4.3")

        truncated = len(raw_text) > char_limit
        question = (data.get('question') or '').strip()
        redacted = redact_pii(raw_text)
        chunks = chunk_policy(redacted)
        selected = retrieve_chunks(chunks, prompt_type, question=question or None)
        text = build_context(selected, char_limit)

        policy_hint = f"The user selected policy type: {policy_type}."
        system_prompt = (
            "You are a clear insurance explainer for regular people and small businesses. "
            "Use simple language. Use only the provided policy excerpts. "
            "If the excerpts do not contain the information needed, or something is unclear or missing, say so. "
            "Do not invent coverage, limits, exclusions, or duties. "
            "This is not insurance advice and not a substitute for the insurer or a licensed professional. "
            "Prefer the document excerpts over the selected policy type if they conflict, and mention the mismatch."
        )

        if prompt_type == 'question':
            user_prompt = (
                f"{policy_hint}\n"
                f"Answer this question in plain English using only the provided policy excerpts: {question}\n"
                "If the excerpts do not contain the answer, say so.\n\n"
                f"Policy excerpts:\n{text}"
            )
            credit_cost = 0
        elif prompt_type == 'risks':
            user_prompt = (
                f"{policy_hint}\n"
                "Extract exclusions, coverage gaps, limits, waiting periods, deductibles, "
                "and duties after a loss. Use short bullet points. "
                "Say what is typically NOT covered if the policy excerpts state it. "
                "Use only the provided excerpts; if something is missing, say so.\n\n"
                f"Policy excerpts:\n{text}"
            )
            credit_cost = 0
        else:
            user_prompt = (
                f"{policy_hint}\n"
                "Summarize this insurance policy in plain English using only the provided excerpts. Cover:\n"
                "- What is insured\n"
                "- Main coverages\n"
                "- Important limits / deductibles\n"
                "- Key exclusions\n"
                "- What the policyholder must do after a loss\n"
                "If an item is not in the excerpts, say it is missing.\n\n"
                f"Policy excerpts:\n{text}"
            )
            credit_cost = 1

        if credit_cost > 0 and analyses_used >= limit:
            return jsonify({
                "error": f"You have used all your analyses ({analyses_used}/{limit}). Please upgrade your plan.",
                "limit_reached": True,
                "analyses_used": analyses_used,
                "limit": limit,
                "remaining": 0,
                "tier": tier
            }), 403

        if credit_cost > 0 and analyses_used + credit_cost > limit:
            return jsonify({
                "error": f"Not enough analyses remaining. You need {credit_cost}.",
                "limit_reached": True,
                "analyses_used": analyses_used,
                "limit": limit,
                "remaining": max(0, limit - analyses_used),
                "tier": tier
            }), 403

        try:
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.4
                },
                timeout=90
            )
            response.raise_for_status()

        except requests.exceptions.Timeout:
            return jsonify({
                "error": "Analysis took too long. Please try a shorter document or try again."
            }), 504

        except requests.exceptions.RequestException:
            return jsonify({
                "error": "Failed to reach AI service. Please try again."
            }), 502

        result = response.json()
        content = (
            result.get('choices', [{}])[0]
            .get('message', {})
            .get('content', str(result))
        )

        if prompt_type == 'summary':
            new_count = analyses_used + credit_cost
            supabase.table("user_usage").update({
                "analyses_used": new_count,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("user_id", user_id).execute()
        else:
            new_count = analyses_used

        remaining = max(0, limit - new_count)

        return jsonify({
            "result": content,
            "summary": content if prompt_type == 'summary' else None,
            "flags": content if prompt_type == 'risks' else None,
            "tier": tier,
            "analyses_used": new_count,
            "limit": limit,
            "remaining": remaining,
            "truncated": truncated,
            "char_limit": char_limit,
            "model": model,
            "policy_type": policy_type
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/my_usage', methods=['GET'])
@login_required
def my_usage():
    user = get_user_data(current_user.id)
    return jsonify({
        "tier": user.get("tier", "free"),
        "analyses_used": user.get("analyses_used", 0)
    })

# ====================== BILLCLEAR ======================
@app.route('/analyze_billclear', methods=['POST'])
@login_required
def analyze_billclear():
    try:
        data = request.json or {}
        user_id = current_user.id
        prompt_type = data.get('type', 'summary')
        raw_text = data.get('text', '') or ''
        bill_type = (data.get('bill_type') or 'other').strip().lower()

        allowed_types = {'hospital', 'clinic', 'eob', 'other'}
        if bill_type not in allowed_types:
            bill_type = 'other'

        user = get_user_data(user_id)
        tier = user.get("tier", "free")
        analyses_used = user.get("analyses_used", 0)

        print(
            f"DEBUG: BillClear {current_user.email} | Tier: {tier} | "
            f"Credits used: {analyses_used} | Bill: {bill_type}"
        )

        TIER_LIMITS = {"free": 3, "credits": 45, "pro": 99999}
        TIER_CHAR_LIMITS = {"free": 5000, "credits": 50000, "pro": 50000}
        TIER_MODELS = {
            "free": "grok-4.3",
            "credits": "grok-4.5",
            "pro": "grok-4.5",
        }

        limit = TIER_LIMITS.get(tier, 3)
        char_limit = TIER_CHAR_LIMITS.get(tier, 5000)
        model = TIER_MODELS.get(tier, "grok-4.3")

        # ---------- Level-A RAG: redact → chunk → retrieve → join ----------
        redacted = redact_pii(raw_text)

        question = (data.get('question') or '').strip() if prompt_type == 'question' else ''

        chunks = chunk_policy(redacted)
        selected = retrieve_chunks(chunks, prompt_type, question=question or None)
        text = build_context(selected, char_limit)
        truncated = len(redacted) > len(text)

        bill_hint = f"The user selected document type: {bill_type}."
        system_prompt = (
            "You are a clear medical-billing explainer for patients in the United States. "
            "Use simple language. Explain only what appears in the provided document excerpts. "
            "If something is unclear or missing from the excerpts, say so. "
            "Do not give medical advice. Do not say a charge is fraud or illegal. "
            "Do not promise a dispute will succeed. "
            "This is not a substitute for the billing office, insurer, or a patient advocate. "
            "Prefer the document text over the selected document type if they conflict, and mention the mismatch."
        )

        if prompt_type == 'question':
            q = data.get('question', '')
            user_prompt = (
                f"{bill_hint}\n"
                f"Answer this question in plain English using only the bill or EOB excerpts: {q}\n\n"
                f"Document excerpts:\n{text}"
            )
            credit_cost = 0
        elif prompt_type == 'risks':
            user_prompt = (
                f"{bill_hint}\n"
                "Extract practical flags and questions to ask — not accusations. Look for:\n"
                "- Possible duplicate charges\n"
                "- Vague lines (miscellaneous, supplies) with no detail\n"
                "- Large patient-responsibility amounts\n"
                "- Missing itemization\n"
                "- Gaps between billed, allowed, and patient amounts if shown\n"
                "- Payment deadlines or collections notes\n"
                "Use short bullet points. For each flag, say what you see and one question "
                "to ask billing or the insurer.\n\n"
                f"Document excerpts:\n{text}"
            )
            credit_cost = 0
        else:
            user_prompt = (
                f"{bill_hint}\n"
                "Summarize this medical bill or EOB in plain English. Cover:\n"
                "- Who billed and for what period (if shown)\n"
                "- Totals: charges, insurance amounts, patient balance (if shown)\n"
                "- Main services in everyday language\n"
                "- Anything incomplete or hard to understand\n\n"
                f"Document excerpts:\n{text}"
            )
            credit_cost = 1

        if credit_cost > 0 and analyses_used >= limit:
            return jsonify({
                "error": (
                    f"You have used all your analyses ({analyses_used}/{limit}). "
                    "Please upgrade your plan."
                ),
                "limit_reached": True,
                "analyses_used": analyses_used,
                "limit": limit,
                "remaining": 0,
                "tier": tier
            }), 403

        if credit_cost > 0 and analyses_used + credit_cost > limit:
            return jsonify({
                "error": f"Not enough analyses remaining. You need {credit_cost}.",
                "limit_reached": True,
                "analyses_used": analyses_used,
                "limit": limit,
                "remaining": max(0, limit - analyses_used),
                "tier": tier
            }), 403

        try:
            response = requests.post(
                "https://api.x.ai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {GROK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": 0.4
                },
                timeout=90
            )
            response.raise_for_status()

        except requests.exceptions.Timeout:
            return jsonify({
                "error": "Analysis took too long. Please try a shorter document or try again."
            }), 504

        except requests.exceptions.RequestException:
            return jsonify({
                "error": "Failed to reach AI service. Please try again."
            }), 502

        result = response.json()
        content = (
            result.get('choices', [{}])[0]
            .get('message', {})
            .get('content', str(result))
        )

        if prompt_type == 'summary':
            new_count = analyses_used + credit_cost
            supabase.table("user_usage").update({
                "analyses_used": new_count,
                "updated_at": datetime.utcnow().isoformat()
            }).eq("user_id", user_id).execute()
        else:
            new_count = analyses_used

        remaining = max(0, limit - new_count)

        return jsonify({
            "result": content,
            "summary": content if prompt_type == 'summary' else None,
            "flags": content if prompt_type == 'risks' else None,
            "tier": tier,
            "analyses_used": new_count,
            "limit": limit,
            "remaining": remaining,
            "truncated": truncated,
            "char_limit": char_limit,
            "model": model,
            "bill_type": bill_type
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ===================== Clinical Trials ==========================

ALLOWED_PHASES = {"EARLY_PHASE1", "PHASE1", "PHASE2", "PHASE3", "PHASE4", "NA"}
ALLOWED_STATUS = {
    "RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
    "ACTIVE_NOT_RECRUITING", "COMPLETED", "TERMINATED", "WITHDRAWN", "SUSPENDED",
}

def _trials_chat(messages, model="grok-4.5", timeout=90):
    response = requests.post(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {GROK_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "temperature": 0.2},
        timeout=timeout,
    )
    response.raise_for_status()
    return (
        response.json().get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )

def _parse_filters(question):
    import json as _json
    raw = _trials_chat([
        {
            "role": "system",
            "content": (
                "Convert a clinician question into JSON filters for ClinicalTrials.gov "
                "and openFDA. Return ONLY JSON with keys: "
                "condition, intervention, phase (array of PHASE1|PHASE2|PHASE3|PHASE4|EARLY_PHASE1|NA), "
                "status (array of RECRUITING|NOT_YET_RECRUITING|ENROLLING_BY_INVITATION|"
                "ACTIVE_NOT_RECRUITING|COMPLETED|TERMINATED|WITHDRAWN|SUSPENDED), "
                "country (empty or US or a country name), "
                "question_type (trials|label|both), "
                "brand_or_generic (drug name or empty). "
                "Do not invent extra keys. If phase or status is not stated, use empty arrays."
            ),
        },
        {"role": "user", "content": question},
    ])
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    data = _json.loads(raw)
    phases = [p for p in (data.get("phase") or []) if p in ALLOWED_PHASES]
    statuses = [s for s in (data.get("status") or []) if s in ALLOWED_STATUS]
    qtype = data.get("question_type") or "trials"
    if qtype not in ("trials", "label", "both"):
        qtype = "trials"
    return {
        "condition": (data.get("condition") or "").strip(),
        "intervention": (data.get("intervention") or "").strip(),
        "phase": phases,
        "status": statuses,
        "country": (data.get("country") or "").strip(),
        "question_type": qtype,
        "brand_or_generic": (data.get("brand_or_generic") or "").strip(),
    }

def _fetch_trials(filters):
    params = {"pageSize": 8, "format": "json"}
    if filters["condition"]:
        params["query.cond"] = filters["condition"]
    if filters["intervention"]:
        params["query.intr"] = filters["intervention"]
    if not filters["condition"] and not filters["intervention"]:
        params["query.term"] = "clinical trial"
    if filters["status"]:
        params["filter.overallStatus"] = ",".join(filters["status"])
    if filters["phase"]:
        clause = " OR ".join(f"AREA[Phase]{p}" for p in filters["phase"])
        if len(filters["phase"]) > 1:
            clause = f"({clause})"
        params["filter.advanced"] = clause
    if filters["country"]:
        loc = filters["country"].strip()
        aliases = {
            "US": "United States",
            "USA": "United States",
            "UK": "United Kingdom",
        }
        params["query.locn"] = aliases.get(loc.upper(), loc)
    r = requests.get("https://clinicaltrials.gov/api/v2/studies", params=params, timeout=30)
    r.raise_for_status()
    rows = []
    for s in (r.json().get("studies") or [])[:8]:
        proto = s.get("protocolSection") or {}
        ident = proto.get("identificationModule") or {}
        status = proto.get("statusModule") or {}
        design = proto.get("designModule") or {}
        cond = proto.get("conditionsModule") or {}
        nct = ident.get("nctId")
        rows.append({
            "nct_id": nct,
            "title": ident.get("briefTitle"),
            "status": status.get("overallStatus"),
            "phases": design.get("phases") or [],
            "conditions": (cond.get("conditions") or [])[:6],
            "url": f"https://clinicaltrials.gov/study/{nct}" if nct else None,
        })
    return rows, params

def _fetch_label(name):
    import os
    if not name:
        return None
    params = {
        "search": f'openfda.brand_name:"{name}" OR openfda.generic_name:"{name}"',
        "limit": 1,
    }
    key = os.environ.get("OPENFDA_API_KEY")
    if key:
        params["api_key"] = key
    r = requests.get("https://api.fda.gov/drug/label.json", params=params, timeout=30)
    if r.status_code != 200:
        return {"error": f"openFDA {r.status_code}", "name": name}
    results = (r.json().get("results") or [None])[0]
    if not results:
        return {"name": name, "found": False}
    openfda = results.get("openfda") or {}
    return {
        "found": True,
        "brand": (openfda.get("brand_name") or [name])[0],
        "generic": (openfda.get("generic_name") or [""])[0],
        "boxed_warning": ((results.get("boxed_warning") or [""])[0])[:800],
        "indications": ((results.get("indications_and_usage") or [""])[0])[:800],
    }

@app.route("/analyze_trials", methods=["POST"])
@login_required
def analyze_trials():
    try:
        data = request.json or {}
        question = (data.get("question") or data.get("text") or "").strip()
        if not question:
            return jsonify({"error": "Enter a clinical question."}), 400
        if len(question) > 2000:
            question = question[:2000]

        filters = _parse_filters(question)
        trials, api_params = [], {}
        label = None
        if filters["question_type"] in ("trials", "both"):
            trials, api_params = _fetch_trials(filters)
        if filters["question_type"] in ("label", "both") or filters["brand_or_generic"]:
            label = _fetch_label(filters["brand_or_generic"] or filters["intervention"])

        brief = _trials_chat([
            {
                "role": "system",
                "content": (
                    "You are a clear research-lookup explainer for clinicians. "
                    "Use ONLY the JSON records provided. Do not invent NCT IDs or indications. "
                    "If results look off-topic, say so. Plain English. Short bullets. "
                    "End with: This is public registry/label text, not medical advice or a care recommendation."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Question:\n{question}\n\nFilters used:\n{filters}\n\n"
                    f"Trials:\n{trials}\n\nLabel slice:\n{label}"
                ),
            },
        ])
        return jsonify({
            "result": brief,
            "filters": filters,
            "api_params": api_params,
            "trials": trials,
            "label": label,
            "nct_ids": [t.get("nct_id") for t in trials if t.get("nct_id")],
        })
    except requests.exceptions.Timeout:
        return jsonify({"error": "Lookup took too long. Try a narrower question."}), 504
    except requests.exceptions.RequestException:
        return jsonify({"error": "Failed to reach a registry API. Try again."}), 502
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

# ====================== HIDDEN ADMIN RESET ======================
@app.route('/admin/reset_usage/<user_id>')
def admin_reset_usage(user_id):
    """
    Hidden admin route to reset a user's credits.
    Example: https://yoursite.com/admin/reset_usage/user@example.com
    """
    try:
        supabase.table("user_usage").update({
            "analyses_used": 0,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("user_id", user_id).execute()

        return f"✅ Usage reset for {user_id}"
    except Exception as e:
        return f"Error: {str(e)}", 500


@app.route('/reset_my_usage')
@login_required
def reset_my_usage():
    """Allow logged-in user to reset their own usage (for testing)"""
    supabase.table("user_usage").update({
        "analyses_used": 0
    }).eq("user_id", current_user.id).execute()

    return f"✅ Your usage has been reset ({current_user.email})"


if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)