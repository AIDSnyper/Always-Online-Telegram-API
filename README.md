# Telegram Presence Manager API

A FastAPI-based backend service that manages multiple Telegram user accounts using Telethon.  
It supports authentication, session persistence, and keeping accounts online in the background with live status tracking.

---

## 🚀 Features

- Multi-account Telegram session management
- Login with verification code + 2FA support
- Persistent session storage (`/sessions`)
- Keep accounts online in background
- REST API control panel
- Account status tracking (online/offline/authenticated)
- JSON-based account database
- Restart / stop / monitor accounts
- FastAPI automatic docs (`/docs`)

---

## 🧰 Tech Stack

- FastAPI
- Telethon
- AsyncIO
- Python 3.10+
- Uvicorn
- dotenv

---

## 📦 Installation

```bash
git clone https://github.com/yourname/telegram-presence-manager.git
cd telegram-presence-manager

python -m venv venv
source venv/bin/activate

pip install -r requirements.txt
