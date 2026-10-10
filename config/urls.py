"""
Корневой роутинг проекта.
Медиа (фото блюд/категорий) отдаём всегда, не только при DEBUG.
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as _serve_media


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("foodcost.urls")),
]

urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        _serve_media,
        {"document_root": settings.MEDIA_ROOT},
    ),
]
