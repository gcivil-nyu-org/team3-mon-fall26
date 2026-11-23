"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from reports.views import submit_report
from sitetools.views import metrics

from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('health/', views.health, name='health'),
    path('ready/', views.ready, name='ready'),
    # Before admin/, which would otherwise read "metrics" as an app name and 404.
    path('admin/metrics/', admin.site.admin_view(metrics), name='admin_metrics'),
    path('admin/', admin.site.urls),
    path('', include('accounts.urls')),
    path('', include('events.urls')),
    path('', include('parks.urls')),
    path('reports/<str:kind>/<int:pk>/', submit_report, name='submit_report'),
]

# Serves uploaded profile photos locally; static() adds nothing when DEBUG is off.
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
