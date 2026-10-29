from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import redirect
from django.views.generic import DetailView, FormView

from .forms import ProfileSetupForm, RegisterForm
from .models import Profile


class RegisterView(FormView):
    template_name = 'registration/register.html'
    form_class = RegisterForm

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect('home')
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        user = form.save(commit=False)
        user.username = form.cleaned_data['display_username']
        try:
            with transaction.atomic():
                user.save()
                Profile.objects.create(
                    user=user,
                    display_username=form.cleaned_data['display_username'],
                )
        except IntegrityError:
            form.add_error(None, 'That username or email is no longer available. Please choose another.')
            return self.form_invalid(form)

        login(self.request, user)
        return redirect('register_profile')


class ProfileSetupView(LoginRequiredMixin, FormView):
    template_name = 'registration/profile_setup.html'
    form_class = ProfileSetupForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs['instance'], _ = Profile.objects.get_or_create(user=self.request.user)
        kwargs['user'] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        is_edit = self.request.resolver_match.url_name == 'edit_profile'
        context['is_edit'] = is_edit
        # Profile settings open read-only; ?edit=1 or a form with errors unlocks the fields.
        # First-time setup is always editable.
        context['editing'] = not is_edit or 'edit' in self.request.GET or context['form'].is_bound
        return context

    def form_valid(self, form):
        user = self.request.user
        user.username = form.cleaned_data['display_username']
        user.first_name = form.cleaned_data['first_name']
        user.last_name = form.cleaned_data['last_name']
        try:
            with transaction.atomic():
                user.save(update_fields=['username', 'first_name', 'last_name'])
                form.save()
        except IntegrityError:
            form.add_error('display_username', 'That username is no longer available. Please choose another username.')
            return self.form_invalid(form)
        messages.success(self.request, 'Profile details saved.', extra_tags='auto-dismiss')
        # Saving settings returns them to read-only; first-time setup carries on home (#69).
        if self.request.resolver_match.url_name == 'edit_profile':
            return redirect('edit_profile')
        return redirect('home')


class PublicProfileView(DetailView):
    """Read-only profile anyone can view at /users/<username>/ (#13)."""

    template_name = 'accounts/public_profile.html'
    context_object_name = 'member'

    def get_object(self, queryset=None):
        # Suspended accounts (is_active=False) 404 like unknown ones (#49).
        member = (
            get_user_model()
            .objects.filter(username__iexact=self.kwargs['username'], is_active=True)
            .select_related('profile')
            .first()
        )
        if member is None:
            raise Http404('No member has that username.')
        return member

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        member = self.object
        # Older accounts may have no Profile row yet; show fallbacks without creating one.
        profile = getattr(member, 'profile', None) or Profile(user=member)
        context['profile'] = profile
        context['display_name'] = profile.display_username or member.username
        context['is_own_profile'] = self.request.user == member
        return context
