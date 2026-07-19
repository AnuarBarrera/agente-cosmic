from django.conf import settings


def ga4(request):
    return {'GA4_MEASUREMENT_ID': settings.GA4_MEASUREMENT_ID}


def umami(request):
    return {'UMAMI_WEBSITE_ID': settings.UMAMI_WEBSITE_ID}
