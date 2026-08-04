import sqlite3
import os
from datetime import datetime
import zoneinfo

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.abspath(os.path.join(BASE_DIR, '..', 'database', 'restaurant_v2.db'))


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def lambda_handler(event=None, context=None):
    conn = get_db_connection()
    local_tz = zoneinfo.ZoneInfo("America/Chicago")
    today_name = datetime.now(local_tz).strftime("%A")

    print(f"--- Triggered Procurement Job for {today_name} ---")

    vendors = conn.execute("SELECT * FROM vendors").fetchall()

    for vendor in vendors:
        vendor_truck_days = vendor['order_days'] or ""
        owner_restock_days = vendor['owner_preferred_days'] or ""

        # If the vendor's truck isn't running today, we can't do anything for them.
        if today_name not in vendor_truck_days:
            continue

        print(f"Processing Vendor: {vendor['vendor_name']}")
        draft_lines = []

        # --- 1. SPECIAL REQUESTS CHECK (Ignores Owner's Preferred Days) ---
        # Fetch any pending special requests for items this vendor supplies
        special_requests = conn.execute('''
            SELECT sr.request_id, sr.requested_qty, i.item_name, i.unit_measure 
            FROM special_requests sr
            JOIN items i ON sr.item_id = i.item_id
            WHERE i.vendor_id = ? AND sr.status = 'Pending'
        ''', (vendor['vendor_id'],)).fetchall()

        if special_requests:
            draft_lines.append("**SPECIAL REQUESTS FROM KITCHEN:**")
            for req in special_requests:
                draft_lines.append(f"- {req['requested_qty']} {req['unit_measure']} of {req['item_name']}")
                conn.execute("UPDATE special_requests SET status = 'Drafted' WHERE request_id = ?",
                             (req['request_id'],))
            draft_lines.append("\n**REGULAR RESTOCK:**")

        # --- 2. AUTOMATED BULK RESTOCK CHECK (Enforces Owner's Preferred Days) ---
        if today_name in owner_restock_days:
            items = conn.execute("SELECT * FROM items WHERE vendor_id = ?", (vendor['vendor_id'],)).fetchall()

            for item in items:
                burn_query = "SELECT SUM(qty_deducted) as total_used FROM usage_logs WHERE item_id = ? AND timestamp >= date('now', '-7 days')"
                burn_data = conn.execute(burn_query, (item['item_id'],)).fetchone()

                daily_burn_rate = (burn_data['total_used'] or 0) / 7.0
                projected_stock = item['current_stock'] - (daily_burn_rate * vendor['lead_time_days'])

                if projected_stock <= item['min_qty']:
                    order_qty = item['max_qty'] - item['current_stock']
                    if order_qty > 0:
                        draft_lines.append(f"- {order_qty} {item['unit_measure']} of {item['item_name']}")

        # --- 3. DRAFT GENERATION ---
        # If we added either special requests or regular restocks, build the draft
        if draft_lines and len(draft_lines) > 1:  # >1 prevents sending a draft with just the "REGULAR RESTOCK:" header
            item_list_str = "\n".join(draft_lines)
            mock_ai_draft = f"Hello {vendor['vendor_name']} Order Desk,\n\nPlease process the following order for delivery:\n{item_list_str}\n\nThank you!"

            # Clear old pending drafts to avoid duplicates
            conn.execute("DELETE FROM draft_orders WHERE vendor_id = ? AND status = 'Pending Review'",
                         (vendor['vendor_id'],))

            conn.execute(
                "INSERT INTO draft_orders (vendor_id, ai_generated_text, status) VALUES (?, ?, 'Pending Review')",
                (vendor['vendor_id'], mock_ai_draft))
            print(f"  -> Draft staged for manager approval.")
        else:
            print(f"  -> No orders needed for this vendor today.")

    conn.commit()
    conn.close()
    print("--- Job Complete ---")
    return {"statusCode": 200, "body": "Procurement pipeline executed successfully."}


if __name__ == '__main__':
    lambda_handler()