from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import models
from operations.models import Restaurant, Vendor, Item, DraftOrder, SpecialRequest


class Command(BaseCommand):
    help = 'Analyzes inventory thresholds and generates deterministic purchase orders via a data pipeline.'

    def handle(self, *args, **kwargs):
        self.stdout.write(self.style.NOTICE("Starting Automated Procurement Pipeline..."))

        # Iterate through every restaurant in a multi-tenant setup
        restaurants = Restaurant.objects.all()

        for restaurant in restaurants:
            self.stdout.write(f"Analyzing inventory for: {restaurant.name}")

            # ==========================================
            # 1. EXTRACTION PHASE
            # ==========================================
            # Find all items where stock is at or below the minimum threshold
            low_stock_items = Item.objects.filter(
                restaurant=restaurant,
                current_stock__lte=models.F('min_qty')
            ).select_related('vendor')

            # Find standard Kitchen Requests (is_emergency=False)
            standard_requests = SpecialRequest.objects.filter(
                item__restaurant=restaurant,
                is_emergency=False
            ).select_related('item', 'item__vendor')

            # ==========================================
            # 2. TRANSFORMATION PHASE
            # ==========================================
            vendor_orders = {}

            # Sweep up low stock items
            for item in low_stock_items:
                if item.vendor not in vendor_orders:
                    vendor_orders[item.vendor] = []

                order_qty = item.max_qty - item.current_stock
                vendor_orders[item.vendor].append({
                    'name': item.name,
                    'qty': order_qty,
                    'unit': item.unit_measure
                })

            # Sweep up standard requests
            for req in standard_requests:
                vendor = req.item.vendor
                if vendor not in vendor_orders:
                    vendor_orders[vendor] = []

                vendor_orders[vendor].append({
                    'name': req.item.name,
                    'qty': req.requested_qty,
                    'unit': req.item.unit_measure
                })
                # Delete the request from the queue so it isn't ordered twice
                req.delete()

            if not vendor_orders:
                self.stdout.write(self.style.SUCCESS(f"  No items or requests found for {restaurant.name}. Moving on."))
                continue

            # ==========================================
            # 3. LOAD PHASE
            # ==========================================
            for vendor, items in vendor_orders.items():
                self.stdout.write(f"  Generating deterministic order for {vendor.name}...")

                try:
                    # Construct the final order text programmatically
                    formatted_text = self._generate_order_text(restaurant, vendor, items)

                    # Save the Draft to the Database
                    # Note: We are keeping the field name `ai_generated_text` to avoid needing a database migration,
                    # but the data is now strictly pipeline-generated.
                    DraftOrder.objects.create(
                        restaurant=restaurant,
                        vendor=vendor,
                        ai_generated_text=formatted_text,
                        status='Pending Review'
                    )
                    self.stdout.write(self.style.SUCCESS(f"  Successfully drafted order for {vendor.name}."))

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  Failed to generate draft for {vendor.name}: {str(e)}"))

        self.stdout.write(self.style.SUCCESS("Procurement Pipeline cycle complete."))

    def _generate_order_text(self, restaurant, vendor, items):
        """Transforms the raw item data into a clean, formatted purchase order string."""

        item_list_str = "\n".join([f"- {i['qty']} {i['unit']} of {i['name']}" for i in items])

        # Check if any items trigger an urgent flag
        is_urgent = any(i['qty'] > 0 for i in items)
        header = "**URGENT PRIORITY ORDER**\n\n" if is_urgent else ""

        order_text = (
            f"{header}"
            f"Hello {vendor.name},\n\n"
            f"Please process the following purchase order for {restaurant.name}:\n\n"
            f"{item_list_str}\n\n"
            f"Thank you,\n"
            f"ChefStack Automated System"
        )

        return order_text