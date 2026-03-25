import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)

IS_VERCEL     = bool(os.environ.get('VERCEL'))
DATABASE_URL  = os.environ.get('DATABASE_URL')          # set this on Vercel → uses PostgreSQL
UPLOAD_FOLDER = '/tmp/st_uploads' if IS_VERCEL else os.path.join(os.path.dirname(__file__), 'uploads')
DB_PATH       = '/tmp/shipments.db' if IS_VERCEL else os.path.join(os.path.dirname(__file__), 'shipments.db')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

USE_PG = bool(DATABASE_URL)
if USE_PG:
    import psycopg2
    import psycopg2.extras


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_db():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    if USE_PG:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS shipments (
                    id            SERIAL PRIMARY KEY,
                    ship_date     TEXT,
                    awb           TEXT UNIQUE,
                    shipping_cost REAL,
                    status        TEXT,
                    invoice_file  TEXT,
                    awb_file      TEXT
                )
            """)
        conn.commit()
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS shipments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ship_date     TEXT,
                awb           TEXT UNIQUE,
                shipping_cost REAL,
                status        TEXT,
                invoice_file  TEXT,
                awb_file      TEXT
            )
        """)
        conn.commit()
    conn.close()


init_db()


def save_file(file_obj, prefix):
    if file_obj and file_obj.filename:
        ext      = os.path.splitext(secure_filename(file_obj.filename))[1]
        filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{prefix}{ext}"
        file_obj.save(os.path.join(UPLOAD_FOLDER, filename))
        return filename
    return ''


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/manifest.json')
def manifest():
    return send_from_directory('static', 'manifest.json',
                               mimetype='application/manifest+json')


@app.route('/sw.js')
def service_worker():
    return send_from_directory('static', 'sw.js',
                               mimetype='application/javascript')


@app.route('/api/dashboard')
def dashboard():
    conn = get_db()
    if USE_PG:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS c FROM shipments");                    total     = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status='Transit'");   transit   = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status='Delivered'"); delivered = cur.fetchone()['c']
            cur.execute("SELECT COUNT(*) AS c FROM shipments WHERE status='Returned'");  returned  = cur.fetchone()['c']
    else:
        total     = conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0]
        transit   = conn.execute("SELECT COUNT(*) FROM shipments WHERE status='Transit'").fetchone()[0]
        delivered = conn.execute("SELECT COUNT(*) FROM shipments WHERE status='Delivered'").fetchone()[0]
        returned  = conn.execute("SELECT COUNT(*) FROM shipments WHERE status='Returned'").fetchone()[0]
    conn.close()
    return jsonify({'Total': total, 'Transit': transit, 'Delivered': delivered, 'Returned': returned})


@app.route('/api/shipments', methods=['GET'])
def get_shipments():
    conn   = get_db()
    params = []
    if USE_PG:
        q = "SELECT * FROM shipments WHERE 1=1"
        if request.args.get('date'):   q += " AND ship_date=%s";   params.append(request.args['date'])
        if request.args.get('awb'):    q += " AND awb LIKE %s";    params.append(f"%{request.args['awb']}%")
        if request.args.get('status'): q += " AND status=%s";      params.append(request.args['status'])
        with conn.cursor() as cur:
            cur.execute(q + " ORDER BY id DESC", params)
            rows = [dict(r) for r in cur.fetchall()]
    else:
        q = "SELECT * FROM shipments WHERE 1=1"
        if request.args.get('date'):   q += " AND ship_date=?";   params.append(request.args['date'])
        if request.args.get('awb'):    q += " AND awb LIKE ?";    params.append(f"%{request.args['awb']}%")
        if request.args.get('status'): q += " AND status=?";      params.append(request.args['status'])
        rows = [dict(r) for r in conn.execute(q + " ORDER BY id DESC", params).fetchall()]
    conn.close()
    return jsonify(rows)


@app.route('/api/shipments', methods=['POST'])
def add_shipment():
    try:
        inv   = save_file(request.files.get('invoice_file'), 'inv')
        awb_f = save_file(request.files.get('awb_file'),     'awb')
        d     = request.form
        conn  = get_db()
        if USE_PG:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO shipments (ship_date,awb,shipping_cost,status,invoice_file,awb_file)"
                    " VALUES(%s,%s,%s,%s,%s,%s) RETURNING *",
                    (d['ship_date'], d['awb'], float(d.get('shipping_cost') or 0), d['status'], inv, awb_f)
                )
                new_row = dict(cur.fetchone())
            conn.commit()
        else:
            conn.execute(
                "INSERT INTO shipments (ship_date,awb,shipping_cost,status,invoice_file,awb_file) VALUES(?,?,?,?,?,?)",
                (d['ship_date'], d['awb'], float(d.get('shipping_cost') or 0), d['status'], inv, awb_f)
            )
            conn.commit()
            new_id  = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            new_row = dict(conn.execute("SELECT * FROM shipments WHERE id=?", (new_id,)).fetchone())
        conn.close()
        return jsonify({'success': True, 'record': new_row})
    except Exception as e:
        msg = str(e)
        if 'UNIQUE' in msg or 'unique' in msg or 'duplicate' in msg.lower():
            return jsonify({'error': 'AWB already exists'}), 400
        return jsonify({'error': msg}), 500


@app.route('/api/shipments/<int:sid>', methods=['PUT'])
def update_shipment(sid):
    try:
        conn = get_db()
        if USE_PG:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM shipments WHERE id=%s", (sid,))
                existing = dict(cur.fetchone())
            inv   = save_file(request.files.get('invoice_file'), 'inv') or existing.get('invoice_file', '')
            awb_f = save_file(request.files.get('awb_file'),     'awb') or existing.get('awb_file', '')
            d     = request.form
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE shipments SET ship_date=%s,awb=%s,shipping_cost=%s,status=%s,invoice_file=%s,awb_file=%s WHERE id=%s RETURNING *",
                    (d['ship_date'], d['awb'], float(d.get('shipping_cost') or 0), d['status'], inv, awb_f, sid)
                )
                updated = dict(cur.fetchone())
            conn.commit()
        else:
            existing = dict(conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone())
            inv   = save_file(request.files.get('invoice_file'), 'inv') or existing.get('invoice_file', '')
            awb_f = save_file(request.files.get('awb_file'),     'awb') or existing.get('awb_file', '')
            d     = request.form
            conn.execute(
                "UPDATE shipments SET ship_date=?,awb=?,shipping_cost=?,status=?,invoice_file=?,awb_file=? WHERE id=?",
                (d['ship_date'], d['awb'], float(d.get('shipping_cost') or 0), d['status'], inv, awb_f, sid)
            )
            conn.commit()
            updated = dict(conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone())
        conn.close()
        return jsonify({'success': True, 'record': updated})
    except Exception as e:
        msg = str(e)
        if 'UNIQUE' in msg or 'unique' in msg or 'duplicate' in msg.lower():
            return jsonify({'error': 'AWB already exists'}), 400
        return jsonify({'error': msg}), 500


@app.route('/api/shipments/<int:sid>', methods=['DELETE'])
def delete_shipment(sid):
    conn = get_db()
    if USE_PG:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM shipments WHERE id=%s", (sid,))
        conn.commit()
    else:
        conn.execute("DELETE FROM shipments WHERE id=?", (sid,))
        conn.commit()
    conn.close()
    return jsonify({'success': True})


@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


if __name__ == '__main__':
    print("▶  ShipTrack  →  http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
