from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import LiteratureReviewJob
from .models import SearchHistory, LiteratureReview
from .serializers import (
    LiteratureReviewJobSerializer,
    LiteratureReviewSerializer,
    SubmitSearchSerializer,
)
from .serializers import SearchHistorySerializer
from .services import create_literature_review_job, cancel_literature_review_job


class LiteratureReviewJobViewSet(viewsets.GenericViewSet):
    """
    A unified ViewSet for managing literature review jobs:
    - POST /jobs/ → create job
    - GET /jobs/{id}/ → check job status
    - GET /jobs/{id}/result/ → fetch completed review
    - POST /jobs/{id}/cancel/ → cancel job
    """
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = LiteratureReviewJobSerializer

    def get_queryset(self):
        return LiteratureReviewJob.objects.filter(user=self.request.user)

    # POST /jobs/
    def create(self, request):
        serializer = SubmitSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        topic = serializer.validated_data["topic"]

        try:
            job = create_literature_review_job(request.user, topic)
            return Response({
                "id": job.id,
                "celery_task_id": job.celery_task_id,
                "status": job.status,
                "message": "Literature review generation started."
            }, status=status.HTTP_201_CREATED)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # GET /jobs/{id}/
    def retrieve(self, request, pk=None):
        job = get_object_or_404(self.get_queryset(), pk=pk)
        serializer = self.get_serializer(job)
        return Response(serializer.data)

    # GET /jobs/{id}/result/
    @action(detail=True, methods=['get'])
    def result(self, request, pk=None):
        job = get_object_or_404(self.get_queryset().select_related('review'), pk=pk)
        if not job.review:
            return Response({"error": "Review not ready."}, status=status.HTTP_202_ACCEPTED)
        review_serializer = LiteratureReviewSerializer(job.review)
        return Response(review_serializer.data)

    # POST /jobs/{id}/cancel/
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        job = get_object_or_404(self.get_queryset(), pk=pk)
        try:
            cancel_literature_review_job(job)
            return Response({"message": "Job cancelled successfully."})
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


class SearchHistoryViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only ViewSet for user's search history.
    Supports filtering by topic or job status.
    """
    serializer_class = SearchHistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = SearchHistory.objects.filter(
            user=self.request.user
        ).select_related("job", "review").order_by("-timestamp")

        topic = self.request.query_params.get("topic")
        if topic:
            queryset = queryset.filter(topic__icontains=topic)

        status_param = self.request.query_params.get("status")
        if status_param:
            queryset = queryset.filter(job__status=status_param)

        return queryset


class ReviewViewSet(viewsets.GenericViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=True, methods=['delete'])
    def delete(self, request, pk=None):
        review = get_object_or_404(LiteratureReview, id=pk, user=request.user)
        review.delete()
        return Response({"message": "Review deleted successfully."}, status=status.HTTP_204_NO_CONTENT)
