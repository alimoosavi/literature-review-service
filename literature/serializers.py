from rest_framework import serializers
from .models import SearchQuery, Paper, LiteratureReview, Citation, TaskStatus


class PaperSerializer(serializers.ModelSerializer):
    class Meta:
        model = Paper
        fields = [
            "id",
            "title",
            "authors",
            "year",
            "doi",
            "url",
            "open_access_pdf",
            "status",
            "word_count",
            "timestamp",
        ]


class CitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Citation
        fields = ["id", "inline_citation", "formatted_reference", "timestamp"]


class LiteratureReviewSerializer(serializers.ModelSerializer):
    citations = CitationSerializer(many=True, read_only=True)

    class Meta:
        model = LiteratureReview
        fields = [
            "id",
            "generated_text",
            "word_count",
            "model_name",
            "bibliography",
            "timestamp",
            "citations",
        ]


class TaskStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskStatus
        fields = ["id", "task_type", "status", "progress", "message", "timestamp"]


class SearchQuerySerializer(serializers.ModelSerializer):
    papers = PaperSerializer(many=True, read_only=True)
    review = LiteratureReviewSerializer(read_only=True)
    tasks = TaskStatusSerializer(many=True, read_only=True)
    user = serializers.StringRelatedField(read_only=True)

    class Meta:
        model = SearchQuery
        fields = [
            "id",
            "topic",
            "user_prompt",
            "user",
            "timestamp",
            "papers",
            "tasks",
            "review",
        ]


class SearchQueryCreateSerializer(serializers.ModelSerializer):
    """
    Used for creating new SearchQuery instances.
    The user is injected from the request context in the ViewSet.
    """
    class Meta:
        model = SearchQuery
        fields = ["topic", "user_prompt"]
