# literature/serializers.py
from rest_framework import serializers
from .models import ReviewTask, Paper


class PaperSerializer(serializers.ModelSerializer):
    class Meta:
        model = Paper
        fields = ['id', 'doi', 'title', 'authors', 'year']


class ReviewTaskCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewTask
        fields = ['topic', 'prompt']


class ReviewTaskStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewTask
        fields = ['tracking_id', 'status', 'current_stage', 'created_at', 'updated_at']


class ReviewTaskDetailSerializer(serializers.ModelSerializer):
    papers = PaperSerializer(many=True, read_only=True)

    class Meta:
        model = ReviewTask
        fields = ['tracking_id', 'topic', 'prompt', 'status', 'current_stage', 'papers', 'created_at', 'updated_at']


class ReviewTaskResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewTask
        fields = ['tracking_id', 'result', 'status', 'created_at']