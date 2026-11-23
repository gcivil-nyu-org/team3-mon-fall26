from django.urls import path

from .views import ParkDetailView, ReviewDeleteView, ReviewModerationDeleteView, ReviewUpdateView

urlpatterns = [
    path('parks/<int:pk>/', ParkDetailView.as_view(), name='park_detail'),
    path('reviews/<int:pk>/edit/', ReviewUpdateView.as_view(), name='park_review_edit'),
    path('reviews/<int:pk>/delete/', ReviewDeleteView.as_view(), name='park_review_delete'),
    path(
        'reviews/<int:pk>/moderate-delete/',
        ReviewModerationDeleteView.as_view(),
        name='park_review_moderate_delete',
    ),
]
