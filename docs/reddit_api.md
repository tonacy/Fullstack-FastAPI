# Reddit Keyword Monitor API Documentation

## Authentication

All endpoints (except `/health`) require API key authentication.

**Header:** `X-API-Key: your_api_key`

## API Key Management

API keys are required for authentication. You can generate a unique API key using:

```
# Generate UUID API key
python -c "import uuid; print(uuid.uuid4())"
```

To use a generated key, insert it into the database:

```sql
INSERT INTO api_keys (api_key, client_id, is_admin) VALUES ('your-generated-uuid', 'your-client-id', false);
```

For admin privileges, set `is_admin` to `true`.

## API Endpoints

### Health Check

Check the status of the API service.

```
GET /health
```

#### Response

```json
{
  "status": "healthy",
  "reddit_api": "available",
  "database": "connected"
}
```

### Keyword Groups

#### List Keyword Groups

Get all keyword groups for a client.

```
GET /keyword-groups?client_id=client1
```

##### Parameters
- `client_id` (string, required): The client identifier

##### Response

```json
{
  "groups": {
    "tech": {
      "keywords": ["python", "ai"],
      "subreddit": "programming"
    },
    "webdev": {
      "keywords": ["fastapi", "react"],
      "subreddit": null
    }
  }
}
```

#### Create Keyword Group

Create a new keyword group for a client.

```
POST /keyword-groups
```

##### Request Body

```json
{
  "client_id": "client1",
  "group_id": "finance",
  "keywords": ["bitcoin", "crypto", "blockchain"],
  "subreddit": "Bitcoin"
}
```

The `subreddit` field is optional. If provided, keywords will only be monitored in that specific subreddit. If omitted or set to `null`, keywords will be monitored across all subreddits.

##### Response

```json
{
  "message": "Keyword group 'finance' created for client1"
}
```

#### Update Keyword Group

Update an existing keyword group.

```
PUT /keyword-groups
```

##### Request Body

```json
{
  "client_id": "client1",
  "group_id": "tech",
  "keywords": ["python", "ai", "machine learning"],
  "subreddit": "programming"
}
```

##### Response

```json
{
  "message": "Keywords updated for client1/tech"
}
```

#### Delete Keyword Group

Delete a keyword group.

```
DELETE /keyword-groups
```

##### Request Body

```json
{
  "client_id": "client1",
  "group_id": "tech"
}
```

##### Response

```json
{
  "message": "Keyword group 'tech' deleted for client1"
}
```

### Webhooks

#### Set Webhook URL

Set or update a client's webhook URL.

```
POST /webhook
```

##### Request Body

```json
{
  "client_id": "client1",
  "webhook_url": "https://example.com/webhook"
}
```

##### Response

```json
{
  "message": "Webhook URL updated for client1"
}
```

#### Delete Webhook URL

Remove a client's webhook URL.

```
DELETE /webhook?client_id=client1
```

##### Parameters
- `client_id` (string, required): The client identifier

##### Response

```json
{
  "message": "Webhook URL removed for client1"
}
```

### Matches

#### Get Matches

Get all keyword matches for a client, optionally filtered by group.

```
GET /matches?client_id=client1&group_id=tech&limit=10
```

##### Parameters
- `client_id` (string, required): The client identifier
- `group_id` (string, optional): Filter by keyword group
- `limit` (integer, optional, default=100): Maximum number of matches to return

##### Response

```json
[
  {
    "id": 1,
    "client_id": "client1",
    "group_id": "tech",
    "keyword": "python",
    "comment_body": "I've been learning Python for the past month and it's amazing!",
    "permalink": "https://reddit.com/r/programming/comments/abc123/comment/def456",
    "subreddit": "programming",
    "timestamp": "2023-09-01T15:30:45",
    "content_type": "comment"
  },
  ...
]
```

### Streaming Control

#### Start Streaming

Manually start the Reddit streaming process for comments and submissions.

```
POST /start-streaming
```

##### Response

```json
{
  "message": "Reddit streaming started"
}
```

#### Get Active Streams

Get information about currently active stream tasks.

```
GET /active-streams
```

##### Response

```json
{
  "active_streams": {
    "all": {
      "active": true,
      "status": ["running", "running"]
    },
    "programming": {
      "active": true,
      "status": ["running", "running"]
    }
  }
}
```

#### Reset Test Counter

Reset the match counter for testing purposes.

```
POST /reset-test
```

##### Response

```json
{
  "message": "Test counter reset. Stream will collect 5 more matches."
}
```

## Error Responses

### Authentication Errors

```json
{
  "detail": "API Key header missing"
}
```

```json
{
  "detail": "Invalid API Key"
}
```

### Resource Errors

```json
{
  "detail": "Group already exists"
}
```

```json
{
  "detail": "Client not found"
}
```

```json
{
  "detail": "Group not found"
}
```

```json
{
  "detail": "Webhook URL not found"
}
```

### Service Errors

```json
{
  "detail": "Database not available"
}
```

```json
{
  "detail": "Database error"
}
```

```json
{
  "detail": "Reddit API credentials not configured. Set CLIENT_ID and CLIENT_SECRET environment variables."
}
``` 