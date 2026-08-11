from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import User


def role_required(*allowed_roles):
    """Security boundary: only a logged-in user whose role is in allowed_roles
    may pass. Combined with login_required so anonymous users are redirected
    to login rather than 403'd. Reused across every role-restricted view."""
    def decorator(view_func):
        @wraps(view_func)
        @login_required
        def wrapper(request, *args, **kwargs):
            if request.user.role not in allowed_roles:
                raise PermissionDenied(f"This page requires one of: {', '.join(allowed_roles)}.")
            return view_func(request, *args, **kwargs)
        return wrapper
    return decorator


hr_admin_required = role_required(User.Role.HR_ADMIN)
