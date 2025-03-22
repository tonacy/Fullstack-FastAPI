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
from psycopg2.extras import Json
from datetime import datetime
from contextlib import asynccontextmanager
from typing import Optional, Tuple

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
# Structure: {"client1": {"webhook_url": "https://example.com/webhook", "groups": {"group1": ["python", "ai"], "group2": ["fastapi"]}}, "client2": {"groups": {"finance": ["crypto"]}}}
client_keywords = {}  # Will be loaded from database at startup

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL")
conn = None
cursor = None

try:
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                id SERIAL PRIMARY KEY,
                client_id TEXT,
                group_id TEXT,
                keyword TEXT,
                comment_body TEXT,
                permalink TEXT,
                subreddit TEXT,
                timestamp TEXT
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
        logger.info("Connected to database successfully")
    else:
        logger.warning("DATABASE_URL not set. Database features will not be available.")
except Exception as e:
    logger.error(f"Database connection error: {e}")

# For testing - track number of matches
match_count = 0
MAX_TEST_MATCHES = 5

# Models for keyword operations
class KeywordGroupUpdate(BaseModel):
    client_id: Optional[str] = None
    group_id: str
    keywords: list[str]

class KeywordGroupCreate(BaseModel):
    client_id: Optional[str] = None
    group_id: str
    keywords: list[str]

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
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
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

async def validate_admin(auth: Tuple[str, str] = Depends(validate_api_key)):
    api_key, client_id = auth
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
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
    if not conn or not cursor:
        logger.warning("Database not available, match not saved")
        return
    
    try:
        cursor.execute("""
            INSERT INTO matches (client_id, group_id, keyword, comment_body, permalink, subreddit, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            match_data["client_id"], match_data["group_id"], match_data["keyword"], match_data["comment_body"],
            match_data["permalink"], match_data["subreddit"], match_data["timestamp"]
        ))
        conn.commit()
        logger.info(f"Match saved to database: {match_data['client_id']}/{match_data['group_id']} - {match_data['keyword']}")
    except Exception as e:
        logger.error(f"Error saving match to database: {e}")

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
            "comment_body": match_data["comment_body"],
            "permalink": match_data["permalink"],
            "subreddit": match_data["subreddit"],
            "timestamp": match_data["timestamp"]
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(webhook_url, json=webhook_payload) as response:
                if response.status >= 400:
                    logger.error(f"Webhook call failed for client {client_id}: {response.status}")
                else:
                    logger.info(f"Webhook called successfully for client {client_id}")
    except Exception as e:
        logger.error(f"Error calling webhook for client {client_id}: {e}")

async def stream_comments():
    """Background task to stream comments."""
    global match_count
    
    if not REDDIT_CREDENTIALS_AVAILABLE:
        logger.error("Cannot stream comments: Reddit API credentials not configured")
        return
    
    try:
        reddit_client = await initialize_reddit()
        subreddit = await reddit_client.subreddit("all")
        logger.info("Starting to stream comments (test mode - will stop after 5 matches)")
        
        while match_count < MAX_TEST_MATCHES:
            try:
                async for comment in subreddit.stream.comments():
                    if match_count >= MAX_TEST_MATCHES:
                        logger.info(f"Test complete: Found {match_count} matches. Stopping stream.")
                        return
                        
                    # Lowercase the comment text and split into words
                    comment_text = comment.body.lower()
                    comment_words = set(word.strip(".,!?:;\"'()[]{}") for word in comment_text.split())
                    timestamp = datetime.fromtimestamp(comment.created_utc).isoformat()
                    
                    # Get permalink - check if it's a coroutine or a property
                    try:
                        permalink = comment.permalink
                        if callable(getattr(permalink, "__await__", None)):
                            permalink = await permalink
                    except Exception:
                        permalink = f"https://reddit.com{comment.id}"
                    
                    for client_id, client_data in client_keywords.items():
                        groups = client_data.get("groups", {})
                        for group_id, keywords in groups.items():
                            for keyword in keywords:
                                # Convert keyword to lowercase
                                keyword_lower = keyword.lower()
                                
                                # Check if the keyword is a whole word in the comment
                                if keyword_lower in comment_words:
                                    match_data = {
                                        "client_id": client_id,
                                        "group_id": group_id,
                                        "keyword": keyword,
                                        "comment_body": comment.body,
                                        "permalink": permalink,
                                        "subreddit": str(comment.subreddit),
                                        "timestamp": timestamp
                                    }
                                    await save_match(match_data)
                                    # Call webhook if client has one configured
                                    await call_webhook(client_id, match_data)
                                    match_count += 1
                                    logger.info(f"MATCH #{match_count}/{MAX_TEST_MATCHES}: {client_id}/{group_id}: {keyword} - {comment.body[:100]}...")
                                    
                                    if match_count >= MAX_TEST_MATCHES:
                                        logger.info(f"Test complete: Found {match_count} matches. Stopping stream.")
                                        await reddit_client.close()
                                        return
            except Exception as e:
                logger.error(f"Stream error, reconnecting: {e}")
                await asyncio.sleep(5)
    except Exception as e:
        logger.error(f"Failed to start streaming: {e}")
    finally:
        # Make sure to close the Reddit client when done
        if reddit:
            await reddit.close()

# Function to be called when the application starts
async def start_streaming():
    """Start streaming Reddit comments if credentials are available."""
    # First load keywords from the database
    await load_keywords_from_db()
    
    if not REDDIT_CREDENTIALS_AVAILABLE:
        logger.warning("Reddit streaming not started: missing API credentials")
        return
    
    try:
        # Check if we're in an event loop
        asyncio.get_running_loop()
        asyncio.create_task(stream_comments())
        logger.info("Reddit comment streaming started automatically on startup")
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
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        if group_id:
            cursor.execute("SELECT * FROM matches WHERE client_id = %s AND group_id = %s ORDER BY id DESC LIMIT %s", 
                        (client_id, group_id, limit))
        else:
            cursor.execute("SELECT * FROM matches WHERE client_id = %s ORDER BY id DESC LIMIT %s", 
                        (client_id, limit))
        matches = cursor.fetchall()
        return [{"id": m[0], "client_id": m[1], "group_id": m[2], "keyword": m[3], 
                "comment_body": m[4], "permalink": m[5], "subreddit": m[6], "timestamp": m[7]} for m in matches]
    except Exception as e:
        logger.error(f"Database error in get_matches: {e}")
        raise HTTPException(status_code=500, detail="Database error")

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
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    client_id = group.client_id or auth_client_id
    
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    if group.client_id and group.client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's data")
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
        
    try:
        # First update the in-memory representation
        if client_id not in client_keywords:
            client_keywords[client_id] = {"groups": {}}
        elif "groups" not in client_keywords[client_id]:
            client_keywords[client_id]["groups"] = {}
        
        if group.group_id in client_keywords[client_id]["groups"]:
            raise HTTPException(status_code=400, detail="Group already exists")
        
        # Update in-memory keywords
        client_keywords[client_id]["groups"][group.group_id] = group.keywords
        
        # Store in database
        cursor.execute(
            "INSERT INTO client_keywords (client_id, group_id, keywords) VALUES (%s, %s, %s)",
            (client_id, group.group_id, Json(group.keywords))
        )
        conn.commit()
        
        return {"message": f"Keyword group '{group.group_id}' created for {client_id}"}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating keyword group: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create keyword group: {str(e)}")

@router.put("/keyword-groups")
async def update_keyword_group(
    update: KeywordGroupUpdate,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    client_id = update.client_id or auth_client_id
    
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    if update.client_id and update.client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's data")
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        # First check if client and group exist in memory
        if client_id not in client_keywords:
            raise HTTPException(status_code=404, detail="Client not found")
        
        if "groups" not in client_keywords[client_id] or update.group_id not in client_keywords[client_id]["groups"]:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Update in-memory keywords
        client_keywords[client_id]["groups"][update.group_id] = update.keywords
        
        # Update in database
        cursor.execute(
            "UPDATE client_keywords SET keywords = %s WHERE client_id = %s AND group_id = %s",
            (Json(update.keywords), client_id, update.group_id)
        )
        
        if cursor.rowcount == 0:
            # If no rows were updated, insert instead
            cursor.execute(
                "INSERT INTO client_keywords (client_id, group_id, keywords) VALUES (%s, %s, %s)",
                (client_id, update.group_id, Json(update.keywords))
            )
            
        conn.commit()
        
        return {"message": f"Keywords updated for {client_id}/{update.group_id}"}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error updating keyword group: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update keyword group: {str(e)}")

@router.delete("/keyword-groups")
async def delete_keyword_group(
    delete: KeywordGroupDelete,
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    _, auth_client_id = auth
    
    # If client_id wasn't provided in the request, use the one from the API key
    client_id = delete.client_id or auth_client_id
    
    # If it was provided but doesn't match the API key's client_id, reject unless it's an admin key
    if delete.client_id and delete.client_id != auth_client_id:
        # Here you could add admin check logic if needed
        raise HTTPException(status_code=403, detail="Not authorized to modify this client's data")
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        # First check if client and group exist in memory
        if client_id not in client_keywords:
            raise HTTPException(status_code=404, detail="Client not found")
        
        if "groups" not in client_keywords[client_id] or delete.group_id not in client_keywords[client_id]["groups"]:
            raise HTTPException(status_code=404, detail="Group not found")
        
        # Delete from in-memory keywords
        del client_keywords[client_id]["groups"][delete.group_id]
        
        # Delete from database
        cursor.execute(
            "DELETE FROM client_keywords WHERE client_id = %s AND group_id = %s",
            (client_id, delete.group_id)
        )
        conn.commit()
        
        return {"message": f"Keyword group '{delete.group_id}' deleted for {client_id}"}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting keyword group: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete keyword group: {str(e)}")

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
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        # Update in-memory representation
        if client_id not in client_keywords:
            client_keywords[client_id] = {"groups": {}}
        
        client_keywords[client_id]["webhook_url"] = str(webhook.webhook_url)
        
        # Update in database
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
        conn.rollback()
        logger.error(f"Error updating webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to update webhook: {str(e)}")

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
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
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
        cursor.execute(
            "DELETE FROM client_webhooks WHERE client_id = %s",
            (client_id,)
        )
        conn.commit()
        
        return {"message": f"Webhook URL removed for {client_id}"}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting webhook: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete webhook: {str(e)}")

@router.post("/start-streaming")
async def start_streaming_endpoint(
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Manually start the Reddit comment streaming."""
    if not REDDIT_CREDENTIALS_AVAILABLE:
        raise HTTPException(
            status_code=503, 
            detail="Reddit API credentials not configured. Set CLIENT_ID and CLIENT_SECRET environment variables."
        )
    
    try:
        # Create a new task for streaming
        asyncio.create_task(stream_comments())
        return {"message": "Reddit comment streaming started"}
    except Exception as e:
        logger.error(f"Failed to start streaming: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/reset-test")
async def reset_test(
    auth: Tuple[str, str] = Depends(validate_api_key)
):
    """Reset the match counter for testing."""
    global match_count
    match_count = 0
    return {"message": "Test counter reset. Stream will collect 5 more matches."}

# API key management endpoints (admin only)
@router.post("/api-keys")
async def create_api_key(
    key_data: ApiKeyCreate,
    auth: Tuple[str, str] = Depends(validate_admin)
):
    """Create a new API key for a client."""
    # This endpoint could be restricted to admin keys only
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        # If no API key is provided, generate one (you could use a more secure method)
        if not key_data.api_key:
            import uuid
            key_data.api_key = f"key_{uuid.uuid4().hex}"
        
        cursor.execute(
            "INSERT INTO api_keys (api_key, client_id) VALUES (%s, %s) RETURNING id",
            (key_data.api_key, key_data.client_id)
        )
        key_id = cursor.fetchone()[0]
        conn.commit()
        
        return {
            "id": key_id,
            "api_key": key_data.api_key,
            "client_id": key_data.client_id
        }
    except Exception as e:
        conn.rollback()
        logger.error(f"Error creating API key: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to create API key: {str(e)}")

@router.get("/api-keys")
async def list_api_keys(
    client_id: Optional[str] = None,
    auth: Tuple[str, str] = Depends(validate_admin)
):
    """List API keys, optionally filtered by client_id."""
    # This endpoint could be restricted to admin keys only
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        if client_id:
            cursor.execute("SELECT id, api_key, client_id FROM api_keys WHERE client_id = %s", (client_id,))
        else:
            cursor.execute("SELECT id, api_key, client_id FROM api_keys")
        
        keys = cursor.fetchall()
        return [{"id": k[0], "api_key": k[1], "client_id": k[2]} for k in keys]
    except Exception as e:
        logger.error(f"Error listing API keys: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list API keys: {str(e)}")

@router.delete("/api-keys/{key_id}")
async def delete_api_key(
    key_id: int,
    auth: Tuple[str, str] = Depends(validate_admin)
):
    """Delete an API key by ID."""
    # This endpoint could be restricted to admin keys only
    
    if not conn or not cursor:
        raise HTTPException(status_code=503, detail="Database not available")
    
    try:
        cursor.execute("DELETE FROM api_keys WHERE id = %s RETURNING id", (key_id,))
        result = cursor.fetchone()
        conn.commit()
        
        if not result:
            raise HTTPException(status_code=404, detail="API key not found")
        
        return {"message": f"API key with ID {key_id} deleted successfully"}
    except Exception as e:
        conn.rollback()
        logger.error(f"Error deleting API key: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete API key: {str(e)}")

# Health check - not protected to allow monitoring systems to access
@router.get("/health")
async def health_check():
    status = {
        "status": "healthy",
        "reddit_api": "available" if REDDIT_CREDENTIALS_AVAILABLE else "unavailable",
        "database": "connected" if conn and cursor else "disconnected"
    }
    return status 

async def load_keywords_from_db():
    """Load client keywords and webhooks from database."""
    global client_keywords
    
    if not conn or not cursor:
        logger.warning("Database not available, using default empty keywords")
        return
        
    try:
        # Load all client keywords
        cursor.execute("SELECT client_id, group_id, keywords FROM client_keywords")
        keyword_rows = cursor.fetchall()
        
        # Load all client webhooks
        cursor.execute("SELECT client_id, webhook_url FROM client_webhooks")
        webhook_rows = cursor.fetchall()
        
        # Initialize client_keywords dictionary
        for client_id, group_id, keywords in keyword_rows:
            if client_id not in client_keywords:
                client_keywords[client_id] = {"groups": {}}
            if "groups" not in client_keywords[client_id]:
                client_keywords[client_id]["groups"] = {}
                
            client_keywords[client_id]["groups"][group_id] = keywords
        
        # Add webhooks
        for client_id, webhook_url in webhook_rows:
            if client_id not in client_keywords:
                client_keywords[client_id] = {"groups": {}}
            client_keywords[client_id]["webhook_url"] = webhook_url
            
        logger.info(f"Loaded keywords for {len(client_keywords)} clients from database")
    except Exception as e:
        logger.error(f"Error loading keywords from database: {e}")
        # Load default keywords as fallback
        client_keywords = json.loads(os.getenv("KEYWORDS", '{"client1": {"groups": {"tech": ["python", "ai"]}}, "client2": {"groups": {"finance": ["crypto"]}}}'))
        logger.info("Using default keywords from environment variable") 