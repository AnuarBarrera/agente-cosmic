from django.apps import AppConfig


class ContentPipelineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core.content_pipeline'
    verbose_name = 'Content Pipeline'
