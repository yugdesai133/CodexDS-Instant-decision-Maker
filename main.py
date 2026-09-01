
import os
import sys
import sqlite3
from typing import List, Literal, Optional
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# Ensure database.py from Hackverse 2.0 is correctly imported
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(CURRENT_DIR)

try:
    import database
    # Initialize SQLite database and tables on startup
    database.init_database()
except Exception as e:
    print(f"[FATAL] Failed to initialize database: {e}")
    raise e

app = FastAPI(
    title="Instant Group Decision Maker API",
    description="High-performance, fault-tolerant backend supporting Swipe and Quadratic voting modes.",
    version="2.1.0"
)

# =====================================================================
# 1. CORS MIDDLEWARE
# =====================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================================================================
# 2. PYDANTIC REQUEST VALIDATION SCHEMAS
# =====================================================================
class CreateRoomReq(BaseModel):
    host_name: str = Field(..., min_length=1, max_length=50, example="Ashish")
    question: str = Field(..., min_length=3, max_length=255, example="Where should we eat dinner?")
    voting_method: Literal["swipe", "quadratic"] = Field(..., example="quadratic")
    options: List[str] = Field(..., min_items=2, example=["Dominos", "Subway", "Taco Bell"])

class JoinRoomReq(BaseModel):
    voter_id: str = Field(..., min_length=3, max_length=50, example="uuid-device-token-123")
    nickname: str = Field(..., min_length=1, max_length=50, example="Tanmay")

class SwipeVoteReq(BaseModel):
    voter_id: str = Field(..., example="uuid-device-token-123")
    option_id: int = Field(..., example=1)
    is_like: bool = Field(..., example=True)

class QuadraticVoteReq(BaseModel):
    voter_id: str = Field(..., example="uuid-device-token-123")
    option_id: int = Field(..., example=1)
    raw_credits: int = Field(..., ge=1, le=16, example=9)

# =====================================================================
# 3. GLOBAL EXCEPTION HANDLERS
# =====================================================================
@app.exception_handler(sqlite3.Error)
async def sqlite_exception_handler(request: Request, exc: sqlite3.Error):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"status": "error", "type": "DatabaseError", "detail": f"Database operation failed: {str(exc)}"}
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"status": "error", "type": "UnhandledServerException", "detail": str(exc)}
    )

# =====================================================================
# 4. API ENDPOINTS & ROUTERS
# =====================================================================

@app.get("/api/health", tags=["Health"])
def health_check():
    """Health check route to verify backend and database accessibility."""
    try:
        with database.get_db() as conn:
            conn.execute("SELECT 1;")
        return {"status": "healthy", "service": "Instant Decision Maker API", "database": "connected"}
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database connectivity issue: {str(e)}"
        )


# --- ROOM MANAGEMENT ---
@app.post("/api/rooms", status_code=status.HTTP_201_CREATED, tags=["Room Management"])
def api_create_room(payload: CreateRoomReq):
    """
    Creates a new dynamic decision room and registers user options.
    """
    try:
        cleaned_options = [opt.strip() for opt in payload.options if opt.strip()]
        if len(cleaned_options) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You must provide at least 2 non-empty valid options."
            )

        code = database.create_room(
            host_name=payload.host_name.strip(),
            question=payload.question.strip(),
            voting_method=payload.voting_method,
            options=cleaned_options
        )
        return {
            "status": "success",
            "room_code": code,
            "host_name": payload.host_name.strip(),
            "question": payload.question.strip(),
            "voting_method": payload.voting_method,
            "options_count": len(cleaned_options)
        }
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except sqlite3.Error as se:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error during room creation: {str(se)}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create room: {str(e)}")


@app.get("/api/rooms/{code}", tags=["Room Management"])
def api_get_room(code: str):
    """
    Fetches details for a specific room code.
    """
    try:
        clean_code = code.upper().strip()
        room = database.get_room_details(clean_code)
        if not room:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Room '{clean_code}' does not exist or has expired."
            )
        return room
    except HTTPException:
        raise
    except sqlite3.Error as se:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error: {str(se)}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch room: {str(e)}")


# --- PARTICIPANT ENGINE ---
@app.post("/api/rooms/{code}/join", status_code=status.HTTP_200_OK, tags=["Participant Engine"])
def api_join_room(code: str, payload: JoinRoomReq):
    """
    Registers a participant under a room code with zero logins.
    """
    try:
        clean_code = code.upper().strip()
        clean_name = payload.nickname.strip()
        clean_voter_id = payload.voter_id.strip()

        if not clean_name:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nickname cannot be empty.")
        if not clean_voter_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Voter ID cannot be empty.")

        participant = database.add_participant(
            room_code=clean_code,
            voter_id=clean_voter_id,
            nickname=clean_name
        )
        return {"status": "success", "participant": participant}
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except sqlite3.Error as se:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error: {str(se)}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to join room: {str(e)}")


# --- DUAL VOTING HANDLERS ---
@app.post("/api/rooms/{code}/vote/swipe", tags=["Voting Handlers"])
def api_vote_swipe(code: str, payload: SwipeVoteReq):
    """
    Swipe voting transaction (Like = +1.0, Pass = 0.0).
    """
    try:
        clean_code = code.upper().strip()
        clean_voter_id = payload.voter_id.strip()

        # Verify room exists and check mode
        room = database.get_room_details(clean_code)
        if not room:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Room '{clean_code}' not found.")
        if room["voting_method"] != "swipe":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"This room uses '{room['voting_method']}' voting, not swipe voting."
            )

        result = database.record_swipe_vote(
            room_code=clean_code,
            voter_id=clean_voter_id,
            option_id=payload.option_id,
            is_like=payload.is_like
        )
        return result
    except HTTPException:
        raise
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except sqlite3.Error as se:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error during swipe vote: {str(se)}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to record swipe vote: {str(e)}")


@app.post("/api/rooms/{code}/vote/quadratic", tags=["Voting Handlers"])
def api_vote_quadratic(code: str, payload: QuadraticVoteReq):
    """
    Quadratic voting transaction (Enforces 1-16 credit bounds and 16-credit global budget).
    """
    try:
        clean_code = code.upper().strip()
        clean_voter_id = payload.voter_id.strip()

        # Verify room exists and check mode
        room = database.get_room_details(clean_code)
        if not room:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Room '{clean_code}' not found.")
        if room["voting_method"] != "quadratic":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"This room uses '{room['voting_method']}' voting, not quadratic voting."
            )

        result = database.record_quadratic_vote(
            room_code=clean_code,
            voter_id=clean_voter_id,
            option_id=payload.option_id,
            raw_credits=payload.raw_credits
        )
        return result
    except HTTPException:
        raise
    except ValueError as ve:
        # Catches credit budget overflow or invalid range
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(ve))
    except sqlite3.Error as se:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error during quadratic vote: {str(se)}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to record quadratic vote: {str(e)}")


@app.get("/api/rooms/{code}/budget/{voter_id}", tags=["Voting Handlers"])
def api_get_voter_budget(code: str, voter_id: str):
    """
    Fetches remaining credit balance and allocation breakdown for a user.
    """
    try:
        clean_code = code.upper().strip()
        clean_voter_id = voter_id.strip()

        # Verify room exists
        room = database.get_room_details(clean_code)
        if not room:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Room '{clean_code}' not found.")

        return database.get_voter_budget_status(clean_code, clean_voter_id)
    except HTTPException:
        raise
    except sqlite3.Error as se:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error: {str(se)}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch budget: {str(e)}")


# --- REAL-TIME POLLING & LEADERBOARD ENGINE ---
@app.get("/api/rooms/{code}/results", tags=["Polling & Leaderboard"])
def api_get_results(code: str):
    """
    Returns the real-time leaderboard and consensus winner.
    """
    try:
        clean_code = code.upper().strip()
        return database.get_room_leaderboard(clean_code)
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(ve))
    except sqlite3.Error as se:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Database error: {str(se)}")
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to fetch results: {str(e)}")
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

# Serve index.html on root access
@app.get("/")
def serve_home():
    return FileResponse(os.path.join(CURRENT_DIR, "index.html"))

# Mount the static directory so app.js is served
app.mount("/static", StaticFiles(directory=CURRENT_DIR), name="static")