[README.md](https://github.com/user-attachments/files/33420621/README.md)
FinTrack — secure personal finance tracker

FinTrack tracks accounts, transactions, budgets, goals, and investments. This copy adds local AI categorization with Ollama, data-driven financial insights, and HTTPS through Caddy. It does not need a paid AI API key.

Cash accounts may use PHP or a supported foreign currency. Enter current and transaction exchange rates manually; reports and budgets remain in PHP. Forex pairs in Portfolio are tracked separately from foreign cash balances.


  Project layout

- backend — FastAPI, PostgreSQL access, local Ollama adapter
- frontend — React and Vite app, same-origin API client
- sql — schema, database routines, safe reference seed, optional demo data
- frontend/Caddyfile — HTTPS, API proxy, and SPA routing
- docker-compose.yml — PostgreSQL, Ollama, backend, and Caddy web service
