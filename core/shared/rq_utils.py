import django_rq
from rq.job import Job
from rq.registry import StartedJobRegistry, DeferredJobRegistry
import logging

logger = logging.getLogger(__name__)

def cancel_jobs_for_tenant(tenant_id: str):
    """
    Finds and cancels all queued, deferred, and started jobs for a specific tenant.
    """
    queue = django_rq.get_queue('default')
    
    # Get job IDs from the queue itself and various registries
    queued_job_ids = queue.job_ids
    started_job_ids = StartedJobRegistry(queue=queue).get_job_ids()
    deferred_job_ids = DeferredJobRegistry(queue=queue).get_job_ids()
    
    all_job_ids = set(queued_job_ids) | set(started_job_ids) | set(deferred_job_ids)
    
    if not all_job_ids:
        logger.info(f"No jobs in queue to check for tenant {tenant_id}.")
        return

    logger.info(f"Checking {len(all_job_ids)} jobs for cancellation for tenant {tenant_id}...")
    
    cancelled_count = 0
    for job_id in all_job_ids:
        try:
            job = Job.fetch(job_id, connection=queue.connection)
            
            # Check the tenant_id from the job's meta
            if job.meta.get('tenant_id') == tenant_id:
                logger.warning(f"Cancelling job {job.id} for tenant {tenant_id}. Description: {job.description}")
                job.cancel()
                cancelled_count += 1
        except Exception as e:
            logger.error(f"Error fetching or cancelling job {job_id}: {e}")
            
    logger.info(f"Cancellation process finished for tenant {tenant_id}. Cancelled {cancelled_count} job(s).")
