from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import User


def hr_admin_required(view_func):
    """Security boundary: only a logged-in HR Admin may pass. Combined with
    login_required so anonymous users are redirected to login rather than 403'd."""
    @wraps(view_func)
    @login_required
    def wrapper(request, *args, **kwargs):
        if request.user.role != User.Role.HR_ADMIN:
            raise PermissionDenied("Only HR Admin can access this page.")
        return view_func(request, *args, **kwargs)
    return wrapper
