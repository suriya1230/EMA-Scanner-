# EMA Scanner — Backend (Phase 1)

Real-time Binance USDT Futures EMA crossover scanner.
FastAPI + PostgreSQL + async WebSockets.

---

## Local Development Setup

### 1. Prerequisites

- Python 3.12+
- PostgreSQL 14+ running locally

### 2. Create PostgreSQL Database

```sql
psql -U postgres
CREATE DATABASE ema_scanner;
\q
```

### 3. Clone & Create Virtual Environment

```bash
cd ema_scanner
python -m venv venv
source venv/bin/activate        # Mac/Linux
venv\Scripts\activate           # Windows
```

### 4. Install Dependencies

```bash
pip install -r requirements.txt
```

### 5. Configure .env

Open `.env` and update your PostgreSQL credentials:

```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/ema_scanner
```

Default assumes user=postgres, password=postgres, host=localhost, port=5432.

### 6. Run Database Migrations

```bash
alembic upgrade head
```

### 7. Start the Server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Visit: http://localhost:8000/docs

---

## Project Structure

```
ema_scanner/
├── .env                        ← Your local config (edit this)
├── .env.example                ← Template reference
├── requirements.txt
├── alembic.ini
├── alembic/
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
├── app/
│   ├── main.py                 ← FastAPI app + startup
│   ├── core/
│   │   └── config.py           ← Settings from .env
│   ├── db/
│   │   └── database.py         ← Async SQLAlchemy engine
│   ├── models/
│   │   └── models.py           ← Candle + Signal tables
│   ├── schemas/
│   │   └── schemas.py          ← Pydantic response models
│   ├── services/
│   │   ├── binance_rest.py     ← REST API client
│   │   ├── ema_engine.py       ← EMA calc + crossover detection
│   │   ├── repository.py       ← DB read/write layer
│   │   └── scanner_service.py  ← Main orchestrator
│   ├── api/
│   │   └── scanner.py          ← API endpoints
│   └── websocket/
│       └── ws_manager.py       ← Binance WebSocket streams
└── tests/
    ├── conftest.py
    └── test_ema_engine.py
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/scanner` | Main scanner table |
| GET | `/api/scanner/{symbol}` | Single symbol detail |
| GET | `/api/signals` | Recent signals |
| GET | `/api/status` | Health + stats |
| GET | `/api/symbols` | All tracked symbols |
| GET | `/health` | Liveness check |
| GET | `/docs` | Swagger UI |

### Scanner Table Example

```
GET /api/scanner?trend=Bullish&limit=50
GET /api/scanner?signal=BUY
GET /api/signals?symbol=BTCUSDT&signal_type=BUY
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## .env Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | postgresql+asyncpg://postgres:postgres@localhost:5432/ema_scanner | PostgreSQL connection |
| `MIN_VOLUME_USDT_COLLECT` | 0 | Min 24H volume to fetch/store candles for (0 = no filter, all coins) |
| `MIN_VOLUME_USDT_SIGNAL` | 10000000 | Min 24H volume to run EMA/signal generation on |
| `CANDLES_LIMIT` | 3000 | Candles kept per symbol |
| `EMA_SHORT` | 7 | EMA 7 period |
| `EMA_MID` | 25 | EMA 25 period |
| `EMA_LONG` | 99 | EMA 99 period |
| `CONVERGENCE_WINDOW` | 3 | Candles window for signal |
| `DEBUG` | false | SQLAlchemy query logging |
