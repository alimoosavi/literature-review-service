from django.shortcuts import get_object_or_404
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import LiteratureReviewJob, SearchHistory
from .serializers import LiteratureReviewJobSerializer, LiteratureReviewSerializer, SearchHistorySerializer
from .tasks import generate_literature_review_job  # Your Celery task


class SubmitLiteratureReviewSearch(APIView):
    """
    User submits a new search. Creates a LiteratureReviewJob with 'pending' status
    and triggers Celery task. Returns the job ID for tracking.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        topic = request.data.get("topic")
        prompt = request.data.get("prompt", topic)
        if not topic:
            return Response({"error": "Topic is required."}, status=status.HTTP_400_BAD_REQUEST)

        job = LiteratureReviewJob.objects.create(user=request.user, topic=topic, prompt=prompt)
        # Trigger Celery task
        generate_literature_review_job.delay(job.id)

        # Log to search history
        SearchHistory.objects.create(user=request.user, topic=topic, prompt=prompt, job=job)

        return Response({"tracking_id": job.id}, status=status.HTTP_201_CREATED)


class CheckJobStatus(APIView):
    """
    Returns the status of a LiteratureReviewJob given a tracking ID.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, tracking_id):
        job = get_object_or_404(LiteratureReviewJob, id=tracking_id, user=request.user)
        serializer = LiteratureReviewJobSerializer(job)
        return Response(serializer.data)


class GetCompletedReview(APIView):
    """
    Returns the literature review content if job is completed.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, tracking_id):
        job = get_object_or_404(LiteratureReviewJob, id=tracking_id, user=request.user)
        if job.status != "completed" or not job.review:
            return Response({"error": "Review not available yet."}, status=status.HTTP_404_NOT_FOUND)

        serializer = LiteratureReviewSerializer(job.review)
        return Response(serializer.data)


class UserSearchHistoryList(generics.ListAPIView):
    """
    Returns paginated search history of the current user.
    """
    serializer_class = SearchHistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return SearchHistory.objects.filter(user=self.request.user).order_by("-timestamp")
