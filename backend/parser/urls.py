from django.urls import path
from . import views 
from . import auth_views
from .views import (
    upload_files, upload_files_multi, analyze_exceptions, explain_pattern,
    reconbot_ask, submit_cause_feedback, latest_reconciliation_run,
)

# urlpatterns = [
#      path("", views.index, name="home"),
#      path("api/upload/", views.upload_files),
# ]

urlpatterns = [
    path("api/v1/reconcile/", upload_files),
    path("api/v1/reconcile/multi/", upload_files_multi),
    path("api/v1/exceptions/analyze/", analyze_exceptions),
    path("api/v1/exceptions/patterns/<str:pattern_id>/explain/", explain_pattern),
    path("api/v1/exceptions/patterns/<str:pattern_id>/causes/feedback/", submit_cause_feedback),
    path("api/v1/reconciliation-runs/latest/", latest_reconciliation_run),
    path("api/v1/reconbot/ask/", reconbot_ask),
    path("api/v1/auth/register/", auth_views.RegisterView.as_view()),
    path("api/v1/auth/login/", auth_views.LoginView.as_view()),
    path("api/v1/auth/refresh/", auth_views.RefreshView.as_view()),
    path("api/v1/auth/logout/", auth_views.LogoutView.as_view()),
    path("api/v1/auth/me/", auth_views.MeView.as_view()),
]
