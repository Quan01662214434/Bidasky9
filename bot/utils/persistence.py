"""
Persistence layer cho python-telegram-bot ConversationHandler.
Lưu conversation state và user_data vào bảng user_states (PostgreSQL).
Giúp bot không mất bước nhập liệu khi Render restart.
"""

import json
import logging
from collections import defaultdict
from typing import Any, Dict, Optional, Tuple

from telegram.ext import BasePersistence, PersistenceInput

from bot.models.database import get_connection, now_utc_iso

logger = logging.getLogger(__name__)


class PostgresPersistence(BasePersistence):
    """Lưu conversation state + user_data vào PostgreSQL.
    
    Bảng user_states:
        telegram_id BIGINT PK
        state_key   TEXT          — conversation state JSON
        state_data  TEXT          — user_data JSON
        updated_at  TEXT
    """

    def __init__(self):
        super().__init__(
            store_data=PersistenceInput(
                bot_data=False,
                chat_data=False,
                user_data=True,
                callback_data=False,
            ),
            update_interval=0,  # Write immediately
        )
        self._user_data: Dict[int, Dict] = {}
        self._conversations: Dict[str, Dict[Tuple[int, int], int]] = defaultdict(dict)
        self._loaded = False

    def _load_all(self):
        """Load all state from database into memory."""
        if self._loaded:
            return
        try:
            conn = get_connection()
            rows = conn.execute("SELECT telegram_id, state_key, state_data FROM user_states").fetchall()
            for row in rows:
                uid = row["telegram_id"]
                # Load user_data
                if row["state_data"]:
                    try:
                        self._user_data[uid] = json.loads(row["state_data"])
                    except (json.JSONDecodeError, TypeError):
                        self._user_data[uid] = {}
                # Load conversation states
                if row["state_key"]:
                    try:
                        conv_states = json.loads(row["state_key"])
                        for conv_name, state in conv_states.items():
                            # Each state maps (user_id, chat_id) → state int
                            self._conversations[conv_name][(uid, uid)] = state
                    except (json.JSONDecodeError, TypeError):
                        pass
            self._loaded = True
            logger.info("Loaded persistence for %d users", len(rows))
        except Exception as e:
            logger.error("Failed to load persistence: %s", e)
            self._loaded = True  # Don't retry on error

    def _save_user(self, user_id: int):
        """Save one user's state to database."""
        try:
            conn = get_connection()
            # Collect conversation states for this user
            conv_states = {}
            for conv_name, states in self._conversations.items():
                key = (user_id, user_id)
                if key in states:
                    conv_states[conv_name] = states[key]
            
            state_key = json.dumps(conv_states, ensure_ascii=False) if conv_states else None
            state_data = json.dumps(self._user_data.get(user_id, {}), ensure_ascii=False, default=str)
            now = now_utc_iso()

            # Upsert
            conn.execute(
                """INSERT INTO user_states (telegram_id, state_key, state_data, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (telegram_id) DO UPDATE SET
                   state_key = EXCLUDED.state_key,
                   state_data = EXCLUDED.state_data,
                   updated_at = EXCLUDED.updated_at""",
                (user_id, state_key, state_data, now)
            )
        except Exception as e:
            logger.error("Failed to save state for user %s: %s", user_id, e)

    # ─── Required interface methods ─────────────────────

    async def get_user_data(self) -> Dict[int, Dict]:
        self._load_all()
        return self._user_data.copy()

    async def update_user_data(self, user_id: int, data: Dict) -> None:
        self._user_data[user_id] = data
        self._save_user(user_id)

    async def refresh_user_data(self, user_id: int, user_data: Dict) -> Dict:
        return self._user_data.get(user_id, {})

    async def drop_user_data(self, user_id: int) -> None:
        self._user_data.pop(user_id, None)
        try:
            conn = get_connection()
            conn.execute("DELETE FROM user_states WHERE telegram_id = ?", (user_id,))
        except Exception as e:
            logger.error("Failed to drop user state: %s", e)

    async def get_conversations(self, name: str) -> Dict[Tuple[int, int], int]:
        self._load_all()
        return self._conversations.get(name, {}).copy()

    async def update_conversation(
        self, name: str, key: Tuple[int, int], new_state: Optional[int]
    ) -> None:
        if new_state is None:
            self._conversations[name].pop(key, None)
        else:
            self._conversations[name][key] = new_state
        # Save the user whose conversation changed
        user_id = key[0]
        self._save_user(user_id)

    # ─── Not used (disabled in PersistenceInput) ────────

    async def get_bot_data(self) -> Dict:
        return {}

    async def update_bot_data(self, data: Dict) -> None:
        pass

    async def refresh_bot_data(self, bot_data: Dict) -> Dict:
        return {}

    async def get_chat_data(self) -> Dict[int, Dict]:
        return {}

    async def update_chat_data(self, chat_id: int, data: Dict) -> None:
        pass

    async def refresh_chat_data(self, chat_id: int, chat_data: Dict) -> Dict:
        return {}

    async def drop_chat_data(self, chat_id: int) -> None:
        pass

    async def get_callback_data(self):
        return None

    async def update_callback_data(self, data) -> None:
        pass

    async def flush(self) -> None:
        """Save all dirty data."""
        for user_id in self._user_data:
            self._save_user(user_id)
