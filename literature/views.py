# literature/views.py
from uuid import UUID

from celery.result import AsyncResult
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import ReviewTask
from .serializers import (
    ReviewTaskCreateSerializer,
    ReviewTaskStatusSerializer,
    ReviewTaskResultSerializer,
    ReviewTaskDetailSerializer
)
from .tasks import generate_review_task
from .utils import export_review_to_pdf, export_review_to_docx


class ReviewTaskViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def create(self, request):
        serializer = ReviewTaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        task = ReviewTask.objects.create(
            user=request.user,
            topic=serializer.validated_data['topic'],
            prompt=serializer.validated_data['prompt'],
            status='pending'
        )

        # Launch Celery task
        celery_task = generate_review_task.delay(task.id)
        task.celery_task_id = celery_task.id
        task.save()

        return Response({
            'tracking_id': str(task.tracking_id),
            'status': task.status,
            'message': 'Review generation started. Use the tracking_id to monitor status.'
        }, status=status.HTTP_201_CREATED)

    def retrieve(self, request, pk=None):
        task = self.get_task(pk)
        serializer = ReviewTaskDetailSerializer(task)
        return Response(serializer.data)

    def list(self, request):
        tasks = ReviewTask.objects.filter(user=request.user).order_by('-created_at')
        serializer = ReviewTaskStatusSerializer(tasks, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None):
        """
        Export the finished review as PDF or DOCX.
        URL example: /api/literature/reviews/<tracking_id>/export?format=pdf
        """
        task = self.get_task(pk)

        if task.status != 'finished' or not task.result:
            return Response({'detail': 'Review not ready yet.'}, status=status.HTTP_400_BAD_REQUEST)

        file_format = request.query_params.get('format', 'pdf').lower()
        if file_format not in ('pdf', 'docx'):
            return Response({'detail': 'Invalid format, choose pdf or docx.'}, status=status.HTTP_400_BAD_REQUEST)

        if file_format == 'pdf':
            file_bytes = export_review_to_pdf(task.result, task.topic)
            content_type = 'application/pdf'
            filename = f'review_{task.tracking_id}.pdf'
        else:
            file_bytes = export_review_to_docx(task.result, task.topic)
            content_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
            filename = f'review_{task.tracking_id}.docx'

        response = HttpResponse(file_bytes, content_type=content_type)
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    # literature/views.py
    @action(detail=True, methods=['get'])
    def status(self, request, pk=None):
        task = self.get_task(pk)
        return Response({
            'tracking_id': str(task.tracking_id),
            'status': task.status,
            'current_stage': task.get_current_stage_display() if task.current_stage else None,
            'progress_percent': task.progress_percent,  # <-- only this
        })

    @action(detail=True, methods=['get'])
    def result(self, request, pk=None):
        task = self.get_task(pk)
        if task.status != 'finished':
            return Response({
                'error': 'Task not finished',
                'status': task.status
            }, status=status.HTTP_400_BAD_REQUEST)

        serializer = ReviewTaskResultSerializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        task = self.get_task(pk)
        if task.status not in ['pending', 'running']:
            return Response({'error': 'Task cannot be canceled'}, status=status.HTTP_400_BAD_REQUEST)

        if task.celery_task_id:
            AsyncResult(task.celery_task_id).revoke(terminate=True, signal=15)

        task.status = 'canceled'
        task.current_stage = None
        task.save()

        return Response({'tracking_id': str(task.tracking_id), 'status': 'canceled'})

    def get_task(self, pk):
        try:
            # Allow pk as int (id) or str (tracking_id)
            if pk.isdigit():
                task = ReviewTask.objects.get(id=int(pk), user=self.request.user)
            else:
                task = ReviewTask.objects.get(tracking_id=UUID(pk), user=self.request.user)
            return task
        except (ReviewTask.DoesNotExist, ValueError):
            from rest_framework.exceptions import NotFound
            raise NotFound('Task not found')
