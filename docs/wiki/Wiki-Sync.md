# Автоматическая wiki GitHub/GitLab

Источник страниц — docs/wiki в основном репозитории. Wiki у GitHub/GitLab имеет
отдельный Git remote, обычно https://host/owner/repository.wiki.git.
Сначала включите wiki и создайте первую страницу на хостинге, например Welcome.
При конфликте имён с уже существующей неуправляемой страницей публикация
останавливается: переименуйте её или согласуйте содержимое до повторного запуска.

Для проверки без сети:
`python scripts/sync_wiki.py --remote https://host/owner/repository.wiki.git`

Для публикации добавьте `--publish`. Используются Git credentials/SSH agent
либо WIKI_TOKEN и WIKI_USERNAME из окружения. Токен не передаётся в URL или
аргументах Git. Настройка не выполняет force-push и сохраняет посторонние страницы.

## GitHub

В repository variables установите WIKI_SYNC_ENABLED=true. В secrets добавьте
WIKI_TOKEN с правом записи в wiki; стандартный GITHUB_TOKEN не следует считать
достаточным без проверки. Необязательный WIKI_GIT_URL позволяет указать другой
wiki remote. Workflow sync-wiki запускается при изменении docs/wiki или вручную.

## GitLab

Настройте protected/masked WIKI_TOKEN, WIKI_SYNC_ENABLED=true и, при необходимости,
WIKI_GIT_URL. Задание sync-wiki использует Linux runner. WIKI_USERNAME по умолчанию
установлен в oauth2. Wiki-публикация зависит от разрешений токена и настроек
проекта, не от наличия Markdown-файлов в основном репозитории.

Без Git remote и credentials публикация остаётся ненастроенной. Подготовленные
страницы доступны локально. Официальные сведения:
https://docs.github.com/en/communities/documenting-your-project-with-wikis/adding-or-editing-wiki-pages
https://docs.gitlab.com/user/project/wiki/
