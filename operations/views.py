from django.shortcuts import render

# Create your views here.
from django.utils import timezone
import os
from django.shortcuts import render, redirect
from django.contrib import messages
from .forms import KitchenLoginForm
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from .models import Item, UsageLog, SpecialRequest, Restaurant, Category, Vendor, VendorContact, DraftOrder, Document, Delivery, DeliveryItem
from django.db import models
from django.contrib.auth import get_user_model  # <-- Added for user management
from .utils import create_restaurant_with_defaults, process_custom_order_pad, get_dashboard_context_data

User = get_user_model()

from django.contrib.auth import authenticate
from django.shortcuts import render, redirect
from django.contrib import messages


def kitchen_login_view(request):
    # If the tablet is already logged in, send them straight to the dashboard
    if 'restaurant_id' in request.session:
        return redirect('kitchen_dashboard')

    if request.method == 'POST':
        # 1. Grab raw inputs from the HTML form
        # (Checking for 'username'/'password' but falling back to 'tablet_id'/'pin' just in case)
        username = request.POST.get('username') or request.POST.get('tablet_id')
        password = request.POST.get('password') or request.POST.get('pin')

        # 2. Use Django's built-in authenticator to check the users we just created!
        user = authenticate(request, username=username, password=password)

        if user is not None:
            # 3. Success! Lock the tenant ID into the session for this tablet
            if hasattr(user, 'restaurant') and user.restaurant:
                request.session['restaurant_id'] = user.restaurant.id
                request.session['restaurant_name'] = user.restaurant.name
                return redirect('kitchen_dashboard')
            else:
                messages.error(request, "Configuration Error: This login is not assigned to a venue.")
        else:
            messages.error(request, "Authentication failed. Please check credentials.")

    # 4. Render the page on a GET request
    # (We safely pass the old form just in case your HTML template relies on it to render without crashing)
    try:
        from .forms import KitchenLoginForm
        form = KitchenLoginForm()
    except ImportError:
        form = None

    return render(request, 'operations/kitchen/login.html', {'form': form})

def kitchen_logout_view(request):
    # Clear the session when the shift ends
    request.session.flush()
    return redirect('kitchen_login')


def kitchen_dashboard_view(request):
    # Protect the route: Ensure the session has a valid restaurant_id
    if 'restaurant_id' not in request.session:
        return redirect('kitchen_login')

    restaurant_id = request.session['restaurant_id']

    # Fetch inventory items strictly isolated to this specific restaurant
    items = Item.objects.filter(restaurant_id=restaurant_id).order_by('name')

    # NEW: Fetch active deliveries isolated to this specific restaurant
    active_deliveries = Delivery.objects.filter(restaurant_id=restaurant_id).order_by('-created_at')

    # Handle incoming form submissions from the tablet
    if request.method == 'POST':
        action = request.POST.get('action')
        item_id = request.POST.get('item_id')

        if action == 'log_usage':
            qty = request.POST.get('qty_deducted')
            if item_id and qty:
                UsageLog.objects.create(item_id=item_id, qty_deducted=float(qty))

                # Automatically deduct from current stock
                item = Item.objects.get(id=item_id)
                item.current_stock -= float(qty)
                item.save()

                messages.success(request, f"Successfully logged usage of {qty} {item.unit_measure} for {item.name}.")

        elif action == 'special_request':
            qty = request.POST.get('requested_qty')
            # Checkbox returns 'on' if checked, otherwise None
            is_emergency = request.POST.get('is_emergency') == 'on'

            if item_id and qty:
                SpecialRequest.objects.create(
                    item_id=item_id,
                    requested_qty=float(qty),
                    is_emergency=is_emergency
                )
                if is_emergency:
                    messages.error(request, "EMERGENCY REQUEST SENT TO MANAGER.")
                else:
                    messages.success(request, "Standard restock request sent to manager.")

        elif action == 'special_request_bulk':
            print("--- BULK REQUEST SUBMITTED ---")  # Debug print
            print("POST DATA:", request.POST)  # Debug print

            is_emergency = request.POST.get('is_emergency') == 'on'
            items_requested = False

            for key, value in request.POST.items():
                if key.startswith('req_qty_'):
                    item_id = key.split('_')[2]
                    try:
                        qty = float(value)
                        print(f"Processing Item ID: {item_id}, Qty: {qty}")  # Debug print
                        if qty > 0:
                            SpecialRequest.objects.create(
                                item_id=item_id,
                                requested_qty=qty,
                                is_emergency=is_emergency
                            )
                            items_requested = True
                    except Exception as e:
                        print(f"ERROR saving item {item_id}: {e}")  # Catch any database errors

            if items_requested:
                if is_emergency:
                    messages.error(request, "Priority Request sent! Manager notified for immediate approval.")
                else:
                    messages.success(request, "Items added to the queue for the next scheduled order.")
            else:
                messages.warning(request, "Your request cart was empty.")
            # ---> ADD THIS NEW BLOCK RIGHT HERE <---
        elif action == 'receive_delivery':
            delivery_id = request.POST.get('delivery_id')
            if delivery_id:
                try:
                    delivery = Delivery.objects.get(id=delivery_id, restaurant_id=restaurant_id)

                    # Loop through the items expected in this delivery
                    for d_item in delivery.delivery_items.all():
                        # The HTML uses name="item_{{ item.item.id }}" for the input field
                        input_name = f"item_{d_item.item.id}"
                        received_qty_str = request.POST.get(input_name)

                        if received_qty_str is not None:
                            try:
                                received_qty = float(received_qty_str)
                                # Update the actual inventory stock
                                d_item.item.current_stock += received_qty
                                d_item.item.save()
                            except ValueError:
                                pass  # Ignore invalid numbers

                    # Once processed, delete the delivery ticket so it clears from the dock
                    vendor_name = delivery.vendor.name
                    delivery.delete()
                    messages.success(request, f"Delivery from {vendor_name} received and stock updated.")

                except Delivery.DoesNotExist:
                    messages.error(request, "Delivery ticket not found or already processed.")
        # Redirect to prevent form resubmission on page refresh
        return redirect('kitchen_dashboard')

    context = {
        'restaurant_name': request.session.get('restaurant_name'),
        'items': items,
        'pending_deliveries': active_deliveries  # NEW: Pass deliveries to the template
    }
    return render(request, 'operations/kitchen/dashboard.html', context)

# ==========================================
#        MANAGER PORTAL VIEWS
# ==========================================

def manager_login_view(request):
    if request.user.is_authenticated and request.user.role in ['ADMIN', 'MANAGER']:
        return redirect('manager_dashboard')

    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        user = authenticate(request, username=u, password=p)

        if user is not None:
            if user.role in ['ADMIN', 'MANAGER']:
                login(request, user)
                messages.success(request, f"Welcome back, {user.username}.")
                return redirect('manager_dashboard')
            else:
                messages.error(request, "Access Denied. Manager privileges required.")
        else:
            messages.error(request, "Invalid username or password.")

    return render(request, 'operations/manager/login.html')


def manager_logout_view(request):
    logout(request)
    messages.info(request, "You have been securely logged out.")
    return redirect('manager_login')






# Make sure these are imported at the top of your file:
# from .models import Restaurant, Category, Vendor, VendorContact, Item, Document, DraftOrder, SpecialRequest, Delivery, DeliveryItem

@login_required(login_url='manager_login')
def manager_dashboard_view(request):
    restaurant = request.user.restaurant

    # Define Roles
    is_admin = request.user.is_superuser
    is_manager = request.user.is_staff or is_admin
    is_basic_user = not request.user.is_staff and not is_admin

    if request.method == 'POST':
        # STRICT SECURITY GATE
        if is_basic_user:
            messages.error(request, "Read-Only Access: You do not have permission to modify data.")
            return redirect('manager_dashboard')

        action = request.POST.get('action')

        # --- RESTAURANT MANAGEMENT ---
        if action in ['switch_restaurant', 'create_restaurant'] and not is_admin:
            messages.error(request, "Security Alert: Only Admins can manage multi-venue settings.")
            return redirect('manager_dashboard')

        if action == 'switch_restaurant':
            try:
                new_restaurant = Restaurant.objects.get(id=request.POST.get('restaurant_id'))
                request.user.restaurant = new_restaurant
                request.user.save()
                messages.success(request, f"Switched dashboard to {new_restaurant.name}.")
            except Restaurant.DoesNotExist:
                messages.error(request, "Location not found.")

        elif action == 'create_restaurant':
            name = request.POST.get('restaurant_name')
            if name:
                new_restaurant = create_restaurant_with_defaults(name)
                request.user.restaurant = new_restaurant
                request.user.save()
                messages.success(request, f"New location '{name}' created! Default categories initialized.")

        # --- TEAM MANAGEMENT ---
        elif action == 'create_team_member':
            username = request.POST.get('username')
            password = request.POST.get('password')
            role = request.POST.get('role')

            if not is_admin and role in ['admin', 'manager']:
                messages.error(request, "Only Admins have permission to create Management roles.")
            elif User.objects.filter(username=username).exists():
                # SMART SUGGESTION: Generate a location-based unique name
                safe_loc = restaurant.name.replace(' ', '').lower()[:6]
                messages.error(request,
                               f"Login ID '{username}' is already taken globally. Try a location-specific name like '{username}_{safe_loc}'.")
            elif username and password:
                new_user = User.objects.create_user(username=username, password=password)
                new_user.restaurant = restaurant
                new_user.is_superuser = (role == 'admin')
                new_user.is_staff = (role in ['admin', 'manager'])
                new_user.save()
                messages.success(request, f"Successfully created {role.title()} account '{username}'.")

        elif action == 'edit_team_member':
            user_id = request.POST.get('user_id')
            username = request.POST.get('username')
            password = request.POST.get('password')
            role = request.POST.get('role')

            user = User.objects.filter(id=user_id).first()
            if user:
                if not is_admin and (user.is_superuser or role in ['admin', 'manager']):
                    messages.error(request, "Permission denied: Only Admins can manage Management roles.")
                elif User.objects.filter(username=username).exclude(id=user_id).exists():
                    # SMART SUGGESTION
                    safe_loc = restaurant.name.replace(' ', '').lower()[:6]
                    messages.error(request, f"Login ID '{username}' is already taken. Try '{username}_{safe_loc}'.")
                else:
                    user.username = username
                    if password:
                        user.set_password(password)

                    if role == 'admin':
                        user.is_superuser, user.is_staff = True, True
                    elif role == 'manager':
                        user.is_superuser, user.is_staff = False, True
                    else:
                        user.is_superuser, user.is_staff = False, False

                    user.save()
                    messages.success(request, f"Account '{username}' successfully updated.")

        elif action == 'delete_team_member':
            user = User.objects.filter(id=request.POST.get('user_id')).first()
            if user:
                if user.is_superuser and not is_admin:
                    messages.error(request, "Permission denied: Cannot remove Admin accounts.")
                else:
                    user.delete()
                    messages.success(request, "Account access revoked.")

        # --- ORDER PAD ENGINE ---
        elif action == 'approve_custom_order':
            vendor = Vendor.objects.get(id=request.POST.get('vendor_id'), restaurant=restaurant)
            selected_uids = request.POST.getlist('selected_items')

            if not selected_uids:
                messages.warning(request, "No items selected for this order.")
            else:
                process_custom_order_pad(restaurant, vendor, selected_uids, request.POST)
                messages.success(request, f"Order successfully sent to {vendor.name}.")

        # --- LEGACY DRAFTS ---
        elif action == 'approve_draft':
            try:
                draft = DraftOrder.objects.get(id=request.POST.get('draft_id'), restaurant=restaurant)
                draft.status, draft.sent_at = 'Sent', timezone.now()
                draft.save()
                Delivery.objects.create(restaurant=restaurant, vendor=draft.vendor)
                messages.success(request, f"Draft approved and sent to {draft.vendor.name}.")
            except DraftOrder.DoesNotExist:
                messages.error(request, "Draft not found.")

        elif action == 'reject_draft':
            DraftOrder.objects.filter(id=request.POST.get('draft_id'), restaurant=restaurant).delete()
            messages.success(request, "Draft discarded.")

        # --- INVENTORY & VENDORS ---
        elif action == 'add_vendor':
            name = request.POST.get('vendor_name')
            if name:
                vendor = Vendor.objects.create(name=name, restaurant=restaurant)
                ctype, cval = request.POST.get('contact_type'), request.POST.get('contact_value')
                if ctype and cval:
                    VendorContact.objects.create(vendor=vendor, contact_type=ctype, contact_value=cval)
                messages.success(request, f"Vendor '{name}' added.")

        elif action == 'delete_vendor':
            Vendor.objects.filter(id=request.POST.get('vendor_id'), restaurant=restaurant).delete()
            messages.success(request, "Vendor deleted.")

        elif action == 'add_item':
            name, v_id = request.POST.get('item_name'), request.POST.get('vendor_id')
            if name and v_id:
                Item.objects.create(
                    restaurant=restaurant, name=name, vendor_id=v_id,
                    category_id=request.POST.get('category_id') or None,
                    unit_measure=request.POST.get('unit_measure', 'units'),
                    current_stock=float(request.POST.get('current_stock', 0)),
                    min_qty=float(request.POST.get('min_qty', 0)),
                    max_qty=float(request.POST.get('max_qty', 0))
                )
                messages.success(request, f"Item '{name}' added.")

        elif action == 'delete_item':
            Item.objects.filter(id=request.POST.get('item_id'), restaurant=restaurant).delete()
            messages.success(request, "Item deleted.")

        elif action == 'add_category':
            name = request.POST.get('category_name')
            if name:
                Category.objects.create(name=name, restaurant=restaurant)
                messages.success(request, f"Category '{name}' created.")

        elif action == 'delete_category':
            Category.objects.filter(id=request.POST.get('category_id'), restaurant=restaurant).delete()
            messages.success(request, "Category deleted.")

        elif action == 'delete_document':
            Document.objects.filter(id=request.POST.get('document_id'), restaurant=restaurant).delete()
            messages.success(request, "Document removed.")

        return redirect('manager_dashboard')

    # Pass the heavy lifting to utils.py
    context = get_dashboard_context_data(request, restaurant, is_admin, is_manager, is_basic_user)
    return render(request, 'operations/manager/dashboard.html', context)