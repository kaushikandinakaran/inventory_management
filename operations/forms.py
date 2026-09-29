from django import forms
from django.contrib.auth.hashers import check_password
from .models import Restaurant


class KitchenLoginForm(forms.Form):
    kitchen_login_id = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Tablet ID'})
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'PIN / Password'})
    )

    def clean(self):
        cleaned_data = super().clean()
        login_id = cleaned_data.get('kitchen_login_id')
        password = cleaned_data.get('password')

        if login_id and password:
            try:
                # Query the normalized RESTAURANT table
                restaurant = Restaurant.objects.get(kitchen_login_id=login_id)

                # Verify the hash (secure against plain-text database leaks)
                if not check_password(password, restaurant.kitchen_password_hash):
                    raise forms.ValidationError("Invalid Tablet ID or Password.")

                # Attach the restaurant object to the cleaned data for the view to use
                cleaned_data['restaurant'] = restaurant

            except Restaurant.DoesNotExist:
                raise forms.ValidationError("Invalid Tablet ID or Password.")

        return cleaned_data