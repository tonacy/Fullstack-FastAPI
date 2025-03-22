import asyncpraw
import os
import json
import asyncio
import aiohttp
import logging
from fastapi import APIRouter, Depends, HTTPException, FastAPI
from pydantic import BaseModel, HttpUrl
import psycopg2
from psycopg2.extras import Json
from datetime import datetime
from contextlib import asynccontextmanager

# Create router
router = APIRouter()

# Setup logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("reddit_monitor")

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
client_keywords = json.loads(os.getenv("KEYWORDS", '{"client1": {"groups": {"tech": ["python", "ai"]}}, "client2": {"groups": {"finance": ["crypto"]}}}'))

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
    client_id: str
    group_id: str
    keywords: list[str]

class KeywordGroupCreate(BaseModel):
    client_id: str
    group_id: str
    keywords: list[str]

class KeywordGroupDelete(BaseModel):
    client_id: str
    group_id: str

class WebhookUpdate(BaseModel):
    client_id: str
    webhook_url: HttpUrl

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

# API endpoints
@router.get("/matches")
async def get_matches(client_id: str, group_id: str = None, limit: int = 100):
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
async def get_keyword_groups(client_id: str):
    if client_id not in client_keywords:
        return {"groups": {}}
    return {"groups": client_keywords[client_id].get("groups", {})}

@router.post("/keyword-groups")
async def create_keyword_group(group: KeywordGroupCreate):
    if group.client_id not in client_keywords:
        client_keywords[group.client_id] = {"groups": {}}
    elif "groups" not in client_keywords[group.client_id]:
        client_keywords[group.client_id]["groups"] = {}
    
    if group.group_id in client_keywords[group.client_id]["groups"]:
        raise HTTPException(status_code=400, detail="Group already exists")
    
    client_keywords[group.client_id]["groups"][group.group_id] = group.keywords
    return {"message": f"Keyword group '{group.group_id}' created for {group.client_id}"}

@router.put("/keyword-groups")
async def update_keyword_group(update: KeywordGroupUpdate):
    if update.client_id not in client_keywords:
        raise HTTPException(status_code=404, detail="Client not found")
    
    if "groups" not in client_keywords[update.client_id] or update.group_id not in client_keywords[update.client_id]["groups"]:
        raise HTTPException(status_code=404, detail="Group not found")
    
    client_keywords[update.client_id]["groups"][update.group_id] = update.keywords
    return {"message": f"Keywords updated for {update.client_id}/{update.group_id}"}

@router.delete("/keyword-groups")
async def delete_keyword_group(delete: KeywordGroupDelete):
    if delete.client_id not in client_keywords:
        raise HTTPException(status_code=404, detail="Client not found")
    
    if "groups" not in client_keywords[delete.client_id] or delete.group_id not in client_keywords[delete.client_id]["groups"]:
        raise HTTPException(status_code=404, detail="Group not found")
    
    del client_keywords[delete.client_id]["groups"][delete.group_id]
    return {"message": f"Keyword group '{delete.group_id}' deleted for {delete.client_id}"}

@router.post("/webhook")
async def set_webhook(webhook: WebhookUpdate):
    """Set or update a client's webhook URL."""
    if webhook.client_id not in client_keywords:
        client_keywords[webhook.client_id] = {"groups": {}}
    
    client_keywords[webhook.client_id]["webhook_url"] = str(webhook.webhook_url)
    return {"message": f"Webhook URL updated for {webhook.client_id}"}

@router.delete("/webhook")
async def delete_webhook(client_id: str):
    """Remove a client's webhook URL."""
    if client_id not in client_keywords:
        raise HTTPException(status_code=404, detail="Client not found")
    
    if "webhook_url" in client_keywords[client_id]:
        del client_keywords[client_id]["webhook_url"]
        return {"message": f"Webhook URL removed for {client_id}"}
    else:
        raise HTTPException(status_code=404, detail="Webhook URL not found")

@router.post("/start-streaming")
async def start_streaming_endpoint():
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

# Reset match count for testing
@router.post("/reset-test")
async def reset_test():
    """Reset the match counter for testing."""
    global match_count
    match_count = 0
    return {"message": "Test counter reset. Stream will collect 5 more matches."}

# Health check
@router.get("/health")
async def health_check():
    status = {
        "status": "healthy",
        "reddit_api": "available" if REDDIT_CREDENTIALS_AVAILABLE else "unavailable",
        "database": "connected" if conn and cursor else "disconnected"
    }
    return status 