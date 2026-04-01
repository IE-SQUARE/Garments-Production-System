import pandas as pd
import pymysql

DB_HOST = "127.0.0.1"
DB_USER = "root"
DB_PASS = "Santarin896935@#"
DB_NAME = "production_db"

TABLE_NAME = "master_data"

EXCEL_FILE = "Buyer_STYLE.xlsx"  # আপনার folder এ এই নামেই আছে

def main():
    # 1) Excel read
    df = pd.read_excel(EXCEL_FILE)
    df.columns = [c.strip().lower() for c in df.columns]

    # 2) column mapping (Buyer Name -> buyer)
    df = df.rename(columns={"buyer name": "buyer"})

    needed = ["buyer", "style", "color", "item"]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        raise Exception(f"Excel এ কলাম missing: {missing}. আছে: {list(df.columns)}")

    df = df[needed].dropna().drop_duplicates()

    # 3) MySQL connect
    conn = pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME
    )
    cursor = conn.cursor()

    # 4) Insert (duplicate থাকলে ignore করতে চাইলে নিচে UNIQUE দরকার—আপাতত simple insert)
    sql = f"""INSERT IGNORE INTO {TABLE_NAME} (buyer, style, color, item) VALUES (%s, %s, %s, %s)
"""

    inserted = 0
    for _, r in df.iterrows():
        cursor.execute(sql, (r["buyer"], r["style"], r["color"], r["item"]))
        inserted += 1

    conn.commit()
    cursor.close()
    conn.close()

    print(f"✅ Import Done! Rows inserted: {inserted}")

if __name__ == "__main__":
    main()