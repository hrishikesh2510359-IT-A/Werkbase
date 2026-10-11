from interviews.models import Notification


def notifications(request):
    """Unread-notification count for the bell/sidebar badge, for every role."""
    if not request.user.is_authenticated:
        return {}
    return {"unread_notifications": Notification.objects.filter(recipient=request.user, is_read=False).count()}
