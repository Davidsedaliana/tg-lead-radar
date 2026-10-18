# Формат выгрузки вакансий — v1

Общий контракт для hh-vacancy-scraper (источник `hh`) и tg-lead-radar (источник `tg`). Одинаковая
копия лежит в обоих репозиториях. Менять — только по согласию обеих сторон, с повышением версии.

## Файлы

Обе стороны пишут в одну папку — `JOBS_EXPORT_DIR` из `.env`.

| Файл | Кто пишет | Что это |
|---|---|---|
| `<source>_vacancies.jsonl` | источник | вакансии, по строке JSON на вакансию — машинный формат, описан ниже |
| `<source>_YYYY-MM-DD.md` | источник | дайджест для человека; в контракт входит только имя файла, структура — на усмотрение источника. hh — дайджест запуска (повторный запуск за день — `hh_YYYY-MM-DD_2.md` и т. д.); tg — дайджест дня публикации, перезаписывается целиком при каждом обновлении |
| `hh_apply_queue.md`, `hh_applications.jsonl` | hh и агент откликов | очередь и журнал откликов (Claude, навык `/hh-apply`). Имена `tg_apply_queue.md` и `tg_applications.jsonl` зарезервированы под то же назначение |
| `tg_channels_report.md`, `tg_channels_report.csv` | tg | отчёт discover по каналам |

`<source>` — `hh` или `tg`.

## Файл вакансий

- UTF-8, переводы строк LF, один JSON-объект на строку.
- Файл перезаписывается целиком каждым запуском (атомарно: временный файл и переименование), а не
  дописывается. Читатель должен быть готов к тому, что файл заменили.
- Без флага — полный снимок всех известных источнику вакансий, прошедших его фильтры.
- `--since-last` — только то, что ещё не выгружалось, плюс изменившееся после выгрузки (tg: вакансия
  нашлась ещё в одном канале или дополнилось поле). Нечего выгружать — пустой файл.
- Одна и та же вакансия может прийти повторно с тем же `id`: потребитель обновляет запись по `id`
  (upsert). Отсеянные фильтрами источника вакансии не выгружаются никогда.

## Поля

Все поля обязательны и идут в этом порядке. «Нет данных» — `null` для скаляров и `[]` для списков.

| Поле | Тип | Смысл |
|---|---|---|
| `id` | string | `<source>:<ключ>`, стабилен между запусками. hh — `hh:<число>` (id вакансии), tg — `tg:<16 hex>` (хэш нормализованного текста первой копии) |
| `source` | string | `hh` или `tg` |
| `url` | string | hh — карточка вакансии без параметров (`https://hh.ru/vacancy/<id>`), tg — ссылка на пост |
| `title` | string | hh — название вакансии, tg — первая содержательная строка поста |
| `company` | string \| null | hh — название работодателя, tg — только из строки «Компания: …» |
| `text` | string | полный текст. hh — из HTML описания: абзацы через пустую строку, пункты списков с `- ` или `1. `, без HTML. tg — текст поста как есть: переносы строк и авторские маркеры списков (—, •, эмодзи) сохранены, скрытые ссылки раскрыты в виде «текст (url)»; у репоста ссылкой текст оригинала — в `raw.original.text` |
| `salary_from`, `salary_to` | number \| null | как опубликовано, без пересчёта gross → net; одна сумма — обе границы равны |
| `currency` | string \| null | ISO 4217 (`RUB`, `USD`, …); `null`, если зарплаты нет |
| `salary_period` | string \| null | `month` / `hour` / `day` / `shift` / `week` / `year` / `project`; `null` — неизвестно |
| `salary_gross` | boolean \| null | `true` — до вычета налогов, `false` — на руки, `null` — не указано |
| `format` | string \| null | `office` / `hybrid` / `remote`; при нескольких — самый свободный (`remote` > `hybrid` > `office`); `null` — неизвестно или неоднозначно (tg: «офис или удалёнка» без слова «гибрид» → `null`); разъездной → `null` |
| `city` | string \| null | город вакансии |
| `grade` | string \| null | `junior` / `middle` / `senior` / `lead`. Сначала по названию (tg — ещё по хэштегам и строкам вида «Грейд:»); из нескольких — старший. Иначе по требуемому опыту: без опыта → `junior`, от 1 до 3 лет → `middle`, от 3 лет и больше → `senior`; `lead` по одному опыту не выводится. `null` — не определить. tg кладёт полный список в `raw.grades` |
| `stack` | string[] | hh — ключевые навыки вакансии как есть; tg — канонические имена из словаря технологий в `jobs.toml` (`python`, `pytest`, `playwright`, …) |
| `contacts` | string[] | email в нижнем регистре; Telegram — `https://t.me/<name>`, `@<name>` или `tg://user?id=<число>` (упоминание без username) |
| `apply_urls` | string[] | куда откликаться; hh — `[url]` |
| `same_as` | string[] | `id` этой же вакансии в другом источнике. tg заполняет из ссылок на hh в посте: `hh:<число>`, любой поддомен `*.hh.ru`, без query. У hh всегда `[]` |
| `posted_at` | string \| null | ISO 8601 со смещением (`2026-10-09T15:20:51+03:00`); у hh иногда только дата — `T00:00:00+03:00` и `raw.posted_at_precision = "day"` |
| `first_seen_at` | string | ISO 8601 со смещением: когда источник впервые увидел вакансию |
| `archived` | boolean | вакансия закрыта; tg — всегда `false` |
| `search_name` | string | hh — имя поиска из `config/searches.yml`, нашедшего вакансию первым; tg — username канала, у приватного канала без username — `c/<id>` |
| `raw` | object | всё специфичное для источника, см. ниже |

## raw

Ключи `raw` — на усмотрение источника; потребитель не должен на них полагаться без проверки.

- hh: `hh_id`, `company_url`, `salary_mode`, `salary_frequency`, `experience`, `experience_label`,
  `employment`, `work_formats`, `schedule`, `address`, `valid_to`, `posted_at_precision`,
  `letter_required`, `has_tests`, `matched_skills`, `match_score`, `search_names`, `parsed_from`,
  `removed`.
- tg: `channel`, `channel_title`, `views`, `sources`, `links`, `attachments`, `original`, `matched_stems`,
  `grades`, `updated_at` (время последнего изменения записи — пригодится при upsert).

## Пример строки (hh)

```json
{"id":"hh:138309032","source":"hh","url":"https://hh.ru/vacancy/138309032","title":"Middle QA Engineer","company":"Информационные Технологии","text":"О проекте\n\n…","salary_from":80000,"salary_to":140000,"currency":"RUB","salary_period":"month","salary_gross":true,"format":"remote","city":"Москва","grade":"middle","stack":["QA","Тестирование API"],"contacts":[],"apply_urls":["https://hh.ru/vacancy/138309032"],"same_as":[],"posted_at":"2026-10-09T15:20:51+03:00","first_seen_at":"2026-10-19T09:02:41+03:00","archived":false,"search_name":"qa-remote","raw":{"hh_id":138309032,"letter_required":true,"has_tests":false}}
```
