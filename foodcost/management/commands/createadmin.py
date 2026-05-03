from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create default admin user"

    def handle(self, *args, **kwargs):
        User = get_user_model()

        if User.objects.filter(username="admin").exists():
            self.stdout.write("Admin already exists")
            return

        User.objects.create_superuser(
            username="admin",
            email="admin@mail.com",
            password="admin123",
        )

        self.stdout.write(self.style.SUCCESS("Admin created"))