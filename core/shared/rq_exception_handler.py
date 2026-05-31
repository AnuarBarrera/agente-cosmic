import logging
from uuid import UUID
from core.tenant_management.domain.entities import ErrorInteraction
from core.tenant_management.infrastructure.repositories import DjangoErrorInteractionRepository

logger = logging.getLogger(__name__)

def log_rq_failure_to_db(job, *exc_info):
    """
    Custom RQ exception handler.
    Logs the details of a failed job to the ErrorInteraction table
    instead of sending an email.
    """
    logger.info(f"RQ job {job.id} failed. Executing custom exception handler.")
    
    tenant_id_str = job.meta.get('tenant_id')
    if not tenant_id_str:
        logger.error(f"Job {job.id} failed but no 'tenant_id' found in meta. Cannot log to ErrorInteraction table.")
        return

    try:
        tenant_id = UUID(tenant_id_str)
        
        error_type = "RQJobExecutionError"
        # exc_info is a tuple (type, value, traceback)
        exception_type = exc_info[0].__name__
        exception_value = str(exc_info[1])
        
        details = {
            "job_id": job.id,
            "job_func_name": job.func_name,
            "job_args": job.args,
            "job_kwargs": job.kwargs,
            "exception_type": exception_type,
            "exception_value": exception_value,
            "traceback": job.exc_info or "No traceback available."
        }

        # Create the error entity
        error_interaction = ErrorInteraction(
            tenant_id=tenant_id,
            error_type=error_type,
            details=details
        )
        
        # Save to the database
        repo = DjangoErrorInteractionRepository()
        repo.save(error_interaction)
        
        logger.info(f"Successfully logged failed job {job.id} for tenant {tenant_id} to the database.")

    except Exception as e:
        logger.critical(
            f"CRITICAL: The custom RQ exception handler itself failed while processing job {job.id}. "
            f"This may result in lost error reports. Error: {e}",
            exc_info=True
        )
    
    # Return True to indicate that the exception has been handled
    # and to prevent RQ's default handler from running.
    return True
