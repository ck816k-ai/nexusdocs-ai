from flask import Flask, request, jsonify, redirect, url_for, session, render_template
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
import os
import requests

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

GROK_API_KEY = os.getenv('GROK_API_KEY')

# ====================== AUTH SETUP ======================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, user_id, email, name):
        self.id = user_id
        self.email = email
        self.name = name

@login_manager.user_loader
def load_user(user_id):
    return User(user_id, session.get('email'), session.get('name'))

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.getenv('GOOGLE_CLIENT_ID'),
    client_secret=os.getenv('GOOGLE_CLIENT_SECRET'),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# ====================== GLOBAL STATE ======================
user_tiers = {}
user_usage = {}

# ====================== ROUTES ======================

@app.route('/login')
def login():
    return render_template('login.html')

@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    try:
        token = google.authorize_access_token()
        user_info = google.parse_id_token(token, nonce=token.get('nonce'))

        user = User(user_info['sub'], user_info['email'], user_info.get('name'))
        login_user(user)

        session['email'] = user_info['email']
        session['name'] = user_info.get('name')

        if user.id not in user_usage:
            user_usage[user.id] = 0
            user_tiers[user.id] = 'free'

        return redirect(url_for('landing'))

    except Exception as e:
        print("Callback error:", str(e))
        return "Login failed. Please try again.", 500

@app.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    return redirect(url_for('login'))

# ====================== MAIN PAGES ======================

# ====================== MAIN PAGES ======================

@app.route('/')
@app.route('/local_index.html')
@login_required
def landing():
    return render_template('local_index.html')

@app.route('/index.html')
@login_required
def app_page():
    return render_template('index.html')

@app.route('/termguard.html')
@login_required
def termguard():
    return render_template('termguard.html')   # or 'termguard.html' if you have a separate file

# Add more pages below as needed
@app.route('/about.html')
@login_required
def about():
    return render_template('about.html')

@app.route('/product.html')
@login_required
def product():
    return render_template('product.html')

@app.route('/pricing.html')
@login_required
def pricing():
    return render_template('pricing.html')

@app.route('/privacy.html')
@login_required
def privacy():
    return render_template('privacy.html')
# Add more pages as needed...

# ====================== API ROUTES ======================

@app.route('/set_tier', methods=['POST'])
@login_required
def set_tier():
    data = request.json
    user_id = data.get('user_id', current_user.id)
    tier = data.get('tier', 'free')
    user_tiers[user_id] = tier
    user_usage[user_id] = 0
    print(f"DEBUG: Set tier {tier} for {user_id}")
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