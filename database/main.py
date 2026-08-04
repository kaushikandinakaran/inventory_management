import sqlite3
import datetime


def initialize_database():
    # Connect to the database (creates it if it doesn't exist)
    conn = sqlite3.connect('restaurant_v2.db')

    # Enable foreign key constraints in SQLite
    conn.execute("PRAGMA foreign_keys = ON;")
    c = conn.cursor()

    # 1. Create Vendors Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS vendors (
            vendor_id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT NOT NULL UNIQUE,
            contact_method TEXT NOT NULL,
            contact_info TEXT NOT NULL
        )
    ''')

    # 2. Create Items Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS items (
            item_id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            item_name TEXT NOT NULL UNIQUE,
            unit_measure TEXT NOT NULL,
            current_stock INTEGER NOT NULL DEFAULT 0,
            min_qty INTEGER NOT NULL,
            max_qty INTEGER NOT NULL,
            FOREIGN KEY (vendor_id) REFERENCES vendors (vendor_id) ON DELETE RESTRICT
        )
    ''')

    # 3. Create Usage Logs Table
    c.execute('''
        CREATE TABLE IF NOT EXISTS usage_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL,
            qty_deducted INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (item_id) REFERENCES items (item_id) ON DELETE CASCADE
        )
    ''')

    # --- Insert Dummy Data for Testing ---

    # Insert Vendors
    vendors_data = [
        ('SJ', 'SMS', '+19405942407'),
        ('Restaurant Depot', 'In-Person', '+19405942407')
    ]
    c.executemany('''
        INSERT OR IGNORE INTO vendors (vendor_name, contact_method, contact_info)
        VALUES (?, ?, ?)
    ''', vendors_data)

    # Insert Items (Mapping to Vendor IDs 1, 2, and 3)
    items_data = [
        (1, 'Yellow Onions', '50lb Bag', 3, 5, 15),  # Supplied by Sysco
        (1, 'Frying Oil', '1 Can', 2, 4, 10),  # Supplied by Sysco
        (2, 'Tomato Paste', '1 Can', 12, 10, 30),  # Supplied by US Foods
        (2, 'Fresh Basil', '1kg Box', 1, 2, 5)  # Supplied by Local Farmer
    ]
    c.executemany('''
        INSERT OR IGNORE INTO items (vendor_id, item_name, unit_measure, current_stock, min_qty, max_qty)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', items_data)

    # Insert a sample usage log for today
    sample_log = [
        (1, 1),  # Used 1 bag of Yellow Onions (item_id 1)
        (3, 2)  # Used 2 cans of Tomato Paste (item_id 3)
    ]
    c.executemany('''
        INSERT INTO usage_logs (item_id, qty_deducted)
        VALUES (?, ?)
    ''', sample_log)

    conn.commit()
    conn.close()
    print("Normalized database 'restaurant_v2.db' created successfully!")


if __name__ == '__main__':
    initialize_database()