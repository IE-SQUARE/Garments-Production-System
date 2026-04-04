from flask import Flask, render_template, request, redirect, session, jsonify
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, date
from functools import wraps
import qrcode
import os
import pandas as pd
from io import BytesIO

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

    url = f"http://127.0.0.1:5000/batch_info/{batch_no}"

    img = qrcode.make(url)

    path = f"static/qr/{batch_no}.png"

    img.save(path)

    return path

@app.route("/")
def home():
    if "uid" in session:
        return redirect("/dashboard")
    return redirect("/login")

@app.route("/login", methods=["GET","POST"])
def login():

    if request.method == "POST":

        updated_from = request.form.get("updated_from")
        entry_date = request.form.get("entry_date")
        user_id = request.form.get("user_id")
        password = request.form.get("password")

        conn = db()
        cur = conn.cursor()

        cur.execute("SELECT * FROM users WHERE user_id=%s",(user_id,))
        user = cur.fetchone()

        cur.close()
        conn.close()

        if user:

            session["uid"] = user["id"]
            session["name"] = user["full_name"]
            session["role"] = user["role"]
            session["section"] = user["section"]
            session["login_time"] = datetime.now().strftime("%H:%M")

            return redirect("/dashboard")

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
@app.route("/entry", methods=["GET","POST"])
@login_required
def entry():

    conn = db()
    cur = conn.cursor()

    # sections
    cur.execute("""
    SELECT DISTINCT section
    FROM processes
    WHERE is_active=1
    ORDER BY section
    """)
    sections = cur.fetchall()

    # processes
    cur.execute("""
    SELECT id,process_name
    FROM processes
    WHERE section=%s AND is_active=1
    ORDER BY process_name
    """,(session.get("section"),))
    processes = cur.fetchall()

    # operators
    cur.execute("""
    SELECT operator_name, operator_id
    FROM operators
    WHERE is_active=1
    ORDER BY operator_name   
    """)
    operators = cur.fetchall()

    # master data
    cur.execute("""
    SELECT buyer,style,color,item,
    CONCAT(buyer,' / ',style,' / ',color,' / ',item) AS label
    FROM master_data
    WHERE is_active=1
    ORDER BY buyer,style,color,item
    """)
    master_rows = cur.fetchall()


    if request.method == "POST":

        entry_date = request.form.get("entry_date") or date.today()
        section = request.form.get("section")
        process_id = request.form.get("process_id")
        operator_input = request.form.get("operator_name")

        parts = operator_input.split(" - ")
 
        operator = parts[0] if len(parts) > 0 else ""
        emp_no = parts[1] if len(parts) > 1 else ""
        qty = request.form.get("qty")
 
        if not qty:
            qty = 0

        qty = int(qty)

        master_label = request.form.get("master_label")
        parts = master_label.split(" / ")

        buyer = parts[0] if len(parts) > 0 else ""
        style = parts[1] if len(parts) > 1 else ""
        color = parts[2] if len(parts) > 2 else ""
        item = parts[3] if len(parts) > 3 else ""

        # =========================
        # AUTO HOUR DETECTION
        # =========================

        now = datetime.now()

        shift_start = now.replace(hour=8, minute=0, second=0, microsecond=0)

        diff = now - shift_start

        hour_no = int(diff.total_seconds() // 3600) + 1

        if hour_no < 1:
            hour_no = 1

        if hour_no > 11:
            hour_no = 11

        labels = ["1st","2nd","3rd","4th","5th","6th","7th","8th","9th","10th","11th"]
        hour_label = labels[hour_no-1]

        # =========================
        # GET PROCESS NAME
        # =========================

        cur.execute("""
        SELECT process_name
        FROM processes
        WHERE id=%s
        """,(process_id,))
        p = cur.fetchone()

        if p:
            process_name = p["process_name"]
        else:
            process_name = ""

        created_by = session.get("uid")

        # =========================
        # INSERT ENTRY
        # =========================

        cur.execute("""

        INSERT INTO production_entries
        (entry_date,hour_label,section,buyer_name,style_name,color_name,item_name,
        process_name,operator_name,operator_id,production_qty,created_by)

        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,(entry_date,hour_label,section,buyer,style,color,item,
        process_name,operator,emp_no,qty,created_by))

        conn.commit()

        return redirect("/entry")


    cur.close()
    conn.close()

    today = date.today()

    return render_template(
        "entry.html",
        sections=sections,
        processes=processes,
        operators=operators,
        master_rows=master_rows,
        today=today
    )
# =========================
# SECTION → PROCESS API
# =========================

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
def create_batch():

    today = date.today()

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":

        now = datetime.now()

        year = now.strftime("%y")
        month = now.strftime("%m")

        prefix = year + month

        batch_date = request.form.get("batch_date")
        master_label = request.form.get("master_label")
        wash_type = request.form.get("wash_type")
        qty = request.form.get("qty")
        weight = request.form.get("weight")
        priority = request.form.get("priority")

        cur.execute("""
            SELECT batch_number
            FROM batches
            WHERE batch_number LIKE %s
            ORDER BY batch_number DESC
            LIMIT 1
        """,(prefix + "%",))

        last = cur.fetchone()

        if last:
            last_serial = int(str(last["batch_number"])[4:])
            serial = last_serial + 1
        else:
            serial = 1

        batch_number = int(prefix + str(serial).zfill(3))

        buyer,style,color,item = master_label.split(" / ")

        cur.execute("""
            INSERT INTO batches
            (batch_number,batch_date,buyer,style,color,item,wash_type,batch_qty,batch_weight,priority)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,(batch_number,batch_date,buyer,style,color,item,wash_type,qty,weight,priority))

        conn.commit()

        return redirect("/create_batch")

    # dropdown label
    cur.execute("""
SELECT CONCAT(buyer,' / ',style,' / ',color,' / ',item) AS label
FROM master_data
ORDER BY buyer,style,color,item
""")
    master_rows = cur.fetchall()

    # batch list
    cur.execute("""
        SELECT
        batch_number,
        buyer,
        style,
        color,
        item,
        wash_type,
        batch_qty,
        batch_weight,
        priority,
        created_at
        FROM batches
        ORDER BY id DESC
    """)
    batches = cur.fetchall()

    return render_template(
        "create_batch.html",
        master_rows=master_rows,
        batches=batches,
        today=today
    )

@app.route("/print_batch/<int:batch_no>")
def print_batch(batch_no):

    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM batches WHERE batch_number=%s",(batch_no,))
    batch = cur.fetchone()

    qr_path = generate_qr(batch_no)

    return render_template(
        "batch_card.html",
        batch=batch,
        qr_path=qr_path
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

    cur.execute("""
        SELECT *
        FROM batches
        WHERE batch_number=%s
    """,(batch_no,))

    batch = cur.fetchone()

    return render_template(
        "batch_info.html",
        batch=batch
    )

@app.route("/operator_status")
@login_required
def operator_status():

    date = request.args.get("date")
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

    if date:
        query += " AND entry_date = %s"
        params.append(date)

    if section:
        query += " AND p.section = %s"
        params.append(section)

    # shift filter
    if shift == "DAY":
        query += " AND hour_label IN ('1st','2nd','3rd','4th','5th','6th')"

    elif shift == "NIGHT":
        query += " AND hour_label IN ('7th','8th','9th','10th','11th')"

    query += " GROUP BY o.operator_name, p.operator_id ORDER BY o.operator_name"

    print("QUERY:", query)
    print("PARAMS:", params)

    cur.execute(query, params)
    rows = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("operator_status.html", rows=rows,date=date)
# =========================
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
    date = request.args.get("date")

    if not date or date == "None":
        date = datetime.today().strftime("%Y-%m-%d")

    # date empty হলে today use করবে
    if not date:
        date = datetime.today().strftime("%Y-%m-%d")

    conn = db()
    cur = conn.cursor()

    query = """
    SELECT
        buyer_name,
        style_name,
        color_name,
        item_name,
        process_name,

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

    FROM production_entries

    WHERE operator_id=%s
    AND entry_date=%s

    GROUP BY buyer_name,style_name,color_name,item_name,process_name
    ORDER BY style_name
    """

    cur.execute(query,(emp_id,date))
    rows = cur.fetchall()

    return render_template("operator_detail.html",
                           rows=rows,
                           emp_id=emp_id,
                           date=date)
@app.route("/master_data", methods=["GET","POST"])
@login_required
def master_data():

    conn = db()
    cur = conn.cursor()

    if request.method == "POST":

        buyer = request.form.get("buyer")
        style = request.form.get("style")
        color = request.form.get("color")
        item = request.form.get("item")

        cur.execute("""
        INSERT INTO master_data (buyer,style,color,item,is_active)
        VALUES (%s,%s,%s,%s,1)
        """,(buyer,style,color,item))

        conn.commit()

    cur.execute("SELECT * FROM master_data ORDER BY buyer")
    rows = cur.fetchall()

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
    if file.filename == '':
        return "No file selected", 400

    if file and file.filename.endswith('.csv'):
        csv_file = TextIOWrapper(file.stream, encoding='utf-8-sig')
        reader = csv.DictReader(csv_file)
        reader.fieldnames = [name.strip() for name in reader.fieldnames]
        
        conn = db()
        cur = conn.cursor()
        
        try:
            for row in reader:
                # CSV থেকে ডাটা নেওয়া
                buyer = (row.get('Buyer') or row.get('Buyer ')).strip()
                style = row.get('Style').strip()
                color = row.get('Color').strip()
                item = row.get('Item').strip()

                # 'ON CONFLICT DO NOTHING' ব্যবহার করা হয়েছে যাতে ডুপ্লিকেট হলে এরর না দেয়
                cur.execute("""
                    INSERT INTO master_data (buyer_name, style_name, color_name, item_name)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (buyer_name, style_name, color_name, item_name) DO NOTHING
                """, (buyer, style, color, item))
                
            conn.commit()
            return "Bulk data processed! (Duplicate entries were skipped automatically)"
        except Exception as e:
            conn.rollback()
            return f"Database Error: {str(e)}", 500
        finally:
            cur.close()
            conn.close()
    
    return "Invalid file format. Please upload a .csv file.", 400
@app.route('/style_details')
@login_required
def style_details():
    selected_info = request.args.get('style_info', '').strip()
    conn = db()
    cur = conn.cursor()
    
    style_list = []
    details_data = []
    
    try:
        # master_data টেবিল থেকে ড্রপডাউন লিস্ট তৈরি
        # এখানে নিশ্চিত করুন আপনার কলামের নামগুলো সঠিক (যেমন: buyer_name না হলে buyer দিন)
        cur.execute("""
            SELECT DISTINCT 
                buyer_name || ' / ' || style_name || ' / ' || color || ' / ' || item_name 
            FROM master_data 
            ORDER BY 1
        """)
        style_list = [row[0] for row in cur.fetchall()]
        
        if selected_info:
            parts = selected_info.split(' / ')
            if len(parts) == 4:
                buyer, style, color, item = parts
                
                # কিউমুলেটিভ টোটাল (SUM) বের করার কুয়েরি
                cur.execute("""
                    SELECT section, process_name, SUM(production_qty)
                    FROM production_entries 
                    WHERE buyer_name = %s AND style_name = %s AND color = %s AND item_name = %s
                    GROUP BY section, process_name
                    ORDER BY section, process_name
                """, (buyer, style, color, item))
                details_data = cur.fetchall()
                
    except Exception as e:
        print(f"Error logic: {e}")
        # যদি buyer_name কলাম না পায়, তবে এখানে প্রিন্ট হবে
        
    cur.close()
    conn.close()
    
    return render_template('style_details.html', 
                           style_list=style_list, 
                           details_data=details_data, 
                           selected_info=selected_info,
                           name=session.get('name'), 
                           role=session.get('role'))

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
