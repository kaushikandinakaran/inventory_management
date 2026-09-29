from django.db import models
from django.contrib.auth.models import AbstractUser


# ==========================================
#        OPERATIONAL DATABASE (OLTP)
# ==========================================

# --- 1. Core & Multi-Tenancy ---

class Restaurant(models.Model):
    name = models.CharField(max_length=255)
    street = models.CharField(max_length=255, blank=True, null=True)
    city = models.CharField(max_length=100, blank=True, null=True)
    state = models.CharField(max_length=100, blank=True, null=True)
    postal_code = models.CharField(max_length=20, blank=True, null=True)

    # Kitchen Kiosk Authentication
    kitchen_login_id = models.CharField(max_length=100, unique=True, help_text="Used for the shared kitchen tablet.")
    kitchen_password_hash = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class User(AbstractUser):
    ROLE_CHOICES = [
        ('ADMIN', 'Admin'),
        ('MANAGER', 'Manager'),
    ]
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, null=True, blank=True, related_name='users')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='MANAGER')

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


# --- 2. Inventory & Logistics ---

class Category(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.name} ({self.restaurant.name})"


class Vendor(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='vendors')
    name = models.CharField(max_length=255)
    lead_time_days = models.IntegerField(default=2)

    def __str__(self):
        return self.name


class VendorContact(models.Model):
    CONTACT_CHOICES = [
        ('Email', 'Email'),
        ('SMS', 'SMS Text'),
        ('API', 'API / Portal'),
        ('Phone', 'Phone Call'),
    ]
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='contacts')
    contact_type = models.CharField(max_length=20, choices=CONTACT_CHOICES)
    contact_value = models.CharField(max_length=255)
    is_primary = models.BooleanField(default=False)


class VendorTruckDay(models.Model):
    DAY_CHOICES = [(day, day) for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']]
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='truck_days')
    day_of_week = models.CharField(max_length=10, choices=DAY_CHOICES)


class VendorRestockDay(models.Model):
    DAY_CHOICES = [(day, day) for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']]
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='restock_days')
    day_of_week = models.CharField(max_length=10, choices=DAY_CHOICES)


class Item(models.Model):
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='items')
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name='items')
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='items')

    name = models.CharField(max_length=255)
    unit_measure = models.CharField(max_length=50)
    current_stock = models.FloatField(default=0)
    min_qty = models.FloatField()
    max_qty = models.FloatField()
    current_unit_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)

    def __str__(self):
        return self.name


# --- 3. Operations & AI Workflows ---

class UsageLog(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='usage_logs')
    qty_deducted = models.FloatField()
    timestamp = models.DateTimeField(auto_now_add=True)


class SpecialRequest(models.Model):
    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Drafted', 'Drafted'),
        ('Ordered', 'Ordered')
    ]
    item = models.ForeignKey(Item, on_delete=models.CASCADE, related_name='special_requests')
    requested_qty = models.FloatField()
    is_emergency = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending')
    timestamp = models.DateTimeField(auto_now_add=True)


class DraftOrder(models.Model):
    STATUS_CHOICES = [
        ('Pending Review', 'Pending Review'),
        ('Sent', 'Sent'),
        ('Rejected', 'Rejected')
    ]
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='draft_orders')
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='draft_orders')
    ai_generated_text = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Pending Review')
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)


class Delivery(models.Model):
    STATUS_CHOICES = [
        ('Arrived', 'Arrived'),
        ('Received', 'Received')
    ]
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='deliveries')
    vendor = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='deliveries')
    draft_order = models.OneToOneField(DraftOrder, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='delivery')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Arrived')
    created_at = models.DateTimeField(auto_now_add=True)


class DeliveryItem(models.Model):
    delivery = models.ForeignKey(Delivery, on_delete=models.CASCADE, related_name='delivery_items')
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    expected_qty = models.FloatField()
    received_qty = models.FloatField(null=True, blank=True)


class Document(models.Model):
    CATEGORY_CHOICES = [
        ('License/Permit', 'License / Permit'),
        ('Contract', 'Vendor Contract'),
        ('Invoice', 'Invoice / Receipt'),
        ('Employee Record', 'Employee Record'),
        ('Other', 'Other')
    ]
    restaurant = models.ForeignKey(Restaurant, on_delete=models.CASCADE, related_name='documents')
    doc_name = models.CharField(max_length=255)
    doc_category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    # FIX: Changed upload_date to upload_to
    file_path = models.FileField(upload_to='documents/%Y/%m/%d/')
    upload_date = models.DateTimeField(auto_now_add=True)

# ==========================================
#    ANALYTICAL DATABASE (RAW DATA STREAM)
# ==========================================
# Note: In Django settings, these models will eventually be routed to a secondary database.

class RawUsageEvent(models.Model):
    event_id = models.CharField(max_length=255, primary_key=True)
    restaurant_id = models.IntegerField()
    item_id = models.IntegerField()
    item_name = models.CharField(max_length=255)
    qty_deducted = models.FloatField()
    event_timestamp = models.DateTimeField()


class RawDeliveryEvent(models.Model):
    event_id = models.CharField(max_length=255, primary_key=True)
    restaurant_id = models.IntegerField()
    vendor_id = models.IntegerField()
    item_id = models.IntegerField()
    expected_qty = models.FloatField()
    received_qty = models.FloatField()
    unit_price_at_time = models.DecimalField(max_digits=10, decimal_places=2)
    total_line_cost = models.DecimalField(max_digits=10, decimal_places=2)
    received_timestamp = models.DateTimeField()


class RawPriceChange(models.Model):
    event_id = models.CharField(max_length=255, primary_key=True)
    restaurant_id = models.IntegerField()
    item_id = models.IntegerField()
    old_price = models.DecimalField(max_digits=10, decimal_places=2)
    new_price = models.DecimalField(max_digits=10, decimal_places=2)
    changed_timestamp = models.DateTimeField()