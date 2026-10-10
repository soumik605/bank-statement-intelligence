# backend/app/main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Bank Statement Intelligence")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000"],
                   allow_methods=["*"], allow_headers=["*"])

@app.get("/health")
def health():
    return {"status": "ok", "version": "0.1.0"}

