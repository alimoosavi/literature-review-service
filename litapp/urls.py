from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import LiteratureReviewJobViewSet, SearchHistoryViewSet, ReviewViewSet

app_name = 'litapp'

router = DefaultRouter()
router.register(r'jobs', LiteratureReviewJobViewSet, basename='jobs')
router.register(r'history', SearchHistoryViewSet, basename='history')
router.register(r'reviews', ReviewViewSet, basename='reviews')

urlpatterns = [
    path('', include(router.urls)),
]
