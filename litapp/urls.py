from django.urls import path

from .views import (
    SubmitLiteratureReviewSearch,
    CheckJobStatus,
    GetCompletedReview,
    UserSearchHistoryList,
    CancelJob,
    DeleteReview,
)

app_name = 'litapp'

urlpatterns = [
    # Main workflow endpoints
    path(
        "search/",
        SubmitLiteratureReviewSearch.as_view(),
        name="submit_search"
    ),
    # Check job status by database ID
    path(
        "job/<int:tracking_id>/status/",
        CheckJobStatus.as_view(),
        name="check_job_status"
    ),
    # Retrieve completed review by database ID
    path(
        "job/<int:tracking_id>/result/",
        GetCompletedReview.as_view(),
        name="get_completed_review"
    ),
    # Cancel job by database ID
    path(
        "job/<int:tracking_id>/cancel/",
        CancelJob.as_view(),
        name="cancel_job"
    ),

    # History and management
    path(
        "history/",
        UserSearchHistoryList.as_view(),
        name="search_history"
    ),
    path(
        "review/<int:review_id>/delete/",
        DeleteReview.as_view(),
        name="delete_review"
    ),
]
