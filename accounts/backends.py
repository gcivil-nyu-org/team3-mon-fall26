from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.models import Permission
from django.db.models import Q

from .roles import ADMINISTRATORS_GROUP


class EmailOrUsernameBackend(ModelBackend):
    """Authenticate by email, or by username for staff accounts."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or password is None:
            return None

        users = get_user_model().objects
        if '@' in username:
            user = users.filter(email__iexact=username).first()
        else:
            user = users.filter(username=username, is_staff=True).first()

        if user is None:
            get_user_model()().set_password(password)
            return None

        return super().authenticate(
            request,
            username=user.get_username(),
            password=password,
            **kwargs,
        )

    def _get_group_permissions(self, user_obj):
        """Group permissions, plus the Administrators group's for every staff account (#44).

        Staff status alone makes someone an administrator, so nobody has to remember to add new
        staff to the group, and revoking staff status takes the permissions away with it.
        """
        in_group = Q(group__user=user_obj)
        if user_obj.is_staff:
            in_group |= Q(group__name=ADMINISTRATORS_GROUP)
        return Permission.objects.filter(in_group).distinct()
