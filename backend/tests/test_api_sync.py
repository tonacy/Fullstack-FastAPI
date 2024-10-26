import pytest
import uuid
from fastapi import status
from fastapi.testclient import TestClient
from backend.fastapi.main import app

client = TestClient(app)

# Mock data for creating a message
valid_message_data = {
    "content": "Hello, world!"
}

invalid_message_data = {
    "invalid_field": "This should fail"
}

def test_create_message():
    # Send a POST request to the /messages/ endpoint
    response = client.post("/api/v1/messages/", json=valid_message_data)
    
    # Verify that the request was successful
    assert response.status_code == status.HTTP_201_CREATED
    
    # Verify that the response contains the expected data
    response_data = response.json()
    assert response_data["content"] == valid_message_data["content"]
    
    # Verify that the 'id' field is a valid UUID
    assert "id" in response_data
    try:
        uuid_obj = uuid.UUID(response_data["id"])
        assert isinstance(uuid_obj, uuid.UUID)
    except ValueError:
        pytest.fail("Invalid UUID format returned in the response.")

def test_create_message_invalid_data():
    # Send a POST request with invalid data
    response = client.post("/api/v1/messages/", json=invalid_message_data)
    
    # Verify that the request was unsuccessful
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

def test_get_messages():
    # Create a sample message to ensure there's at least one message
    client.post("/api/v1/messages/", json=valid_message_data)
    
    # Send a GET request to retrieve messages
    response = client.get("/api/v1/messages/")
    
    # Verify that the request was successful
    assert response.status_code == status.HTTP_200_OK
    
    # Check if the response contains a list of messages
    response_data = response.json()
    assert isinstance(response_data, list)
    assert len(response_data) > 0

def test_get_message():
    # Create a sample message
    create_response = client.post("/api/v1/messages/", json=valid_message_data)
    message_id = create_response.json()["id"]
    
    # Send a GET request to retrieve the specific message
    response = client.get(f"/api/v1/messages/{message_id}")
    
    # Verify that the request was successful
    assert response.status_code == status.HTTP_200_OK
    
    # Verify that the response data matches the created message
    response_data = response.json()
    assert response_data["id"] == message_id
    assert response_data["content"] == valid_message_data["content"]

def test_update_message():
    # Create a sample message
    create_response = client.post("/api/v1/messages/", json=valid_message_data)
    message_id = create_response.json()["id"]
    
    # Update the message content
    updated_data = {
        "content": "Updated content"
    }
    response = client.put(f"/api/v1/messages/{message_id}", json=updated_data)
    
    # Verify that the update request was successful
    assert response.status_code == status.HTTP_200_OK
    
    # Verify that the content was updated
    response_data = response.json()
    assert response_data["content"] == updated_data["content"]

def test_delete_message():
    # Create a sample message
    create_response = client.post("/api/v1/messages/", json=valid_message_data)
    message_id = create_response.json()["id"]
    
    # Send a DELETE request to remove the message
    delete_response = client.delete(f"/api/v1/messages/{message_id}")
    
    # Verify that the delete request was successful
    assert delete_response.status_code == status.HTTP_200_OK
    
    # Verify that the message no longer exists
    get_response = client.get(f"/api/v1/messages/{message_id}")
    assert get_response.status_code == status.HTTP_404_NOT_FOUND