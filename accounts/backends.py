from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


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
