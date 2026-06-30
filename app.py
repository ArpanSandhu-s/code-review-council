"""
Code Review Council — Web App
================================

Thin Flask layer over orchestrator.py.
"""

from flask import Flask, render_template, request, jsonify
from orchestrator import run_council

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/review", methods=["POST"])
def review():
    data = request.get_json()
    code = (data.get("code") or "").strip()
    language = data.get("language", "")

    if not code:
        return jsonify({"error": "No code provided."}), 400

    try:
        result = run_council(code, language)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5000)