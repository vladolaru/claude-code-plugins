# WordPress HTTP API

Comprehensive guide to making HTTP requests in WordPress for interacting with external APIs and web services.

## Overview

The WordPress HTTP API provides a unified interface for making HTTP requests, abstracting away server configuration differences. It supports multiple HTTP transports and automatically selects the best available method.

**Use Cases:**
- Fetching data from external APIs (payment gateways, shipping providers)
- Sending data to remote services (webhooks, notifications)
- Checking resource availability without downloading (HEAD requests)
- Integrating with third-party services (social media, analytics)

## HTTP Methods

| Method | Purpose | WordPress Function |
|--------|---------|-------------------|
| GET | Retrieve data | `wp_remote_get()` |
| POST | Send/create data | `wp_remote_post()` |
| HEAD | Get headers only | `wp_remote_head()` |
| PUT | Update data | `wp_remote_request()` |
| DELETE | Remove data | `wp_remote_request()` |
| PATCH | Partial update | `wp_remote_request()` |

## HTTP Response Codes

| Range | Category | Common Codes |
|-------|----------|--------------|
| 2xx | Success | 200 OK, 201 Created, 204 No Content |
| 3xx | Redirect | 301 Moved Permanently, 302 Found, 304 Not Modified |
| 4xx | Client Error | 400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 429 Too Many Requests |
| 5xx | Server Error | 500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable |

## GET Requests

### wp_remote_get()

```php
/**
 * Performs an HTTP GET request.
 *
 * @param string $url  URL to retrieve.
 * @param array  $args Optional. Request arguments.
 * @return array|WP_Error Response array or WP_Error on failure.
 */
wp_remote_get( $url, $args );
```

**Basic Example:**

```php
$response = wp_remote_get( 'https://api.example.com/data' );

if ( is_wp_error( $response ) ) {
    $error_message = $response->get_error_message();
    // Handle error
    return;
}

$body = wp_remote_retrieve_body( $response );
$data = json_decode( $body, true );
```

**With Arguments:**

```php
$response = wp_remote_get(
    'https://api.example.com/data',
    [
        'timeout'     => 30,
        'redirection' => 5,
        'httpversion' => '1.1',
        'headers'     => [
            'Accept'        => 'application/json',
            'Authorization' => 'Bearer ' . $api_key,
        ],
        'cookies'     => [],
        'sslverify'   => true,
    ]
);
```

### Complete GET Request Pattern

```php
/**
 * Fetches payment methods from external API.
 *
 * @since 1.0.0
 *
 * @param string $account_id The account ID.
 * @return array|WP_Error Payment methods on success, WP_Error on failure.
 */
function myplugin_fetch_payment_methods( $account_id ) {
    $url = sprintf(
        'https://api.stripe.com/v1/accounts/%s/payment_methods',
        rawurlencode( $account_id )
    );

    $response = wp_remote_get(
        $url,
        [
            'timeout' => 30,
            'headers' => [
                'Authorization' => 'Bearer ' . myplugin_get_api_key(),
                'Content-Type'  => 'application/json',
            ],
        ]
    );

    // Check for WP_Error (connection failure, timeout, etc.)
    if ( is_wp_error( $response ) ) {
        return new WP_Error(
            'api_connection_error',
            sprintf(
                /* translators: %s: Error message */
                __( 'API connection failed: %s', 'my-plugin' ),
                $response->get_error_message()
            )
        );
    }

    // Check HTTP status code
    $status_code = wp_remote_retrieve_response_code( $response );

    if ( 200 !== $status_code ) {
        $body = wp_remote_retrieve_body( $response );
        $error = json_decode( $body, true );

        return new WP_Error(
            'api_error',
            isset( $error['message'] ) ? $error['message'] : __( 'Unknown API error', 'my-plugin' ),
            [ 'status' => $status_code ]
        );
    }

    // Parse response body
    $body = wp_remote_retrieve_body( $response );
    $data = json_decode( $body, true );

    if ( null === $data ) {
        return new WP_Error(
            'invalid_response',
            __( 'Invalid JSON response from API', 'my-plugin' )
        );
    }

    return $data;
}
```

## POST Requests

### wp_remote_post()

```php
/**
 * Performs an HTTP POST request.
 *
 * @param string $url  URL to post to.
 * @param array  $args Optional. Request arguments.
 * @return array|WP_Error Response array or WP_Error on failure.
 */
wp_remote_post( $url, $args );
```

**Basic Example:**

```php
$response = wp_remote_post(
    'https://api.example.com/orders',
    [
        'body' => [
            'product_id' => 123,
            'quantity'   => 2,
            'customer'   => 'john@example.com',
        ],
    ]
);
```

**JSON Body:**

```php
$response = wp_remote_post(
    'https://api.example.com/orders',
    [
        'headers' => [
            'Content-Type'  => 'application/json',
            'Authorization' => 'Bearer ' . $api_key,
        ],
        'body'    => wp_json_encode( [
            'product_id' => 123,
            'quantity'   => 2,
            'customer'   => [
                'email' => 'john@example.com',
                'name'  => 'John Doe',
            ],
        ] ),
        'timeout' => 30,
    ]
);
```

### Complete POST Request Pattern

```php
/**
 * Creates a payment intent via external API.
 *
 * @since 1.0.0
 *
 * @param array $payment_data Payment data.
 * @return array|WP_Error Payment intent on success, WP_Error on failure.
 */
function myplugin_create_payment_intent( $payment_data ) {
    $url = 'https://api.stripe.com/v1/payment_intents';

    $body = [
        'amount'               => absint( $payment_data['amount'] ),
        'currency'             => sanitize_text_field( $payment_data['currency'] ),
        'payment_method_types' => [ 'card' ],
        'metadata'             => [
            'order_id' => absint( $payment_data['order_id'] ),
        ],
    ];

    $response = wp_remote_post(
        $url,
        [
            'timeout' => 30,
            'headers' => [
                'Authorization' => 'Bearer ' . myplugin_get_secret_key(),
                'Content-Type'  => 'application/x-www-form-urlencoded',
            ],
            'body'    => $body,
        ]
    );

    if ( is_wp_error( $response ) ) {
        myplugin_log( 'Payment intent creation failed: ' . $response->get_error_message(), 'error' );

        return new WP_Error(
            'connection_error',
            __( 'Could not connect to payment service.', 'my-plugin' )
        );
    }

    $status_code = wp_remote_retrieve_response_code( $response );
    $body = wp_remote_retrieve_body( $response );
    $data = json_decode( $body, true );

    if ( $status_code >= 400 ) {
        $error_message = isset( $data['error']['message'] )
            ? $data['error']['message']
            : __( 'Payment processing failed.', 'my-plugin' );

        myplugin_log( 'Payment intent error: ' . $error_message, 'error' );

        return new WP_Error( 'payment_error', $error_message );
    }

    return $data;
}
```

## HEAD Requests

### wp_remote_head()

Use HEAD requests to check resource metadata without downloading the full response body. Useful for:
- Checking if content has changed (Last-Modified header)
- Verifying resource exists before downloading
- Checking rate limits
- Getting content size

```php
/**
 * Checks if remote resource has been modified.
 *
 * @since 1.0.0
 *
 * @param string $url           Resource URL.
 * @param string $last_modified Stored Last-Modified value.
 * @return bool True if modified, false otherwise.
 */
function myplugin_is_resource_modified( $url, $last_modified ) {
    $response = wp_remote_head(
        $url,
        [
            'timeout' => 10,
            'headers' => [
                'If-Modified-Since' => $last_modified,
            ],
        ]
    );

    if ( is_wp_error( $response ) ) {
        // Assume modified on error to trigger refresh
        return true;
    }

    $status_code = wp_remote_retrieve_response_code( $response );

    // 304 = Not Modified
    if ( 304 === $status_code ) {
        return false;
    }

    return true;
}
```

**Checking Rate Limits:**

```php
/**
 * Checks API rate limit status.
 *
 * @since 1.0.0
 *
 * @return array Rate limit information.
 */
function myplugin_check_rate_limit() {
    $response = wp_remote_head(
        'https://api.example.com/status',
        [
            'headers' => [
                'Authorization' => 'Bearer ' . myplugin_get_api_key(),
            ],
        ]
    );

    if ( is_wp_error( $response ) ) {
        return [
            'available' => false,
            'error'     => $response->get_error_message(),
        ];
    }

    return [
        'available'  => true,
        'limit'      => wp_remote_retrieve_header( $response, 'x-ratelimit-limit' ),
        'remaining'  => wp_remote_retrieve_header( $response, 'x-ratelimit-remaining' ),
        'reset'      => wp_remote_retrieve_header( $response, 'x-ratelimit-reset' ),
    ];
}
```

## Custom HTTP Methods (PUT, DELETE, PATCH)

### wp_remote_request()

For HTTP methods beyond GET, POST, and HEAD:

```php
/**
 * Performs a custom HTTP request.
 *
 * @param string $url  URL for the request.
 * @param array  $args Request arguments including 'method'.
 * @return array|WP_Error Response array or WP_Error on failure.
 */
wp_remote_request( $url, $args );
```

**PUT Request:**

```php
/**
 * Updates a resource via PUT request.
 *
 * @since 1.0.0
 *
 * @param string $resource_id Resource ID.
 * @param array  $data        Data to update.
 * @return array|WP_Error Updated resource or WP_Error.
 */
function myplugin_update_resource( $resource_id, $data ) {
    $url = 'https://api.example.com/resources/' . rawurlencode( $resource_id );

    $response = wp_remote_request(
        $url,
        [
            'method'  => 'PUT',
            'timeout' => 30,
            'headers' => [
                'Authorization' => 'Bearer ' . myplugin_get_api_key(),
                'Content-Type'  => 'application/json',
            ],
            'body'    => wp_json_encode( $data ),
        ]
    );

    if ( is_wp_error( $response ) ) {
        return $response;
    }

    $status_code = wp_remote_retrieve_response_code( $response );
    $body = json_decode( wp_remote_retrieve_body( $response ), true );

    if ( $status_code >= 400 ) {
        return new WP_Error(
            'update_failed',
            $body['message'] ?? __( 'Update failed', 'my-plugin' )
        );
    }

    return $body;
}
```

**DELETE Request:**

```php
/**
 * Deletes a resource via DELETE request.
 *
 * @since 1.0.0
 *
 * @param string $resource_id Resource ID.
 * @return bool|WP_Error True on success, WP_Error on failure.
 */
function myplugin_delete_resource( $resource_id ) {
    $url = 'https://api.example.com/resources/' . rawurlencode( $resource_id );

    $response = wp_remote_request(
        $url,
        [
            'method'  => 'DELETE',
            'timeout' => 30,
            'headers' => [
                'Authorization' => 'Bearer ' . myplugin_get_api_key(),
            ],
        ]
    );

    if ( is_wp_error( $response ) ) {
        return $response;
    }

    $status_code = wp_remote_retrieve_response_code( $response );

    // 204 No Content or 200 OK indicates success
    if ( in_array( $status_code, [ 200, 204 ], true ) ) {
        return true;
    }

    $body = json_decode( wp_remote_retrieve_body( $response ), true );

    return new WP_Error(
        'delete_failed',
        $body['message'] ?? __( 'Delete failed', 'my-plugin' )
    );
}
```

**PATCH Request:**

```php
/**
 * Partially updates a resource via PATCH request.
 *
 * @since 1.0.0
 *
 * @param string $resource_id Resource ID.
 * @param array  $changes     Fields to update.
 * @return array|WP_Error Updated resource or WP_Error.
 */
function myplugin_patch_resource( $resource_id, $changes ) {
    $url = 'https://api.example.com/resources/' . rawurlencode( $resource_id );

    $response = wp_remote_request(
        $url,
        [
            'method'  => 'PATCH',
            'timeout' => 30,
            'headers' => [
                'Authorization' => 'Bearer ' . myplugin_get_api_key(),
                'Content-Type'  => 'application/json',
            ],
            'body'    => wp_json_encode( $changes ),
        ]
    );

    if ( is_wp_error( $response ) ) {
        return $response;
    }

    $status_code = wp_remote_retrieve_response_code( $response );
    $body = json_decode( wp_remote_retrieve_body( $response ), true );

    if ( $status_code >= 400 ) {
        return new WP_Error(
            'patch_failed',
            $body['message'] ?? __( 'Update failed', 'my-plugin' )
        );
    }

    return $body;
}
```

## Request Arguments

All HTTP functions accept an `$args` array with these options:

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `method` | string | 'GET' | HTTP method (for `wp_remote_request`) |
| `timeout` | float | 5 | Request timeout in seconds |
| `redirection` | int | 5 | Number of redirects to follow |
| `httpversion` | string | '1.0' | HTTP version ('1.0' or '1.1') |
| `user-agent` | string | WordPress/x.x | User agent string |
| `blocking` | bool | true | Whether to wait for response |
| `headers` | array | [] | Request headers |
| `cookies` | array | [] | Cookies to send |
| `body` | string\|array | '' | Request body |
| `compress` | bool | false | Whether to compress body |
| `decompress` | bool | true | Whether to decompress response |
| `sslverify` | bool | true | Verify SSL certificate |
| `sslcertificates` | string | (path) | Path to CA bundle |
| `stream` | bool | false | Stream response to file |
| `filename` | string | '' | Filename for streaming |
| `limit_response_size` | int | null | Max response size in bytes |

### Timeout Configuration

```php
// Short timeout for non-critical requests
$response = wp_remote_get( $url, [ 'timeout' => 5 ] );

// Longer timeout for complex operations
$response = wp_remote_post( $url, [ 'timeout' => 60 ] );

// Very long timeout (use sparingly)
$response = wp_remote_get( $url, [ 'timeout' => 300 ] );
```

### Non-Blocking Requests

For fire-and-forget requests (webhooks, analytics):

```php
/**
 * Sends analytics event without waiting for response.
 *
 * @since 1.0.0
 *
 * @param array $event Event data.
 */
function myplugin_send_analytics( $event ) {
    wp_remote_post(
        'https://analytics.example.com/events',
        [
            'blocking' => false,  // Don't wait for response
            'timeout'  => 0.01,   // Minimal timeout
            'body'     => wp_json_encode( $event ),
            'headers'  => [
                'Content-Type' => 'application/json',
            ],
        ]
    );
}
```

## Response Helper Functions

### wp_remote_retrieve_body()

```php
$body = wp_remote_retrieve_body( $response );

// Parse JSON
$data = json_decode( $body, true );

// Check for JSON errors
if ( json_last_error() !== JSON_ERROR_NONE ) {
    // Invalid JSON
}
```

### wp_remote_retrieve_response_code()

```php
$status_code = wp_remote_retrieve_response_code( $response );

if ( 200 === $status_code ) {
    // Success
} elseif ( 401 === $status_code ) {
    // Unauthorized
} elseif ( 404 === $status_code ) {
    // Not found
} elseif ( $status_code >= 500 ) {
    // Server error
}
```

### wp_remote_retrieve_response_message()

```php
$message = wp_remote_retrieve_response_message( $response );
// Returns: "OK", "Not Found", "Internal Server Error", etc.
```

### wp_remote_retrieve_header()

```php
// Get single header (case-insensitive)
$content_type = wp_remote_retrieve_header( $response, 'content-type' );
$rate_limit = wp_remote_retrieve_header( $response, 'x-ratelimit-remaining' );
```

### wp_remote_retrieve_headers()

```php
// Get all headers as Requests_Utility_CaseInsensitiveDictionary
$headers = wp_remote_retrieve_headers( $response );

// Access headers
$content_type = $headers['content-type'];

// Convert to array
$headers_array = $headers->getAll();
```

### wp_remote_retrieve_cookies()

```php
// Get cookies from response
$cookies = wp_remote_retrieve_cookies( $response );

foreach ( $cookies as $cookie ) {
    $name = $cookie->name;
    $value = $cookie->value;
}
```

## Authentication Methods

### Basic Authentication

```php
$response = wp_remote_get(
    $url,
    [
        'headers' => [
            'Authorization' => 'Basic ' . base64_encode( $username . ':' . $password ),
        ],
    ]
);
```

### Bearer Token

```php
$response = wp_remote_get(
    $url,
    [
        'headers' => [
            'Authorization' => 'Bearer ' . $access_token,
        ],
    ]
);
```

### API Key (Header)

```php
$response = wp_remote_get(
    $url,
    [
        'headers' => [
            'X-API-Key' => $api_key,
        ],
    ]
);
```

### API Key (Query Parameter)

```php
$url = add_query_arg( 'api_key', $api_key, $base_url );
$response = wp_remote_get( $url );
```

### OAuth 2.0

```php
/**
 * Makes an authenticated OAuth request.
 *
 * @since 1.0.0
 *
 * @param string $url    Request URL.
 * @param string $method HTTP method.
 * @param array  $body   Request body.
 * @return array|WP_Error Response or error.
 */
function myplugin_oauth_request( $url, $method = 'GET', $body = [] ) {
    $access_token = myplugin_get_access_token();

    // Check if token needs refresh
    if ( myplugin_is_token_expired() ) {
        $access_token = myplugin_refresh_access_token();

        if ( is_wp_error( $access_token ) ) {
            return $access_token;
        }
    }

    $args = [
        'method'  => $method,
        'timeout' => 30,
        'headers' => [
            'Authorization' => 'Bearer ' . $access_token,
            'Content-Type'  => 'application/json',
        ],
    ];

    if ( ! empty( $body ) && in_array( $method, [ 'POST', 'PUT', 'PATCH' ], true ) ) {
        $args['body'] = wp_json_encode( $body );
    }

    return wp_remote_request( $url, $args );
}
```

## Error Handling

### Comprehensive Error Handling Pattern

```php
/**
 * Makes an API request with comprehensive error handling.
 *
 * @since 1.0.0
 *
 * @param string $endpoint API endpoint.
 * @param string $method   HTTP method.
 * @param array  $data     Request data.
 * @return array|WP_Error Response data or error.
 */
function myplugin_api_request( $endpoint, $method = 'GET', $data = [] ) {
    $url = 'https://api.example.com/v1/' . ltrim( $endpoint, '/' );

    $args = [
        'method'  => $method,
        'timeout' => 30,
        'headers' => [
            'Authorization' => 'Bearer ' . myplugin_get_api_key(),
            'Content-Type'  => 'application/json',
            'Accept'        => 'application/json',
        ],
    ];

    if ( ! empty( $data ) && in_array( $method, [ 'POST', 'PUT', 'PATCH' ], true ) ) {
        $args['body'] = wp_json_encode( $data );
    }

    $response = wp_remote_request( $url, $args );

    // Connection error (timeout, DNS failure, etc.)
    if ( is_wp_error( $response ) ) {
        myplugin_log(
            sprintf( 'API connection error: %s', $response->get_error_message() ),
            'error'
        );

        return new WP_Error(
            'connection_error',
            __( 'Could not connect to the payment service. Please try again.', 'my-plugin' ),
            [ 'original_error' => $response->get_error_message() ]
        );
    }

    $status_code = wp_remote_retrieve_response_code( $response );
    $body = wp_remote_retrieve_body( $response );
    $data = json_decode( $body, true );

    // JSON parse error
    if ( null === $data && ! empty( $body ) ) {
        myplugin_log( 'API returned invalid JSON: ' . substr( $body, 0, 500 ), 'error' );

        return new WP_Error(
            'invalid_response',
            __( 'Received invalid response from payment service.', 'my-plugin' )
        );
    }

    // Client errors (4xx)
    if ( $status_code >= 400 && $status_code < 500 ) {
        $error_code = $data['error']['code'] ?? 'client_error';
        $error_message = $data['error']['message'] ?? __( 'Request failed', 'my-plugin' );

        myplugin_log(
            sprintf( 'API client error %d: %s', $status_code, $error_message ),
            'error'
        );

        // Map specific error codes to user-friendly messages
        $user_message = myplugin_get_user_error_message( $error_code, $error_message );

        return new WP_Error( $error_code, $user_message, [
            'status'   => $status_code,
            'response' => $data,
        ] );
    }

    // Server errors (5xx)
    if ( $status_code >= 500 ) {
        myplugin_log(
            sprintf( 'API server error %d: %s', $status_code, $body ),
            'error'
        );

        return new WP_Error(
            'server_error',
            __( 'The payment service is temporarily unavailable. Please try again later.', 'my-plugin' ),
            [ 'status' => $status_code ]
        );
    }

    return $data;
}

/**
 * Maps API error codes to user-friendly messages.
 *
 * @since 1.0.0
 *
 * @param string $code    Error code.
 * @param string $default Default message.
 * @return string User-friendly message.
 */
function myplugin_get_user_error_message( $code, $default ) {
    $messages = [
        'card_declined'        => __( 'Your card was declined. Please try a different payment method.', 'my-plugin' ),
        'expired_card'         => __( 'Your card has expired. Please use a different card.', 'my-plugin' ),
        'insufficient_funds'   => __( 'Your card has insufficient funds.', 'my-plugin' ),
        'invalid_cvc'          => __( 'Your card security code is incorrect.', 'my-plugin' ),
        'rate_limit_exceeded'  => __( 'Too many requests. Please wait a moment and try again.', 'my-plugin' ),
        'authentication_error' => __( 'Authentication failed. Please contact support.', 'my-plugin' ),
    ];

    return $messages[ $code ] ?? $default;
}
```

## Caching API Responses

Use transients to cache expensive or rate-limited API calls:

```php
/**
 * Gets payment methods with caching.
 *
 * @since 1.0.0
 *
 * @param bool $force_refresh Force cache refresh.
 * @return array|WP_Error Payment methods or error.
 */
function myplugin_get_payment_methods_cached( $force_refresh = false ) {
    $cache_key = 'myplugin_payment_methods';

    // Check cache first
    if ( ! $force_refresh ) {
        $cached = get_transient( $cache_key );
        if ( false !== $cached ) {
            return $cached;
        }
    }

    // Fetch from API
    $response = wp_remote_get(
        'https://api.example.com/payment_methods',
        [
            'timeout' => 30,
            'headers' => [
                'Authorization' => 'Bearer ' . myplugin_get_api_key(),
            ],
        ]
    );

    if ( is_wp_error( $response ) ) {
        return $response;
    }

    $status_code = wp_remote_retrieve_response_code( $response );

    if ( 200 !== $status_code ) {
        return new WP_Error( 'api_error', __( 'Failed to fetch payment methods', 'my-plugin' ) );
    }

    $body = wp_remote_retrieve_body( $response );
    $data = json_decode( $body, true );

    // Cache successful response
    set_transient( $cache_key, $data, HOUR_IN_SECONDS );

    return $data;
}

/**
 * Clears payment methods cache.
 *
 * @since 1.0.0
 */
function myplugin_clear_payment_methods_cache() {
    delete_transient( 'myplugin_payment_methods' );
}

// Clear cache when settings change
add_action( 'myplugin_settings_updated', 'myplugin_clear_payment_methods_cache' );
```

## Retry Logic

Implement retry logic for transient failures:

```php
/**
 * Makes an API request with retry logic.
 *
 * @since 1.0.0
 *
 * @param string $url         Request URL.
 * @param array  $args        Request arguments.
 * @param int    $max_retries Maximum retry attempts.
 * @return array|WP_Error Response or error.
 */
function myplugin_request_with_retry( $url, $args = [], $max_retries = 3 ) {
    $attempt = 0;
    $last_error = null;

    while ( $attempt < $max_retries ) {
        $attempt++;

        $response = wp_remote_request( $url, $args );

        // Connection error - retry
        if ( is_wp_error( $response ) ) {
            $last_error = $response;
            myplugin_log(
                sprintf( 'Request failed (attempt %d/%d): %s', $attempt, $max_retries, $response->get_error_message() ),
                'warning'
            );

            // Exponential backoff
            if ( $attempt < $max_retries ) {
                sleep( pow( 2, $attempt - 1 ) ); // 1s, 2s, 4s...
            }
            continue;
        }

        $status_code = wp_remote_retrieve_response_code( $response );

        // Server error (5xx) - retry
        if ( $status_code >= 500 ) {
            $last_error = new WP_Error( 'server_error', 'Server error: ' . $status_code );
            myplugin_log(
                sprintf( 'Server error %d (attempt %d/%d)', $status_code, $attempt, $max_retries ),
                'warning'
            );

            if ( $attempt < $max_retries ) {
                sleep( pow( 2, $attempt - 1 ) );
            }
            continue;
        }

        // Rate limited (429) - retry with longer delay
        if ( 429 === $status_code ) {
            $retry_after = wp_remote_retrieve_header( $response, 'retry-after' );
            $wait_time = $retry_after ? intval( $retry_after ) : pow( 2, $attempt );

            myplugin_log(
                sprintf( 'Rate limited, waiting %d seconds (attempt %d/%d)', $wait_time, $attempt, $max_retries ),
                'warning'
            );

            if ( $attempt < $max_retries ) {
                sleep( $wait_time );
            }
            continue;
        }

        // Success or client error (don't retry client errors)
        return $response;
    }

    return $last_error ?? new WP_Error( 'max_retries', 'Maximum retry attempts reached' );
}
```

## SSL Configuration

### Disabling SSL Verification (Development Only!)

```php
// NEVER use in production!
if ( defined( 'WP_DEBUG' ) && WP_DEBUG && 'development' === wp_get_environment_type() ) {
    $response = wp_remote_get( $url, [ 'sslverify' => false ] );
}
```

### Custom CA Bundle

```php
$response = wp_remote_get(
    $url,
    [
        'sslcertificates' => '/path/to/custom-ca-bundle.crt',
    ]
);
```

## Streaming Large Responses

For large file downloads:

```php
/**
 * Downloads a file from URL.
 *
 * @since 1.0.0
 *
 * @param string $url      File URL.
 * @param string $filename Local filename.
 * @return string|WP_Error File path or error.
 */
function myplugin_download_file( $url, $filename ) {
    $upload_dir = wp_upload_dir();
    $file_path = $upload_dir['basedir'] . '/wcpay/' . sanitize_file_name( $filename );

    // Ensure directory exists
    wp_mkdir_p( dirname( $file_path ) );

    $response = wp_remote_get(
        $url,
        [
            'timeout'  => 300,
            'stream'   => true,
            'filename' => $file_path,
        ]
    );

    if ( is_wp_error( $response ) ) {
        return $response;
    }

    $status_code = wp_remote_retrieve_response_code( $response );

    if ( 200 !== $status_code ) {
        // Clean up failed download
        if ( file_exists( $file_path ) ) {
            wp_delete_file( $file_path );
        }

        return new WP_Error( 'download_failed', 'Download failed with status: ' . $status_code );
    }

    return $file_path;
}
```

## Debugging HTTP Requests

### Logging Requests

```php
/**
 * Makes a logged API request.
 *
 * @since 1.0.0
 *
 * @param string $url  Request URL.
 * @param array  $args Request arguments.
 * @return array|WP_Error Response or error.
 */
function myplugin_logged_request( $url, $args = [] ) {
    $start_time = microtime( true );

    // Remove sensitive data from logs
    $logged_args = $args;
    if ( isset( $logged_args['headers']['Authorization'] ) ) {
        $logged_args['headers']['Authorization'] = '[REDACTED]';
    }

    myplugin_log( sprintf(
        'HTTP Request: %s %s | Args: %s',
        $args['method'] ?? 'GET',
        $url,
        wp_json_encode( $logged_args )
    ), 'debug' );

    $response = wp_remote_request( $url, $args );

    $duration = round( ( microtime( true ) - $start_time ) * 1000, 2 );

    if ( is_wp_error( $response ) ) {
        myplugin_log( sprintf(
            'HTTP Error after %sms: %s',
            $duration,
            $response->get_error_message()
        ), 'error' );
    } else {
        myplugin_log( sprintf(
            'HTTP Response: %d | Duration: %sms | Size: %s bytes',
            wp_remote_retrieve_response_code( $response ),
            $duration,
            strlen( wp_remote_retrieve_body( $response ) )
        ), 'debug' );
    }

    return $response;
}
```

### Filter for Debugging

```php
// Log all HTTP requests (development only)
add_filter( 'http_request_args', function( $args, $url ) {
    if ( defined( 'WP_DEBUG_LOG' ) && WP_DEBUG_LOG ) {
        error_log( 'HTTP Request to: ' . $url );
    }
    return $args;
}, 10, 2 );

// Log all HTTP responses (development only)
add_action( 'http_api_debug', function( $response, $context, $class, $args, $url ) {
    if ( defined( 'WP_DEBUG_LOG' ) && WP_DEBUG_LOG ) {
        $status = is_wp_error( $response )
            ? 'Error: ' . $response->get_error_message()
            : 'Status: ' . wp_remote_retrieve_response_code( $response );

        error_log( 'HTTP Response from ' . $url . ' - ' . $status );
    }
}, 10, 5 );
```

## Best Practices

### 1. Always Handle Errors

```php
$response = wp_remote_get( $url );

// Always check for WP_Error
if ( is_wp_error( $response ) ) {
    // Handle connection error
    return;
}

// Always check status code
$status_code = wp_remote_retrieve_response_code( $response );
if ( $status_code >= 400 ) {
    // Handle HTTP error
    return;
}
```

### 2. Set Appropriate Timeouts

```php
// Default 5 seconds may be too short for complex APIs
$response = wp_remote_post( $url, [ 'timeout' => 30 ] );
```

### 3. Use JSON Properly

```php
// Sending JSON
$args = [
    'headers' => [ 'Content-Type' => 'application/json' ],
    'body'    => wp_json_encode( $data ),  // Use wp_json_encode, not json_encode
];

// Receiving JSON
$body = wp_remote_retrieve_body( $response );
$data = json_decode( $body, true );  // true for associative array
```

### 4. Cache When Appropriate

```php
// Cache read-only, infrequently changing data
// Don't cache user-specific or time-sensitive data
```

### 5. Never Disable SSL in Production

```php
// Only for local development with self-signed certs
if ( 'local' === wp_get_environment_type() ) {
    $args['sslverify'] = false;
}
```

### 6. Sanitize URLs

```php
// Validate URL before request
$url = esc_url_raw( $user_provided_url );

if ( empty( $url ) || ! wp_http_validate_url( $url ) ) {
    return new WP_Error( 'invalid_url', 'Invalid URL provided' );
}
```

## Summary Checklist

- [ ] Check for `WP_Error` after every request
- [ ] Verify HTTP status code
- [ ] Handle JSON parsing errors
- [ ] Set appropriate timeout values
- [ ] Use `wp_json_encode()` for JSON bodies
- [ ] Include proper Content-Type headers
- [ ] Implement caching for expensive/rate-limited APIs
- [ ] Log errors for debugging (without sensitive data)
- [ ] Never disable SSL verification in production
- [ ] Implement retry logic for transient failures
- [ ] Sanitize user-provided URLs