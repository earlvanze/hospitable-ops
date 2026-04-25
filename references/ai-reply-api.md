# Hospitable API Reference

## Authentication
All requests must include the `Authorization` header with the API key:
`Authorization: Bearer <API_KEY>`

## Endpoints (Hypothetical - Requires Verification)

### 1. Generate AI Suggestion
**POST** `/v2/conversations/{conversation_id}/suggest`
- **Response**: `{ "suggestion": "Hello guest, thank you for booking..." }`

### 2. Send Message
**POST** `/v2/conversations/{conversation_id}/messages`
- **Body**: `{ "body": "Message text here" }`

*Note: If the "Suggest with AI" button in the UI corresponds to a specific internal endpoint, it should be mapped here.*
