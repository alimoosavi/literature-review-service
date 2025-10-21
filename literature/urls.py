from rest_framework.routers import DefaultRouter
from .views import SearchQueryViewSet

router = DefaultRouter()
router.register(r"queries", SearchQueryViewSet, basename="searchquery")

urlpatterns = router.urls
