import logging
import os
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from google.cloud import storage
from core.tenant_management.models import User
from core.brand_dna.models import AnalysisJob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Delete GCS images and local files for users deactivated more than 30 days ago'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        users = User.objects.filter(
            is_active=False,
            deactivated_at__isnull=False,
            deactivated_at__lt=cutoff,
        )

        if not users.exists():
            self.stdout.write('No users to clean up.')
            return

        bucket_name = settings.GOOGLE_CLOUD_STORAGE_BUCKET
        gcs_client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = gcs_client.bucket(bucket_name)

        total_blobs = 0
        total_jobs = 0

        for user in users:
            jobs = AnalysisJob.objects.filter(user=user)
            for job in jobs:
                blobs = list(bucket.list_blobs(prefix=f'posts/{job.id}-'))
                for blob in blobs:
                    blob.delete()
                    total_blobs += 1

                if job.logo_file_path:
                    full = os.path.join(settings.MEDIA_ROOT, job.logo_file_path)
                    if os.path.exists(full):
                        os.remove(full)

                for path in (job.post_images_paths or []):
                    full = os.path.join(settings.MEDIA_ROOT, path)
                    if os.path.exists(full):
                        os.remove(full)

                job.logo_file_path = ''
                job.post_images_paths = []
                job.save(update_fields=['logo_file_path', 'post_images_paths'])
                total_jobs += 1

            logger.info(f'Cleaned images for user {user.email}')

        self.stdout.write(
            f'Cleanup complete: {users.count()} users, {total_jobs} jobs, {total_blobs} GCS blobs deleted.'
        )
