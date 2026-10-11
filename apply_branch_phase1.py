# -*- coding: utf-8 -*-
"""
Фаза 1 «Пользователи + Доступы»: вторая ось доступа — ФИЛИАЛЫ.
Делает доступ двумерным (что × где), НЕ меняя поведение экранов.

  1) foodcost/models.py — UserProfile.locations (M2M к Location), рядом с
     countries. Старое поле location сохраняется (back-compat).
  2) foodcost/views.py  — create_user / update_user сохраняют набор филиалов
     (getlist("locations")) + держат одиночный location = первый из набора.
  3) foodcost/branch_scope.py — единая точка правды: user_location_ids(user),
     scope_by_location(qs, user), user_allowed_locations(user, country).
     (В Фазе 1 только определяется; экраны включим в Фазе 2.)

После применения: python manage.py makemigrations foodcost && python manage.py migrate
Идемпотентно. Запуск из корня проекта: python apply_branch_phase1.py
"""
import ast
import pathlib
import sys

MODELS = "foodcost/models.py"
VIEWS = "foodcost/views.py"
SCOPE = "foodcost/branch_scope.py"

M_ANCHOR = '''        related_name="user_profiles"
    )

    def __str__(self):
        return f"{self.user.username} — {self.get_role_display()}"'''

M_REPL = '''        related_name="user_profiles"
    )

    # Несколько филиалов на сотрудника (доступ к данным филиала).
    # Пусто → ограничения нет (все филиалы). Back-compat со старым `location`.
    locations = models.ManyToManyField(
        Location,
        blank=True,
        related_name="staff_profiles",
    )

    def __str__(self):
        return f"{self.user.username} — {self.get_role_display()}"'''

C_ANCHOR = '''                profile, created = UserProfile.objects.get_or_create(user=user)
                profile.role = role
                profile.allowed_sections = allowed_sections
                profile.location_id = location_id or None
                profile.save()
                profile.countries.set(country_ids)

                return redirect(f"/c/{country.slug}/users/")'''

C_REPL = '''                profile, created = UserProfile.objects.get_or_create(user=user)
                profile.role = role
                profile.allowed_sections = allowed_sections
                _loc_ids = request.POST.getlist("locations") or ([location_id] if location_id else [])
                profile.location_id = (_loc_ids[0] if _loc_ids else None)
                profile.save()
                profile.countries.set(country_ids)
                profile.locations.set(_loc_ids)

                return redirect(f"/c/{country.slug}/users/")'''

U_ANCHOR = '''            profile.location_id = location_id or None

            profile.save()
            profile.countries.set(country_ids)'''

U_REPL = '''            _loc_ids = request.POST.getlist("locations") or ([location_id] if location_id else [])
            profile.location_id = (_loc_ids[0] if _loc_ids else None)

            profile.save()
            profile.countries.set(country_ids)
            profile.locations.set(_loc_ids)'''

SCOPE_BODY = '''# -*- coding: utf-8 -*-
"""
Доступ по филиалам — вторая ось доступа (рядом с секциями allowed_sections).
Единая точка правды: какие филиалы видит пользователь. Используется формами
(Фаза 1) и операционными экранами (Фаза 2).
"""


def user_location_ids(user):
    """None  → все филиалы (супер-админ, is_superuser, или филиалы не назначены).
    set(id) → только эти филиалы."""
    if getattr(user, "is_superuser", False):
        return None
    profile = getattr(user, "profile", None)
    if profile is None:
        return None
    if profile.is_super_admin():
        return None
    ids = set(profile.locations.values_list("id", flat=True))
    if not ids and getattr(profile, "location_id", None):
        ids = {profile.location_id}  # back-compat со старым одиночным филиалом
    return ids or None  # пусто → все (ничего не ограничиваем)


def scope_by_location(qs, user, field="location"):
    """Сузить queryset по филиалам пользователя. None → без изменений."""
    ids = user_location_ids(user)
    if ids is None:
        return qs
    return qs.filter(**{f"{field}__in": list(ids)})


def user_allowed_locations(user, country):
    """Филиалы (Location) этой компании, доступные пользователю — для
    чекбоксов/выпадашек в формах. Админ/без привязки → все филиалы страны."""
    from .models import Location
    qs = Location.objects.filter(country=country).order_by("site_sort_order", "name")
    ids = user_location_ids(user)
    if ids is None:
        return qs
    return qs.filter(id__in=list(ids))
'''


def patch_file(path, anchor, repl, label):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"! не найден {path} — запусти из корня проекта")
        sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if repl in s:
        print(f"~ {label}: уже применено")
        return s, False
    n = s.count(anchor)
    if n != 1:
        print(f"! {label}: якорь найден {n} раз (ожидалось 1) — не меняю, покажи Клоду")
        sys.exit(1)
    s2 = s.replace(anchor, repl, 1)
    ast.parse(s2)
    p.write_text(s2, encoding="utf-8")
    print(f"✓ {label}: готово")
    return s2, True


def main():
    patch_file(MODELS, M_ANCHOR, M_REPL, "models.py (UserProfile.locations)")
    patch_file(VIEWS, C_ANCHOR, C_REPL, "views.py (create_user)")
    patch_file(VIEWS, U_ANCHOR, U_REPL, "views.py (update_user)")

    sp = pathlib.Path(SCOPE)
    if sp.exists() and "def user_location_ids" in sp.read_text(encoding="utf-8"):
        print("~ branch_scope.py: уже есть")
    else:
        ast.parse(SCOPE_BODY)
        sp.write_text(SCOPE_BODY, encoding="utf-8")
        print("✓ branch_scope.py: создан")

    print("\nДальше: python manage.py makemigrations foodcost && python manage.py migrate")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e)
        sys.exit(1)
