from django.urls import path
from .views import SubmitLiteratureReviewSearch, CheckJobStatus, GetCompletedReview, UserSearchHistoryList

urlpatterns = [
    path("search/", SubmitLiteratureReviewSearch.as_view(), name="submit_search"),
    path("job/<int:tracking_id>/status/", CheckJobStatus.as_view(), name="check_job_status"),
    path("job/<int:tracking_id>/result/", GetCompletedReview.as_view(), name="get_completed_review"),
    path("history/", UserSearchHistoryList.as_view(), name="search_history"),
]
