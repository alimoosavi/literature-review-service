# literature/urls.py
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import ReviewTaskViewSet

# Create a router and register the ViewSet
router = DefaultRouter(trailing_slash=False)
router.register(r'reviews', ReviewTaskViewSet, basename='reviewtask')

# The API URLs are determined by the router
urlpatterns = [
    path('', include(router.urls)),
]