from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create or reset default admin user"

    def handle(self, *args, **kwargs):
        User = get_user_model()

        user, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@mail.com",
                "is_staff": True,
                "is_superuser": True,
            }
        )

        user.email = "admin@mail.com"
        user.is_staff = True
        user.is_superuser = True
        user.set_password("admin123")
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS("Admin created"))
        else:
            self.stdout.write(self.style.SUCCESS("Admin password reset"))