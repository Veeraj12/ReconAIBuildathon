from rest_framework.permissions import BasePermission


class IsAccountantOrAdmin(BasePermission):
    """
    Gate reconciliation actions (upload/run) behind accountant or admin
    roles. Viewers can authenticate and see past results (once §8 of
    the migration guide — persisted runs — is built) but can't trigger
    new reconciliations.
    """

    message = "Your account doesn't have permission to run reconciliations."

    def has_permission(self, request, view):
        profile = getattr(request.user, "profile", None)
        return bool(profile and profile.can_reconcile)


class IsAdmin(BasePermission):

    message = "This action requires an admin account."

    def has_permission(self, request, view):
        profile = getattr(request.user, "profile", None)
        return bool(profile and profile.is_admin)
