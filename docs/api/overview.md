# API Reference Overview

The DIALOGIX API is a RESTful web service that enables you to programmatically interact with all platform features. Whether you're building integrations, custom dashboards, or mobile applications, our API provides comprehensive access to your chatbot data and functionality.

## Base URL

```
https://api.dialogix.com/api/v1/
```

For local development:
```
http://localhost:8000/api/v1/
```

## Authentication

DIALOGIX uses **JWT (JSON Web Tokens)** for API authentication. All API requests must include a valid JWT token in the `Authorization` header.

### Getting a Token

```bash
POST /api/v1/token/
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "your-password"
}
```

**Response:**
```json
{
  "access": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...",
  "user": {
    "email": "user@example.com",
    "tenant_id": "123e4567-e89b-12d3-a456-426614174000"
  }
}
```

### Using the Token

Include the access token in all subsequent requests:

```bash
GET /api/v1/tenants/my-tenant/
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9...
Content-Type: application/json
```

### Token Refresh

Access tokens expire after 1 hour. Use the refresh token to get a new access token:

```bash
POST /api/v1/token/refresh/
Content-Type: application/json

{
  "refresh": "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9..."
}
```

## API Endpoints Overview

### Core Resources

| Resource | Endpoint | Description |
|----------|----------|-------------|
| **Authentication** | `/token/` | Login, refresh tokens |
| **Tenants** | `/tenants/` | Tenant management and configuration |
| **Plans** | `/plans/` | Available subscription plans |
| **Subscriptions** | `/subscriptions/` | User subscription management |
| **Conversations** | `/conversations/` | Chat conversations and messages |
| **Channels** | `/channels/` | Communication channel setup |
| **Escalation** | `/escalation/` | Escalation rules and routing |

### Quick Examples

#### Get Tenant Information
```bash
GET /api/v1/tenants/my-tenant/
Authorization: Bearer <token>
```

#### List Subscription Plans
```bash
GET /api/v1/plans/
Authorization: Bearer <token>
```

#### Get Dashboard Metrics
```bash
GET /api/v1/subscriptions/my-subscription/
Authorization: Bearer <token>
```

## Response Format

All API responses follow a consistent JSON format:

### Success Response
```json
{
  "data": {
    "id": "123",
    "name": "Example Resource",
    "created_at": "2024-01-15T10:30:00Z"
  },
  "meta": {
    "timestamp": "2024-01-15T10:30:00Z"
  }
}
```

### Error Response
```json
{
  "error": {
    "code": "VALIDATION_ERROR", 
    "message": "Invalid input data",
    "details": {
      "field_name": ["This field is required"]
    }
  },
  "meta": {
    "timestamp": "2024-01-15T10:30:00Z"
  }
}
```

## HTTP Status Codes

| Code | Status | Description |
|------|--------|-------------|
| `200` | OK | Request successful |
| `201` | Created | Resource created successfully |
| `400` | Bad Request | Invalid request data |
| `401` | Unauthorized | Invalid or missing authentication |
| `403` | Forbidden | Insufficient permissions |
| `404` | Not Found | Resource not found |
| `429` | Too Many Requests | Rate limit exceeded |
| `500` | Internal Server Error | Server error |

## Rate Limiting

API requests are rate limited based on your subscription plan:

| Plan | Requests per Hour | Burst Limit |
|------|------------------|-------------|
| **Free** | 100 | 20 |
| **Starter** | 1,000 | 50 |
| **Professional** | 10,000 | 200 |
| **Enterprise** | Unlimited | 500 |

Rate limit headers are included in all responses:

```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 999
X-RateLimit-Reset: 1640995200
```

## Pagination

List endpoints support pagination using cursor-based pagination:

### Request
```bash
GET /api/v1/conversations/?limit=50&cursor=eyJjcmVhdGVkX2F0IjoiMjAyNC0wMS0xNSJ9
```

### Response
```json
{
  "data": [
    // ... conversation objects
  ],
  "pagination": {
    "next": "eyJjcmVhdGVkX2F0IjoiMjAyNC0wMS0xNiJ9",
    "previous": null,
    "count": 150,
    "limit": 50
  }
}
```

## Filtering and Sorting

Many endpoints support filtering and sorting:

### Filtering
```bash
GET /api/v1/conversations/?status=active&created_after=2024-01-01
```

### Sorting
```bash
GET /api/v1/conversations/?ordering=-created_at
```

Available sort fields vary by endpoint (use `-` prefix for descending order).

## Webhooks

DIALOGIX can send webhook events to your application when certain events occur:

- New conversation started
- Message received/sent  
- Conversation escalated
- Subscription changed

Configure webhooks in your dashboard

## SDKs and Libraries

Official SDKs are available for popular programming languages:

=== "Python"

    ```python
    from dialogix import DialogixClient
    
    client = DialogixClient(
        api_key="your-api-key",
        base_url="https://api.dialogix.com"
    )
    
    tenant = client.tenants.get_my_tenant()
    ```

=== "JavaScript/Node.js"

    ```javascript
    import { DialogixClient } from '@dialogix/sdk';
    
    const client = new DialogixClient({
      apiKey: 'your-api-key',
      baseURL: 'https://api.dialogix.com'
    });
    
    const tenant = await client.tenants.getMyTenant();
    ```

=== "cURL"

    ```bash
    curl -X GET "https://api.dialogix.com/api/v1/tenants/my-tenant/" \
         -H "Authorization: Bearer your-jwt-token" \
         -H "Content-Type: application/json"
    ```

## API Versioning

The API is versioned using URL path versioning. The current version is `v1`.

- Current: `/api/v1/`
- Future: `/api/v2/` (when available)

We maintain backwards compatibility and provide migration guides for major version updates.

## Next Steps

<div class="grid cards" markdown>

### 🔐 Authentication
Detailed guide on JWT authentication, token management, and security best practices.

### 👥 Tenants API  
Complete reference for tenant management, configuration, and AI settings.

### 💬 Conversations API
Handle chat conversations, messages, and real-time communication.

### ⚠️ Error Handling
Complete list of error codes, status codes, and troubleshooting guide.

</div>

---

!!! tip "API Explorer"

    Use our interactive API explorer to test endpoints directly from your browser:
    
    [Open API Explorer →](https://api.dialogix.com/docs/)
    
    The explorer provides real-time testing, request/response examples, and automatic code generation.