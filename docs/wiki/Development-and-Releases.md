# Сборка и публикация

Используйте нативный Python 3.12 или 3.13; CI нацелен на 3.13. Текущий NetworkX
требует Python 3.12+, поэтому прежняя инструкция для 3.11 устарела.
Зависимости зафиксированы в requirements.lock. Build-зависимости
находятся в packaging/requirements-build.txt. Выполните `python packaging/build.py`
на каждой целевой ОС/архитектуре отдельно. PyInstaller не является cross-compiler.
Пакет включает web, skills, wiki, иконку, Noto Sans с OFL и runtime-зависимости.

GitHub workflow native-build собирает macOS/Windows/Linux и запускает проверки.
GitLab pipeline содержит соответствующие jobs с platform runner tags; их нужно
связать с собственными runners. Отсутствующий runner не означает успешную сборку.
Полученные артефакты не публикуются автоматически как подписанные релизы.

Основная проверка: `python -m pytest -q`. Кроме тестов, релиз проверяется реальным
запуском standalone, Graphify extraction, MCP handshake, PNG и повторной
установкой с неизменной базой. Результат одной ОС не доказывает остальные.

Отдельный Git нужен только для clone/diff/wiki. Иконка создана для Code Atlas;
лицензия шрифта поставляется в packaging/assets/NotoSans-LICENSE.txt.
