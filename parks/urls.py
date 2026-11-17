from django.urls import path

from .views import ParkDetailView, ReviewDeleteView, ReviewUpdateView

urlpatterns = [
    path('parks/<int:pk>/', ParkDetailView.as_view(), name='park_detail'),
    path('reviews/<int:pk>/edit/', ReviewUpdateView.as_view(), name='park_review_edit'),
    path('reviews/<int:pk>/delete/', ReviewDeleteView.as_view(), name='park_review_delete'),
]
