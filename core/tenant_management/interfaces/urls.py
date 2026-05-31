from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    TenantViewSet,
    UserRegistrationView,
    PlanViewSet,
    SubscriptionViewSet,
    MyTokenObtainPairView,
    EmailVerificationView,
    PasswordResetRequestView,
    PasswordResetConfirmView,
    LogoutView,
    TokenRefreshView,
    UserSessionsView,
    ChangePasswordView,
)

router = DefaultRouter()
router.register(r"tenants", TenantViewSet, basename="tenant")
router.register(r"plans", PlanViewSet, basename="plan")
router.register(r"subscriptions", SubscriptionViewSet, basename="subscription")

urlpatterns = [
    path("register/", UserRegistrationView.as_view(), name="register"),
    path("verify-email/", EmailVerificationView.as_view(), name="verify_email"),
    path("password-reset-request/", PasswordResetRequestView.as_view(), name="password_reset_request"),
    path("password-reset-confirm/", PasswordResetConfirmView.as_view(), name="password_reset_confirm"),
    path("change-password/", ChangePasswordView.as_view(), name="change_password"),
    path("token/", MyTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("sessions/", UserSessionsView.as_view(), name="user_sessions"),
    path("", include(router.urls)),
]
