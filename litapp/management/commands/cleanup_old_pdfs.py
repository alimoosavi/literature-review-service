"""
litapp/management/commands/retry_failed_jobs.py
Retry failed literature review jobs
"""

from django.core.management.base import BaseCommand
from litapp.models import LiteratureReviewJob
from litapp.tasks import generate_literature_review_job


class Command(BaseCommand):
    help = 'Retry failed literature review jobs'

    def add_arguments(self, parser):
        parser.add_argument(
            '--job-id',
            type=int,
            help='Specific job ID to retry'
        )
        parser.add_argument(
            '--all',
            action='store_true',
            help='Retry all failed jobs from the last 24 hours'
        )

    def handle(self, *args, **options):
        from django.utils import timezone
        from datetime import timedelta

        if options['job_id']:
            # Retry specific job
            try:
                job = LiteratureReviewJob.objects.get(id=options['job_id'])

                if job.status != 'failed':
                    self.stdout.write(
                        self.style.WARNING(f'Job {job.id} is not in failed state (current: {job.status})')
                    )
                    return

                job.status = 'pending'
                job.error_message = None
                job.save()

                generate_literature_review_job.apply_async(args=[job.id], countdown=1)

                self.stdout.write(
                    self.style.SUCCESS(f'Successfully queued retry for job {job.id}')
                )

            except LiteratureReviewJob.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f'Job {options["job_id"]} not found')
                )

        elif options['all']:
            # Retry all failed jobs from last 24 hours
            one_day_ago = timezone.now() - timedelta(days=1)
            failed_jobs = LiteratureReviewJob.objects.filter(
                status='failed',
                created_at__gte=one_day_ago
            )

            count = failed_jobs.count()

            if count == 0:
                self.stdout.write('No failed jobs found in the last 24 hours')
                return

            self.stdout.write(f'Found {count} failed jobs to retry')

            for job in failed_jobs:
                job.status = 'pending'
                job.error_message = None
                job.save()

                generate_literature_review_job.apply_async(args=[job.id], countdown=1)

                self.stdout.write(f'  Queued job {job.id}: {job.topic[:50]}')

            self.stdout.write(
                self.style.SUCCESS(f'Successfully queued {count} jobs for retry')
            )
        else:
            self.stdout.write(
                self.style.ERROR('Please specify either --job-id or --all')
            )


# ---


"""
litapp/management/commands/generate_stats.py
Generate statistics about the literature review system
"""

from django.core.management.base import BaseCommand
from django.db.models import Count, Avg, Q
from django.utils import timezone
from datetime import timedelta
from litapp.models import LiteratureReviewJob, LiteratureReview, Paper


class Command(BaseCommand):
    help = 'Generate statistics about the literature review system'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('=== Literature Review System Statistics ===\n'))

        # Job statistics
        total_jobs = LiteratureReviewJob.objects.count()
        completed_jobs = LiteratureReviewJob.objects.filter(status='completed').count()
        failed_jobs = LiteratureReviewJob.objects.filter(status='failed').count()
        pending_jobs = LiteratureReviewJob.objects.filter(status='pending').count()
        processing_jobs = LiteratureReviewJob.objects.filter(status='processing').count()

        self.stdout.write('Job Statistics:')
        self.stdout.write(f'  Total jobs: {total_jobs}')
        self.stdout.write(
            f'  Completed: {completed_jobs} ({completed_jobs / total_jobs * 100:.1f}%)' if total_jobs else '  Completed: 0')
        self.stdout.write(
            f'  Failed: {failed_jobs} ({failed_jobs / total_jobs * 100:.1f}%)' if total_jobs else '  Failed: 0')
        self.stdout.write(f'  Pending: {pending_jobs}')
        self.stdout.write(f'  Processing: {processing_jobs}\n')

        # Average processing metrics
        completed_with_data = LiteratureReviewJob.objects.filter(
            status='completed',
            papers_found__gt=0
        )

        if completed_with_data.exists():
            avg_papers_found = completed_with_data.aggregate(Avg('papers_found'))['papers_found__avg']
            avg_papers_extracted = completed_with_data.aggregate(Avg('papers_extracted'))['papers_extracted__avg']

            self.stdout.write('Processing Metrics (Completed Jobs):')
            self.stdout.write(f'  Avg papers found: {avg_papers_found:.1f}')
            self.stdout.write(f'  Avg papers extracted: {avg_papers_extracted:.1f}')
            self.stdout.write(f'  Avg extraction rate: {avg_papers_extracted / avg_papers_found * 100:.1f}%\n')

        # Paper statistics
        total_papers = Paper.objects.count()
        papers_with_pdf = Paper.objects.exclude(cached_file='').count()
        papers_with_text = Paper.objects.filter(text_length__gt=100).count()

        self.stdout.write('Paper Statistics:')
        self.stdout.write(f'  Total papers: {total_papers}')
        self.stdout.write(
            f'  With cached PDF: {papers_with_pdf} ({papers_with_pdf / total_papers * 100:.1f}%)' if total_papers else '  With cached PDF: 0')
        self.stdout.write(
            f'  With extracted text: {papers_with_text} ({papers_with_text / total_papers * 100:.1f}%)' if total_papers else '  With extracted text: 0')

        # Storage statistics
        import os
        from django.conf import settings

        pdf_dir = os.path.join(settings.MEDIA_ROOT, 'pdfs')
        if os.path.exists(pdf_dir):
            total_size = sum(
                os.path.getsize(os.path.join(pdf_dir, f))
                for f in os.listdir(pdf_dir)
                if os.path.isfile(os.path.join(pdf_dir, f))
            )
            total_size_mb = total_size / (1024 * 1024)
            self.stdout.write(f'  Total PDF storage: {total_size_mb:.2f} MB\n')

        # Review statistics
        total_reviews = LiteratureReview.objects.count()

        if total_reviews > 0:
            avg_word_count = LiteratureReview.objects.aggregate(Avg('word_count'))['word_count__avg']

            self.stdout.write('Review Statistics:')
            self.stdout.write(f'  Total reviews: {total_reviews}')
            self.stdout.write(f'  Avg word count: {avg_word_count:.0f}\n')

        # Recent activity (last 7 days)
        week_ago = timezone.now() - timedelta(days=7)
        recent_jobs = LiteratureReviewJob.objects.filter(created_at__gte=week_ago).count()
        recent_reviews = LiteratureReview.objects.filter(created_at__gte=week_ago).count()

        self.stdout.write('Recent Activity (Last 7 Days):')
        self.stdout.write(f'  Jobs created: {recent_jobs}')
        self.stdout.write(f'  Reviews generated: {recent_reviews}\n')

        self.stdout.write(self.style.SUCCESS('=== End of Statistics ==='))


# ---


"""
litapp/management/commands/test_openalex.py
Test OpenAlex API connectivity and search
"""

from django.core.management.base import BaseCommand
from litapp.utils import fetch_openalex_works_data


class Command(BaseCommand):
    help = 'Test OpenAlex API connectivity and search functionality'

    def add_arguments(self, parser):
        parser.add_argument(
            'topic',
            type=str,
            help='Topic to search for'
        )
        parser.add_argument(
            '--count',
            type=int,
            default=5,
            help='Number of results to fetch (default: 5)'
        )

    def handle(self, *args, **options):
        topic = options['topic']
        count = options['count']

        self.stdout.write(f'Testing OpenAlex API with topic: "{topic}"')
        self.stdout.write(f'Fetching {count} results...\n')

        results = fetch_openalex_works_data(topic, per_page=count)

        if not results:
            self.stdout.write(
                self.style.ERROR('No results returned. Check your internet connection and OpenAlex API status.')
            )
            return

        self.stdout.write(
            self.style.SUCCESS(f'Successfully fetched {len(results)} results:\n')
        )

        for idx, paper in enumerate(results, 1):
            self.stdout.write(f'{idx}. {paper.get("display_name", "No title")}')
            self.stdout.write(f'   Year: {paper.get("publication_year", "Unknown")}')
            self.stdout.write(f'   OpenAlex ID: {paper.get("id", "Unknown")}')

            oa_location = paper.get('open_access', {}).get('best_oa_location')
            pdf_url = oa_location.get('pdf_url') if oa_location else None

            if pdf_url:
                self.stdout.write(f'   PDF: ✓ Available')
            else:
                self.stdout.write(f'   PDF: ✗ Not available')

            self.stdout.write('')

        # Summary
        papers_with_pdf = sum(
            1 for p in results
            if p.get('open_access', {}).get('best_oa_location', {}).get('pdf_url')
        )

        self.stdout.write(
            self.style.SUCCESS(
                f'Summary: {papers_with_pdf}/{len(results)} papers have PDFs available '
                f'({papers_with_pdf / len(results) * 100:.1f}%)'
            )
        )