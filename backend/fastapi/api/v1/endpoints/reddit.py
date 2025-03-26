import asyncpraw
import os
import json
import asyncio
import aiohttp
import logging
from fastapi import APIRouter, Depends, HTTPException, FastAPI, Header, Security
from fastapi.security.api_key import APIKeyHeader, APIKey
from pydantic import BaseModel, HttpUrl
import psycopg2
from psycopg2 import pool
from psycopg2.extras import Json
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional, Tuple, Literal, Dict, Set, List

# Create router
router = APIRouter()

# Setup logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reddit_monitor")

# API Key settings
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# Reddit API client (initialized lazily)
reddit = None

# Check if Reddit credentials are set
REDDIT_CLIENT_ID = os.getenv("CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDDIT_CREDENTIALS_AVAILABLE = bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)

if not REDDIT_CREDENTIALS_AVAILABLE:
    logger.warning(
        "Reddit API credentials not found in environment variables. "
        "Set CLIENT_ID and CLIENT_SECRET environment variables to enable Reddit monitoring."
    )

# Client keywords with groups (mutable via API)
# Structure: {"client1": {"webhook_url": "https://example.com/webhook", "groups": {"group1": {"keywords": ["python", "ai"], "subreddit": "programming"}, "group2": {"keywords": ["fastapi"], "subreddit": null}}}, "client2": {"groups": {"finance": {"keywords": ["crypto"], "subreddit": "Bitcoin"}}}}
client_keywords = {}  # Will be loaded from database at startup

# Track active subreddit streams
active_streams = {}  # {"subreddit_name": {"task": asyncio.Task, "clients": set(), "count": 0}}

# Database connection pool setup
DATABASE_URL = os.getenv("DATABASE_URL")
db_pool = None

# Initialize connection pool
if DATABASE_URL:
    try:
        # Create connection pool with min=2, max=10 connections
        db_pool = pool.ThreadedConnectionPool(minconn=2, maxconn=10, dsn=DATABASE_URL)
        logger.info("Database connection pool initialized successfully")
        
        # Initialize database schema
        conn = db_pool.getconn()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    id SERIAL PRIMARY KEY,
                    client_id TEXT,
                    group_id TEXT,
                    keyword TEXT,
                    content_text TEXT,
                    permalink TEXT,
                    subreddit TEXT,
                    timestamp TEXT,
                    content_type TEXT DEFAULT 'comment'
                )
            """)
            
            # Ensure the keys table exists (if it doesn't already)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS api_keys (
                    id SERIAL PRIMARY KEY,
                    api_key TEXT UNIQUE NOT NULL,
                    client_id TEXT NOT NULL,
                    is_admin BOOLEAN NOT NULL DEFAULT false
                )
            """)
            
            # Create table for client keywords and groups
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS client_keywords (
                    id SERIAL PRIMARY KEY,
                    client_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    keywords JSONB NOT NULL,
                    subreddit TEXT,
                    UNIQUE(client_id, group_id)
                )
            """)
            
            # Create table for client webhooks
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS client_webhooks (
                    id SERIAL PRIMARY KEY,
                    client_id TEXT UNIQUE NOT NULL,
                    webhook_url TEXT NOT NULL
                )
            """)
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Error initializing database schema: {e}")
        finally:
            # Return connection to pool
            cursor.close()
            db_pool.putconn(conn)
    except Exception as e:
        logger.error(f"Failed to initialize database connection pool: {e}")
        db_pool = None
else:
    logger.warning("DATABASE_URL not set. Database features will not be available.")

# For testing - track number of matches
match_count = 0
MAX_TEST_MATCHES = 100

# Helper function to get a connection from the pool
def get_db_connection():
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool.getconn()

# Helper function to return a connection to the pool
def release_db_connection(conn):
    if conn and db_pool:
        db_pool.putconn(conn)

# Models for keyword operations
class KeywordGroupUpdate(BaseModel):
    client_id: Optional[str] = None
    group_id: str
    keywords: list[str]
    subreddit: Optional[str] = None

class KeywordGroupCreate(BaseModel):
    client_id: Optional[str] = None
    group_id: str
    keywords: list[str]
    subreddit: Optional[str] = None

class KeywordGroupDelete(BaseModel):
    client_id: Optional[str] = None
    group_id: str

class WebhookUpdate(BaseModel):
    client_id: Optional[str] = None
    webhook_url: HttpUrl

class ApiKeyCreate(BaseModel):
    client_id: str
    api_key: Optional[str] = None  # If not provided, we'll generate one

# API Key validation function
async def validate_api_key(api_key_header: str = Security(api_key_header)) -> Tuple[str, str]:
    """
    Validates the API key against the database and returns a tuple of (api_key, client_id)
    """
    if not api_key_header:
        raise HTTPException(
            status_code=401,
            detail="API Key header missing",
            headers={"WWW-Authenticate": "API-Key"},
        )
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT api_key, client_id FROM api_keys WHERE api_key = %s", (api_key_header,))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(
                status_code=403,
                detail="Invalid API Key",
                headers={"WWW-Authenticate": "API-Key"},
            )
        
        return result[0], result[1]  # Return (api_key, client_id)
    except Exception as e:
        logger.error(f"Database error in validate_api_key: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        if conn:
            release_db_connection(conn)

async def validate_admin(auth: Tuple[str, str] = Depends(validate_api_key)):
    api_key, client_id = auth
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT is_admin FROM api_keys WHERE api_key = %s", (api_key,))
        result = cursor.fetchone()
        
        if not result or not result[0]:  # If not admin
            raise HTTPException(
                status_code=403,
                detail="Admin privileges required",
                headers={"WWW-Authenticate": "API-Key"},
            )
        
        return auth
    except Exception as e:
        logger.error(f"Database error in validate_admin: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        if conn:
            release_db_connection(conn)

async def initialize_reddit():
    """Initialize Reddit API client if credentials are available."""
    global reddit
    if not REDDIT_CREDENTIALS_AVAILABLE:
        raise HTTPException(
            status_code=503, 
            detail="Reddit API credentials not configured. Set CLIENT_ID and CLIENT_SECRET environment variables."
        )
    
    if reddit is None:
        try:
            reddit = asyncpraw.Reddit(
                client_id=REDDIT_CLIENT_ID,
                client_secret=REDDIT_CLIENT_SECRET,
                user_agent="KeywordMonitor v1.0 by /u/Keywrodeo"
            )
            logger.info("Reddit API client initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing Reddit API client: {e}")
            raise HTTPException(status_code=503, detail=f"Failed to initialize Reddit API client: {str(e)}")
    
    return reddit

async def save_match(match_data):
    """Save a match to the database."""
    if not db_pool:
        logger.warning("Database not available, match not saved")
        return
    
    conn = None
    try:
        # Truncate content_text to 200 characters
        truncated_content = match_data["content_text"][:200]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO matches (client_id, group_id, keyword, content_text, permalink, subreddit, timestamp, content_type)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            match_data["client_id"], match_data["group_id"], match_data["keyword"], truncated_content,
            match_data["permalink"], match_data["subreddit"], match_data["timestamp"], match_data["content_type"]
        ))
        conn.commit()
        logger.info(f"Match saved to database: {match_data['client_id']}/{match_data['group_id']} - {match_data['keyword']} ({match_data['content_type']})")
    except Exception as e:
        if conn:
            conn.rollback()  # Add rollback to reset transaction state
        logger.error(f"Error saving match to database: {e}")
    finally:
        if conn:
            release_db_connection(conn)

async def call_webhook(client_id, match_data):
    """Call the client's webhook URL with match data if configured."""
    webhook_url = client_keywords.get(client_id, {}).get("webhook_url")
    if not webhook_url:
        return
    
    try:
        webhook_payload = {
            "client_id": match_data["client_id"],
            "group_id": match_data["group_id"],
            "keyword": match_data["keyword"],
            "content_text": match_data["content_text"][:200],  # Truncate to 200 characters
            "permalink": match_data["permalink"],
            "subreddit": match_data["subreddit"],
            "timestamp": match_data["timestamp"],
            "content_type": match_data["content_type"]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=webhook_payload) as response:
                if response.status >= 400:
                    logger.error(f"Webhook call failed for client {client_id}: {response.status}")
                else:
                    logger.info(f"Webhook called successfully for client {client_id}")
    except Exception as e:
        logger.error(f"Error calling webhook for client {client_id}: {e}")

async def check_content_for_keywords(content_text, content_type, permalink, subreddit_obj, timestamp, subreddit_name):
    """Process content (comment or submission) and check for keyword matches."""
    
    # Lowercase the text and split into words
    content_text_lower = content_text.lower()
    content_words = set(word.strip(".,!?:;\"'()[]{}") for word in content_text_lower.split())
    
    for client_id, client_data in client_keywords.items():
        groups = client_data.get("groups", {})
        for group_id, group_data in groups.items():
            # Skip if this group is configured for a different subreddit
            group_subreddit = group_data.get("subreddit")
            # If group has no subreddit specified (None or empty string), it matches 'all'
            # If group has a specific subreddit, it should match the current subreddit
            if group_subreddit and group_subreddit != subreddit_name and subreddit_name != "all":
                continue
                
            keywords = group_data.get("keywords", [])
            for keyword in keywords:
                # Convert keyword to lowercase
                keyword_lower = keyword.lower()
                
                # Check if the keyword is a whole word in the content
                if keyword_lower in content_words:
                    match_data = {
                        "client_id": client_id,
                        "group_id": group_id,
                        "keyword": keyword,
                        "content_text": content_text,
                        "permalink": permalink,
                        "subreddit": str(subreddit_obj),
                        "timestamp": timestamp,
                        "content_type": content_type
                    }
                    await save_match(match_data)
                    # Call webhook if client has one configured
                    await call_webhook(client_id, match_data)
                    # Truncate content for logs
                    logger.info(f"MATCH: {client_id}/{group_id}: {keyword} - {content_type} - r/{subreddit_name} - {content_text[:50]}...")
                    
    return False

async def start_subreddit_stream(subreddit_name: str):
    """Start streaming for a specific subreddit if not already streaming."""
    global active_streams
    
    # If already streaming this subreddit, just return
    if subreddit_name in active_streams and active_streams[subreddit_name]["task"] is not None:
        logger.info(f"Already streaming subreddit: {subreddit_name}")
        return
    
    # Initialize subreddit tracking if not exists
    if subreddit_name not in active_streams:
        active_streams[subreddit_name] = {
            "task": None,
            "clients": set(),
            "count": 0
        }
    
    try:
        # Create tasks for both comments and submissions
        comments_task = asyncio.create_task(stream_subreddit_comments(subreddit_name))
        submissions_task = asyncio.create_task(stream_subreddit_submissions(subreddit_name))
        
        # Store both tasks as a list
        active_streams[subreddit_name]["task"] = [comments_task, submissions_task]
        logger.info(f"Started streaming for subreddit: {subreddit_name}")
    except Exception as e:
        logger.error(f"Failed to start stream for subreddit {subreddit_name}: {e}")

async def stop_subreddit_stream(subreddit_name: str):
    """Stop streaming for a specific subreddit."""
    global active_streams
    
    if subreddit_name not in active_streams or active_streams[subreddit_name]["task"] is None:
        logger.info(f"No active stream for subreddit: {subreddit_name}")
        return
    
    try:
        # Cancel both tasks
        for task in active_streams[subreddit_name]["task"]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Reset task
        active_streams[subreddit_name]["task"] = None
        logger.info(f"Stopped streaming for subreddit: {subreddit_name}")
    except Exception as e:
        logger.error(f"Error stopping stream for subreddit {subreddit_name}: {e}")

async def update_active_streams():
    """Review all keyword groups and update which subreddits should be streamed."""
    # Get all needed subreddits
    needed_subreddits = set(["all"])  # Always monitor 'all'
    subreddit_keyword_counts = {"all": 0}
    
    # Count keywords per subreddit
    for client_id, client_data in client_keywords.items():
        if "groups" not in client_data:
            continue
            
        for group_id, group_data in client_data["groups"].items():
            subreddit = group_data.get("subreddit")
            keywords_count = len(group_data.get("keywords", []))
            
            if keywords_count > 0:
                # If subreddit is None or empty string, use "all" instead
                if not subreddit:  # This will handle both None and empty string
                    subreddit = "all"
                    
                needed_subreddits.add(subreddit)
                subreddit_keyword_counts[subreddit] = subreddit_keyword_counts.get(subreddit, 0) + keywords_count
    
    # Make sure None is not in the set of needed subreddits
    if None in needed_subreddits:
        needed_subreddits.remove(None)
    
    # Start streams for new subreddits
    for subreddit in needed_subreddits:
        if subreddit not in active_streams or active_streams[subreddit]["task"] is None:
            await start_subreddit_stream(subreddit)
    
    # Stop streams for unnecessary subreddits
    for subreddit in list(active_streams.keys()):
        if subreddit not in needed_subreddits or subreddit_keyword_counts.get(subreddit, 0) == 0:
            await stop_subreddit_stream(subreddit)

async def stream_subreddit_comments(subreddit_name: str):
    """Stream comments from a specific subreddit."""
    
    if not REDDIT_CREDENTIALS_AVAILABLE:
        logger.error(f"Cannot stream comments for {subreddit_name}: Reddit API credentials not configured")
        return
    
    try:
        reddit_client = await initialize_reddit()
        subreddit = await reddit_client.subreddit(subreddit_name)
        logger.info(f"Starting to stream comments from r/{subreddit_name}")
        
        while True:
            try:
                async for comment in subreddit.stream.comments():
                    # Get comment text
                    comment_text = comment.body
                    
                    # Get permalink
                    try:
                        permalink = comment.permalink
                        if callable(getattr(permalink, "__await__", None)):
                            permalink = await permalink
                    except Exception:
                        permalink = f"https://reddit.com{comment.id}"
                    
                    timestamp = datetime.fromtimestamp(comment.created_utc).isoformat()
                    
                    # Check for keywords
                    await check_content_for_keywords(
                        comment_text,
                        "comment",
                        permalink,
                        comment.subreddit,
                        timestamp,
                        str(comment.subreddit)
                    )
            except Exception as e:
                logger.error(f"Comment stream error for r/{subreddit_name}, reconnecting: {e}")
                await asyncio.sleep(5)
    except Exception as e:
        logger.error(f"Failed to start streaming comments for r/{subreddit_name}: {e}")
    finally:
        # Don't close the Reddit client here as other streams might be using it
        pass

async def stream_subreddit_submissions(subreddit_name: str):
    """Stream submissions from a specific subreddit."""
    
    if not REDDIT_CREDENTIALS_AVAILABLE:
        logger.error(f"Cannot stream submissions for {subreddit_name}: Reddit API credentials not configured")
        return
    
    try:
        reddit_client = await initialize_reddit()
        subreddit = await reddit_client.subreddit(subreddit_name)
        logger.info(f"Starting to stream submissions from r/{subreddit_name}")
        
        while True:
            try:
                async for submission in subreddit.stream.submissions():
                    # Get content from either title or selftext
                    title_text = submission.title
                    selftext = getattr(submission, "selftext", "")
                    combined_text = f"{title_text}\n{selftext}"
                    
                    # Get permalink
                    try:
                        permalink = submission.permalink
                        if callable(getattr(permalink, "__await__", None)):
                            permalink = await permalink
                    except Exception:
                        permalink = f"https://reddit.com{submission.id}"
                    
                    timestamp = datetime.fromtimestamp(submission.created_utc).isoformat()
                    
                    # Check for keywords
                    await check_content_for_keywords(
                        combined_text,
                        "submission",
                        permalink,
                        submission.subreddit,
                        timestamp,
                        str(submission.subreddit)
                    )
            except Exception as e:
                logger.error(f"Submission stream error for r/{subreddit_name}, reconnecting: {e}")
                await asyncio.sleep(5)
    except Exception as e:
        logger.error(f"Failed to start streaming submissions for r/{subreddit_name}: {e}")
    finally:
        # Don't close the Reddit client here as other streams might be using it
        pass

# Function to be called when the application starts
async def start_streaming():
    """Start streaming Reddit comments and submissions if credentials are available."""
    # First load keywords from the database
    await load_keywords_from_db()
    
    if not REDDIT_CREDENTIALS_AVAILABLE:
        logger.warning("Reddit streaming not started: missing API credentials")
        return
    
    try:
        # Check if we're in an event loop
        asyncio.get_running_loop()
        # Update active streams based on keywords
        await update_active_streams()
        logger.info("Reddit streaming started automatically on startup")
    except RuntimeError:
        # If no event loop is running, log a warning
        logger.warning("No running event loop found. Streaming will not start automatically.")
        logger.info("Streaming will be started by the lifespan manager when the application starts.")

# API endpoints - All are protected by API key authentication
@router.get("/matches")
async def get_matches(
    client_id: Optional[str] = None, 
    group_id: Optional[str] = None, 
    limit: int = 100,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    if not client_id:
        client_id = auth_client_id
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    elif client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to access this client's data")
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        if group_id:
            cursor.execute("SELECT * FROM matches WHERE client_id = %s AND group_id = %s ORDER BY id DESC LIMIT %s", 
                        (client_id, group_id, limit))
        else:
            cursor.execute("SELECT * FROM matches WHERE client_id = %s ORDER BY id DESC LIMIT %s", 
                        (client_id, limit))
        matches = cursor.fetchall()
        return [{"id": m[0], "client_id": m[1], "group_id": m[2], "keyword": m[3], 
                "comment_body": m[4][:200] if m[4] else "", "permalink": m[5], "subreddit": m[6], 
                "timestamp": m[7], "content_type": m[8] if len(m) > 8 else "comment"} for m in matches]
    except Exception as e:
        logger.error(f"Database error in get_matches: {e}")
        raise HTTPException(status_code=500, detail="Database error")
    finally:
        if conn:
            release_db_connection(conn)

@router.get("/keyword-groups")
async def get_keyword_groups(
    client_id: Optional[str] = None,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    if not client_id:
        client_id = auth_client_id
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    elif client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to access this client's data")
    
    if client_id not in client_keywords:
        return {"groups": {}}
    return {"groups": client_keywords[client_id].get("groups", {})}

@router.post("/keyword-groups")
async def create_keyword_group(
    group: KeywordGroupCreate,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Create a new keyword group, optionally with a specific subreddit to monitor."""
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    client_id = group.client_id or auth_client_id
    
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    if group.client_id and group.client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's data")
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
        
    conn = None
    try:
        # First update the in-memory representation
        if client_id not in client_keywords:
            client_keywords[client_id] = {"groups": {}}
        elif "groups" not in client_keywords[client_id]:
            client_keywords[client_id]["groups"] = {}
        
        if group.group_id in client_keywords[client_id]["groups"]:
            raise HTTPException(status_code=400, detail="Group already exists")
        
        # Update in-memory keywords with subreddit info
        client_keywords[client_id]["groups"][group.group_id] = {
            "keywords": group.keywords,
            "subreddit": group.subreddit
        }
        
        # Store in database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO client_keywords (client_id, group_id, keywords, subreddit) VALUES (%s, %s, %s, %s)",
            (client_id, group.group_id, Json(group.keywords), group.subreddit)
        )
        conn.commit()
        
        # Update active streams
        await update_active_streams()
        
        return {"message": f"Keyword group '{group.group_id}' created for {client_id}"}
    except Exception as e:
        if conn:
            conn.rollback()  # Ensure transaction is rolled back
        logger.error(f"Error creating keyword group: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create keyword group: {str(e)}")
    finally:
        if conn:
            release_db_connection(conn)

@router.put("/keyword-groups")
async def update_keyword_group(
    update: KeywordGroupUpdate,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Update a keyword group, including its subreddit setting."""
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    client_id = update.client_id or auth_client_id
    
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    if update.client_id and update.client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's data")
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    
    conn = None
    try:
        # First check if client and group exist in memory
        if client_id not in client_keywords:
            raise HTTPException(status_code=404, detail="Client not found")
        
        if "groups" not in client_keywords[client_id] or update.group_id not in client_keywords[client_id]["groups"]:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Update in-memory keywords with subreddit info
        client_keywords[client_id]["groups"][update.group_id] = {
            "keywords": update.keywords,
            "subreddit": update.subreddit
        }
        
        # Update in database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE client_keywords SET keywords = %s, subreddit = %s WHERE client_id = %s AND group_id = %s",
            (Json(update.keywords), update.subreddit, client_id, update.group_id)
        )
        
        if cursor.rowcount == 0:
            # If no rows were updated, insert instead
            cursor.execute(
                "INSERT INTO client_keywords (client_id, group_id, keywords, subreddit) VALUES (%s, %s, %s, %s)",
                (client_id, update.group_id, Json(update.keywords), update.subreddit)
            )
            
        conn.commit()
        
        # Update active streams
        await update_active_streams()
        
        return {"message": f"Keywords updated for {client_id}/{update.group_id}"}
    except Exception as e:
        if conn:
            conn.rollback()  # Ensure transaction is rolled back
        logger.error(f"Error updating keyword group: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update keyword group: {str(e)}")
    finally:
        if conn:
            release_db_connection(conn)

@router.delete("/keyword-groups")
async def delete_keyword_group(
    delete: KeywordGroupDelete,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Delete a keyword group."""
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    client_id = delete.client_id or auth_client_id
    
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    if delete.client_id and delete.client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's data")
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    
    conn = None
    try:
        # First check if client and group exist in memory
        if client_id not in client_keywords:
            raise HTTPException(status_code=404, detail="Client not found")
        
        if "groups" not in client_keywords[client_id] or delete.group_id not in client_keywords[client_id]["groups"]:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Delete from in-memory keywords
        del client_keywords[client_id]["groups"][delete.group_id]
        
        # Delete from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM client_keywords WHERE client_id = %s AND group_id = %s",
            (client_id, delete.group_id)
        )
        conn.commit()
        
        # Update active streams
        await update_active_streams()
        
        return {"message": f"Keyword group '{delete.group_id}' deleted for {client_id}"}
    except Exception as e:
        if conn:
            conn.rollback()  # Ensure transaction is rolled back
        logger.error(f"Error deleting keyword group: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete keyword group: {str(e)}")
    finally:
        if conn:
            release_db_connection(conn)

@router.post("/webhook")
async def set_webhook(
    webhook: WebhookUpdate,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Set or update a client's webhook URL."""
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    client_id = webhook.client_id or auth_client_id
    
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    if webhook.client_id and webhook.client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's webhook")
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    
    conn = None
    try:
        # Update in-memory representation
        if client_id not in client_keywords:
            client_keywords[client_id] = {"groups": {}}
        
        client_keywords[client_id]["webhook_url"] = str(webhook.webhook_url)
        
        # Update in database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM client_webhooks WHERE client_id = %s",
            (client_id,)
        )
        exists = cursor.fetchone()
        
        if exists:
            cursor.execute(
                "UPDATE client_webhooks SET webhook_url = %s WHERE client_id = %s",
                (str(webhook.webhook_url), client_id)
            )
        else:
            cursor.execute(
                "INSERT INTO client_webhooks (client_id, webhook_url) VALUES (%s, %s)",
                (client_id, str(webhook.webhook_url))
            )
        
        conn.commit()
        
        return {"message": f"Webhook URL updated for {client_id}"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error updating webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update webhook: {str(e)}")
    finally:
        if conn:
            release_db_connection(conn)

@router.delete("/webhook")
async def delete_webhook(
    client_id: Optional[str] = None,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Remove a client's webhook URL."""
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    if not client_id:
        client_id = auth_client_id
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    elif client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's webhook")
    
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    
    conn = None
    try:
        # Check if client exists in memory
        if client_id not in client_keywords:
            raise HTTPException(status_code=404, detail="Client not found")
        
        # Check if webhook exists in memory
        if "webhook_url" not in client_keywords[client_id]:
            raise HTTPException(status_code=404, detail="Webhook URL not found")
        
        # Remove from in-memory representation
        del client_keywords[client_id]["webhook_url"]
        
        # Remove from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM client_webhooks WHERE client_id = %s",
            (client_id,)
        )
        conn.commit()
        
        return {"message": f"Webhook URL removed for {client_id}"}
    except Exception as e:
        if conn:
            conn.rollback()
        logger.error(f"Error deleting webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete webhook: {str(e)}")
    finally:
        if conn:
            release_db_connection(conn)

@router.post("/start-streaming")
async def start_streaming_endpoint(
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Manually start the Reddit streaming based on configured keywords and subreddits."""
    if not REDDIT_CREDENTIALS_AVAILABLE:
        raise HTTPException(
            status_code=503, 
            detail="Reddit API credentials not configured. Set CLIENT_ID and CLIENT_SECRET environment variables."
        )
    
    try:
        # Update streams based on keywords
        await update_active_streams()
        return {"message": "Reddit streaming started"}
    except Exception as e:
        logger.error(f"Failed to start streaming: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/active-streams")
async def get_active_streams(
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Get information about currently active stream tasks."""
    streams_info = {}
    
    for subreddit, data in active_streams.items():
        is_active = data["task"] is not None
        if is_active:
            task_status = [
                "running" if not t.done() else 
                "completed" if not t.cancelled() else 
                "cancelled" 
                for t in data["task"]
            ]
        else:
            task_status = None
            
        streams_info[subreddit] = {
            "active": is_active,
            "status": task_status
        }
    
    return {"active_streams": streams_info}

async def load_keywords_from_db():
    """Load client keywords and webhooks from database."""
    global client_keywords
    
    if not db_pool:
        logger.warning("Database not available, using default empty keywords")
        return
    
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # Load all client keywords
        cursor.execute("SELECT client_id, group_id, keywords, subreddit FROM client_keywords")
        keyword_rows = cursor.fetchall()
        
        # Load all client webhooks
        cursor.execute("SELECT client_id, webhook_url FROM client_webhooks")
        webhook_rows = cursor.fetchall()
        
        # Initialize client_keywords dictionary
        for client_id, group_id, keywords, subreddit in keyword_rows:
            if client_id not in client_keywords:
                client_keywords[client_id] = {"groups": {}}
            if "groups" not in client_keywords[client_id]:
                client_keywords[client_id]["groups"] = {}
                
            client_keywords[client_id]["groups"][group_id] = {
                "keywords": keywords,
                "subreddit": subreddit
            }
        
        # Add webhooks
        for client_id, webhook_url in webhook_rows:
            if client_id not in client_keywords:
                client_keywords[client_id] = {"groups": {}}
            client_keywords[client_id]["webhook_url"] = webhook_url
            
        logger.info(f"Loaded keywords for {len(client_keywords)} clients from database")
    except Exception as e:
        if conn:
            conn.rollback()  # Ensure transaction is rolled back
        logger.error(f"Error loading keywords from database: {e}")
        # Load default keywords as fallback
        client_keywords = {
            "client1": {
                "groups": {
                    "tech": {
                        "keywords": ["python", "ai"],
                        "subreddit": None  # Monitor all subreddits
                    }
                }
            }, 
            "client2": {
                "groups": {
                    "finance": {
                        "keywords": ["crypto"],
                        "subreddit": "Bitcoin"  # Only monitor r/Bitcoin
                    }
                }
            }
        }
        logger.info("Using default keywords from environment variable")
    finally:
        if conn:
            release_db_connection(conn) 