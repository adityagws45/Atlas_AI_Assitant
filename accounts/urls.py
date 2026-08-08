from django.urls import path

from accounts.views_oauth import google_oauth_callback

urlpatterns = [
    path("google/callback/", google_oauth_callback, name="google_oauth_callback"),
]
