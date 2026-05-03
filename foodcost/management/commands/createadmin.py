from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Create superuser"

    def handle(self, *args, **kwargs):
        User = get_user_model()

        username = "owner"
        password = "owner12345"

        user, created = User.objects.get_or_create(username=username)

        user.email = "owner@mail.com"
        user.is_staff = True
        user.is_superuser = True
        user.set_password(password)
        user.save()

        self.stdout.write(self.style.SUCCESS("Superuser ready: owner / owner12345"))