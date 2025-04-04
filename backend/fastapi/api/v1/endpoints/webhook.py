import aiohttp
import logging

async def call_webhook(client_id, match_data, client_keywords, logger=None):
    """Call the client's webhook URL with match data if configured.
    
    Args:
        client_id: The client identifier
        match_data: Dictionary containing match information
        client_keywords: Dictionary of client configuration including webhook URLs
        logger: Logger instance (optional)
    """
    # Use provided logger or create a default one
    if logger is None:
        logger = logging.getLogger("webhook")
        
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