# -*- coding: utf-8 -*-
"""
Тесты страницы «Реквизиты компании» (кабинет клиента).
Доступ по разделу «Настройки», запись — только с правом редактирования,
изоляция по арендатору.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.models_company import CompanyProfile


class CompanyProfileTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.url = f"/c/{self.country.slug}/company/"

    def _mk_user(self, name, role, sections, superuser=False):
        u = User.objects.create_user(name, password="x", is_superuser=superuser)
        if not superuser:
            p = UserProfile.objects.create(user=u, role=role, allowed_sections=sections)
            p.countries.set([self.country])
        return u

    # ---- доступ -------------------------------------------------------------
    def test_admin_with_settings_can_open(self):
        u = self._mk_user("admin1", UserProfile.ROLE_ADMIN, ["settings"])
        self.client.force_login(u)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_user_without_settings_forbidden(self):
        u = self._mk_user("nobody", UserProfile.ROLE_VIEWER, [])
        self.client.force_login(u)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 403)

    # ---- сохранение ---------------------------------------------------------
    def test_admin_can_save_requisites(self):
        u = self._mk_user("admin2", UserProfile.ROLE_ADMIN, ["settings"])
        self.client.force_login(u)
        self.client.post(self.url, {
            "legal_name": "ООО Navruz",
            "inn": "123456789",
            "mfo": "00014",
            "bank_account": "20208000900000000001",
            "vat_payer": "on",
            "director_name": "Иванов И.И.",
        })
        prof = CompanyProfile.objects.get(country=self.country)
        self.assertEqual(prof.legal_name, "ООО Navruz")
        self.assertEqual(prof.inn, "123456789")
        self.assertEqual(prof.mfo, "00014")
        self.assertTrue(prof.vat_payer)
        self.assertEqual(prof.director_name, "Иванов И.И.")

    def test_viewer_cannot_save(self):
        # просмотр раздела есть, прав на правку нет
        u = self._mk_user("viewer1", UserProfile.ROLE_VIEWER, ["settings"])
        self.client.force_login(u)
        get = self.client.get(self.url)
        self.assertEqual(get.status_code, 200)
        post = self.client.post(self.url, {"legal_name": "Хакер"})
        self.assertEqual(post.status_code, 403)
        self.assertFalse(
            CompanyProfile.objects.filter(
                country=self.country, legal_name="Хакер").exists())

    # ---- изоляция -----------------------------------------------------------
    def test_other_tenant_user_cannot_open(self):
        other = Country.objects.create(name="Другой", slug="other")
        u = User.objects.create_user("stranger", password="x")
        p = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_ADMIN, allowed_sections=["settings"])
        p.countries.set([other])
        self.client.force_login(u)
        resp = self.client.get(self.url)   # чужой арендатор
        self.assertEqual(resp.status_code, 404)
