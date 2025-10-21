from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .models import SearchQuery, TaskStatus
from .serializers import (
    SearchQuerySerializer,
    SearchQueryCreateSerializer,
    TaskStatusSerializer,
    LiteratureReviewSerializer,
)
from .tasks import process_search_query  # async task handler


class IsOwner(permissions.BasePermission):
    """Ensure users only access their own queries."""
    def has_object_permission(self, request, view, obj):
        return obj.user == request.user


class SearchQueryViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated, IsOwner]

    def get_queryset(self):
        return SearchQuery.objects.filter(user=self.request.user).order_by("-timestamp")

    def get_serializer_class(self):
        if self.action == "create":
            return SearchQueryCreateSerializer
        return SearchQuerySerializer

    def perform_create(self, serializer):
        query = serializer.save(user=self.request.user)
        for t in ["search", "download", "extract", "generate"]:
            TaskStatus.objects.create(search_query=query, task_type=t, status="pending")

        # Trigger async job (Celery or background thread)
        try:
            process_search_query.delay(query.id)
        except Exception as e:
            print("Async task failed:", e)

    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        query = get_object_or_404(SearchQuery, pk=pk, user=request.user)
        serializer = TaskStatusSerializer(query.tasks.all(), many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def review(self, request, pk=None):
        query = get_object_or_404(SearchQuery, pk=pk, user=request.user)
        if not hasattr(query, "review"):
            return Response({"detail": "Review not generated yet."}, status=404)
        serializer = LiteratureReviewSerializer(query.review)
        return Response(serializer.data)
