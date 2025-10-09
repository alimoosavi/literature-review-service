from rest_framework import serializers
from .models import LiteratureReviewJob, LiteratureReview, SearchHistory


class LiteratureReviewJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiteratureReviewJob
        fields = ["id", "topic", "prompt", "status", "created_at", "updated_at"]


class LiteratureReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = LiteratureReview
        fields = ["id", "topic", "prompt", "content", "citations", "created_at"]


class SearchHistorySerializer(serializers.ModelSerializer):
    class Meta:
        model = SearchHistory
        fields = ["id", "topic", "prompt", "timestamp", "job", "review"]
