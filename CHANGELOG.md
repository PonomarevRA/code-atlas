# Changelog

For each stable version, record one to five short user-facing changes in Russian and English. Describe what users can do or what now works better. Keep installation steps and internal implementation details in their own documentation.

Для каждой стабильной версии добавляйте от одного до пяти коротких пунктов на русском и английском. Опишите, что пользователь сможет сделать или что стало работать лучше. Инструкции по установке и внутренние изменения ведите отдельно.

## [0.1.9]

### Русский

- Агенты в связанных Git worktree могут читать один базовый снимок и видеть свои изменения в отдельном слое. Режим экспериментальный. Актуальные связи подтверждаются после пересборки рабочего checkout.

### English

- Agents in linked Git worktrees can share a base snapshot and see their own changes in separate overlays. This experimental mode requires a checkout rebuild to confirm current links.

## [0.1.8]

### Русский

- Карта сначала показывает обзор проектов и групп, а узлы загружает при раскрытии группы.
- Агент может искать объявления в конкретном файле, пока строится карта, и получает подсказку, когда проверить её готовность.
- Каталог точек входа загружается по запросу, различает подписки Kafka и места отправки и обновляется при смене снимка.
- Нативный Atlas проверяет обновления в фоне, а в меню «Справка» доступны проверка версии и страница релизов.

### English

- Maps open with a project and group overview, then load nodes when you open a group.
- Agents can find declarations in a specific file while the map builds and see when to check its status again.
- The entrypoint catalog loads on demand, distinguishes Kafka subscriptions from publish sites, and refreshes when you switch snapshots.
- Native Atlas checks for updates in the background and offers a version check and Releases link in Help.

## [0.1.7]

### Русский

- Карта сначала показывает обзор проектов и групп, а узлы загружает при раскрытии группы.
- Агент может искать объявления в конкретном файле, пока строится карта, и получает подсказку, когда проверить её готовность.
- Каталог точек входа загружается по запросу и различает подписки Kafka и места отправки.
- Нативный Atlas проверяет обновления в фоне, а в меню «Справка» доступны проверка версии и страница релизов.

### English

- Maps open with a project and group overview, then load nodes when you open a group.
- Agents can find declarations in a specific file while the map builds and see when to check its status again.
- The entrypoint catalog loads on demand and distinguishes Kafka subscriptions from publish sites.
- Native Atlas checks for updates in the background and offers a version check and Releases link in Help.
