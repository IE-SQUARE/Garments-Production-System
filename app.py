from flask import Flask, render_template, request, redirect, session, jsonify, send_file
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
from functools import wraps
import qrcode
import os
import pandas as pd
from io import BytesIO, TextIOWrapper
import pytz
import csv

app = Flask(__name__)
app.secret_key = "abc123xyz789_secure"
CORS(app)

@app.context_processor
def inject_user():
    return dict(
        name=session.get("name"),
        role=session.get("role"),
        section=session.get("section")
    )

# =========================
# DATABASE CONNECTION
# =========================
def db():
    # Render-এর Environment Variable থেকে ডাটাবেস লিংকটি অটোমেটিক নিবে
    DATABASE_URL = os.environ.get('DATABASE_URL')
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# =========================
# PRODUCTION INFO (SHIFT & AUTO HOUR)
# =========================
def get_production_info():
    # বাংলাদেশ টাইমজোন সেট করা
    tz = pytz.timezone('Asia/Dhaka')
    now = datetime.now(tz)
    current_hour = now.hour  

    if 7 <= current_hour < 19:
        shift = "Day"
        prod_hour = current_hour - 6 
    else:
        shift = "Night"
        if current_hour >= 19:
            prod_hour = current_hour - 18
        else:
            prod_hour = current_hour + 6

    labels = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "11th", "12th"]
    idx = max(0, min(prod_hour - 1, 11))
    return shift, labels[idx]

# =========================
# AUTHENTICATION DECORATOR
# =========================
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "uid" not in session:
            return redirect("/login")
        return f(*args, **kwargs)
    return wrapper

# =========================
# ROUTES
# =========================

@app.route("/")
def home():
    if "uid" in session:
        return redirect("/dashboard")
    return redirect("/login")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        user_id = request.form.get("user_id")
        password = request.form.get("password")

        conn = db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=%s AND password=%s", (user_id, password))
        user = cur.fetchone()

        if user:
            session["uid"] = user["id"]
            session["name"] = user["full_name"]
            session["role"] = user["role"]
            session["section"] = user["section"]
            session["login_time"] = datetime.now().strftime("%H:%M")
            
            # Permission System
            cur.execute("SELECT menu_name FROM user_permissions WHERE user_id = %s", (user_id,))
            rows = cur.fetchall()
            session['permissions'] = [row['menu_name'] for row in rows]

            cur.close()
            conn.close()
            return redirect("/dashboard")
        
        cur.close()
        conn.close()
        return "Invalid Credentials", 401
    return render_template("login.html")

@app.route("/dashboard")
@login_required
def dashboard():
    return render_template("dashboard.html", login_time=session.get("login_time"))

@app.route("/entry", methods=["GET", "POST"])
@login_required
def entry():
    connection = db()
    cursor = connection.cursor()
    current_shift, current_hour_label = get_production_info()

    if request.method == "POST":
        entry_date = request.form.get("entry_date") or date.today()
        section = request.form.get("section")
        process_id = request.form.get("process_id")
        
        # Operator parsing
        operator_data = request.form.get("operator_name", "")
        parsed_op = operator_data.split(" - ")
        operator_name = parsed_op[0] if len(parsed_op) > 0 else ""
        employee_id = parsed_op[1] if len(parsed_op) > 1 else ""
        
        raw_qty = request.form.get("qty")
        production_qty = int(raw_qty) if raw_qty and raw_qty.isdigit() else 0

        # Style parsing
        master_label = request.form.get("master_label", "")
        parts = master_label.split(" / ")
        buyer = parts[0] if len(parts) > 0 else ""
        style = parts[1] if len(parts) > 1 else ""
        color = parts[2] if len(parts) > 2 else ""
        item = parts[3] if len(parts) > 3 else ""

        cursor.execute("SELECT process_name FROM processes WHERE id = %s", (process_id,))
        p_row = cursor.fetchone()
        process_name = p_row["process_name"] if p_row else ""

        cursor.execute("""
            INSERT INTO production_entries (
                entry_date, hour_label, section, buyer_name, style_name, 
                color_name, item_name, process_name, operator_name, 
                operator_id, production_qty, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (entry_date, current_hour_label, section, buyer, style, 
              color, item, process_name, operator_name, employee_id, 
              production_qty, session.get("uid")))

        connection.commit()
        return redirect("/entry")

    # Dropdown Data
    cursor.execute("SELECT DISTINCT section FROM processes WHERE is_active = 1 ORDER BY section")
    sections = cursor.fetchall()

    cursor.execute("SELECT id, process_name FROM processes WHERE section = %s AND is_active = 1", (session.get("section"),))
    processes = cursor.fetchall()

    cursor.execute("SELECT operator_name, operator_id FROM operators WHERE is_active = 1")
    operators = cursor.fetchall()

    # dropdown label fetching
        cur.execute("""
            SELECT DISTINCT 
                   buyer || ' / ' || style || ' / ' || color || ' / ' || item AS label
            FROM master_data
            WHERE is_active = 1
            ORDER BY label
    """)
    master_rows = cur.fetchall()

    cursor.close()
    connection.close()
    return render_template("entry.html", sections=sections, processes=processes, operators=operators, 
                           master_rows=master_rows, today=date.today(), auto_shift=current_shift, auto_hour=current_hour_label)

# =========================
# BATCH MANAGEMENT
# =========================
@app.route('/create_batch', methods=['GET','POST'])
@login_required
def create_batch():
    conn = db()
    cur = conn.cursor()

    if request.method == "POST":
        now = datetime.now()
        prefix = now.strftime("%y%m")
        
        batch_date = request.form.get("batch_date")
        master_label = request.form.get("master_label")
        wash_type = request.form.get("wash_type")
        qty = request.form.get("qty")
        weight = request.form.get("weight")
        priority = request.form.get("priority")

        cur.execute("SELECT batch_number FROM batches WHERE CAST(batch_number AS TEXT) LIKE %s ORDER BY batch_number DESC LIMIT 1", (prefix + "%",))
        last = cur.fetchone()
        serial = int(str(last["batch_number"])[4:]) + 1 if last else 1
        batch_number = int(prefix + str(serial).zfill(3))

        parts = master_label.split(" / ")
        buyer, style, color, item = parts[0], parts[1], parts[2], parts[3]

        cur.execute("""
            INSERT INTO batches (batch_number, batch_date, buyer, style, color, item, wash_type, batch_qty, batch_weight, priority)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (batch_number, batch_date, buyer, style, color, item, wash_type, qty, weight, priority))
        conn.commit()
        return redirect("/create_batch")

    cur.execute("SELECT buyer || ' / ' || style || ' / ' || color || ' / ' || item AS label FROM master_data WHERE is_active=1")
    master_rows = cur.fetchall()
    cur.execute("SELECT * FROM batches ORDER BY id DESC")
    batches = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template("create_batch.html", master_rows=master_rows, batches=batches, today=date.today())

# 🔥 আপনার লগ ফাইলের এরর (bulk_import_buyer) এখানে ফিক্স করা হয়েছে। এই ফাংশনটি এখন মাত্র একবারই আছে।
@app.route('/bulk_import_buyer', methods=['POST'])
@login_required
def bulk_import_buyer():
    if 'file' not in request.files: return "No file", 400
    file = request.files['file']
    if file and file.filename.endswith('.csv'):
        csv_file = TextIOWrapper(file.stream, encoding='utf-8-sig')
        reader = csv.DictReader(csv_file)
        conn = db()
        cur = conn.cursor()
        try:
            for row in reader:
                buyer = (row.get('Buyer') or row.get('buyer') or "").strip()
                style = (row.get('Style') or row.get('style') or "").strip()
                if buyer and style:
                    cur.execute("INSERT INTO master_data (buyer, style, color, item, is_active) VALUES (%s,%s,%s,%s,1) ON CONFLICT DO NOTHING", 
                                (buyer, style, row.get('Color',''), row.get('Item','')))
            conn.commit()
            return "Success"
        except Exception as e:
            return str(e), 500
        finally:
            cur.close()
            conn.close()
    return "Invalid format", 400

# QR Code Generation
def generate_qr(batch_no):
    url = f"https://your-render-app-url.com/batch_info/{batch_no}"
    img = qrcode.make(url)
    path = f"static/qr/{batch_no}.png"
    if not os.path.exists('static/qr'): os.makedirs('static/qr')
    img.save(path)
    return path

@app.route("/print_batch/<int:batch_no>")
def print_batch(batch_no):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM batches WHERE batch_number=%s", (batch_no,))
    batch = cur.fetchone()
    qr_path = generate_qr(batch_no)
    return render_template("batch_card.html", batch=batch, qr_path=qr_path)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

if __name__ == "__main__":
    app.run(debug=True)
