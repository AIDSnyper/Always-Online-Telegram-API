import asyncio
import os
import json
import threading
from datetime import datetime
from typing import Dict, List
from dotenv import load_dotenv
from telethon import TelegramClient, functions
from telethon.errors import SessionPasswordNeededError
from fastapi import FastAPI, HTTPException
import uvicorn
import signal
import sys

# Load environment variables
load_dotenv()

# ===== CONFIG =====
API_ID = int(os.getenv("API_ID", "39241212"))
API_HASH = os.getenv("API_HASH", "a02f1278bb11b991b0123ca03870be33")

os.makedirs("sessions", exist_ok=True)

# ===== GLOBAL STATE =====
class AccountState:
    def __init__(self):
        self.accounts: Dict[str, dict] = {}
        self.running_tasks: Dict[str, asyncio.Task] = {}
        self.file = "accounts.json"
        self.load()

    def load(self):
        if os.path.exists(self.file):
            with open(self.file, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    self.accounts = {acc["phone"]: acc for acc in data}
                else:
                    self.accounts = data
        else:
            self.accounts = {}

    def save(self):
        with open(self.file, "w") as f:
            json.dump(self.accounts, f, indent=2)

    def update_account(self, phone, status, session_exists=True):
        self.accounts[phone] = {
            "phone": phone,
            "status": status,
            "session_exists": session_exists,
            "last_updated": datetime.now().isoformat(),
            "is_running": status == "online"
        }
        self.save()

    def get_account(self, phone):
        return self.accounts.get(phone, {"status": "not_found"})

state = AccountState()

# ===== TELEGRAM AGENT =====
class TelegramAgent:
    def __init__(self, phone):
        self.phone = phone
        self.client = None
        self._running = False

    @property
    def is_running(self):
        return self._running

    async def authenticate(self):
        """Authenticate once, saves session for later use"""
        try:
            print(f"\n{'='*60}")
            print(f"🔐 AUTHENTICATING: {self.phone}")
            print(f"{'='*60}")

            self.client = TelegramClient(
                f"sessions/{self.phone}",
                API_ID,
                API_HASH
            )

            await self.client.connect()

            if not await self.client.is_user_authorized():
                print(f"\n📱 PHONE: {self.phone}")
                print("📲 Telegram will send you a verification code")
                print("💬 Check your Telegram app for the code")
                print("🔄 Waiting for code input...")

                try:
                    await self.client.start(phone=self.phone)
                    print(f"\n✅ SUCCESS! {self.phone} is now authenticated")
                    return True
                except SessionPasswordNeededError:
                    print("\n🔒 Account has 2FA password")
                    password = input("Enter 2FA password: ")
                    await self.client.start(phone=self.phone, password=password)
                    print(f"\n✅ SUCCESS! {self.phone} authenticated with 2FA")
                    return True
                except Exception as e:
                    print(f"\n❌ Authentication error: {e}")
                    return False
            else:
                print(f"\n✅ Session exists for {self.phone}")
                return True

        except Exception as e:
            print(f"\n❌ Authentication failed: {e}")
            return False
        finally:
            if self.client:
                await self.client.disconnect()

    async def _keep_online_task(self):
        """Background task to keep account online"""
        if self._running:
            print(f"⚠️  {self.phone} is already running")
            return False

        print(f"\n▶️  Starting online mode for {self.phone}")
        self._running = True

        try:
            self.client = TelegramClient(
                f"sessions/{self.phone}",
                API_ID,
                API_HASH
            )

            await self.client.connect()

            if not await self.client.is_user_authorized():
                print(f"❌ No valid session for {self.phone}")
                self._running = False
                return False

            state.update_account(self.phone, "online")

            while self._running:
                try:
                    # Set online status
                    await self.client(functions.account.UpdateStatusRequest(
                        offline=False
                    ))

                    # Get user info
                    user = await self.client.get_me()

                    current_time = datetime.now().strftime("%H:%M:%S")
                    print(f"🟢 {self.phone} - Online ({current_time})")

                    # Wait 5 minutes
                    for i in range(300):  # 300 seconds
                        if not self._running:
                            break
                        await asyncio.sleep(1)

                except Exception as e:
                    print(f"⚠️  {self.phone} error: {e}")
                    await asyncio.sleep(60)

        except Exception as e:
            print(f"❌ Online task error: {e}")
            return False
        finally:
            self._running = False
            state.update_account(self.phone, "offline")
            if self.client:
                try:
                    await self.client.disconnect()
                except:
                    pass
            print(f"🛑 Online mode stopped for {self.phone}")

        return True

    def start_online(self):
        """Start online mode - returns immediately"""
        if self._running:
            return False

        # Create and start task
        task = asyncio.create_task(self._keep_online_task())
        state.running_tasks[self.phone] = task
        return True

    async def stop_online(self):
        """Stop online mode"""
        self._running = False

        # Cancel the task
        if self.phone in state.running_tasks:
            task = state.running_tasks[self.phone]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            del state.running_tasks[self.phone]

        state.update_account(self.phone, "offline")
        return True

# ===== ASYNC MANAGER =====
class AsyncManager:
    def __init__(self):
        self.agents: Dict[str, TelegramAgent] = {}
        self.loop = asyncio.get_event_loop()

    def get_agent(self, phone):
        if phone not in self.agents:
            self.agents[phone] = TelegramAgent(phone)
        return self.agents[phone]

    async def authenticate(self, phone):
        agent = self.get_agent(phone)
        success = await agent.authenticate()
        if success:
            state.update_account(phone, "authenticated", True)
        return success

    async def start_online(self, phone):
        agent = self.get_agent(phone)

        # Check if session exists
        account = state.get_account(phone)
        if not account.get("session_exists", False):
            print(f"⚠️  No session found for {phone}. Please authenticate first.")
            return False

        # Start online mode directly (not in thread)
        success = await agent._keep_online_task()  # Changed this line

        return success

    async def stop_online(self, phone):
        if phone in self.agents:
            agent = self.agents[phone]
            return await agent.stop_online()
        return False

# ===== FASTAPI APP =====
app = FastAPI(title="Telegram Online Manager")
manager = AsyncManager()

# Store background tasks
background_tasks = {}

def start_background_loop(loop: asyncio.AbstractEventLoop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

# Create background loop
bg_loop = asyncio.new_event_loop()
bg_thread = threading.Thread(target=start_background_loop, args=(bg_loop,), daemon=True)
bg_thread.start()

@app.on_event("startup")
async def startup():
    print("\n" + "="*60)
    print("🚀 TELEGRAM ONLINE MANAGER STARTED")
    print("="*60)
    print(f"📡 API: http://localhost:8000")
    print(f"📚 Docs: http://localhost:8000/docs")
    print("="*60 + "\n")

@app.get("/")
async def root():
    return {
        "app": "Telegram Online Manager",
        "status": "running",
        "time": datetime.now().isoformat(),
        "endpoints": {
            "authenticate": "POST /auth/{phone}",
            "start": "POST /start/{phone}",
            "stop": "POST /stop/{phone}",
            "status": "GET /status/{phone}",
            "all": "GET /status"
        },
        "note": "Check terminal for verification prompts when authenticating"
    }

@app.post("/auth/{phone}")
async def authenticate(phone: str):
    """Authenticate account (once, with terminal input)"""
    print(f"\n📋 Authentication requested for: {phone}")
    print("⚠️  Check terminal for verification code input!")

    # Run authentication directly
    success = await manager.authenticate(phone)

    return {
        "success": success,
        "message": f"Authentication {'successful' if success else 'failed'} for {phone}",
    }

@app.post("/start/{phone}")
async def start_online(phone: str):
    """Start keeping account online 24/7"""
    # Check if authenticated
    account = state.get_account(phone)
    if not account.get("session_exists", False):
        raise HTTPException(
            status_code=400,
            detail=f"Account {phone} not authenticated. Use /auth/{phone} first"
        )

    # Start in background task
    asyncio.create_task(manager.start_online(phone))

    return {
        "success": True,
        "message": f"{phone} online mode started in background",
        "note": "Check terminal for status updates"
    }

@app.post("/stop/{phone}")
async def stop_online(phone: str):
    """Stop keeping account online"""
    success = await manager.stop_online(phone)

    if success:
        return {
            "success": True,
            "message": f"{phone} online mode stopped"
        }
    else:
        raise HTTPException(
            status_code=404,
            detail=f"Account {phone} not found or not running"
        )

@app.get("/status/{phone}")
async def get_status(phone: str):
    """Get account status"""
    account = state.get_account(phone)
    agent = manager.agents.get(phone)

    return {
        "phone": phone,
        "status": account.get("status", "not_found"),
        "session_exists": account.get("session_exists", False),
        "is_running": agent.is_running if agent else False,
        "last_updated": account.get("last_updated"),
        "timestamp": datetime.now().isoformat()
    }

@app.get("/status")
async def get_all_status():
    """Get all accounts status"""
    accounts = []
    for phone, data in state.accounts.items():
        agent = manager.agents.get(phone)
        accounts.append({
            "phone": phone,
            "status": data.get("status", "unknown"),
            "session_exists": data.get("session_exists", False),
            "is_running": agent.is_running if agent else False,
            "last_updated": data.get("last_updated")
        })

    return {
        "timestamp": datetime.now().isoformat(),
        "total_accounts": len(accounts),
        "online_count": len([a for a in accounts if a.get("is_running")]),
        "accounts": accounts
    }

@app.get("/accounts/all")
async def get_all_accounts_detailed():
    """Get detailed info about all accounts from JSON file"""
    try:
        # Load from JSON file
        if os.path.exists("accounts.json"):
            with open("accounts.json", "r") as f:
                data = json.load(f)

            # Convert dict to list if needed
            accounts = []
            if isinstance(data, dict):
                for phone, account_data in data.items():
                    agent = manager.agents.get(phone)
                    accounts.append({
                        "phone": phone,
                        "status": account_data.get("status", "unknown"),
                        "session_exists": account_data.get("session_exists", False),
                        "is_running": agent.is_running if agent else False,
                        "last_updated": account_data.get("last_updated")
                    })
            else:
                # If it's already a list
                accounts = data

            online_count = len([a for a in accounts if isinstance(a, dict) and a.get("is_running")])

            return {
                "accounts": accounts,
                "total": len(accounts),
                "online": online_count,
                "offline": len(accounts) - online_count
            }
        else:
            return {
                "accounts": [],
                "total": 0,
                "online": 0,
                "offline": 0
            }
    except Exception as e:
        print(f"Error loading accounts: {e}")
        return {
            "accounts": [],
            "total": 0,
            "online": 0,
            "offline": 0
        }
@app.post("/restart/{phone}")
async def restart_account(phone: str):
    """Restart online mode"""
    # Stop if running
    await manager.stop_online(phone)
    await asyncio.sleep(2)

    # Start again
    success = await manager.start_online(phone)

    if success:
        return {
            "success": True,
            "message": f"{phone} restarted"
        }
    else:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to restart {phone}"
        )

# Cleanup on shutdown
@app.on_event("shutdown")
async def shutdown():
    print("\n🛑 Shutting down...")
    for phone, agent in manager.agents.items():
        if agent.is_running:
            await agent.stop_online()

# ===== MAIN =====
if __name__ == "__main__":
    # Handle Ctrl+C
    def signal_handler(sig, frame):
        print("\n\n🛑 Shutting down gracefully...")
        # Stop all agents
        for phone, agent in manager.agents.items():
            if agent.is_running:
                asyncio.run(agent.stop_online())
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print("""
    ╔══════════════════════════════════════════════════╗
    ║          TELEGRAM ONLINE MANAGER v2.0           ║
    ║      ------------------------------------       ║
    ║      Keep multiple accounts online 24/7         ║
    ╚══════════════════════════════════════════════════╝
    
    📌 HOW TO USE:
    1. First authenticate: POST /auth/+998901234567
    2. Check TERMINAL for verification code
    3. Then start: POST /start/+998901234567
    4. Check status: GET /status
    
    📌 VERIFICATION:
    • When authenticating, switch to TERMINAL
    • Enter the code sent to your Telegram
    • For 2FA, enter password in terminal
    
    ⚡ SERVER STARTING...
    """)

    # Start the server
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )