# Telegram Print Bot

Telegram-бот для отправки документов на печать через CUPS.

## Возможности

- Печать PDF, изображений, документов Word/Excel/PowerPoint, текстовых файлов
- Отслеживание статуса задания (в очереди / напечатано / ошибка)
- Авторизация по инвайт-коду
- Админ-управление пользователями

## Команды

| Команда | Описание |
|---|---|
| `/start` | Приветствие |
| `/auth <код>` | Авторизация по инвайт-коду |
| `/status` | Статус принтера |
| `/whoami` | Ваш Telegram ID и статус |

### Админ-команды

| Команда | Описание |
|---|---|
| `/code <код>` | Установить/посмотреть инвайт-код |
| `/users` | Список авторизованных пользователей |
| `/revoke <user_id>` | Отозвать доступ |

## Установка

### Зависимости (в контейнере)

```bash
apt-get install -y python3 python3-venv cups ghostscript libreoffice-core libreoffice-writer
```

### Настройка

```bash
mkdir -p /opt/printbot
python3 -m venv /opt/printbot/venv
/opt/printbot/venv/bin/pip install -r requirements.txt
cp .env.example /opt/printbot/.env
# Отредактировать .env
```

### Systemd

```bash
cp printbot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now printbot
```

## Поддерживаемые форматы

PDF, JPG, PNG, BMP, TIFF, GIF, DOC, DOCX, ODT, XLS, XLSX, ODS, PPT, PPTX, ODP, TXT, CSV, RTF
