# PR #1 — Безопасность и гигиена репозитория

Это первый шаг программы «ERP → продукт». Он независим от рефакторинга аренды,
низкорисковый и чинит боевые дыры. **Читать целиком перед деплоем — порядок
действий важен, иначе прод не поднимется.**

---

## 0. Что меняется

| Файл | Было | Стало |
|---|---|---|
| `config/settings.py` | `SECRET_KEY` захардкожен, `DEBUG=True`, `ALLOWED_HOSTS=["*"]` | всё из окружения; prod fail-closed без `DJANGO_SECRET_KEY`; `CSRF_TRUSTED_ORIGINS`, secure-куки, ssl-заголовок прокси |
| `config/urls.py` | медиа отдаётся только при `DEBUG=True` | медиа отдаётся всегда (работает при `DEBUG=False`) |
| `.gitignore` | только `__pycache__`, `*.pyc`, `db.sqlite3` | + `.env*`, `media/`, `staticfiles/`, `*.csv`, `.DS_Store`, venv |
| `.env.example` | — | шаблон окружения (без реальных значений) |
| `requirements.txt` | без версий | версии закреплены (`~=`) |

Положить файлы по путям: `settings.py`→`config/`, `urls.py`→`config/`,
остальные — в корень репозитория.

---

## 1. СНАЧАЛА задать переменные в Render (до деплоя!)

В Render → сервис backend → **Environment** добавить:

- `DJANGO_SECRET_KEY` — **новый** случайный ключ. Сгенерировать:
  `python -c "import secrets; print(secrets.token_urlsafe(64))"`
- `DJANGO_DEBUG=False`
- `DJANGO_ALLOWED_HOSTS=montenegro-8y6i.onrender.com,raccoon.uz,www.raccoon.uz`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://montenegro-8y6i.onrender.com,https://raccoon.uz,https://www.raccoon.uz`

Проверить, что уже заданы (они и раньше читались из env): `DATABASE_URL`,
`CLICK_*`, `PAYME_*`, `ESKIZ_*`, `TELEGRAM_*`, `OTP_EXPOSE_CODE_FOR_TESTING=False`.

> Если `DJANGO_SECRET_KEY` не задать, приложение **намеренно** не стартует в
> проде (fail-closed) — это защита, а не баг. Поэтому сначала env, потом деплой.

---

## 2. Выкинуть секреты и мусор из git

Файлы всё ещё в индексе — просто добавить их в `.gitignore` мало, нужно убрать
из отслеживания:

```bash
git rm --cached .env.local leads.csv
git rm --cached -r staticfiles media
git rm --cached "foodcost/public_api (1).py" "foodcost/public_api"   # дубли
find . -name .DS_Store -exec git rm --cached {} +
git add .gitignore .env.example requirements.txt config/settings.py config/urls.py
git commit -m "security: env-based settings, ignore secrets/media/junk, pin deps"
```

> `foodcost/public_api.py` — оставить (рабочий). `public_api (1).py` и файл
> `public_api` без расширения — удалить (это дубли ~204 КБ).

---

## 3. Прокрутить (rotate) засветившиеся ключи — ОБЯЗАТЕЛЬНО

`.env.local` лежал в публичном репозитории, значит все значения из него надо
считать скомпрометированными и перевыпустить:

- **Click**: `CLICK_SECRET_KEY` — в кабинете мерчанта Click.
- **Payme**: `PAYME_SECRET_KEY` — в кабинете Payme/Paycom.
- **Яндекс.Карты**: `NEXT_PUBLIC_YMAPS_*` — в кабинете Яндекса (ограничить по
  домену).
- Ранее в переписке/скриншотах светился **GitHub PAT** — отозвать в GitHub →
  Settings → Developer settings → Tokens.

После ротации обновить значения в Render (backend) и в окружении фронта.

---

## 4. Почистить историю git (секреты остаются в истории!)

Удаление файла новым коммитом НЕ убирает его из прошлых коммитов. Вычистить:

```bash
# вариант с git-filter-repo (рекомендуется)
pip install git-filter-repo
git filter-repo --invert-paths \
  --path .env.local --path leads.csv \
  --path "foodcost/public_api (1).py" --path "foodcost/public_api"
git push --force origin <ветка>
```

> Форс-пуш переписывает историю — согласовать, если с репо работает кто-то ещё.
> Даже после очистки истории ключи из п.3 всё равно считать скомпрометированными
> (репозиторий публичный, могли склонировать). Ротация обязательна в любом случае.

---

## 5. Деплой и проверка

1. Убедиться, что env из п.1 заданы.
2. `git push` → дождаться деплоя.
3. Проверить:
   - сайт и админка открываются (нет 400 Bad Request → значит `ALLOWED_HOSTS` ок);
   - вход в ERP работает (нет 403 CSRF на POST → `CSRF_TRUSTED_ORIGINS` ок);
   - фото блюд открываются (медиа отдаётся при `DEBUG=False`);
   - оплата Click/Payme проходит колбэк (после ротации ключей — проверить тестовый
     платёж).
4. Если что-то отвалилось по хостам/CSRF — добавить недостающий домен в
   соответствующий env и передеплоить (код менять не нужно).

---

## Откат

Все изменения — в 5 файлах и переменных окружения. Откат: вернуть прежние
`config/settings.py` и `config/urls.py` из git и передеплоить. Env-переменные
можно оставить — старый код их просто игнорировал.

---

## Дальше (PR #2)

Страховочная сетка перед рефакторингом аренды: смоук-тесты на денежно-критичные
потоки (создание/сохранение заказа, пересчёт себестоимости, колбэки Payme/Click,
вход по OTP), сид-скрипт и CI (migrate + tests на каждый push). Готовлю отдельно.
