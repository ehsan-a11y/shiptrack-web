import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Vercel uses /tmp for writable storage; local uses project folder
IS_VERCEL     = bool(os.environ.get('VERCEL'))
UPLOAD_FOLDER = '/tmp/st_uploads' if IS_VERCEL else os.path.join(os.path.dirname(__file__), 'uploads')
DB_PATH       = '/tmp/shipments.db' if IS_VERCEL else os.path.join(os.path.dirname(__file__), 'shipments.db')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
    CREATE TABLE IF NOT EXISTS shipments (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        ship_date     TEXT,
        awb           TEXT UNIQUE,
        shipping_cost REAL,
        status        TEXT,
        invoice_file  TEXT,
        awb_file      TEXT
    )""")
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


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/dashboard')
def dashboard():
    conn = get_db()
    stats = {
        'Total':     conn.execute("SELECT COUNT(*) FROM shipments").fetchone()[0],
        'Transit':   conn.execute("SELECT COUNT(*) FROM shipments WHERE status='Transit'").fetchone()[0],
        'Delivered': conn.execute("SELECT COUNT(*) FROM shipments WHERE status='Delivered'").fetchone()[0],
        'Returned':  conn.execute("SELECT COUNT(*) FROM shipments WHERE status='Returned'").fetchone()[0],
    }
    conn.close()
    return jsonify(stats)


@app.route('/api/shipments', methods=['GET'])
def get_shipments():
    conn  = get_db()
    q     = "SELECT * FROM shipments WHERE 1=1"
    params = []
    if request.args.get('date'):
        q += " AND ship_date=?";   params.append(request.args['date'])
    if request.args.get('awb'):
        q += " AND awb LIKE ?";    params.append(f"%{request.args['awb']}%")
    if request.args.get('status'):
        q += " AND status=?";      params.append(request.args['status'])
    rows = conn.execute(q + " ORDER BY id DESC", params).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route('/api/shipments', methods=['POST'])
def add_shipment():
    try:
        inv   = save_file(request.files.get('invoice_file'), 'inv')
        awb_f = save_file(request.files.get('awb_file'),     'awb')
        d     = request.form
        conn  = get_db()
        conn.execute(
            "INSERT INTO shipments (ship_date,awb,shipping_cost,status,invoice_file,awb_file) VALUES(?,?,?,?,?,?)",
            (d['ship_date'], d['awb'], float(d.get('shipping_cost') or 0), d['status'], inv, awb_f)
        )
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'AWB already exists'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/shipments/<int:sid>', methods=['PUT'])
def update_shipment(sid):
    try:
        conn     = get_db()
        existing = dict(conn.execute("SELECT * FROM shipments WHERE id=?", (sid,)).fetchone())
        inv   = save_file(request.files.get('invoice_file'), 'inv') or existing.get('invoice_file', '')
        awb_f = save_file(request.files.get('awb_file'),     'awb') or existing.get('awb_file', '')
        d     = request.form
        conn.execute(
            "UPDATE shipments SET ship_date=?,awb=?,shipping_cost=?,status=?,invoice_file=?,awb_file=? WHERE id=?",
            (d['ship_date'], d['awb'], float(d.get('shipping_cost') or 0), d['status'], inv, awb_f, sid)
        )
        conn.commit(); conn.close()
        return jsonify({'success': True})
    except sqlite3.IntegrityError:
        return jsonify({'error': 'AWB already exists'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/shipments/<int:sid>', methods=['DELETE'])
def delete_shipment(sid):
    conn = get_db()
    conn.execute("DELETE FROM shipments WHERE id=?", (sid,))
    conn.commit(); conn.close()
    return jsonify({'success': True})


@app.route('/uploads/<filename>')
def serve_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


if __name__ == '__main__':
    print("▶  ShipTrack  →  http://localhost:5000")
    app.run(debug=True, host='0.0.0.0', port=5000)
