from flask import Flask, request, jsonify, redirect, url_for, session, render_template
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
PRICE_PRO = "price_1TwKdLL7SZXKHM4vJ5gz4fzP"
PRICE_CREDITS = "price_1TwKfgL7SZXKHM4vXS4m2kar"

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
    return render_template('index.html')


@app.route('/termguard.html')
@app.route('/termguard')
def termguard():
    return render_template('termguard.html')


@app.route('/pricing.html')
@app.route('/pricing')
def pricing():
    return render_template('pricing.html')


@app.route('/about.html')
@app.route('/about')
def about():
    return render_template('about.html')


@app.route('/privacy')
@app.route('/privacy.html')
def privacy():
    return render_template('privacy.html')


@app.route('/login')
@app.route('/login.html')
def login_page():
    return render_template('login.html')


@app.route('/tg_app')
@app.route('/tg_app.html')
@app.route('/app')
@login_required
def tg_app():
    return render_template('tg_app.html')


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

        return redirect('/tg_app')

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

        return redirect('/tg_app')

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"X Login failed: {str(e)}", 500

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    return redirect('/login')

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
        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']

            # Safely convert to dict
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

            print(f"Email: {customer_email} | Price: {price_id}")

            if customer_email and price_id:
                if price_id == PRICE_PRO:
                    supabase.table('user_usage').update({
                        'tier': 'pro',
                        'analyses_used': 0
                    }).eq('email', customer_email).execute()
                    print("Updated to Pro")
                elif price_id == PRICE_CREDITS:
                    supabase.table('user_usage').update({
                        'tier': 'credits',
                        'analyses_used': 0
                    }).eq('email', customer_email).execute()
                    print("Updated to credits")

    except Exception as e:
        print(f"Error processing webhook: {e}")

    print("=== WEBHOOK FINISHED SUCCESSFULLY ===")
    return jsonify({"status": "success"}), 200

# ====================== API ROUTES ======================
@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    try:
        data = request.json
        user_id = current_user.id
        text = data.get('text', '')[:5000]
        prompt_type = data.get('type', 'summary')

        user = get_user_data(user_id)
        tier = user.get("tier", "free")
        analyses_used = user.get("analyses_used", 0)

        print(f"DEBUG: User {current_user.email} | Tier: {tier} | Credits used: {analyses_used}")

        # ---------- Credit limits ----------
        TIER_LIMITS = {
            "free": 9,
            "credits": 45,
            "pro": 99999          # effectively unlimited
        }

        limit = TIER_LIMITS.get(tier, 9)

        if analyses_used >= limit:
            return jsonify({
                "error": f"You have used all your credits ({analyses_used}/{limit}). Please upgrade your plan.",
                "limit_reached": True,
                "analyses_used": analyses_used,
                "limit": limit
            }), 403

        # ---------- Credit cost per request ----------
        if prompt_type == 'question':
            q = data.get('question', '')
            user_prompt = f"Answer this question in plain English about the document: {q}\n\nDocument: {text}"
            credit_cost = 1
        elif prompt_type == 'risks':
            user_prompt = f"Extract key privacy, data selling, sharing, and legal risks in bullet points:\n\n{text}"
            credit_cost = 1
        else:  # summary
            user_prompt = f"Summarize the document in plain English focusing on user rights:\n\n{text}"
            credit_cost = 1

        # Double-check they still have enough credits for this request
        if analyses_used + credit_cost > limit:
            return jsonify({
                "error": f"Not enough credits remaining. You need {credit_cost} credit(s).",
                "limit_reached": True
            }), 403

        # ---------- Call Grok ----------
        response = requests.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROK_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "grok-4",
                "messages": [
                    {"role": "system", "content": "You are a clear legal explainer. Use simple language."},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7
            }
        )

        result = response.json()
        content = result.get('choices', [{}])[0].get('message', {}).get('content', str(result))

        # ---------- Deduct credits ----------
        new_count = analyses_used + credit_cost
        supabase.table("user_usage").update({
            "analyses_used": new_count,
            "updated_at": datetime.utcnow().isoformat()
        }).eq("user_id", user_id).execute()

        return jsonify({
            "result": content,
            "tier": tier,
            "analyses_used": new_count,
            "limit": limit
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