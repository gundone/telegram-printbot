import os

BOT_TOKEN = os.environ["BOT_TOKEN"]
PRINTER = os.environ.get("PRINTER", "KX-MB1500")
ADMIN_ID = int(os.environ["ADMIN_ID"])

INVITE_CODE_FILE = "/opt/printbot/invite_code.txt"
USERS_FILE = "/opt/printbot/users.json"
PENDING_DIR = "/opt/printbot/pending"

PRINT_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif",
    ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp", ".txt", ".csv", ".rtf",
}

OFFICE_EXTENSIONS = {
    ".doc", ".docx", ".odt", ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx", ".odp", ".rtf", ".csv", ".txt",
}

NUP_SCALE = {1: 100, 2: 70, 4: 50}
