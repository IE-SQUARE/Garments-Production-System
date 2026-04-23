from flask import Flask, render_template, request, redirect, session, jsonify
from flask_cors import CORS
from flask import request
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
from functools import wraps
import qrcode
import os
import pandas as pd
from io import BytesIO
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
# DATABASE
# =========================

def db():
    # Render-এর Environment Variable থেকে ডাটাবেস লিংকটি অটোমেটিক নিবে
    DATABASE_URL = os.environ.get('DATABASE_URL')
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

# =========================
# PRODUCTION INFO (SHIFT & AUTO HOUR)
# =========================

def get_production_info():
    # বাংলাদেশ টাইমজোন সেট করা (Render সার্ভারের টাইম ফিক্স করার জন্য)
    tz = pytz.timezone('Asia/Dhaka')
    now = datetime.now(tz)
    
    current_hour = now.hour  # ২৪ ঘণ্টার ফরম্যাট (০-২৩)

    # শিফট এবং আওয়ার ক্যালকুলেশন লজিক (সকাল ০৭:০০ থেকে শুরু)
    if 7 <= current_hour < 19:
        shift = "Day"
        # সকাল ৭টা হলো ১ম ঘণ্টা (৭-৬=১)
        prod_hour = current_hour - 6 
    else:
        shift = "Night"
        # রাত ৭টা (১৯:০০) হলো ১ম ঘণ্টা (১৯-১৮=১)
        if current_hour >= 19:
            prod_hour = current_hour - 18
        else:
            # রাত ১২টার পরের সময়ের জন্য (০, ১, ২...)
            # রাত ১২টা হলো ৬ষ্ঠ ঘণ্টা (০+৬=৬)
            prod_hour = current_hour + 6

    # ডাটাবেসে সেভ করার জন্য আওয়ার লেবেল
    labels = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th", "8th", "9th", "10th", "11th", "12th"]
    
    # ইন্ডেক্স যাতে সীমার বাইরে না যায় (Safety Check)
    idx = max(0, min(prod_hour - 1, 11))
    hour_label = labels[idx]

    return shift, hour_label
# =========================
# LOGIN REQUIRED
# =========================

def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):

        if "uid" not in session:
            return redirect("/login")

        return f(*args, **kwargs)

    return wrapper


# =========================
# LOGIN PAGE
# =========================

def generate_qr(batch_no):
    # request.host_url ব্যবহার করলে এটি অটোমেটিক রেন্ডার বা লোকালহোস্টের ইউআরএল নিয়ে নিবে
    base_url = request.host_url.rstrip('/') 
    url = f"{base_url}/batch_info/{batch_no}"
    
    print(f"Generating QR for: {url}") # এটি লগে চেক করার জন্য

    img = qrcode.make(url)
    
    path_dir = os.path.join(app.root_path, 'static', 'qr')
    if not os.path.exists(path_dir):
        os.makedirs(path_dir)

    filename = f"{batch_no}.png"
    path = os.path.join(path_dir, filename)
    img.save(path)
    return f"qr/{filename}"

@app.route("/print_batch/<int:batch_no>")
@login_required # নিরাপত্তা নিশ্চিত করা
def print_batch(batch_no):
    conn = db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM batches WHERE batch_number=%s", (batch_no,))
        batch = cur.fetchone()

        if not batch:
            return "Batch not found!", 404

        # QR জেনারেট করা
        qr_path = generate_qr(batch_no)

        return render_template(
            "batch_card.html",
            batch=batch,
            qr_path=qr_path
        )
    except Exception as e:
        print(f"Print Batch Error: {e}")
        return f"Error: {str(e)}", 500
    finally:
        # ডাটাবেস কানেকশন ক্লোজ
        cur.close()
        conn.close()

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

        # ১. ইউজার ভ্যালিডেশন চেক
        cur.execute("SELECT * FROM users WHERE user_id=%s AND password=%s", (user_id, password))
        user = cur.fetchone()

        if user:
            # ২. সেশনে ইউজারের বেসিক তথ্য সেভ করা
            session["uid"] = user["id"]
            session["name"] = user["full_name"]
            session["role"] = user["role"]
            session["section"] = user["section"]
            session["login_time"] = datetime.now().strftime("%H:%M")

            # ৩. 🔥 নতুন পারমিশন সিস্টেম: ডাটাবেস থেকে পারমিশন তুলে আনা
            try:
                cur.execute("SELECT menu_name FROM user_permissions WHERE user_id = %s", (user_id,))
                rows = cur.fetchall()
                # RealDictCursor ব্যবহার করছেন তাই row['menu_name'] হবে
                session['permissions'] = [row['menu_name'] for row in rows]
            except Exception as e:
                print(f"Permission Loading Error: {e}")
                session['permissions'] = [] # এরর হলে খালি লিস্ট দিবে

            cur.close()
            conn.close()
            return redirect("/dashboard")
        
        cur.close()
        conn.close()
        return "Invalid Credentials", 401 # পাসওয়ার্ড বা আইডি ভুল হলে

    return render_template("login.html")

# =========================
# DASHBOARD
# =========================

@app.route("/dashboard")
@login_required
def dashboard():

    return render_template(
        "dashboard.html",
        name=session.get("name"),
        role=session.get("role"),
        section=session.get("section"),
        login_time=session.get("login_time")
    )


# =========================
# ENTRY PAGE
# =========================
# =========================
# ENTRY PAGE
# =========================
# ==========================================
# PRODUCTION ENTRY ROUTE
# ==========================================
@app.route("/entry", methods=["GET", "POST"])
@login_required
def entry():
    """
    Handles the production data entry. 
    Automatically determines Shift and Hour based on BD Time (07:00 AM start).
    """
    connection = db()
    cursor = connection.cursor()

    # বর্তমান শিফট এবং প্রোডাকশন আওয়ার অটোমেটিক নির্ধারণ (BD Time 07:00 AM Logic)
    current_shift, current_hour_label = get_production_info()

    # ড্রপডাউনের জন্য সকল সেশন (Sections) ফেচ করা
    cursor.execute("""
        SELECT DISTINCT section 
        FROM processes 
        WHERE is_active = 1 
        ORDER BY section
    """)
    sections = cursor.fetchall()

    # শুধুমাত্র বর্তমান ইউজারের সেকশন অনুযায়ী প্রসেসগুলো ফেচ করা
    cursor.execute("""
        SELECT id, process_name 
        FROM processes 
        WHERE section = %s AND is_active = 1 
        ORDER BY process_name
    """, (session.get("section"),))
    processes = cursor.fetchall()

    # অপারেটরদের লিস্ট ফেচ করা
    cursor.execute("""
        SELECT operator_name, operator_id 
        FROM operators 
        WHERE is_active = 1 
        ORDER BY operator_name
    """)
    operators = cursor.fetchall()

    # মাস্টার স্টাইল ডাটা লেবেলসহ ফেচ করা
    cursor.execute("""
        SELECT buyer, style, color, item,
               CONCAT(buyer, ' / ', style, ' / ', color, ' / ', item) AS label
        FROM master_data
        WHERE is_active = 1
        ORDER BY buyer, style, color, item
    """)
    master_rows = cursor.fetchall()

    if request.method == "POST":
        # ফর্ম থেকে ডাটা সংগ্রহ করা
        entry_date = request.form.get("entry_date") or date.today()
        section = request.form.get("section")
        process_id = request.form.get("process_id")
        
        # অপারেটর নাম এবং আইডি আলাদা করা
        operator_data = request.form.get("operator_name", "")
        parsed_op = operator_data.split(" - ")
        operator_name = parsed_op[0] if len(parsed_op) > 0 else ""
        employee_id = parsed_op[1] if len(parsed_op) > 1 else ""
        
        # প্রোডাকশন কোয়ান্টিটি চেক ও কনভার্ট করা
        raw_qty = request.form.get("qty")
        production_qty = int(raw_qty) if raw_qty and raw_qty.isdigit() else 0

        # মাস্টার লেবেল থেকে বায়ার, স্টাইল, কালার এবং আইটেম আলাদা করা
        master_label = request.form.get("master_label", "")
        parsed_master = master_label.split(" / ")
        buyer = parsed_master[0] if len(parsed_master) > 0 else ""
        style = parsed_master[1] if len(parsed_master) > 1 else ""
        color = parsed_master[2] if len(parsed_master) > 2 else ""
        item = parsed_master[3] if len(parsed_master) > 3 else ""

        # আইডি অনুযায়ী অফিসিয়াল প্রসেস নাম খুঁজে বের করা
        cursor.execute("SELECT process_name FROM processes WHERE id = %s", (process_id,))
        process_row = cursor.fetchone()
        process_name = process_row["process_name"] if process_row else ""

        user_uid = session.get("uid")

        # ডাটাবেসে ফাইনাল এন্ট্রি ইনসার্ট করা
        cursor.execute("""
            INSERT INTO production_entries (
                entry_date, hour_label, section, buyer_name, style_name, 
                color_name, item_name, process_name, operator_name, 
                operator_id, production_qty, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            entry_date, current_hour_label, section, buyer, style, 
            color, item, process_name, operator_name, employee_id, 
            production_qty, user_uid
        ))

        connection.commit()
        return redirect("/entry")

    # কানেকশন ক্লোজ করা (GET রিকোয়েস্টের জন্য)
    cursor.close()
    connection.close()

    return render_template(
        "entry.html",
        sections=sections,
        processes=processes,
        operators=operators,
        master_rows=master_rows,
        today=date.today(),
        auto_shift=current_shift,
        auto_hour=current_hour_label
    )
@app.route("/get_processes/<section>")
@login_required
def get_processes(section):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT id,process_name
    FROM processes
    WHERE section=%s AND is_active=1
    ORDER BY process_name
    """,(section,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify(rows)


# =========================

@app.route("/report")
@login_required
def report():

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT DISTINCT section
    FROM processes
    WHERE is_active=1
    ORDER BY section
    """)
    sections = cur.fetchall()

    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    section = request.args.get("section")

    query = """
    SELECT 
    buyer_name,
    style_name,
    color_name,
    process_name,

    SUM(CASE WHEN hour_label IN ('1st','2nd','3rd','4th','5th','6th')
    THEN production_qty ELSE 0 END) AS day_qty,

    SUM(CASE WHEN hour_label IN ('7th','8th','9th','10th','11th')
    THEN production_qty ELSE 0 END) AS night_qty,

    SUM(production_qty) AS total_qty,

    (
    SELECT SUM(p2.production_qty)
    FROM production_entries p2
    WHERE 
        p2.buyer_name = p1.buyer_name
        AND p2.style_name = p1.style_name
        AND p2.color_name = p1.color_name
        AND p2.process_name = p1.process_name
    ) AS cum_total

    FROM production_entries p1
    WHERE 1=1
    """

    params = []

    if from_date:
        query += " AND entry_date >= %s"
        params.append(from_date)

    if to_date:
        query += " AND entry_date <= %s"
        params.append(to_date)

    if section:
        query += " AND section = %s"
        params.append(section)

    query += """
GROUP BY buyer_name,style_name,color_name,process_name
ORDER BY buyer_name DESC
"""

    cur.execute(query, params)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return render_template(
    "report.html",
    rows=rows,
    sections=sections,
    from_date=from_date,
    to_date=to_date,
    section=section
)

# =========================
# USER MANAGEMENT
# =========================

@app.route("/users")
@login_required
def users():

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM users")
    users = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("users.html", users=users)


# =========================
# LOGOUT
# =========================

@app.route("/shifts", methods=["GET","POST"])
@login_required
def shifts():

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":

        updated_from = request.form.get("updated_from")
        shift_name = request.form.get("shift_name")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")

        # previous version close
        cur.execute("""
        UPDATE shifts
        SET effective_to = DATE_SUB(%s, INTERVAL 1 DAY)
        WHERE effective_to IS NULL
        """,(updated_from,))

        # insert main shift
        cur.execute("""
        INSERT INTO shifts
        (shift_name,start_time,end_time,updated_from,effective_from,effective_to)
        VALUES (%s,%s,%s,%s,%s,NULL)
        """,(shift_name,start_time,end_time,updated_from,updated_from))

        # auto create opposite shift
        if shift_name.lower() == "day":

            cur.execute("""
            INSERT INTO shifts
            (shift_name,start_time,end_time,updated_from,effective_from,effective_to)
            VALUES (%s,%s,%s,%s,%s,NULL)
            """,("Night", end_time, start_time, updated_from, updated_from))

        elif shift_name.lower() == "night":

            cur.execute("""
            INSERT INTO shifts
            (shift_name,start_time,end_time,updated_from,effective_from,effective_to)
            VALUES (%s,%s,%s,%s,%s,NULL)
            """,("Day", end_time, start_time, updated_from, updated_from))

    conn.commit()

    cur.execute("SELECT * FROM shifts ORDER BY start_time")
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("shifts.html", rows=rows)

@app.route("/logout")
def logout():

    session.clear()

    return redirect("/login")


# =========================
# RUN
# =========================

@app.route('/create_batch', methods=['GET','POST'])
@login_required # নিরাপত্তা নিশ্চিত করা
def create_batch():
    today = date.today()
    conn = db()
    cur = conn.cursor()

    if request.method == "POST":
        try:
            now = datetime.now()
            # বছর এবং মাস দিয়ে প্রিফিক্স (যেমন: ২০২৬ সালের এপ্রিল হলে ২৬০৪)
            prefix = now.strftime("%y%m")

            batch_date = request.form.get("batch_date")
            master_label = request.form.get("master_label", "")
            wash_type = request.form.get("wash_type")
            qty = request.form.get("qty") or 0
            weight = request.form.get("weight") or 0
            priority = request.form.get("priority")

            # ১. মাস্টার লেবেল স্প্লিট করা (নিরাপদ পদ্ধতি)
            if " / " in master_label:
                parts = master_label.split(" / ")
                if len(parts) == 4:
                    buyer, style, color, item = parts
                else:
                    return "Error: Invalid label format!", 400
            else:
                return "Error: Please select a valid style!", 400

            # ২. ব্যাচ নম্বর জেনারেশন (ডুপ্লিকেট রোখার জন্য)
            cur.execute("""
                SELECT batch_number 
                FROM batches 
                WHERE CAST(batch_number AS TEXT) LIKE %s 
                ORDER BY batch_number DESC LIMIT 1
            """, (prefix + "%",))
            last = cur.fetchone()

            if last and last["batch_number"]:
                # শেষ ৩টি ডিজিট নিয়ে সিরিয়াল বাড়ানো
                last_serial = int(str(last["batch_number"])[-3:])
                serial = last_serial + 1
            else:
                serial = 1

            # চূড়ান্ত ব্যাচ নম্বর (যেমন: ২৬০৪০০১)
            batch_number = int(prefix + str(serial).zfill(3))

            # ৩. ডাটা ইনসার্ট করা
            cur.execute("""
                INSERT INTO batches 
                (batch_number, batch_date, buyer, style, color, item, wash_type, batch_qty, batch_weight, priority)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (batch_number, batch_date, buyer, style, color, item, wash_type, qty, weight, priority))

            conn.commit()
            return redirect("/create_batch")

        except Exception as e:
            conn.rollback()
            print(f"Error in Batch Creation: {e}")
            return f"Internal Error: {str(e)}", 500
        finally:
            # ৪. কানেকশন ক্লোজ করা (খুবই গুরুত্বপূর্ণ)
            cur.close()
            conn.close()

    # GET মেথড: ড্রপডাউন এবং টেবিল লিস্টের জন্য ডাটা আনা
    try:
        # ড্রপডাউনে ডুপ্লিকেট সরানো
        cur.execute("""
            SELECT DISTINCT buyer || ' / ' || style || ' / ' || color || ' / ' || item AS label
            FROM master_data
            WHERE is_active = 1
            ORDER BY label
        """)
        master_rows = cur.fetchall()

        # ব্যাচ লিস্ট (সর্বশেষ ২০টি)
        cur.execute("SELECT * FROM batches ORDER BY id DESC LIMIT 20")
        batches = cur.fetchall()
        
    except Exception as e:
        print(f"Fetch Error: {e}")
        master_rows, batches = [], []
    finally:
        cur.close()
        conn.close()

    return render_template(
        "create_batch.html",
        master_rows=master_rows,
        batches=batches,
        today=today
    )
@app.route("/delete_batch/<int:batch_no>", methods=["POST"])
def delete_batch(batch_no):

    conn = db()
    cur = conn.cursor()

    cur.execute("DELETE FROM batches WHERE batch_number=%s",(batch_no,))
    conn.commit()

    cur.close()
    conn.close()

    return jsonify({"status":"success"})

@app.route("/update_batch", methods=["POST"])
def update_batch():

    data = request.get_json()

    batch_no = data["batch_no"]
    wash_type = data["wash_type"]
    qty = data["qty"]
    weight = data["weight"]

    conn = db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE batches
        SET wash_type=%s,
            batch_qty=%s,
            batch_weight=%s
        WHERE batch_number=%s
    """,(wash_type,qty,weight,batch_no))

    conn.commit()

    cur.close()
    conn.close()

    return jsonify({"status":"success"})

@app.route("/batch_info/<int:batch_no>")
def batch_info(batch_no):
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM batches WHERE batch_number=%s", (batch_no,))
    batch = cur.fetchone()
    cur.close()
    conn.close()
    
    if batch:
        # আপনি এই ডাটাগুলো একটি সিম্পল পেজে দেখাতে পারেন
        return f"""
        <h1>Batch Details</h1>
        <p>Batch: {batch['batch_number']}</p>
        <p>Buyer: {batch['buyer']}</p>
        <p>Style: {batch['style']}</p>
        <p>Color: {batch['color']}</p>
        <p>Wash Type: {batch['wash_type']}</p>
        <p>Qty: {batch['batch_qty']}</p>
        """
    else:
        return "Batch Not Found!", 404

@app.route("/operator_status")
@login_required
def operator_status():
    date_val = request.args.get("date")
    shift = request.args.get("shift")
    section = request.args.get("section")

    conn = db()
    cur = conn.cursor()

    query = """
    SELECT
        o.operator_name,
        p.operator_id AS emp_no,
        SUM(CASE WHEN hour_label='1st' THEN production_qty ELSE 0 END) AS h1,
        SUM(CASE WHEN hour_label='2nd' THEN production_qty ELSE 0 END) AS h2,
        SUM(CASE WHEN hour_label='3rd' THEN production_qty ELSE 0 END) AS h3,
        SUM(CASE WHEN hour_label='4th' THEN production_qty ELSE 0 END) AS h4,
        SUM(CASE WHEN hour_label='5th' THEN production_qty ELSE 0 END) AS h5,
        SUM(CASE WHEN hour_label='6th' THEN production_qty ELSE 0 END) AS h6,
        SUM(CASE WHEN hour_label='7th' THEN production_qty ELSE 0 END) AS h7,
        SUM(CASE WHEN hour_label='8th' THEN production_qty ELSE 0 END) AS h8,
        SUM(CASE WHEN hour_label='9th' THEN production_qty ELSE 0 END) AS h9,
        SUM(CASE WHEN hour_label='10th' THEN production_qty ELSE 0 END) AS h10,
        SUM(CASE WHEN hour_label='11th' THEN production_qty ELSE 0 END) AS h11,
        SUM(production_qty) AS total
    FROM production_entries p
    LEFT JOIN operators o ON p.operator_id = o.operator_id
    WHERE 1=1
    """
    params = []
    if date_val:
        query += " AND entry_date = %s"
        params.append(date_val)
    if section:
        query += " AND p.section = %s"
        params.append(section)
    if shift == "DAY":
        query += " AND hour_label IN ('1st','2nd','3rd','4th','5th','6th')"
    elif shift == "NIGHT":
        query += " AND hour_label IN ('7th','8th','9th','10th','11th')"

    query += " GROUP BY o.operator_name, p.operator_id ORDER BY o.operator_name"
    
    cur.execute(query, params)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("operator_status.html", rows=rows, date=date_val)
# OPERATOR MANAGEMENT
# =========================

@app.route("/operators", methods=["GET","POST"])
@login_required
def operators():

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":

        name = request.form.get("operator_name")
        operator_id = request.form.get("operator_id")

        cur.execute("""
        INSERT INTO operators (operator_id,operator_name,is_active)
        VALUES (%s,%s,1)
        """,(operator_id,name))

        conn.commit()

    cur.execute("SELECT * FROM operators ORDER BY operator_name")
    operators = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("operator_management.html", operators=operators)


@app.route("/disable_operator/<int:oid>")
@login_required
def disable_operator(oid):

    conn = db()
    cur = conn.cursor()

    cur.execute("UPDATE operators SET is_active=0 WHERE id=%s",(oid,))
    conn.commit()

    cur.close()
    conn.close()

    return redirect("/operators")


@app.route("/enable_operator/<int:oid>")
@login_required
def enable_operator(oid):

    conn = db()
    cur = conn.cursor()

    cur.execute("UPDATE operators SET is_active=1 WHERE id=%s",(oid,))
    conn.commit()

    cur.close()
    conn.close()

    return redirect("/operators")

@app.route("/edit_operator/<int:oid>", methods=["GET","POST"])
@login_required
def edit_operator(oid):

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":

        name = request.form.get("operator_name")
        operator_id = request.form.get("operator_id")

        cur.execute("""
        UPDATE operators
        SET operator_name=%s,
            operator_id=%s
        WHERE id=%s
        """,(name,operator_id,oid))

        conn.commit()

        cur.close()
        conn.close()

        return redirect("/operators")

    cur.execute("SELECT * FROM operators WHERE id=%s",(oid,))
    op = cur.fetchone()

    cur.close()
    conn.close()

    return render_template("edit_operator.html", op=op)

@app.route("/operator_detail")
@login_required
def operator_detail():
    emp_id = request.args.get("emp_id")
    date_val = request.args.get("date")
    
    if not date_val or date_val == "None":
        date_val = date.today().strftime("%Y-%m-%d")

    conn = db()
    cur = conn.cursor()

    # এখানে আমরা production_entries এর সাথে hourly_targets জয়েন দিচ্ছি
    # যাতে বোঝা যায় ওই অপারেটর যে সেকশনে কাজ করছে সেটার টার্গেট কত
    query = """
    SELECT
        p.buyer_name, p.style_name, p.color_name, p.item_name, p.process_name,
        COALESCE(t.target_qty, 0) AS hourly_target,
        SUM(CASE WHEN hour_label='1st' THEN production_qty ELSE 0 END) h1,
        SUM(CASE WHEN hour_label='2nd' THEN production_qty ELSE 0 END) h2,
        SUM(CASE WHEN hour_label='3rd' THEN production_qty ELSE 0 END) h3,
        SUM(CASE WHEN hour_label='4th' THEN production_qty ELSE 0 END) h4,
        SUM(CASE WHEN hour_label='5th' THEN production_qty ELSE 0 END) h5,
        SUM(CASE WHEN hour_label='6th' THEN production_qty ELSE 0 END) h6,
        SUM(CASE WHEN hour_label='7th' THEN production_qty ELSE 0 END) h7,
        SUM(CASE WHEN hour_label='8th' THEN production_qty ELSE 0 END) h8,
        SUM(CASE WHEN hour_label='9th' THEN production_qty ELSE 0 END) h9,
        SUM(CASE WHEN hour_label='10th' THEN production_qty ELSE 0 END) h10,
        SUM(CASE WHEN hour_label='11th' THEN production_qty ELSE 0 END) h11,
        SUM(production_qty) total
    FROM production_entries p
    LEFT JOIN hourly_targets t ON p.section = t.section_name
    WHERE p.operator_id=%s AND p.entry_date=%s
    GROUP BY p.buyer_name, p.style_name, p.color_name, p.item_name, p.process_name, t.target_qty
    ORDER BY p.style_name
    """
    
    cur.execute(query, (emp_id, date_val))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    return render_template("operator_detail.html", rows=rows, emp_id=emp_id, date=date_val)

@app.route("/master_data", methods=["GET","POST"])
@login_required
def master_data():
    conn = db()
    cur = conn.cursor()

    if request.method == "POST":
        # ডাটা ক্লিন করা (বাড়তি স্পেস থাকলে বাদ দিবে)
        buyer = request.form.get("buyer", "").strip()
        style = request.form.get("style", "").strip()
        color = request.form.get("color", "").strip()
        item = request.form.get("item", "").strip()

        if buyer and style:
            try:
                # ১. ডুপ্লিকেট চেক করা (LOWER ব্যবহার করা হয়েছে যাতে বড়/ছোট হাতের অক্ষরে সমস্যা না হয়)
                cur.execute("""
                    SELECT id FROM master_data 
                    WHERE LOWER(buyer)=LOWER(%s) AND LOWER(style)=LOWER(%s) 
                    AND LOWER(color)=LOWER(%s) AND LOWER(item)=LOWER(%s)
                """, (buyer, style, color, item))
                
                duplicate = cur.fetchone()

                if duplicate:
                    # ২. ডুপ্লিকেট থাকলে সেটি ডিলিট করা
                    cur.execute("DELETE FROM master_data WHERE id=%s", (duplicate['id'],))

                # ৩. ফ্রেশ ডাটা ইনসার্ট করা
                cur.execute("""
                    INSERT INTO master_data (buyer, style, color, item, is_active)
                    VALUES (%s, %s, %s, %s, 1)
                """, (buyer, style, color, item))
                
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"Error: {e}")

    # লিস্ট দেখানোর সময় ইউনিক ডাটা দেখানো
    cur.execute("SELECT * FROM master_data ORDER BY id DESC")
    rows = cur.fetchall()
    
    cur.close()
    conn.close()
    return render_template("master_data.html", rows=rows)
@app.route("/disable_style/<int:id>")
@login_required
def disable_style(id):

    conn = db()
    cur = conn.cursor()

    cur.execute("UPDATE master_data SET is_active=0 WHERE id=%s",(id,))
    conn.commit()

    return redirect("/master_data")
@app.route("/enable_style/<int:id>")
@login_required
def enable_style(id):

    conn = db()
    cur = conn.cursor()

    cur.execute("UPDATE master_data SET is_active=1 WHERE id=%s",(id,))
    conn.commit()

    return redirect("/master_data")

@app.route("/get_operations/<section>")
@login_required
def get_operations(section):

    conn = db()
    cur = conn.cursor()

    cur.execute("""
    SELECT process_name AS name
    FROM process_master
    WHERE section_name=%s AND is_active=1
    ORDER BY process_name
    """,(section,))

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return jsonify(rows)

@app.route("/process_target", methods=["GET","POST"])
@login_required
def process_target():

    conn = db()
    cur = conn.cursor()

    # dropdown data
    cur.execute("""
    SELECT buyer,style,color,item,
    CONCAT(buyer,' / ',style,' / ',color,' / ',item) AS label
    FROM master_data
    WHERE is_active=1
    """)
    master_rows = cur.fetchall()

    if request.method == "POST":

        style_label = request.form.get("style_label")
        parts = style_label.split(" / ")

        buyer = parts[0]
        style = parts[1]
        color = parts[2]
        item = parts[3]

        sections = request.form.getlist("section[]")
        operations = request.form.getlist("operation[]")
        seqs = request.form.getlist("seq[]")
        sams = request.form.getlist("sam[]")
        qtys = request.form.getlist("qty[]")
        targets = request.form.getlist("target[]")
        batch_times = request.form.getlist("batch_time[]")
        weights = request.form.getlist("weight[]")
        no_batches = request.form.getlist("no_batch[]")
        waters = request.form.getlist("water[]")
        length = min(
            len(sections),
            len(operations),
            len(seqs),
            len(sams),
            len(batch_times),
            len(qtys),
            len(weights),
            len(no_batches),
            len(targets),
            len(waters)
        	)

        for i in range(len(sections)):

            if not sections[i] or not operations[i]:
                continue

            # 🔥 Wet Process validation
            if sections[i] == "Wet Process":

                if not batch_times[i] or not qtys[i] or not weights[i] or not no_batches[i] or not waters[i]:
                    return "⚠️ Wet Process row incomplete. Fill all fields!"

            cur.execute("""
            INSERT INTO process_target_details
            (buyer,style,color,item,section_name,operation_name,
            sequence_no,sam,batch_time,batch_qty,batch_weight,
            no_of_batch,plan_target,total_water)

            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,(
                buyer,style,color,item,
                sections[i],
                operations[i],
                seqs[i] if i < len(seqs) else None,
                sams[i] if i < len(sams) else None,
                batch_times[i] if i < len(batch_times) else None,
                qtys[i] if i < len(qtys) else None,
                weights[i] if i < len(weights) else None,
                no_batches[i] if i < len(no_batches) else None,
                targets[i] if i < len(targets) else None,
                waters[i] if i < len(waters) else None
           ))

        conn.commit()

    return render_template(
        "process_target.html",
        master_rows=master_rows
    )
import pandas as pd
from io import BytesIO
from flask import send_file

@app.route('/export_excel', methods=['POST'])
def export_excel():
    data = request.json.get('report_data', [])
    if not data:
        return "No data", 400

    df = pd.DataFrame(data)
    output = BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Production Report')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='Production_Report.xlsx'
    )

import csv
from io import TextIOWrapper

@app.route('/bulk_import_buyer', methods=['POST'])
@login_required
def bulk_import_buyer():
    if 'file' not in request.files:
        return "No file uploaded", 400
    
    file = request.files['file']
    if file and file.filename.endswith('.csv'):
        # utf-8-sig ব্যবহার করা হয়েছে যাতে এক্সেল থেকে সেভ করা CSV-র ইনভিজিবল ক্যারেক্টার সমস্যা না করে
        csv_file = TextIOWrapper(file.stream, encoding='utf-8-sig')
        reader = csv.DictReader(csv_file)
        
        # হেডার ট্রিম এবং ছোট হাতের অক্ষরে কনভার্ট (যেমন: Buyer -> buyer)
        reader.fieldnames = [name.strip().lower() for name in reader.fieldnames]
        
        conn = db()
        cur = conn.cursor()
        
        try:
            count = 0
            for row in reader:
                # CSV থেকে ডাটা নেওয়া এবং বাড়তি স্পেস ডিলিট করা
                buyer = (row.get('buyer') or "").strip()
                style = (row.get('style') or "").strip()
                color = (row.get('color') or "").strip()
                item = (row.get('item') or "").strip()

                if buyer and style:
                    # ডুপ্লিকেট রোধে এই কুয়েরিটি সবচেয়ে নিরাপদ
                    # যদি বায়ার, স্টাইল, কালার ও আইটেম হুবহু মিলে যায় তবে ডাটা ইনসার্ট হবে না
                    cur.execute("""
                        INSERT INTO master_data (buyer, style, color, item, is_active)
                        VALUES (%s, %s, %s, %s, 1)
                        ON CONFLICT (buyer, style, color, item) DO NOTHING
                    """, (buyer, style, color, item))
                    
                    # যদি রো ইনসার্ট হয় তবে কাউন্ট বাড়বে
                    if cur.rowcount > 0:
                        count += 1
            
            conn.commit()
            return f"Success! {count} new rows added. Duplicates were skipped."
            
        except Exception as e:
            conn.rollback()
            return f"Database Error: {str(e)}", 500
        finally:
            cur.close()
            conn.close()
    
    return "Invalid file format", 400

@app.route('/style_details')
@login_required
def style_details():
    selected_info = request.args.get('style_info', '').strip()
    conn = db()
    cur = conn.cursor()
    
    style_list = []
    details_data = []
    
    try:
        # ১. ড্রপডাউনের জন্য master_data থেকে ডাটা আনা
        cur.execute("""
            SELECT DISTINCT 
                buyer || ' / ' || style || ' / ' || color || ' / ' || item AS label
            FROM master_data 
            WHERE is_active=1
            ORDER BY 1
        """)
        rows = cur.fetchall()
        # RealDictCursor এর জন্য row['label'] ব্যবহার করতে হবে
        style_list = [row['label'] for row in rows]
        
        if selected_info:
            parts = selected_info.split(' / ')
            if len(parts) == 4:
                b, s, c, i = parts
                
                # ২. কিউমুলেটিভ টোটাল কুয়েরি
                cur.execute("""
                    SELECT section, process_name, SUM(production_qty) as total
                    FROM production_entries 
                    WHERE buyer_name = %s AND style_name = %s AND color_name = %s AND item_name = %s
                    GROUP BY section, process_name
                    ORDER BY section
                """, (b, s, c, i))
                
                res = cur.fetchall()
                # HTML এ row[0], row[1] ভাবে দেখানোর জন্য ডাটা ফরম্যাট করা
                details_data = [[r['section'], r['process_name'], r['total']] for r in res]
                
    except Exception as e:
        print(f"Error: {e}")
        
    cur.close()
    conn.close()
    
    return render_template('style_details.html', 
                           style_list=style_list, 
                           details_data=details_data, 
                           selected_info=selected_info)

@app.route('/user_access', methods=['GET', 'POST'])
@login_required
def user_access():
    # শুধু অ্যাডমিন এই পেজে ঢুকতে পারবে (আপনার রোল সিস্টেম অনুযায়ী এটি চেক করুন)
    if session.get('role') != 'admin':
        return "Access Denied", 403

    conn = db()
    cur = conn.cursor()
    
    # সার্চের জন্য সব অপারেটর/ইউজার আইডি নিয়ে আসা
    cur.execute("SELECT operator_id, operator_name FROM operators ORDER BY operator_name ASC")
    all_users = cur.fetchall()

    selected_user = request.args.get('user_id')
    user_permissions = []

    if selected_user:
        # ওই ইউজারের বর্তমান পারমিশনগুলো ডাটাবেস থেকে চেক করা
        cur.execute("SELECT menu_name FROM user_permissions WHERE user_id = %s", (selected_user,))
        user_permissions = [row[0] for row in cur.fetchall()]

    if request.method == 'POST':
        u_id = request.form.get('user_id')
        selected_menus = request.form.getlist('menus')
        
        try:
            # আগের পারমিশন মুছে নতুনগুলো সেভ করা
            cur.execute("DELETE FROM user_permissions WHERE user_id = %s", (u_id,))
            for menu in selected_menus:
                cur.execute("INSERT INTO user_permissions (user_id, menu_name) VALUES (%s, %s)", (u_id, menu))
            conn.commit()
            return redirect(f"/user_access?user_id={u_id}")
        except Exception as e:
            conn.rollback()
            return f"Error: {str(e)}"
        finally:
            cur.close()
            conn.close()

    return render_template('user_access.html', users=all_users, selected_user=selected_user, user_permissions=user_permissions)

@app.route('/set_hourly_target', methods=['GET', 'POST'])
@login_required
def set_hourly_target():
    conn = db()
    cur = conn.cursor()
    
    if request.method == 'POST':
        try:
            master_label = request.form.get('master_label', "")
            process_name = request.form.get('process_name')
            target_qty = request.form.get('target_qty')

            # ডাটা স্প্লিট করা
            parts = [p.strip() for p in master_label.split("/")]
            if len(parts) == 4:
                buyer, style, color, item = parts
                
                cur.execute("""
                    INSERT INTO hourly_targets (buyer, style, color, item, process_name, target_qty)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (buyer, style, color, item, process_name, target_qty))
                
                conn.commit()
                return redirect(url_for('set_hourly_target'))
            else:
                return "Invalid selection format!", 400

        except Exception as e:
            conn.rollback()
            print(f"Post Error: {e}") # এটি আপনার কনসোলে এরর দেখাবে
            return f"Database Error: {str(e)}", 500
        finally:
            cur.close()
            conn.close()

    # GET পার্ট
    try:
        cur.execute("""
            SELECT DISTINCT buyer || ' / ' || style || ' / ' || color || ' / ' || item AS label 
            FROM master_data WHERE is_active=1 ORDER BY label
        """)
        master_rows = cur.fetchall()

        # টেবিলের জন্য ডাটা আনা
        cur.execute("SELECT * FROM hourly_targets ORDER BY id DESC")
        targets = cur.fetchall()
        
        return render_template('set_target.html', master_rows=master_rows, targets=targets)
    except Exception as e:
        print(f"Fetch Error: {e}")
        return f"Fetch Error: {str(e)}", 500
    finally:
        cur.close()
        conn.close()
@app.route("/get_processes_for_target/<section>")
@login_required
def get_processes_for_target(section):
    conn = db()
    cur = conn.cursor()
    
    cur.execute("""
        SELECT DISTINCT process_name 
        FROM processes 
        WHERE section = %s AND is_active = 1 
        ORDER BY process_name
    """, (section,))
    
    rows = cur.fetchall()
    cur.close()
    conn.close()
    
    # 🔥 খুবই গুরুত্বপূর্ণ: শুধুমাত্র নামের লিস্ট রিটার্ন করতে হবে
    return jsonify([row['process_name'] for row in rows])
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
