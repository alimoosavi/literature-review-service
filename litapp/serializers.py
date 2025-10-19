from rest_framework import serializers
from .models import LiteratureReviewJob, LiteratureReview, SearchHistory, Paper


class PaperSerializer(serializers.ModelSerializer):
    """Serializer for Paper model."""
    has_full_text = serializers.ReadOnlyField()

    class Meta:
        model = Paper
        fields = [
            "id",
            "openalex_id",
            "title",
            "authors",
            "year",
            "doi",
            "url",
            "pdf_url",
            "has_full_text",
            "text_length",
            "created_at"
        ]
        read_only_fields = fields


class LiteratureReviewJobSerializer(serializers.ModelSerializer):
    """Serializer for LiteratureReviewJob with computed fields."""
    is_active = serializers.ReadOnlyField()
    duration = serializers.ReadOnlyField()
    user_username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = LiteratureReviewJob
        fields = [
            "id",
            "user_username",
            "topic",
            "status",
            "result_text",
            "error_message",
            "papers_found",
            "papers_downloaded",
            "papers_extracted",
            "is_active",
            "duration",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        read_only_fields = fields


class LiteratureReviewSerializer(serializers.ModelSerializer):
    """Serializer for LiteratureReview with related papers."""
    papers = PaperSerializer(many=True, read_only=True)
    user_username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = LiteratureReview
        fields = [
            "id",
            "user_username",
            "topic",
            "content",
            "citations",
            "word_count",
            "papers",
            "created_at",
            "updated_at"
        ]
        read_only_fields = fields


class LiteratureReviewListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for listing reviews without full content."""
    user_username = serializers.CharField(source='user.username', read_only=True)
    content_preview = serializers.SerializerMethodField()
    paper_count = serializers.SerializerMethodField()

    class Meta:
        model = LiteratureReview
        fields = [
            "id",
            "user_username",
            "topic",
            "content_preview",
            "word_count",
            "paper_count",
            "created_at"
        ]
        read_only_fields = fields

    def get_content_preview(self, obj):
        """Return first 200 characters of content."""
        return obj.content[:200] + "..." if len(obj.content) > 200 else obj.content

    def get_paper_count(self, obj):
        """Return count of papers used in review."""
        return obj.papers.count()


class SearchHistorySerializer(serializers.ModelSerializer):
    """Serializer for SearchHistory with nested job/review info."""
    job_status = serializers.CharField(source='job.status', read_only=True)
    review_id = serializers.IntegerField(source='review.id', read_only=True)
    user_username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = SearchHistory
        fields = [
            "id",
            "user_username",
            "topic",
            "timestamp",
            "job",
            "job_status",
            "review",
            "review_id"
        ]
        read_only_fields = fields


class SubmitSearchSerializer(serializers.Serializer):
    """Serializer for validating search submission requests."""
    topic = serializers.CharField(
        min_length=3,
        max_length=255,
        required=True,
        help_text="Research topic to search for"
    )

    def validate_topic(self, value):
        topic = value.strip()
        if not topic:
            raise serializers.ValidationError("Topic cannot be empty or whitespace only.")
        return topic
