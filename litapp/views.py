import logging
import uuid

from celery import current_app
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView

from .models import LiteratureReviewJob, SearchHistory, LiteratureReview
from .serializers import (
    LiteratureReviewSerializer,
    SearchHistorySerializer,
    SubmitSearchSerializer
)
from .tasks import generate_literature_review_job

logger = logging.getLogger(__name__)


class LiteratureReviewRateThrottle(UserRateThrottle):
    """Rate limit for literature review generation requests"""
    scope = "literature_review"


class SubmitLiteratureReviewSearch(APIView):
    """
    Submit a new literature review generation request.
    Only 'topic' is provided by the user — prompt is static on the server.
    """
    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [LiteratureReviewRateThrottle]

    def post(self, request):
        serializer = SubmitSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        topic = serializer.validated_data["topic"]

        # Check user's pending jobs
        pending_jobs_count = LiteratureReviewJob.objects.filter(
            user=request.user,
            status__in=["pending", "processing"]
        ).count()

        if pending_jobs_count >= 3:
            return Response(
                {
                    "error": "You have too many pending requests. Please wait for them to complete.",
                    "pending_count": pending_jobs_count,
                },
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        try:
            with transaction.atomic():
                # Create job
                job = LiteratureReviewJob.objects.create(
                    user=request.user,
                    topic=topic,
                    status="pending",
                )

                # Log search history
                SearchHistory.objects.create(
                    user=request.user,
                    topic=topic,
                    job=job,
                )

                # Generate a UUID for Celery task
                custom_task_id = str(uuid.uuid4())
                job.celery_task_id = custom_task_id
                job.save(update_fields=["celery_task_id"])

            # Trigger async background processing with custom task ID
            generate_literature_review_job.apply_async(
                args=[job.id],
                countdown=1,
                task_id=custom_task_id
            )

            return Response({
                "tracking_id": job.id,
                "celery_task_id": job.celery_task_id,
                "topic": job.topic,
                "status": job.status,
                "message": (
                    "Literature review generation started. "
                    "Use tracking_id to check progress."
                ),
                "created_at": job.created_at,
            }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception(f"Failed to create job for user {request.user.id}: {e}")
            return Response(
                {"error": f"Failed to create job: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CheckJobStatus(APIView):
    """
    Check the status of a literature review generation job.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, tracking_id):
        try:
            job = get_object_or_404(
                LiteratureReviewJob,
                id=tracking_id,
                user=request.user
            )

            response_data = {
                "id": job.id,
                "topic": job.topic,
                "status": job.status,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
            }

            # Include progress information if available
            if job.result_text:
                response_data["progress_message"] = job.result_text

            # Include error if failed
            if job.status == "failed" and job.error_message:
                response_data["error_message"] = job.error_message

            # Include review ID if completed
            if job.status == "completed" and job.review:
                response_data["review_id"] = job.review.id
                response_data["review_ready"] = True

            return Response(response_data)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GetCompletedReview(APIView):
    """
    Retrieve the completed literature review content.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, tracking_id):
        try:
            job = get_object_or_404(
                LiteratureReviewJob.objects.select_related('review'),
                id=tracking_id,
                user=request.user
            )

            if job.status == "pending" or job.status == "processing":
                return Response(
                    {
                        "error": "Review is still being generated.",
                        "status": job.status,
                        "progress_message": job.result_text
                    },
                    status=status.HTTP_202_ACCEPTED
                )

            if job.status == "failed":
                return Response(
                    {
                        "error": "Review generation failed.",
                        "error_message": job.error_message
                    },
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

            if not job.review:
                return Response(
                    {"error": "Review not available. Please try again later."},
                    status=status.HTTP_404_NOT_FOUND
                )

            serializer = LiteratureReviewSerializer(job.review)
            return Response(serializer.data)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UserSearchHistoryList(generics.ListAPIView):
    """
    Returns paginated search history for the authenticated user.
    Supports filtering and ordering.
    """
    serializer_class = SearchHistorySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = SearchHistory.objects.filter(
            user=self.request.user
        ).select_related('job', 'review').order_by("-timestamp")

        # Optional filtering by topic
        topic_filter = self.request.query_params.get('topic', None)
        if topic_filter:
            queryset = queryset.filter(topic__icontains=topic_filter)

        # Optional filtering by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(job__status=status_filter)

        return queryset


class CancelJob(APIView):
    """
    Allows users to cancel a pending or processing literature review job.
    Terminates the Celery task and marks the job as failed.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, job_id):
        job = LiteratureReviewJob.objects.filter(
            id=job_id, user=request.user
        ).first()

        if not job:
            return Response(
                {"error": "Job not found or not owned by user."},
                status=status.HTTP_404_NOT_FOUND
            )

        if job.status not in ["pending", "processing"]:
            return Response(
                {"error": f"Cannot cancel job with status '{job.status}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if job.celery_task_id:
            try:
                current_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGKILL")
                logger.info(f"Revoked Celery task {job.celery_task_id} for job {job.id}")
            except Exception as e:
                logger.warning(f"Failed to revoke task {job.celery_task_id}: {e}")

        job.status = "failed"
        job.error_message = "Cancelled by user"
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at"])

        return Response(
            {
                "message": "Job cancelled successfully.",
                "job_id": job.id,
                "status": job.status,
            },
            status=status.HTTP_200_OK
        )


class DeleteReview(APIView):
    """
    Delete a completed literature review.
    """
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, review_id):
        try:
            review = get_object_or_404(
                LiteratureReview,
                id=review_id,
                user=request.user
            )

            review_topic = review.topic
            review.delete()

            return Response({
                "message": f"Review '{review_topic}' deleted successfully"
            }, status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
