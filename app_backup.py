from flask import session, redirect, url_for, render_template, request, flash
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

from flask import Flask, request, jsonify, session, redirect, url_for, render_template_string
from flask_cors import CORS
import mysql.connector
import bcrypt
import jwt
import datetime

APP_SECRET = "CHANGE_THIS_SECRET"
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Santarin896935@#", 
    "database": "production_db"
}

app = Flask(__name__)
app.secret_key = "CHANGE_THIS_TO_A_LONG_RANDOM_SECRET"
CORS(app)

def login_required(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if "uid" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return wrapped

def db():
    return mysql.connector.connect(**DB_CONFIG)

def make_token(user_row):
    payload = {
        "uid": user_row["id"],
        "user_id": user_row["user_id"],
        "section": user_row["section"],
        "role": user_row["role"],
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=12),
    }
    return jwt.encode(payload, APP_SECRET, algorithm="HS256")

def auth_required(fn):
    def wrapper(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Missing token"}), 401
        token = auth.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(token, APP_SECRET, algorithms=["HS256"])
        except Exception:
            return jsonify({"error": "Invalid/expired token"}), 401
        request.user = payload
        return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    return wrapper

@app.post("/auth/login")
def login():
    data = request.json or {}
    user_id = str(data.get("user_id", "")).strip()
    password = str(data.get("password", "")).strip()
    section = str(data.get("section", "")).strip()

    if not user_id or not password or not section:
        return jsonify({"error": "user_id/password/section required"}), 400

    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM users WHERE user_id=%s AND is_active=1", (user_id,))
    user = cur.fetchone()
    cur.close(); conn.close()

    if not user:
        return jsonify({"error": "User not found"}), 401

    if user["section"] != section and user["role"] != "admin":
        return jsonify({"error": "Section not allowed"}), 403

    if not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
        return jsonify({"error": "Wrong password"}), 401

    token = make_token(user)
    return jsonify({
        "token": token,
        "full_name": user["full_name"],
        "role": user["role"],
        "section": section
    })

@app.get("/meta/processes")
@auth_required
def get_processes():
    section = request.args.get("section", "").strip()
    if not section:
        return jsonify({"error": "section required"}), 400
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, process_name FROM processes WHERE section=%s AND is_active=1 ORDER BY process_name", (section,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@app.get("/meta/operators")
@auth_required
def get_operators():
    section = request.args.get("section", "").strip()
    if not section:
        return jsonify({"error": "section required"}), 400
    conn = db()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT id, operator_id, operator_name FROM operators WHERE section=%s AND is_active=1 ORDER BY operator_name", (section,))
    rows = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(rows)

@app.post("/production/entry")
@auth_required
def create_entry():
    data = request.json or {}
    entry_date = str(data.get("entry_date", "")).strip()
    hour_label = str(data.get("hour_label", "")).strip()
    section = str(data.get("section", "")).strip()
    process_id = data.get("process_id")
    operator_db_id = data.get("operator_db_id")
    production_qty = data.get("production_qty")

    if not entry_date or not hour_label or not section:
        return jsonify({"error": "entry_date/hour_label/section required"}), 400

    if not isinstance(production_qty, int) or production_qty < 0:
        return jsonify({"error": "production_qty must be int >= 0"}), 400

    conn = db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO production_entries
        (entry_date, hour_label, section, process_id, operator_id, production_qty, created_by)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
    """, (entry_date, hour_label, section, process_id, operator_db_id, production_qty, request.user["uid"]))
    conn.commit()
    cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/")
def home():
    return "Server OK"

@app.route("/login")
def login_page():
    return "Login Page"
@app.route("/processes")
def processes_page():
    conn = db()
    cur = conn.cursor()
    cur.execute("""
        SELECT section, process_name
        FROM processes
        ORDER BY section, process_name
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    html = "<h2>Process List</h2>"

    current_section = None

    for section, process_name in rows:
        if section != current_section:
            if current_section is not None:
                html += "</ul>"
            html += f"<h3>{section}</h3><ul>"
            current_section = section

        html += f"<li>{process_name}</li>"

    html += "</ul>"
    return html

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)