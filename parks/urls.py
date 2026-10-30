from django.urls import path

from .views import ParkDetailView

urlpatterns = [
    path('parks/<int:pk>/', ParkDetailView.as_view(), name='park_detail'),
]
