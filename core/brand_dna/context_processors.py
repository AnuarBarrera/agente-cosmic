from django.conf import settings


def ga4(request):
    return {'GA4_MEASUREMENT_ID': settings.GA4_MEASUREMENT_ID}
