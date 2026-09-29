from django.urls import path
from . import views

urlpatterns = [
    # Kitchen Kiosk Routes
    path('kitchen/login/', views.kitchen_login_view, name='kitchen_login'),
    path('kitchen/logout/', views.kitchen_logout_view, name='kitchen_logout'),
    path('kitchen/dashboard/', views.kitchen_dashboard_view, name='kitchen_dashboard'),

    # Manager Portal Routes
    path('manager/login/', views.manager_login_view, name='manager_login'),
    path('manager/logout/', views.manager_logout_view, name='manager_logout'),
    path('manager/dashboard/', views.manager_dashboard_view, name='manager_dashboard'),
]