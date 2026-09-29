from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, Restaurant, Category, Vendor, Item, DraftOrder, Document

# Add our custom 'restaurant' and 'role' fields to the default Django User screen
@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (
        ('OpsHub Settings', {'fields': ('restaurant', 'role')}),
    )

# Register the rest of our operational tables
admin.site.register(Restaurant)
admin.site.register(Category)
admin.site.register(Vendor)
admin.site.register(Item)
admin.site.register(DraftOrder)
admin.site.register(Document)