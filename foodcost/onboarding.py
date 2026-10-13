# -*- coding: utf-8 -*-
"""
Конфиг опросника онбординга по типу бизнеса.

Интерим-версия: формирует стартовый набор модулей арендатора. Позже это станет
полноценным конструктором цены по направлениям (см. долги в заметках проекта).
Ключи — идентификаторы модулей (строки), стабильные для последующего гейтинга.
"""

TYPE_LABELS = {
    "retail": "🛍 Магазин",
    "restaurant": "🍽 Ресторан",
    "services": "💇 Услуги",
}

ONBOARDING_QUESTIONS = {
    "restaurant": [
        {"key": "hall", "title": "Обслуживание в зале",
         "desc": "Карта столов, официанты, разделение счёта.", "default": True},
        {"key": "delivery", "title": "Доставка и самовывоз",
         "desc": "Зоны, курьеры, статусы, самовывоз.", "default": True},
        {"key": "kds", "title": "Кухонный экран (KDS)",
         "desc": "Очередь, цеха (горячий/бар/выпечка), таймеры.", "default": False},
        {"key": "foodcost", "title": "Техкарты и себестоимость",
         "desc": "Рецептуры, нормы расхода, FoodCost и маржа.", "default": True},
        {"key": "aggregators", "title": "Агрегаторы",
         "desc": "Uzum, Yandex Еда и другие — заказы в единый контур.", "default": False},
        {"key": "multibranch", "title": "Несколько филиалов",
         "desc": "Общий каталог, остатки и отчёты по точкам.", "default": False},
    ],
    "retail": [
        {"key": "barcode", "title": "Штрихкоды и весовой товар",
         "desc": "Сканер, весы, печать этикеток.", "default": True},
        {"key": "warehouse", "title": "Склад и приёмка",
         "desc": "Остатки, приходы от поставщиков, перемещения.", "default": True},
        {"key": "batches", "title": "Маркировка, партии, сроки",
         "desc": "Серии, сроки годности, коды маркировки.", "default": False},
        {"key": "wholesale", "title": "Оптовая торговля",
         "desc": "Оптовые цены, минимальные партии, прайс-листы.", "default": False},
        {"key": "multibranch", "title": "Несколько точек",
         "desc": "Общий каталог, остатки и отчёты по точкам.", "default": False},
    ],
    "services": [
        {"key": "booking", "title": "Запись по времени",
         "desc": "Календарь, онлайн-запись, напоминания.", "default": True},
        {"key": "staff_schedule", "title": "Мастера и расписание",
         "desc": "Графики специалистов, загрузка, комиссии.", "default": True},
        {"key": "packages", "title": "Абонементы и пакеты",
         "desc": "Пакеты процедур, остаток посещений, сроки.", "default": False},
        {"key": "goods", "title": "Продажа товаров",
         "desc": "Косметика и сопутствующие товары на кассе.", "default": False},
        {"key": "multibranch", "title": "Несколько филиалов",
         "desc": "Общий каталог, остатки и отчёты по точкам.", "default": False},
    ],
}


def questions_for(business_type):
    return ONBOARDING_QUESTIONS.get(business_type, [])


def valid_keys(business_type):
    return {q["key"] for q in questions_for(business_type)}
