web: cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT
worker-high: cd backend && python -m rq worker high --url $REDIS_URL
worker-default: cd backend && python -m rq worker default --url $REDIS_URL