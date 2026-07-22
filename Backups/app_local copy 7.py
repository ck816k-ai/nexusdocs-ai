from flask import Flask, request, jsonify, redirect, url_for, session, render_template
from flask_cors import CORS
from flask import render_template
from flask import url_for
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import timedelta
import os
import requests

load_dotenv()

app = Flask(__name__)
app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

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

# X (Twitter) OAuth
oauth.register(
    name='x',
    client_id=os.getenv('X_CLIENT_ID'),
    client_secret=os.getenv('X_CLIENT_SECRET'),
    authorize_url='https://twitter.com/i/oauth2/authorize',
    access_token_url='https://api.twitter.com/2/oauth2/token',
    api_base_url='https://api.twitter.com/2/',
    client_kwargs={
        'scope': 'users.read offline.access',
        'token_endpoint_auth_method': 'client_secret_post'
    }
)

GROK_API_KEY = os.getenv('GROK_API_KEY')

# ====================== GLOBAL STATE ======================
user_tiers = {}
user_usage = {}

# ====================== ROUTES ======================

@app.route('/login')
@app.route('/login.html')
def login_page():
    return render_template('login.html')

@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

# ====================== Google Call Back ======================
@app.route('/auth/google/callback')
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = google.parse_id_token(token, nonce=token.get('nonce'))

        email = user_info['email']

        user = User(email, email, user_info.get('name'))
        login_user(user, remember=True)

        session['email'] = email
        session['name'] = user_info.get('name')
        session.permanent = True

        return redirect('/tg_app')

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Login failed: {str(e)}", 500

    # ====================== X (Twitter) Login ======================
@app.route('/auth/x')
def x_login():
    print("X Login URL being generated...")
    redirect_uri = url_for('x_callback', _external=True)
    return oauth.x.authorize_redirect(
        redirect_uri,
        scope='users.read offline.access'
    )

@app.route('/auth/x/callback')
def x_callback():
    try:
        token = oauth.x.authorize_access_token()
        print("Token received:", token)  # Debug

        resp = oauth.x.get('users/me?user.fields=id,name,username')
        print("User response:", resp.json())  # Debug

        user_info = resp.json().get('data', {})

        email = user_info.get('email') or f"{user_info.get('username')}@x.com"

        user = User(email, email, user_info.get('name') or user_info.get('username'))
        login_user(user, remember=True)

        session['email'] = email
        session['name'] = user_info.get('name') or user_info.get('username')
        session.permanent = True

        return redirect('/tg_app')

    except Exception as e:
        import traceback
        print("X Login Error:")
        traceback.print_exc()
        return f"X Login failed: {str(e)}", 500

# ====================== MAIN PAGES ======================

# Public pages (no login required)
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/about.html')
def about():
    return render_template('about.html')

@app.route('/pricing.html')
def pricing():
    return render_template('pricing.html')

@app.route('/termguard.html')
def termguard():
    return render_template('termguard.html')

@app.route('/privacy')
@app.route('/privacy.html')
def privacy():
    return render_template('privacy.html')

# Protected app page
@app.route('/tg_app')
@app.route('/tg_app.html')
@app.route('/app')
@login_required
def tg_app():
    return render_template('tg_app.html')

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    return redirect('/login')

# ====================== API ROUTES ======================

@app.route('/set_tier', methods=['POST'])
@login_required
def set_tier():
    data = request.json
    user_id = data.get('user_id', current_user.id)
    tier = data.get('tier', 'free')
    user_tiers[user_id] = tier
    user_usage[user_id] = 0
    return jsonify({"status": "ok", "tier": tier, "usage": 0})

@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    data = request.json
    user_id = data.get('user_id', current_user.id)
    text = data.get('text', '')[:5000]
    prompt_type = data.get('type', 'summary')

    tier = user_tiers.get(user_id, 'free')
    usage = user_usage.get(user_id, 0)

    print(f"DEBUG: Analyzing - User: {user_id}, Tier: {tier}, Usage: {usage}")

    if tier == 'free' and usage >= 3:
        return jsonify({"error": "Free tier limit reached (3 analyses). Upgrade for more."}), 403

    if prompt_type == 'question':
        q = data.get('question', '')
        user_prompt = f"Answer this question in plain English about the document: {q}\n\nDocument: {text}"
    elif prompt_type == 'risks':
        user_prompt = f"Extract key privacy, data selling, sharing, and legal risks in bullet points:\n\n{text}"
    else:
        user_prompt = f"Summarize the document in plain English focusing on user rights:\n\n{text}"

    try:
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

        user_usage[user_id] = usage + 1

        return jsonify({
            "result": content,
            "tier": tier,
            "usage": user_usage[user_id]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/debug_tier', methods=['GET'])
@login_required
def debug_tier():
    return jsonify({
        "user_tiers": user_tiers,
        "user_usage": user_usage
    })

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)