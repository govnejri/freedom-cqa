# CQA — Conversation Quality Analytics (Freedom Broker Hackathon)

Прототип автоматической оценки чатов и звонков операторов по настраиваемому чек-листу:
оценка с репликами-доказательствами, апелляция и пересмотр, редактор чек-листа.

- Задание: [docs/task.txt](docs/task.txt)
- Чек-лист: [docs/checklist.txt](docs/checklist.txt), [PDF](docs/CQA_Chat_Quality_Checklist%201.pdf)
- Концепция: [CQA Freedom Broker — концепция проекта.md](CQA%20Freedom%20Broker%20—%20концепция%20проекта.md)

## Данные

```bash
cp .env.example .env   # вставить ссылку API из гайда
set -a; . ./.env; set +a
mkdir -p data && curl -sS "${CQA_API_URL}&group_id=${CQA_GROUP_ID}" -o data/dev.json
```

`data/` и `.env` в git не попадают: ссылка API и каждый `audio_url` содержат подпись `sig=`.

## Запуск

_TBD_
