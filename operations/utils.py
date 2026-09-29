from django.utils import timezone
from django.db import models
from django.contrib.auth import get_user_model

# Import your models
from .models import (
    Restaurant, Category, Vendor, VendorContact, Item,
    Document, DraftOrder, SpecialRequest, Delivery, DeliveryItem
)

User = get_user_model()


def create_restaurant_with_defaults(name):
    """Creates a new restaurant and initializes default inventory categories."""
    new_restaurant = Restaurant.objects.create(name=name)
    default_categories = ['Produce', 'Meat & Poultry', 'Dairy', 'Dry Goods', 'Packaging & Disposables']
    for cat_name in default_categories:
        Category.objects.create(name=cat_name, restaurant=new_restaurant)
    return new_restaurant


def process_custom_order_pad(restaurant, vendor, selected_uids, post_data):
    """Processes the interactive AI order pad and generates a delivery ticket."""
    items_to_order = []
    new_delivery = Delivery.objects.create(restaurant=restaurant, vendor=vendor)
    is_urgent = False

    for uid in selected_uids:
        qty_str = post_data.get(f'qty_{uid}')
        item_id = post_data.get(f'item_id_{uid}')
        item_type = post_data.get(f'type_{uid}')

        if qty_str and float(qty_str) > 0:
            qty = float(qty_str)
            actual_item = Item.objects.get(id=item_id, restaurant=restaurant)
            items_to_order.append({'name': actual_item.name, 'qty': qty, 'unit': actual_item.unit_measure})

            DeliveryItem.objects.create(delivery=new_delivery, item=actual_item, expected_qty=qty)

            if item_type == 'special_request':
                req_id = post_data.get(f'req_id_{uid}')
                try:
                    req = SpecialRequest.objects.get(id=req_id)
                    if req.is_emergency:
                        is_urgent = True
                    req.delete()
                except SpecialRequest.DoesNotExist:
                    pass

    item_list_str = "\n".join([f"- {i['qty']} {i['unit']} of {i['name']}" for i in items_to_order])
    header = "**URGENT PRIORITY ORDER**\n\n" if is_urgent else ""
    order_text = (
        f"{header}Hello {vendor.name},\n\n"
        f"Please process the following purchase order for {restaurant.name}:\n\n"
        f"{item_list_str}\n\n"
        f"Thank you,\nChefStack Automated System"
    )

    DraftOrder.objects.create(
        restaurant=restaurant, vendor=vendor, ai_generated_text=order_text,
        status='Sent', sent_at=timezone.now()
    )


def get_dashboard_context_data(request, restaurant, is_admin, is_manager, is_basic_user):
    """Builds the dictionary required to render the dashboard HTML."""
    action_vendors = {}

    # 1. Filter out items that are already on an active delivery ticket
    pending_delivery_item_ids = DeliveryItem.objects.filter(
        delivery__restaurant=restaurant
    ).values_list('item_id', flat=True)

    # 2. Gather Low Stock Items
    low_stock = Item.objects.filter(
        restaurant=restaurant, current_stock__lte=models.F('min_qty')
    ).exclude(id__in=pending_delivery_item_ids).select_related('vendor')

    for item in low_stock:
        if item.vendor not in action_vendors:
            action_vendors[item.vendor] = []

        qty_needed = item.max_qty - item.current_stock
        if qty_needed > 0:
            action_vendors[item.vendor].append({
                'type': 'low_stock', 'item_id': item.id, 'name': item.name, 'qty': qty_needed,
                'unit': item.unit_measure, 'is_special': False, 'uid': f"ls_{item.id}"
            })

    # 3. Gather Kitchen Requests
    special_reqs = SpecialRequest.objects.filter(item__restaurant=restaurant).select_related('item', 'item__vendor')
    for req in special_reqs:
        vendor = req.item.vendor
        if vendor not in action_vendors:
            action_vendors[vendor] = []

        action_vendors[vendor].append({
            'type': 'special_request', 'item_id': req.item.id, 'name': req.item.name, 'qty': req.requested_qty,
            'unit': req.item.unit_measure, 'is_special': True, 'is_emergency': req.is_emergency,
            'req_id': req.id, 'uid': f"sr_{req.id}"
        })

    # 4. Gather Users based on role permissions
    if is_admin:
        team_users = User.objects.filter(restaurant=restaurant).exclude(id=request.user.id).order_by('-is_superuser',
                                                                                                     '-is_staff',
                                                                                                     'username')
    else:
        team_users = User.objects.filter(restaurant=restaurant, is_superuser=False, is_staff=False).exclude(
            id=request.user.id).order_by('username')

    return {
        'restaurant': restaurant,
        'all_restaurants': Restaurant.objects.all().order_by('name'),
        'categories': Category.objects.filter(restaurant=restaurant).order_by('name'),
        'vendors': Vendor.objects.filter(restaurant=restaurant).order_by('name'),
        'all_items': Item.objects.filter(restaurant=restaurant).order_by('name'),
        'documents': Document.objects.filter(restaurant=restaurant).order_by('-upload_date'),
        'pending_drafts': DraftOrder.objects.filter(restaurant=restaurant, status='Pending Review').order_by(
            '-created_at'),
        'approved_drafts': DraftOrder.objects.filter(restaurant=restaurant, status='Sent').order_by('-sent_at'),
        'discrepancies': [],
        'action_vendors': action_vendors,
        'team_users': team_users,
        'is_admin': is_admin,
        'is_manager': is_manager,
        'is_basic_user': is_basic_user,
    }