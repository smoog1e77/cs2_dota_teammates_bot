# 🚀 Деплой на TimeWeb (VPS + PostgreSQL)

Пошагово: поднять бота на сервере TimeWeb, перевести базу на PostgreSQL,
настроить автозапуск и бэкапы. Делать **в спокойную фазу** (пока ~200/день),
до закупки рекламы.

> Перед стартом: **перевыпусти токен** у @BotFather (старый светился в файлах)
> и держи его только в `.env` на сервере — в репозиторий он не попадает.

---

## ⚡ Быстрый старт на 512 МБ + SQLite (самый дешёвый)

Для тихого старта (до ~сотен/день). Сервер — Ubuntu 22.04, у тебя есть его IP и доступ по SSH.

```bash
# 1) Зайти на сервер
ssh root@IP_СЕРВЕРА

# 2) SWAP — обязательно на 512 МБ (страховка от нехватки памяти при установке/работе)
fallocate -l 1G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

# 3) Базовые пакеты + uv (менеджер Python-окружения)
apt update && apt install -y curl git rsync
curl -LsSf https://astral.sh/uv/install.sh | sh && source ~/.bashrc
```

```bash
# 4) Залить проект — выполнять на ЛОКАЛЬНОЙ машине (НЕ на сервере).
#    Без .venv, кэша и bot.db. .env берём текущий (там уже новый токен + админ + SQLite).
rsync -av --exclude '.venv' --exclude '__pycache__' --exclude 'bot.db*' \
  "/Users/apple/Desktop/Новая папка/cs2_dota_teammates_bot/" root@IP_СЕРВЕРА:/opt/bot/
```

```bash
# 5) Снова на сервере: окружение и зависимости
cd /opt/bot
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt

# 6) Проверка ядра (создаст временную БД, боевую не трогает)
.venv/bin/python -m tests.test_smoke
```

Дальше — автозапуск через systemd (раздел 6 ниже) и логи (раздел 7). Postgres
и миграция данных на этом тарифе НЕ нужны — переедешь на них при росте.

---

## 0. Что понадобится

- Аккаунт TimeWeb Cloud.
- Новый `BOT_TOKEN` от @BotFather.
- Твой Telegram ID для `ADMIN_IDS` (узнать у @userinfobot).

---

## 1. Создать сервер

TimeWeb Cloud → **Облачные серверы** → создать:
- ОС: **Ubuntu 22.04**.
- Тариф: минимальный на старте (1–2 vCPU, 2 ГБ RAM) — хватит на первые тысячи.
  Поднять тариф можно в пару кликов без переустановки.
- Сохрани **root-пароль / SSH-ключ** и **IP сервера**.

Подключиться:
```bash
ssh root@IP_СЕРВЕРА
```

---

## 2. PostgreSQL

**Вариант А (просто и дёшево) — Postgres на том же сервере:**
```bash
apt update && apt install -y postgresql
sudo -u postgres psql -c "CREATE DATABASE teammates;"
sudo -u postgres psql -c "CREATE USER botuser WITH PASSWORD 'СГЕНЕРИРУЙ_ПАРОЛЬ';"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE teammates TO botuser;"
sudo -u postgres psql -d teammates -c "GRANT ALL ON SCHEMA public TO botuser;"
```
Строка подключения будет:
`postgresql+asyncpg://botuser:СГЕНЕРИРУЙ_ПАРОЛЬ@localhost:5432/teammates`

**Вариант Б (надёжнее, с авто-бэкапами) — TimeWeb Cloud → Базы данных → PostgreSQL.**
Там дадут host/port/user/password — собери из них такую же строку
`postgresql+asyncpg://USER:PASS@HOST:PORT/DBNAME`.

---

## 3. Загрузить код и зависимости

Установить Python-окружение (через uv — как локально):
```bash
apt install -y curl git
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc
```

Скопировать проект на сервер (с локальной машины, БЕЗ `.venv` и БЕЗ `bot.db`,
если переносишь данные миграцией):
```bash
# выполнять на ЛОКАЛЬНОЙ машине, из папки с проектом
rsync -av --exclude '.venv' --exclude '__pycache__' \
  "cs2_dota_teammates_bot/" root@IP_СЕРВЕРА:/opt/bot/
```

На сервере поставить зависимости:
```bash
cd /opt/bot
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.txt
```

---

## 4. Настроить `.env`

```bash
cd /opt/bot
cp .env.example .env
nano .env
```
Заполнить:
```
BOT_TOKEN=НОВЫЙ_ТОКЕН_ОТ_BOTFATHER
DATABASE_URL=postgresql+asyncpg://botuser:ПАРОЛЬ@localhost:5432/teammates
ADMIN_IDS=ТВОЙ_TELEGRAM_ID
REPORTS_TO_HIDE=5
REBROWSE_COOLDOWN_MINUTES=60
```

---

## 5. Перенести данные из SQLite (если нужно сохранить текущие анкеты)

Залей локальный `bot.db` на сервер (`rsync .../bot.db root@IP:/opt/bot/bot.db`),
затем на сервере:
```bash
cd /opt/bot
SOURCE_URL="sqlite+aiosqlite:///bot.db" \
DATABASE_URL="postgresql+asyncpg://botuser:ПАРОЛЬ@localhost:5432/teammates" \
BOT_TOKEN=x .venv/bin/python -m scripts.migrate_sqlite_to_pg
```
Скрипт создаст таблицы и перенесёт users / profiles / interactions / search_filters.
Если стартуешь с чистого листа — этот шаг пропусти (таблицы создадутся сами при
первом запуске).

Проверка ядра на сервере (не трогает боевую БД):
```bash
.venv/bin/python -m tests.test_smoke
```

---

## 6. Автозапуск через systemd

```bash
nano /etc/systemd/system/teammates-bot.service
```
Вставить:
```ini
[Unit]
Description=CS2/Dota2 teammates bot
After=network.target postgresql.service

[Service]
WorkingDirectory=/opt/bot
ExecStart=/opt/bot/.venv/bin/python bot.py
Restart=always
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
```
Запустить:
```bash
systemctl daemon-reload
systemctl enable --now teammates-bot
systemctl status teammates-bot
```
`Restart=always` поднимет бота автоматически при падении или перезагрузке сервера.

---

## 7. Логи

```bash
journalctl -u teammates-bot -f        # живой лог
journalctl -u teammates-bot --since "10 min ago"
```
Здесь же увидишь предупреждения о недоставленных уведомлениях
(`notify_like → ... не доставлено`) и флуд-лимиты.

---

## 8. Бэкап базы (вариант А — локальный Postgres)

Разовый:
```bash
sudo -u postgres pg_dump teammates > /opt/backup_$(date +%F).sql
```
Ежедневно по cron:
```bash
crontab -e
# добавить строку:
0 4 * * * sudo -u postgres pg_dump teammates > /opt/backup_$(date +\%F).sql
```
(В варианте Б — managed Postgres — бэкапы делает сам TimeWeb.)

---

## 9. Обновление бота позже

```bash
# залить изменённые файлы (локально):
rsync -av --exclude '.venv' --exclude '__pycache__' --exclude '.env' --exclude 'bot.db*' \
  "cs2_dota_teammates_bot/" root@IP_СЕРВЕРА:/opt/bot/
# на сервере:
cd /opt/bot && .venv/bin/python -m tests.test_smoke && systemctl restart teammates-bot
```
Меняй код только в **спокойные окна**, не под пиковым трафиком рекламы.

---

## ✅ Чек-лист перед закупкой рекламы

- [ ] Токен перевыпущен, лежит только в `.env` на сервере.
- [ ] `DATABASE_URL` указывает на PostgreSQL (не SQLite).
- [ ] Смоук-тест на сервере зелёный.
- [ ] systemd-сервис включён (`Restart=always`).
- [ ] Бэкап базы настроен (cron или managed).
- [ ] Прогнал сам себя: создал анкету, лайк, взаимный лайк, рассылка — всё дошло.
- [ ] `journalctl` чистый, без ошибок при базовых действиях.
</content>
