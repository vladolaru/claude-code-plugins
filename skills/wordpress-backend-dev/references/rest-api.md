# WordPress REST API Development

Comprehensive guide to building REST API endpoints in WordPress.

Based on official WordPress handbook: https://developer.wordpress.org/rest-api/

## Key Concepts

### Routes vs Endpoints

**Routes** are the URL "names" used to access resources (e.g., `/wp/v2/posts/123`).
**Endpoints** are the functions available through the API that handle specific operations on routes.

A single route can host multiple endpoints distinguished by HTTP methods:
- `GET /wp/v2/posts/123` - Retrieve a post
- `PUT /wp/v2/posts/123` - Update a post
- `DELETE /wp/v2/posts/123` - Delete a post

### Namespacing

Namespaces prevent route collisions between plugins. Format: `vendor/version/resource`.

```
wp/v2              # WordPress core - NEVER use for custom endpoints
myplugin/v1        # Custom plugin namespace
myplugin/v1           # WooCommerce Payments namespace
```

**Rules:**
- Never place custom endpoints in the `wp` namespace
- Use versioning (v1, v2) for backwards compatibility when API changes

### Requests and Responses

**WP_REST_Request** - Stores and retrieves information about incoming requests:
- HTTP method (GET, POST, PUT, DELETE)
- Route being accessed
- Parameters (URL, query, body)
- Headers

**WP_REST_Response** - Manages response data returned by endpoints:
- Response data (automatically JSON-encoded)
- HTTP status code
- Headers

### Schema

JSON Schema (Draft 4) documents data structures for:
- **Resource Schema**: Fields present in response objects
- **Argument Schema**: Validates incoming request parameters

Schema provides security through validation and sanitization.

## HTTP Methods

```php
// WP_REST_Server constants
WP_REST_Server::READABLE   // GET
WP_REST_Server::CREATABLE  // POST
WP_REST_Server::EDITABLE   // POST, PUT, PATCH
WP_REST_Server::DELETABLE  // DELETE
WP_REST_Server::ALLMETHODS // All methods

// Or use strings
'methods' => 'GET'
'methods' => 'POST, PUT'
'methods' => [ 'GET', 'POST' ]
```

**Idempotence**: GET, PUT, DELETE operations should produce the same result when repeated. POST is not idempotent (creates new resources).

## Global Parameters

These meta-parameters work across all endpoints:

### _fields

Restrict responses to specific fields for better performance:

```
/wp/v2/posts?_fields=author,id,title,link
/wp/v2/posts?_fields[]=author&_fields[]=id
/wp/v2/posts?_fields=meta.custom_key           # Nested (WP 5.3+)
```

**Key benefit**: WordPress skips unneeded fields during response generation, avoiding expensive computation.

### _embed

Include linked resources to reduce HTTP requests:

```
/wp/v2/posts?_embed                            # All embeddable
/wp/v2/posts?_embed=author,wp:term             # Specific (WP 5.4+)
/wp/v2/posts?_embed=author&_fields=title,_embedded
```

Only links with `embeddable: true` can be embedded. Results appear under `_embedded` key.

### _method (or X-HTTP-Method-Override header)

Override HTTP methods for incompatible servers:

```
POST /wp-json/wp/v2/posts/42?_method=DELETE
```

Or use header: `X-HTTP-Method-Override: DELETE`

**Important**: Only send method overrides with POST requests.

### _envelope

Wrap response data for problematic proxies:

```
/wp/v2/posts?_envelope
```

Returns HTTP 200 with status, headers, and body in JSON structure.

### _jsonp

Enable JSONP for cross-domain requests in legacy browsers:

```
/wp/v2/posts?_jsonp=receiveData
```

Allowed characters: alphanumeric, underscore, period only.

## Pagination

### Parameters

| Parameter | Description | Range |
|-----------|-------------|-------|
| `page` | Page number to retrieve | 1+ |
| `per_page` | Records per request | 1-100 |
| `offset` | Skip N records from start | 0+ |

```
/wp/v2/posts?page=2&per_page=20
/wp/v2/posts?per_page=5&offset=15   # Same as page=4&per_page=5
```

### Response Headers

Every paginated response includes:
- `X-WP-Total` - Total records in collection
- `X-WP-TotalPages` - Total pages available

### Ordering

```
/wp/v2/posts?order=asc&orderby=title
/wp/v2/posts?order=desc&orderby=date    # Default
```

Valid `orderby` values depend on resource type (date, title, id, slug, etc.).

## Linking and Embedding

### _links Property

Contains related API resources grouped by relation type:

```json
{
  "id": 42,
  "_links": {
    "self": [{"href": "https://example.com/wp-json/wp/v2/posts/42"}],
    "collection": [{"href": "https://example.com/wp-json/wp/v2/posts"}],
    "author": [{
      "href": "https://example.com/wp-json/wp/v2/users/1",
      "embeddable": true
    }]
  }
}
```

### _embedded Property

When using `?_embed`, linked resources appear under `_embedded`:

```json
{
  "id": 42,
  "title": {"rendered": "Hello World"},
  "_embedded": {
    "author": [{
      "id": 1,
      "name": "admin"
    }]
  }
}
```

## Discovery

### Link Header (Preferred)

WordPress adds a Link header to all front-end pages:

```
Link: <http://example.com/wp-json/>; rel="https://api.w.org/"
```

For non-pretty permalinks:
```
Link: <http://example.com/?rest_route=/>; rel="https://api.w.org/"
```

### HTML Link Element

```html
<link rel='https://api.w.org/' href='http://example.com/wp-json/' />
```

### API Index

The root endpoint (`/wp-json/`) returns:
- Site information
- Available namespaces
- Authentication methods
- All registered routes

Check for `wp/v2` in namespaces to confirm full API availability.

## Internal Requests

Make REST API calls from PHP without HTTP overhead:

```php
$request = new WP_REST_Request( 'GET', '/wp/v2/posts' );
$request->set_param( 'per_page', 5 );

$response = rest_do_request( $request );

if ( $response->is_error() ) {
    $error = $response->as_error();
    // Handle error
}

$data = $response->get_data();
```

## Related Reference Files

- **rest-api-endpoints.md** - Route registration, parameters, validation
- **rest-api-authentication.md** - Auth methods, nonces, application passwords
- **rest-api-controllers.md** - Controller classes and patterns

## Quick Reference

### Register a Simple Endpoint

```php
add_action( 'rest_api_init', function() {
    register_rest_route( 'myplugin/v1', '/status', [
        'methods'             => 'GET',
        'callback'            => 'myplugin_get_status',
        'permission_callback' => '__return_true',
    ] );
} );

function myplugin_get_status( WP_REST_Request $request ) {
    return rest_ensure_response( [ 'status' => 'active' ] );
}
```

### Return an Error

```php
return new WP_Error(
    'not_found',
    __( 'Resource not found.', 'text-domain' ),
    [ 'status' => 404 ]
);
```

### Check Permission

```php
'permission_callback' => function() {
    return current_user_can( 'manage_options' );
}
```
