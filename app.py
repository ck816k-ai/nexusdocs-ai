from flask import Flask, request, jsonify, redirect, url_for, session, render_template
from flask_cors import CORS
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from datetime import timedelta, datetime
import os
import requests
import sqlite3

load_dotenv()

base_dir = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=os.path.join(base_dir, 'templates'))

app.config['SESSION_COOKIE_SECURE'] = False
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=60)
app.secret_key = os.getenv("SECRET_KEY")
CORS(app)

# ====================== DATABASE SETUP ======================
DB_PATH = 'users.db'


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT,
            name TEXT,
            tier TEXT DEFAULT 'free',
            credits INTEGER DEFAULT 0,
            monthly_analyses INTEGER DEFAULT 0,
            last_reset_date TEXT
        )
    ''')
    conn.commit()
    conn.close()


init_db()

# ====================== Get_User_DATA ======================
def get_user_data(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    row = c.fetchone()

    if not row:
        email = getattr(current_user, 'email', f"user_{user_id}")
        name = getattr(current_user, 'name', 'User')

        c.execute("INSERT INTO users (id, email, name, monthly_analyses, last_reset_date) VALUES (?, ?, ?, 0, ?)",
                  (user_id, email, name, datetime.now().date().isoformat()))
        conn.commit()

        c.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        row = c.fetchone()

    conn.close()
    return row


def update_usage(user_id, increment=True):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    today = datetime.now().date().isoformat()

    print(f"UPDATE_USAGE called for user {user_id}, increment={increment}, today={today}")

    # Check current state
    c.execute("SELECT id, monthly_analyses, last_reset_date FROM users WHERE id = ?", (user_id,))
    data = c.fetchone()
    print(f"Before update: {data}")

    if data and data[2] != today:
        c.execute("UPDATE users SET monthly_analyses = 0, last_reset_date = ? WHERE id = ?", (today, user_id))
        conn.commit()
        print("Daily reset applied")

    if increment:
        c.execute("UPDATE users SET monthly_analyses = monthly_analyses + 1 WHERE id = ?", (user_id,))
        conn.commit()
        print("Increment applied")

    # Verify after update
    c.execute("SELECT monthly_analyses FROM users WHERE id = ?", (user_id,))
    new_count = c.fetchone()[0]
    print(f"After update: monthly_analyses = {new_count}")

    conn.close()
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
    client_kwargs={'scope': 'users.read offline.access', 'token_endpoint_auth_method': 'client_secret_post'}
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


# Protected app page
@app.route('/tg_app')
@app.route('/tg_app.html')
@app.route('/app')
@login_required
def tg_app():
    return render_template('tg_app.html')


# ====================== AUTH ROUTES (add your full callback routes here) ======================
# ... Paste your full google_callback, x_callback, logout, etc. here if they are missing ...

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

@app.route('/logout')
def logout():
    logout_user()
    session.clear()
    return redirect('/login')

# ====================== API ROUTES ======================
@app.route('/analyze', methods=['POST'])
@login_required
def analyze():
    print("=== ANALYZE ROUTE STARTED ===")

    try:
        data = request.json
        user_id = current_user.id
        text = data.get('text', '')[:5000]
        prompt_type = data.get('type', 'summary')

        user_data = get_user_data(user_id)
        tier = user_data[3] if user_data and len(user_data) > 3 else 'free'
        monthly_analyses = user_data[5] if user_data and len(user_data) > 5 else 0

        print(f"DEBUG: User {current_user.email} | Tier: {tier} | Monthly Analyses BEFORE: {monthly_analyses}")

        # Check limit BEFORE processing
        if tier == 'free' and monthly_analyses >= 9:
            return jsonify({
                "error": "Free tier limit reached (3 analyses this month). Upgrade your plan or purchase credits to continue.",
                "limit_reached": True,
                "monthly_analyses": monthly_analyses
            }), 403

        if prompt_type == 'question':
            q = data.get('question', '')
            user_prompt = f"Answer this question in plain English about the document: {q}\n\nDocument: {text}"
        elif prompt_type == 'risks':
            user_prompt = f"Extract key privacy, data selling, sharing, and legal risks in bullet points:\n\n{text}"
        else:
            user_prompt = f"Summarize the document in plain English focusing on user rights:\n\n{text}"

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

        # Update usage AFTER successful analysis
        update_usage(user_id)

        # Refresh count
        updated_data = get_user_data(user_id)
        # monthly_analyses is now at index 5 (0-based)
        new_count = updated_data[5] if updated_data and len(updated_data) > 5 else monthly_analyses + 1

        print(f"DEBUG: Updated data from DB: {updated_data}")
        
        print(f"DEBUG: User {current_user.email} | Tier: {tier} | Monthly Analyses AFTER: {new_count}")

        return jsonify({
            "result": content,
            "tier": tier,
            "monthly_analyses": new_count
        })

    except Exception as e:
        print("ERROR in /analyze:", str(e))
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500

@app.route('/my_usage', methods=['GET'])
@login_required
def my_usage():
    user_data = get_user_data(current_user.id)
    return jsonify({
        "tier": user_data[3],
        "credits": user_data[4],
        "monthly_analyses": user_data[5],
    })


@app.route('/reset_my_usage')
@login_required
def reset_my_usage():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("UPDATE users SET monthly_analyses = 0 WHERE id = ?", (current_user.id,))
    conn.commit()
    conn.close()
    return f"✅ Usage reset for {current_user.email}. You can now analyze again."

if __name__ == '__main__':
    app.run(debug=True, host='127.0.0.1', port=8080)