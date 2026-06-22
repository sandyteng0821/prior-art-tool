#!/usr/bin/env python3
"""
run_api.py — Uvicorn entry point for the Prior Art Tool API.

Usage:
    python run_api.py

Environment variables:
    API_HOST       — bind address (default: 0.0.0.0)
    API_PORT       — bind port (default: 8007)
    DATABASE_PATH  — path to patents.db (default: cache/patents.db)
"""

import os

import uvicorn


def main():
    host = os.environ.get("API_HOST", "0.0.0.0")
    port = int(os.environ.get("API_PORT", "8007"))

    uvicorn.run(
        "api.main:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
    