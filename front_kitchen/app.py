from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, session
from werkzeug.utils import secure_filename
from functools import wraps
import sqlite3
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "super_secret_key"

MANAGER_PIN = "1234"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '..', 'database', 'restaurant_v2.db'))
UPLOAD_FOLDER = os.path.abspath(os.path.join(BASE_DIR, '..', 'uploads'))
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'csv', 'docx', 'txt'}


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db_updates():
    conn = get_db_connection()

    conn.execute('''CREATE TABLE IF NOT EXISTS documents (
            doc_id INTEGER PRIMARY KEY AUTOINCREMENT, doc_name TEXT NOT NULL, doc_category TEXT NOT NULL,
            file_name TEXT NOT NULL, upload_date DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    conn.execute('''CREATE TABLE IF NOT EXISTS draft_orders (
            draft_id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id INTEGER NOT NULL, ai_generated_text TEXT NOT NULL,
            status TEXT DEFAULT 'Pending Review', created_at DATETIME DEFAULT CURRENT_TIMESTAMP, sent_at DATETIME,
            FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id))''')

    conn.execute('''CREATE TABLE IF NOT EXISTS pending_deliveries (
            delivery_id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id INTEGER, status TEXT DEFAULT 'Pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id))''')

    conn.execute('''CREATE TABLE IF NOT EXISTS delivery_items (
            line_id INTEGER PRIMARY KEY AUTOINCREMENT, delivery_id INTEGER, item_id INTEGER, expected_qty INTEGER,
            FOREIGN KEY (delivery_id) REFERENCES pending_deliveries (delivery_id), FOREIGN KEY (item_id) REFERENCES items (item_id))''')

    conn.execute('''CREATE TABLE IF NOT EXISTS active_conversations (
            thread_id INTEGER PRIMARY KEY AUTOINCREMENT, vendor_id INTEGER, status TEXT DEFAULT 'Awaiting Boss', 
            latest_boss_prompt TEXT, latest_vendor_message TEXT, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)''')

    conn.execute('''CREATE TABLE IF NOT EXISTS categories (
            category_id INTEGER PRIMARY KEY AUTOINCREMENT, category_name TEXT NOT NULL UNIQUE)''')

    # --- NEW: Special Requests Table ---
    conn.execute('''CREATE TABLE IF NOT EXISTS special_requests (
            request_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER, requested_qty INTEGER,
            is_emergency BOOLEAN, status TEXT DEFAULT 'Pending', created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items (item_id))''')

    try:
        conn.execute("ALTER TABLE delivery_items ADD COLUMN received_qty INTEGER")
    except:
        pass
    try:
        conn.execute("ALTER TABLE vendors ADD COLUMN order_days TEXT")
    except:
        pass
    try:
        conn.execute("ALTER TABLE vendors ADD COLUMN lead_time_days INTEGER DEFAULT 2")
    except:
        pass
    try:
        conn.execute("ALTER TABLE items ADD COLUMN category_id INTEGER")
    except:
        pass

    try:
        conn.execute("ALTER TABLE vendors ADD COLUMN owner_preferred_days TEXT")
    except:
        pass

    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        default_cats = ['Produce', 'Meat & Poultry', 'Dairy', 'Spices & Dry Goods', 'Packaging']
        for cat in default_cats:
            conn.execute("INSERT OR IGNORE INTO categories (category_name) VALUES (?)", (cat,))

    conn.commit()
    conn.close()


init_db_updates()


# ==========================================
# AUTHENTICATION ROUTES
# ==========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)

    return decorated_function


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('pin') == MANAGER_PIN:
            session['logged_in'] = True
            return redirect(url_for('manager_dashboard'))
        flash("Invalid PIN Code.")
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash("Successfully logged out.")
    return redirect(url_for('login'))


# ==========================================
# KITCHEN ROUTES
# ==========================================
@app.route('/', methods=('GET', 'POST'))
def kitchen():
    conn = get_db_connection()
    if request.method == 'POST':
        item_id = request.form['item_id']
        qty_used = int(request.form['qty_used'])
        item = conn.execute('SELECT * FROM items WHERE item_id = ?', (item_id,)).fetchone()

        if item:
            if qty_used > item['current_stock']: qty_used = item['current_stock']
            if qty_used > 0:
                try:
                    conn.execute('UPDATE items SET current_stock = current_stock - ? WHERE item_id = ?',
                                 (qty_used, item_id))
                    conn.execute('INSERT INTO usage_logs (item_id, qty_deducted) VALUES (?, ?)', (item_id, qty_used))
                    conn.commit()
                    flash(f"Logged: Used {qty_used} {item['unit_measure']} of {item['item_name']}.")
                except sqlite3.Error as e:
                    conn.rollback()
                    flash(f"Database error occurred: {e}")
            else:
                flash(f"Cannot log usage: {item['item_name']} is currently out of stock.")
        return redirect(url_for('kitchen'))

    items_query = conn.execute('''
        SELECT i.*, c.category_name 
        FROM items i 
        LEFT JOIN categories c ON i.category_id = c.category_id
    ''').fetchall()
    items = [dict(row) for row in items_query]

    # --- NEW: Fetch "Buy Again" Items (Top 6 most delivered items) ---
    buy_again_query = conn.execute('''
        SELECT i.*, c.category_name, COUNT(di.item_id) as order_count 
        FROM items i 
        LEFT JOIN categories c ON i.category_id = c.category_id
        LEFT JOIN delivery_items di ON i.item_id = di.item_id
        GROUP BY i.item_id 
        ORDER BY order_count DESC LIMIT 6
    ''').fetchall()
    buy_again_items = [dict(row) for row in buy_again_query]

    categories_query = conn.execute('SELECT * FROM categories ORDER BY category_name').fetchall()
    categories = [dict(row) for row in categories_query]

    deliveries_query = conn.execute('''
        SELECT pd.delivery_id, pd.vendor_id, v.vendor_name 
        FROM pending_deliveries pd
        JOIN vendors v ON pd.vendor_id = v.vendor_id
        WHERE pd.status = 'Pending'
    ''').fetchall()

    pending_deliveries = []
    for d in deliveries_query:
        d_items = conn.execute('''
            SELECT di.item_id, di.expected_qty, i.item_name, i.unit_measure
            FROM delivery_items di
            JOIN items i ON di.item_id = i.item_id
            WHERE di.delivery_id = ?
        ''', (d['delivery_id'],)).fetchall()

        pending_deliveries.append({
            'delivery_id': d['delivery_id'], 'vendor_id': d['vendor_id'],
            'vendor_name': d['vendor_name'], 'items': [dict(row) for row in d_items]
        })

    conn.close()
    return render_template('kitchen.html', items=items, buy_again_items=buy_again_items, categories=categories,
                           pending_deliveries=pending_deliveries)


@app.route('/receive_delivery/<int:delivery_id>', methods=['POST'])
def receive_delivery(delivery_id):
    conn = get_db_connection()
    for key, value in request.form.items():
        if key.startswith('item_'):
            item_id = int(key.split('_')[1])
            received_qty = int(value)
            if received_qty > 0:
                conn.execute("UPDATE items SET current_stock = current_stock + ? WHERE item_id = ?",
                             (received_qty, item_id))
            existing_row = conn.execute("SELECT * FROM delivery_items WHERE delivery_id = ? AND item_id = ?",
                                        (delivery_id, item_id)).fetchone()
            if existing_row:
                conn.execute("UPDATE delivery_items SET received_qty = ? WHERE delivery_id = ? AND item_id = ?",
                             (received_qty, delivery_id, item_id))
            else:
                conn.execute(
                    "INSERT INTO delivery_items (delivery_id, item_id, expected_qty, received_qty) VALUES (?, ?, 0, ?)",
                    (delivery_id, item_id, received_qty))
    conn.execute("UPDATE pending_deliveries SET status = 'Received' WHERE delivery_id = ?", (delivery_id,))
    conn.commit()
    conn.close()
    flash("Delivery logged successfully! Stock has been updated.")
    return redirect(url_for('kitchen'))


# --- NEW: Submit Special Request Cart ---
@app.route('/submit_request', methods=['POST'])
def submit_request():
    conn = get_db_connection()
    is_emergency = 1 if request.form.get('is_emergency') == 'on' else 0
    items_requested = []

    for key, value in request.form.items():
        if key.startswith('req_qty_') and int(value) > 0:
            item_id = int(key.split('_')[2])
            qty = int(value)
            items_requested.append((item_id, qty))
            # Save request in database
            conn.execute('INSERT INTO special_requests (item_id, requested_qty, is_emergency) VALUES (?, ?, ?)',
                         (item_id, qty, is_emergency))

    if is_emergency and items_requested:
        # Group by vendor to create urgent drafts immediately
        vendors_to_items = {}
        for item_id, qty in items_requested:
            item = conn.execute('SELECT * FROM items WHERE item_id = ?', (item_id,)).fetchone()
            v_id = item['vendor_id']
            if v_id not in vendors_to_items: vendors_to_items[v_id] = []
            vendors_to_items[v_id].append(f"- {qty} {item['unit_measure']} of {item['item_name']}")

        for v_id, lines in vendors_to_items.items():
            v = conn.execute('SELECT * FROM vendors WHERE vendor_id=?', (v_id,)).fetchone()
            draft_text = f"**URGENT EMERGENCY ORDER**\n\nHello {v['vendor_name']} team,\nWe urgently need the following items delivered ASAP outside of our normal schedule:\n\n" + "\n".join(
                lines) + "\n\nPlease confirm."
            conn.execute(
                "INSERT INTO draft_orders (vendor_id, ai_generated_text, status) VALUES (?, ?, 'Pending Review')",
                (v_id, draft_text))

        flash("EMERGENCY Request sent to Manager Inbox for immediate approval!")
    elif items_requested:
        flash("Standard Request logged. It will be added to the next automated order.")
    else:
        flash("Cart was empty.")

    conn.commit()
    conn.close()
    return redirect(url_for('kitchen'))



# ==========================================
# MANAGER ROUTES (Locked by PIN)
# ==========================================
@app.route('/manager')
@login_required
def manager_dashboard():
    conn = get_db_connection()

    pending_drafts = conn.execute('''
        SELECT d.draft_id, d.ai_generated_text, d.created_at, v.vendor_name, v.contact_method, v.contact_info
        FROM draft_orders d JOIN vendors v ON d.vendor_id = v.vendor_id
        WHERE d.status = 'Pending Review' ORDER BY d.created_at DESC
    ''').fetchall()

    approved_drafts = conn.execute('''
        SELECT d.draft_id, d.ai_generated_text, d.sent_at, v.vendor_name, v.contact_method
        FROM draft_orders d JOIN vendors v ON d.vendor_id = v.vendor_id
        WHERE d.status = 'Sent' ORDER BY d.sent_at DESC
    ''').fetchall()

    discrepancies = conn.execute('''
        SELECT v.vendor_name, i.item_name, i.unit_measure, di.expected_qty, di.received_qty, pd.created_at
        FROM delivery_items di
        JOIN pending_deliveries pd ON di.delivery_id = pd.delivery_id
        JOIN items i ON di.item_id = i.item_id
        JOIN vendors v ON pd.vendor_id = v.vendor_id
        WHERE pd.status = 'Received' AND di.received_qty != di.expected_qty
        ORDER BY pd.created_at DESC
    ''').fetchall()

    needed_items = conn.execute('''
        SELECT i.*, v.vendor_name, c.category_name
        FROM items i 
        JOIN vendors v ON i.vendor_id = v.vendor_id 
        LEFT JOIN categories c ON i.category_id = c.category_id
        WHERE i.current_stock <= i.min_qty ORDER BY v.vendor_name
    ''').fetchall()

    all_items = conn.execute('''
        SELECT i.*, v.vendor_name, c.category_name 
        FROM items i 
        JOIN vendors v ON i.vendor_id = v.vendor_id
        LEFT JOIN categories c ON i.category_id = c.category_id
    ''').fetchall()

    vendors = conn.execute('SELECT * FROM vendors ORDER BY vendor_name').fetchall()
    documents = conn.execute('SELECT * FROM documents ORDER BY upload_date DESC').fetchall()
    categories = conn.execute('SELECT * FROM categories ORDER BY category_name').fetchall()

    conn.close()

    return render_template('manager.html', pending_drafts=pending_drafts, approved_drafts=approved_drafts,
                           discrepancies=discrepancies, needed_items=needed_items, all_items=all_items,
                           vendors=vendors, documents=documents, categories=categories)


@app.route('/manager/add_category', methods=['POST'])
@login_required
def add_category():
    category_name = request.form['category_name']
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO categories (category_name) VALUES (?)', (category_name,))
        conn.commit()
        flash(f"Category '{category_name}' added.")
    except:
        flash("Category already exists.")
    finally:
        conn.close()
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/edit_category', methods=['POST'])
@login_required
def edit_category():
    category_id = request.form['category_id']
    category_name = request.form['category_name']
    conn = get_db_connection()
    conn.execute('UPDATE categories SET category_name = ? WHERE category_id = ?', (category_name, category_id))
    conn.commit()
    conn.close()
    flash(f"Category updated to '{category_name}'.")
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/delete_category/<int:category_id>', methods=['POST'])
@login_required
def delete_category(category_id):
    conn = get_db_connection()
    conn.execute('UPDATE items SET category_id = NULL WHERE category_id = ?', (category_id,))
    conn.execute('DELETE FROM categories WHERE category_id = ?', (category_id,))
    conn.commit()
    conn.close()
    flash("Category deleted successfully.")
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/add', methods=['POST'])
@login_required
def add_item():
    item_name = request.form['item_name']
    vendor_id = request.form['vendor_id']
    category_id = request.form.get('category_id') or None
    unit_measure = request.form['unit_measure']
    current_stock = request.form['current_stock']
    min_qty = request.form['min_qty']
    max_qty = request.form['max_qty']
    conn = get_db_connection()
    try:
        conn.execute(
            'INSERT INTO items (vendor_id, category_id, item_name, unit_measure, current_stock, min_qty, max_qty) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (vendor_id, category_id, item_name, unit_measure, current_stock, min_qty, max_qty))
        conn.commit()
        flash(f"Successfully added {item_name}!")
    except:
        flash(f"Error: {item_name} already exists.")
    finally:
        conn.close()
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/edit_item', methods=['POST'])
@login_required
def edit_item():
    item_id = request.form['item_id']
    item_name = request.form['item_name']
    vendor_id = request.form['vendor_id']
    category_id = request.form.get('category_id') or None
    unit_measure = request.form['unit_measure']
    current_stock = request.form['current_stock']
    min_qty = request.form['min_qty']
    max_qty = request.form['max_qty']
    conn = get_db_connection()
    conn.execute(
        'UPDATE items SET item_name=?, vendor_id=?, category_id=?, unit_measure=?, current_stock=?, min_qty=?, max_qty=? WHERE item_id=?',
        (item_name, vendor_id, category_id, unit_measure, current_stock, min_qty, max_qty, item_id))
    conn.commit()
    conn.close()
    flash(f"Successfully updated {item_name}!")
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/delete/<int:item_id>', methods=['POST'])
@login_required
def delete_item(item_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM items WHERE item_id = ?', (item_id,))
    conn.commit()
    conn.close()
    flash("Item successfully deleted.")
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/add_vendor', methods=['POST'])
@login_required
def add_vendor():
    vendor_name = request.form['vendor_name']
    contact_method = request.form['contact_method']
    contact_info = request.form['contact_info']
    lead_time_days = request.form.get('lead_time_days', 2)

    order_days = ",".join(request.form.getlist('order_days')) if request.form.getlist(
        'order_days') else "Monday,Tuesday,Wednesday,Thursday,Friday"
    owner_preferred_days = ",".join(request.form.getlist('owner_preferred_days')) if request.form.getlist(
        'owner_preferred_days') else "Monday,Thursday"

    conn = get_db_connection()
    try:
        conn.execute(
            'INSERT INTO vendors (vendor_name, contact_method, contact_info, order_days, owner_preferred_days, lead_time_days) VALUES (?, ?, ?, ?, ?, ?)',
            (vendor_name, contact_method, contact_info, order_days, owner_preferred_days, lead_time_days))
        conn.commit()
        flash(f"Successfully added vendor {vendor_name}!")
    except:
        flash(f"Error: Vendor {vendor_name} already exists.")
    finally:
        conn.close()
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/edit_vendor', methods=['POST'])
@login_required
def edit_vendor():
    vendor_id = request.form['vendor_id']
    vendor_name = request.form['vendor_name']
    contact_method = request.form['contact_method']
    contact_info = request.form['contact_info']
    lead_time_days = request.form.get('lead_time_days', 2)

    order_days = ",".join(request.form.getlist('order_days'))
    owner_preferred_days = ",".join(request.form.getlist('owner_preferred_days'))

    conn = get_db_connection()
    conn.execute(
        'UPDATE vendors SET vendor_name=?, contact_method=?, contact_info=?, order_days=?, owner_preferred_days=?, lead_time_days=? WHERE vendor_id=?',
        (vendor_name, contact_method, contact_info, order_days, owner_preferred_days, lead_time_days, vendor_id))
    conn.commit()
    conn.close()
    flash(f"Successfully updated vendor {vendor_name}!")
    return redirect(url_for('manager_dashboard'))

@app.route('/manager/delete_vendor/<int:vendor_id>', methods=['POST'])
@login_required
def delete_vendor(vendor_id):
    conn = get_db_connection()
    item_count = conn.execute("SELECT COUNT(*) FROM items WHERE vendor_id = ?", (vendor_id,)).fetchone()[0]

    if item_count > 0:
        flash(f"Cannot delete vendor. They still supply {item_count} items. Reassign or delete those items first.")
    else:
        conn.execute('DELETE FROM vendors WHERE vendor_id = ?', (vendor_id,))
        conn.commit()
        flash("Vendor safely deleted.")

    conn.close()
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/approve_draft/<int:draft_id>', methods=['POST'])
@login_required
def approve_draft(draft_id):
    conn = get_db_connection()
    draft = conn.execute("SELECT vendor_id FROM draft_orders WHERE draft_id = ?", (draft_id,)).fetchone()

    # 1. Update the draft status and create the empty delivery panel
    conn.execute("UPDATE draft_orders SET status = 'Sent', sent_at = CURRENT_TIMESTAMP WHERE draft_id = ?", (draft_id,))
    cursor = conn.execute("INSERT INTO pending_deliveries (vendor_id) VALUES (?)", (draft['vendor_id'],))
    delivery_id = cursor.lastrowid

    # 2. Add REGULAR restock items to the checklist
    ordered_items = conn.execute(
        "SELECT item_id, (max_qty - current_stock) as qty FROM items WHERE vendor_id = ? AND current_stock <= min_qty",
        (draft['vendor_id'],)).fetchall()
    for item in ordered_items:
        if item['qty'] > 0:
            conn.execute("INSERT INTO delivery_items (delivery_id, item_id, expected_qty) VALUES (?, ?, ?)",
                         (delivery_id, item['item_id'], item['qty']))

    # 3. Add SPECIAL/EMERGENCY Request items to the checklist
    # We look for both 'Drafted' (regular schedule) and 'Pending' (urgent emergency) requests
    special_items = conn.execute('''
        SELECT sr.item_id, sr.requested_qty, sr.request_id 
        FROM special_requests sr
        JOIN items i ON sr.item_id = i.item_id
        WHERE i.vendor_id = ? AND sr.status IN ('Drafted', 'Pending')
    ''', (draft['vendor_id'],)).fetchall()

    for s_item in special_items:
        # Check if the item is already on the checklist from the regular restock
        existing = conn.execute("SELECT * FROM delivery_items WHERE delivery_id = ? AND item_id = ?",
                                (delivery_id, s_item['item_id'])).fetchone()

        if existing:
            # If it's already on the list, just add the extra requested quantity to it
            conn.execute(
                "UPDATE delivery_items SET expected_qty = expected_qty + ? WHERE delivery_id = ? AND item_id = ?",
                (s_item['requested_qty'], delivery_id, s_item['item_id']))
        else:
            # Otherwise, add it as a new line item
            conn.execute("INSERT INTO delivery_items (delivery_id, item_id, expected_qty) VALUES (?, ?, ?)",
                         (delivery_id, s_item['item_id'], s_item['requested_qty']))

        # Mark the special request as successfully ordered
        conn.execute("UPDATE special_requests SET status = 'Ordered' WHERE request_id = ?", (s_item['request_id'],))

    conn.commit()
    conn.close()
    flash("Order Approved and Delivery Checklist created for the Kitchen!")
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/reject_draft/<int:draft_id>', methods=['POST'])
@login_required
def reject_draft(draft_id):
    conn = get_db_connection()
    conn.execute('DELETE FROM draft_orders WHERE draft_id = ?', (draft_id,))
    conn.commit()
    conn.close()
    flash("Draft order discarded.")
    return redirect(url_for('manager_dashboard'))


@app.route('/manager/upload_doc', methods=['POST'])
@login_required
def upload_doc():
    if 'document_file' not in request.files:
        return redirect(url_for('manager_dashboard'))
    file = request.files['document_file']
    if file.filename == '':
        return redirect(url_for('manager_dashboard'))

    # --- NEW: Check if file extension is safe ---
    if file and allowed_file(file.filename):
        doc_name = request.form['doc_name']
        doc_category = request.form['doc_category']
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        conn = get_db_connection()
        conn.execute('INSERT INTO documents (doc_name, doc_category, file_name) VALUES (?, ?, ?)',
                     (doc_name, doc_category, filename))
        conn.commit()
        conn.close()
        flash(f"Document '{doc_name}' uploaded successfully!")
    else:
        flash("Invalid file type. Allowed types: PDF, PNG, JPG, JPEG, CSV, DOCX, TXT")

    return redirect(url_for('manager_dashboard'))


@app.route('/manager/download_doc/<filename>')
@login_required
def download_doc(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/manager/delete_doc/<int:doc_id>/<filename>', methods=['POST'])
@login_required
def delete_doc(doc_id, filename):
    conn = get_db_connection()
    conn.execute('DELETE FROM documents WHERE doc_id = ?', (doc_id,))
    conn.commit()
    conn.close()
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path): os.remove(file_path)
    flash("Document deleted successfully.")
    return redirect(url_for('manager_dashboard'))


# ==========================================
# API ROUTES - AI CHAT (PLACEHOLDERS)
# ==========================================
@app.route('/api/sms', methods=['POST'])
def sms_webhook():
    return "OK", 200


@app.route('/api/get_active_chats')
def get_active_chats():
    return jsonify([])


@app.route('/api/web_chat_reply', methods=['POST'])
def web_chat_reply():
    return jsonify({"status": "success"})


if __name__ == '__main__':
    app.run(debug=True, port=5000)