from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
import requests

load_dotenv()
app = Flask(__name__)
CORS(app)

GROK_API_KEY = os.getenv('GROK_API_KEY')

@app.route('/analyze', methods=['POST'])
def analyze():
    data = request.json
    text = data.get('text', '')[:5000]
    prompt_type = data.get('type', 'summary')

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
                "model": "grok-4",   # Use your working model
                "messages": [
                    {"role": "system", "content": "You are a clear legal explainer. Use simple language."},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.7
            }
        )

        result = response.json()
        content = result.get('choices', [{}])[0].get('message', {}).get('content', str(result))
        return jsonify({"result": content})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)