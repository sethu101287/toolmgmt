# Toolmgmt Docker Migration Guide

## 1) Copy project to target server
- Copy the `toolmgmt` folder to the Docker server.

## 2) Prepare environment
- In the server shell, go to the project directory.
- Create `.env` from `.env.example` and set Oracle password.

```bash
cp .env.example .env
# edit .env and set ORACLE_PASSWORD
```

## 3) Optional: migrate existing SQLite data from laptop
- Copy `database.db` from old machine into the Docker volume using one of the options below.

Option A (before first start):
- Place old `database.db` in project folder temporarily.
- Start container once, then copy DB into named volume:

```bash
docker compose up -d --build
docker compose stop
docker run --rm -v toolmgmt_toolmgmt_data:/data -v $(pwd):/src alpine sh -c "cp /src/database.db /data/database.db"
docker compose up -d
```

Option B (fresh instance):
- Skip this step; app auto-creates an empty database.

## 4) Build and run

```bash
docker compose up -d --build
```

## 5) Verify

```bash
docker compose ps
docker compose logs -f toolmgmt
```

Access app:
- http://<server-ip>:5000

## 6) Upgrade workflow

```bash
docker compose pull || true
docker compose up -d --build
```

## Notes
- Database is persisted in named volume `toolmgmt_toolmgmt_data`.
- Do not store secrets in source; keep them in `.env`.
- Default login can be overridden using `TOOLMGMT_DEFAULT_USERNAME` and `TOOLMGMT_DEFAULT_PASSWORD`.
