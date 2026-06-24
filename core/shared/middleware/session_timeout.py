import time
from django.shortcuts import redirect


class SessionTimeoutMiddleware:
    INACTIVITY_TIMEOUT = 1800  # 30 minutes

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            last = request.session.get('last_activity')
            if last is not None:
                idle = time.time() - last
                if idle > self.INACTIVITY_TIMEOUT:
                    request.session.flush()
                    return redirect('/auth/login/?reason=inactivity')
            request.session['last_activity'] = time.time()
        return self.get_response(request)
