"""
Корневой роутинг проекта.

Медиа (загруженные фото блюд/категорий) отдаём ВСЕГДА, а не только при
DEBUG=True — иначе при переходе на DEBUG=False фото перестанут открываться.
Для роста нагрузки вынести медиа во внешнее хранилище (S3 / Cloudinary /
смонтированный том) и убрать этот маршрут.
"""
from django.conf import settings
from django.contrib import admin
from django.urls import include, path, re_path
from django.views.static import serve as _serve_media


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("foodcost.urls")),
]

# 🖼 Отдача пользовательских загрузок (MEDIA) не зависит от DEBUG.
urlpatterns += [
    re_path(
        r"^media/(?P<path>.*)$",
        _serve_media,
        {"document_root": settings.MEDIA_ROOT},
    ),
]
