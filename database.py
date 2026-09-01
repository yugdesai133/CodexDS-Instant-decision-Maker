
import sqlite3
import math
import string
import random
from contextlib import contextmanager
from typing import List, Dict, Optional, Any

DB_NAME = "decision_maker.db"


@contextmanager
def get_db():
    """Thread-safe context manager for database connections."""
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")  # High concurrency & speed
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.row_factory = sqlite3.Row  # Access columns by name
    try:
        yield conn
    finally:
        conn.close()


def init_database():
    """Initializes all 4 tables with constraints and performance indexes."""
    with get_db() as conn:
        cursor = conn.cursor()

        # 1. ROOMS TABLE
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS rooms (
            room_code VARCHAR(6) PRIMARY KEY,
            host_name VARCHAR(50) NOT NULL,
            question TEXT NOT NULL,
            voting_method VARCHAR(20) NOT NULL CHECK(voting_method IN ('swipe', 'quadratic')),
            total_participants INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 2. OPTIONS TABLE
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS options (
            option_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code VARCHAR(6) NOT NULL,
            option_text TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 0.0,
            FOREIGN KEY (room_code) REFERENCES rooms (room_code) ON DELETE CASCADE
        );
        """)

        # 3. PARTICIPANTS TABLE
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            voter_id VARCHAR(36) PRIMARY KEY,           -- Unique UUID / Token per device
            room_code VARCHAR(6) NOT NULL,
            nickname VARCHAR(50) NOT NULL,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_code) REFERENCES rooms (room_code) ON DELETE CASCADE,
            UNIQUE(room_code, nickname)
        );
        """)

        # 4. VOTES TABLE (Audit Trail & Scoring Ledger)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS votes (
            vote_id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code VARCHAR(6) NOT NULL,
            voter_id VARCHAR(36) NOT NULL,
            option_id INTEGER NOT NULL,
            swipe_action VARCHAR(10) CHECK(swipe_action IN ('LIKE', 'PASS', NULL)),
            raw_credits INTEGER CHECK(raw_credits >= 0 AND raw_credits <= 16),
            effective_weight REAL NOT NULL DEFAULT 0.0,
            voted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (room_code) REFERENCES rooms (room_code) ON DELETE CASCADE,
            FOREIGN KEY (voter_id) REFERENCES participants (voter_id) ON DELETE CASCADE,
            FOREIGN KEY (option_id) REFERENCES options (option_id) ON DELETE CASCADE,
            UNIQUE(voter_id, option_id)                  -- Prevents duplicate voting on same option
        );
        """)

        # Performance Indexes for Instant Lookups
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_options_room ON options(room_code);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_participants_room ON participants(room_code);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_votes_room ON votes(room_code);")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_votes_option ON votes(option_id);")

        conn.commit()


# =====================================================================
# 1. ROOM OPERATIONS
# =====================================================================

def create_room(host_name: str, question: str, voting_method: str, options: List[str]) -> str:
    """
    Creates a new room with user-inputted host, question, method, and initial options.
    Returns the unique 4-character room code.
    """
    voting_method = voting_method.strip().lower()
    if voting_method not in ("swipe", "quadratic"):
        raise ValueError("voting_method must be either 'swipe' or 'quadratic'")

    # Generate 4-character alphanumeric code
    chars = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    room_code = "".join(random.choices(chars, k=4))

    with get_db() as conn:
        cursor = conn.cursor()

        # Insert Room
        cursor.execute("""
            INSERT INTO rooms (room_code, host_name, question, voting_method, total_participants)
            VALUES (?, ?, ?, ?, 0);
        """, (room_code, host_name.strip(), question.strip(), voting_method))

        # Insert Dynamic Options
        for opt in options:
            cleaned = opt.strip()
            if cleaned:
                cursor.execute("""
                    INSERT INTO options (room_code, option_text, score)
                    VALUES (?, ?, 0.0);
                """, (room_code, cleaned))

        conn.commit()

    return room_code


def get_room_details(room_code: str) -> Optional[Dict[str, Any]]:
    """Fetches room information, participant count, and all options."""
    with get_db() as conn:
        cursor = conn.cursor()
        room = cursor.execute("SELECT * FROM rooms WHERE room_code = ?;", (room_code.upper(),)).fetchone()
        if not room:
            return None

        options = cursor.execute("""
            SELECT option_id, option_text, score
            FROM options
            WHERE room_code = ?
            ORDER BY option_id ASC;
        """, (room_code.upper(),)).fetchall()

        return {
            "room_code": room["room_code"],
            "host_name": room["host_name"],
            "question": room["question"],
            "voting_method": room["voting_method"],
            "total_participants": room["total_participants"],
            "created_at": room["created_at"],
            "options": [dict(opt) for opt in options]
        }


# =====================================================================
# 2. PARTICIPANT OPERATIONS
# =====================================================================

def add_participant(room_code: str, voter_id: str, nickname: str) -> Dict[str, Any]:
    """
    Registers a participant under a room with their unique voter_id and nickname.
    Updates the room's total_participants count automatically.
    """
    with get_db() as conn:
        cursor = conn.cursor()

        # Check if room exists
        room = cursor.execute("SELECT 1 FROM rooms WHERE room_code = ?;", (room_code.upper(),)).fetchone()
        if not room:
            raise ValueError(f"Room {room_code} does not exist.")

        # Insert participant (or ignore if already joined with same voter_id)
        cursor.execute("""
            INSERT OR IGNORE INTO participants (voter_id, room_code, nickname)
            VALUES (?, ?, ?);
        """, (voter_id, room_code.upper(), nickname.strip()))

        # Update live participant counter
        cursor.execute("""
            UPDATE rooms 
            SET total_participants = (SELECT COUNT(*) FROM participants WHERE room_code = ?)
            WHERE room_code = ?;
        """, (room_code.upper(), room_code.upper()))

        conn.commit()

        participant = cursor.execute("""
            SELECT voter_id, room_code, nickname, joined_at 
            FROM participants 
            WHERE voter_id = ?;
        """, (voter_id,)).fetchone()

        return dict(participant)


# =====================================================================
# 3. VOTING OPERATIONS (SWIPE & QUADRATIC)
# =====================================================================

def record_swipe_vote(room_code: str, voter_id: str, option_id: int, is_like: bool) -> Dict[str, Any]:
    """
    Swipe Voting Logic[cite: 5]:
    - Right Swipe (is_like=True)  -> effective_weight = 1.0, score increases by 1[cite: 5].
    - Left Swipe  (is_like=False) -> effective_weight = 0.0, score remains unchanged[cite: 5].
    """
    swipe_action = "LIKE" if is_like else "PASS"
    effective_weight = 1.0 if is_like else 0.0

    with get_db() as conn:
        cursor = conn.cursor()

        # Record or update vote transaction
        cursor.execute("""
            INSERT INTO votes (room_code, voter_id, option_id, swipe_action, raw_credits, effective_weight)
            VALUES (?, ?, ?, ?, 1, ?)
            ON CONFLICT(voter_id, option_id) DO UPDATE SET
                swipe_action = excluded.swipe_action,
                effective_weight = excluded.effective_weight,
                voted_at = CURRENT_TIMESTAMP;
        """, (room_code.upper(), voter_id, option_id, swipe_action, effective_weight))

        # Re-aggregate score for the option
        cursor.execute("""
            UPDATE options
            SET score = (
                SELECT COALESCE(SUM(effective_weight), 0.0) 
                FROM votes 
                WHERE option_id = ?
            )
            WHERE option_id = ?;
        """, (option_id, option_id))

        conn.commit()

    return {
        "status": "success",
        "mode": "swipe",
        "option_id": option_id,
        "swipe_action": swipe_action,
        "effective_weight": effective_weight
    }


def record_quadratic_vote(room_code: str, voter_id: str, option_id: int, raw_credits: int) -> Dict[str, Any]:
    """
    Quadratic Voting Logic[cite: 5]:
    - raw_credits between 1 and 16[cite: 5].
    - effective_weight = sqrt(raw_credits) added to the option score[cite: 5].
    """
    if raw_credits < 0 or raw_credits > 16:
        raise ValueError("raw_credits must be an integer between 0 and 16.")

    # Calculate effective weight via square root
    effective_weight = round(math.sqrt(raw_credits), 2)

    with get_db() as conn:
        cursor = conn.cursor()

        # Record or update vote transaction
        cursor.execute("""
            INSERT INTO votes (room_code, voter_id, option_id, swipe_action, raw_credits, effective_weight)
            VALUES (?, ?, ?, 'ALLOCATE', ?, ?)
            ON CONFLICT(voter_id, option_id) DO UPDATE SET
                raw_credits = excluded.raw_credits,
                effective_weight = excluded.effective_weight,
                voted_at = CURRENT_TIMESTAMP;
        """, (room_code.upper(), voter_id, option_id, raw_credits, effective_weight))

        # Re-aggregate score for the option
        cursor.execute("""
            UPDATE options
            SET score = (
                SELECT COALESCE(SUM(effective_weight), 0.0) 
                FROM votes 
                WHERE option_id = ?
            )
            WHERE option_id = ?;
        """, (option_id, option_id))

        conn.commit()

    return {
        "status": "success",
        "mode": "quadratic",
        "option_id": option_id,
        "raw_credits": raw_credits,
        "effective_weight": effective_weight
    }


# =====================================================================
# 4. RESULTS & AUDIT QUERIES
# =====================================================================

def get_room_leaderboard(room_code: str) -> Dict[str, Any]:
    """Fetches real-time leaderboard sorted by highest score."""
    with get_db() as conn:
        cursor = conn.cursor()
        
        room = cursor.execute("SELECT * FROM rooms WHERE room_code = ?;", (room_code.upper(),)).fetchone()
        if not room:
            raise ValueError("Room not found")

        options = cursor.execute("""
            SELECT option_id, option_text, score
            FROM options
            WHERE room_code = ?
            ORDER BY score DESC;
        """, (room_code.upper(),)).fetchall()

        opts_list = [dict(opt) for opt in options]
        winner = opts_list[0] if opts_list and opts_list[0]["score"] > 0 else None

        return {
            "room_code": room["room_code"],
            "question": room["question"],
            "voting_method": room["voting_method"],
            "total_participants": room["total_participants"],
            "winner": winner,
            "leaderboard": opts_list
        }


# =====================================================================
# SELF-TEST SCRIPT
# =====================================================================

if __name__ == "__main__":
    print("--- Initializing Database ---")
    init_database()

    print("\n1. Testing Room Creation...")
    code = create_room(
        host_name="Saksham",
        question="Where should the team eat dinner?",
        voting_method="quadratic",
        options=["Dominos", "Subway", "Taco Bell"]
    )
    print(f"Room Created! Code: {code}")

    print("\n2. Testing Participant Joining...")
    p1 = add_participant(code, "voter-uuid-001", "Ashish")
    p2 = add_participant(code, "voter-uuid-002", "Tanmay")
    print(f"Participants joined: {p1['nickname']}, {p2['nickname']}")

    room_info = get_room_details(code)
    opt1_id = room_info["options"][0]["option_id"]
    opt2_id = room_info["options"][1]["option_id"]

    print("\n3. Testing Quadratic Votes...")
    # Ashish gives 9 credits to Dominos (sqrt(9) = 3.0 points)
    v1 = record_quadratic_vote(code, "voter-uuid-001", opt1_id, raw_credits=9)
    # Tanmay gives 4 credits to Dominos (sqrt(4) = 2.0 points) and 16 to Subway (sqrt(16) = 4.0 points)
    v2 = record_quadratic_vote(code, "voter-uuid-002", opt1_id, raw_credits=4)
    v3 = record_quadratic_vote(code, "voter-uuid-002", opt2_id, raw_credits=16)
    print(f"Vote 1: {v1}")
    print(f"Vote 2: {v2}")
    print(f"Vote 3: {v3}")

    print("\n4. Leaderboard / Winner Results:")
    leaderboard = get_room_leaderboard(code)
    print(f"Winner: {leaderboard['winner']}")
    for rank, item in enumerate(leaderboard["leaderboard"], 1):
        print(f"  #{rank} {item['option_text']} -> Score: {item['score']}")