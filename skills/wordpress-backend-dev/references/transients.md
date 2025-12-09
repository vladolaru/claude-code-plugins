# WordPress Transients API

Comprehensive guide to using the WordPress Transients API for caching data with automatic expiration.

## Overview

The Transients API provides a standardized method for temporarily storing cached data in the database with automatic expiration. It mirrors the Options API but adds expiration functionality.

**Key Benefits:**
- Automatic expiration handling
- Inherently sped up by caching plugins (object caching)
- Automatic serialization of complex data types
- Network-wide variants for Multisite

**When to Use Transients:**
- Caching expensive database queries
- Storing results of external API calls
- Caching processed/computed data
- Any data that can be regenerated if lost

**When NOT to Use Transients:**
- Permanent data storage (use Options API)
- Session data (use WP Session or cookies)
- User-specific data that must persist (use user meta)
- Data that cannot be regenerated

## Core Functions

### Setting Transients

```php
/**
 * Sets a transient value.
 *
 * @param string $transient  Transient name (max 172 characters).
 * @param mixed  $value      Data to store (automatically serialized).
 * @param int    $expiration Time until expiration in seconds (0 = no expiration).
 * @return bool True if set successfully, false otherwise.
 */
set_transient( $transient, $value, $expiration );
```

**Example:**

```php
// Cache payment methods for 1 hour
$payment_methods = myplugin_fetch_payment_methods_from_api();
set_transient( 'myplugin_payment_methods', $payment_methods, HOUR_IN_SECONDS );

// Cache with longer expiration
set_transient( 'myplugin_exchange_rates', $rates, DAY_IN_SECONDS );
```

### Retrieving Transients

```php
/**
 * Retrieves a transient value.
 *
 * @param string $transient Transient name.
 * @return mixed Value of transient, or false if not set/expired.
 */
get_transient( $transient );
```

**Critical:** Always check return value with identity operator (`===`) because the stored value itself might be falsy:

```php
// WRONG - Fails if stored value is 0, '', [], or false
$data = get_transient( 'myplugin_cache' );
if ( ! $data ) {
    // This runs even if valid data (0 or empty array) is cached
}

// CORRECT - Strict comparison
$data = get_transient( 'myplugin_cache' );
if ( false === $data ) {
    // Only runs when transient doesn't exist or expired
}
```

### Deleting Transients

```php
/**
 * Deletes a transient.
 *
 * @param string $transient Transient name.
 * @return bool True if deleted, false otherwise.
 */
delete_transient( $transient );
```

**Example:**

```php
// Clear cache when settings change
function myplugin_on_settings_update() {
    delete_transient( 'myplugin_payment_methods' );
    delete_transient( 'myplugin_account_status' );
}
add_action( 'myplugin_settings_updated', 'myplugin_on_settings_update' );
```

## Multisite Functions

For network-wide transients in WordPress Multisite:

| Single Site | Multisite (Network-wide) |
|-------------|--------------------------|
| `set_transient()` | `set_site_transient()` |
| `get_transient()` | `get_site_transient()` |
| `delete_transient()` | `delete_site_transient()` |

```php
// Network-wide cache (shared across all sites)
set_site_transient( 'myplugin_network_config', $config, DAY_IN_SECONDS );

$config = get_site_transient( 'myplugin_network_config' );
if ( false === $config ) {
    $config = myplugin_fetch_network_config();
    set_site_transient( 'myplugin_network_config', $config, DAY_IN_SECONDS );
}
```

## Time Constants

WordPress provides time constants (since 3.5) for readable expiration values:

| Constant | Value (seconds) | Usage |
|----------|----------------|-------|
| `MINUTE_IN_SECONDS` | 60 | Short-lived cache |
| `HOUR_IN_SECONDS` | 3,600 | API responses |
| `DAY_IN_SECONDS` | 86,400 | Daily refreshed data |
| `WEEK_IN_SECONDS` | 604,800 | Stable data |
| `MONTH_IN_SECONDS` | 2,592,000 | Rarely changing data |
| `YEAR_IN_SECONDS` | 31,536,000 | Nearly permanent cache |

**Example usage:**

```php
// 12 hours
set_transient( 'myplugin_rates', $rates, 12 * HOUR_IN_SECONDS );

// 3 days
set_transient( 'myplugin_promotions', $promos, 3 * DAY_IN_SECONDS );

// 15 minutes
set_transient( 'myplugin_session_data', $data, 15 * MINUTE_IN_SECONDS );
```

## Standard Caching Pattern

The recommended pattern for using transients:

```php
/**
 * Retrieves payment methods with caching.
 *
 * @since 1.0.0
 *
 * @param bool $force_refresh Whether to bypass cache.
 * @return array Payment methods.
 */
function myplugin_get_payment_methods( $force_refresh = false ) {
    $cache_key = 'myplugin_payment_methods';

    // Check for cached data (unless force refresh)
    if ( ! $force_refresh ) {
        $cached = get_transient( $cache_key );
        if ( false !== $cached ) {
            return $cached;
        }
    }

    // Fetch fresh data
    $payment_methods = myplugin_api_fetch_payment_methods();

    // Cache the result
    if ( ! is_wp_error( $payment_methods ) ) {
        set_transient( $cache_key, $payment_methods, HOUR_IN_SECONDS );
    }

    return $payment_methods;
}
```

## Critical Behaviors

### Expiration Is Not Guaranteed Minimum

**Important:** Transients may disappear at ANY time before expiration. The expiration time is a maximum, not a minimum.

```php
// This transient might be gone in 1 second or 23 hours
// But it WILL be gone after 24 hours
set_transient( 'myplugin_data', $data, DAY_IN_SECONDS );
```

**Reasons transients may disappear early:**
- Object cache plugins may evict data (LRU policies)
- Database cleanup processes
- Cache invalidation by other code
- Server restarts (with memory-based object cache)

**Consequence:** Always code defensively - assume the transient might not exist:

```php
// ALWAYS have fallback logic
$data = get_transient( 'myplugin_expensive_data' );
if ( false === $data ) {
    // Regenerate data - this MUST work even if transient was just set
    $data = myplugin_generate_expensive_data();
    set_transient( 'myplugin_expensive_data', $data, HOUR_IN_SECONDS );
}
```

### Storage Location

**Without object cache:** Transients are stored in the `wp_options` table:
- Transient value: `_transient_{$transient}`
- Expiration time: `_transient_timeout_{$transient}`

**With object cache:** Transients are stored in memory (Redis, Memcached, etc.) and may not touch the database at all.

### Name Length Limit

Transient names are limited to **172 characters**. For dynamic keys, ensure the total length stays within limits:

```php
// BAD - Could exceed limit with long order IDs or UUIDs
$key = 'myplugin_payment_intent_details_for_order_' . $order_id . '_' . $intent_id;

// GOOD - Use hashing for variable-length components
$key = 'myplugin_intent_' . md5( $order_id . '_' . $intent_id );

// BETTER - Short, predictable keys
$key = 'myplugin_pi_' . $intent_id;  // Payment intents have fixed-length IDs
```

## Cache Invalidation

### Manual Invalidation on Data Change

Always invalidate transients when underlying data changes:

```php
/**
 * Clears payment method cache when settings are updated.
 *
 * @since 1.0.0
 *
 * @param array $old_settings Previous settings.
 * @param array $new_settings Updated settings.
 */
function myplugin_clear_cache_on_settings_change( $old_settings, $new_settings ) {
    // Clear relevant caches
    delete_transient( 'myplugin_payment_methods' );
    delete_transient( 'myplugin_account_status' );
    delete_transient( 'myplugin_available_currencies' );
}
add_action( 'myplugin_settings_updated', 'myplugin_clear_cache_on_settings_change', 10, 2 );
```

### Invalidation on Related Actions

```php
// Clear cache when new payment method is added
add_action( 'myplugin_payment_method_added', function( $method_id ) {
    delete_transient( 'myplugin_payment_methods' );
} );

// Clear cache when order status changes
add_action( 'woocommerce_order_status_changed', function( $order_id, $old_status, $new_status ) {
    delete_transient( 'myplugin_order_stats_' . get_current_user_id() );
}, 10, 3 );
```

### Bulk Cache Clearing

For clearing multiple related transients:

```php
/**
 * Clears all My_Plugin transients.
 *
 * @since 1.0.0
 *
 * @return int Number of transients deleted.
 */
function myplugin_clear_all_caches() {
    global $wpdb;

    // Delete from options table (works even with object cache)
    $count = $wpdb->query(
        "DELETE FROM {$wpdb->options}
         WHERE option_name LIKE '_transient_myplugin_%'
         OR option_name LIKE '_transient_timeout_myplugin_%'"
    );

    // Also clear from object cache if available
    if ( function_exists( 'wp_cache_flush_group' ) ) {
        wp_cache_flush_group( 'transient' );
    }

    return $count;
}
```

## Best Practices

### 1. Use Descriptive, Prefixed Keys

```php
// GOOD - Prefixed and descriptive
set_transient( 'myplugin_payment_methods_v2', $methods, HOUR_IN_SECONDS );
set_transient( 'myplugin_account_' . $account_id . '_balance', $balance, 15 * MINUTE_IN_SECONDS );

// BAD - Generic, collision-prone
set_transient( 'payment_methods', $methods, HOUR_IN_SECONDS );
set_transient( 'cache', $data, HOUR_IN_SECONDS );
```

### 2. Never Store Plain Boolean False

Storing `false` makes it impossible to distinguish from "not found":

```php
// BAD - Cannot distinguish cached false from missing transient
$is_enabled = myplugin_check_feature_enabled();
set_transient( 'myplugin_feature_enabled', $is_enabled, HOUR_IN_SECONDS ); // If $is_enabled is false, get_transient returns false!

// GOOD - Wrap in array or convert to integer
set_transient( 'myplugin_feature_enabled', [ 'value' => $is_enabled ], HOUR_IN_SECONDS );

// Retrieve
$cached = get_transient( 'myplugin_feature_enabled' );
if ( false !== $cached ) {
    $is_enabled = $cached['value'];
}

// ALTERNATIVE - Use integers
set_transient( 'myplugin_feature_enabled', $is_enabled ? 1 : 0, HOUR_IN_SECONDS );
```

### 3. Include Version in Cache Keys

When data structure changes, bump the version to avoid stale data issues:

```php
define( 'MYPLUGIN_CACHE_VERSION', '2' );

function myplugin_get_cached_data() {
    $key = 'myplugin_data_v' . MYPLUGIN_CACHE_VERSION;

    $data = get_transient( $key );
    if ( false === $data ) {
        $data = myplugin_generate_data();
        set_transient( $key, $data, DAY_IN_SECONDS );
    }

    return $data;
}
```

### 4. Handle Errors Gracefully

Don't cache error responses:

```php
function myplugin_get_exchange_rates() {
    $cached = get_transient( 'myplugin_exchange_rates' );
    if ( false !== $cached ) {
        return $cached;
    }

    $rates = myplugin_api_fetch_exchange_rates();

    // Don't cache errors
    if ( is_wp_error( $rates ) ) {
        // Optionally cache for shorter time to prevent hammering API
        set_transient( 'myplugin_exchange_rates_failed', time(), 5 * MINUTE_IN_SECONDS );
        return $rates;
    }

    set_transient( 'myplugin_exchange_rates', $rates, 12 * HOUR_IN_SECONDS );
    return $rates;
}
```

### 5. Implement Cache Warming

For critical data, warm the cache proactively:

```php
/**
 * Warms payment method cache on plugin activation.
 *
 * @since 1.0.0
 */
function myplugin_warm_cache_on_activation() {
    // Pre-populate cache with fresh data
    delete_transient( 'myplugin_payment_methods' );
    myplugin_get_payment_methods(); // This will fetch and cache
}
register_activation_hook( __FILE__, 'myplugin_warm_cache_on_activation' );

/**
 * Refreshes cache periodically via cron.
 *
 * @since 1.0.0
 */
function myplugin_scheduled_cache_refresh() {
    myplugin_get_payment_methods( true ); // Force refresh
    myplugin_get_exchange_rates( true );
}
add_action( 'myplugin_daily_cache_refresh', 'myplugin_scheduled_cache_refresh' );
```

### 6. Use Appropriate Expiration Times

| Data Type | Recommended Expiration |
|-----------|----------------------|
| Real-time data (stock, prices) | 1-5 minutes |
| Session-related data | 15-30 minutes |
| API responses | 1-6 hours |
| Configuration data | 12-24 hours |
| Rarely changing data | 1-7 days |

```php
// Real-time: Short expiration
set_transient( 'myplugin_live_balance', $balance, 5 * MINUTE_IN_SECONDS );

// API response: Medium expiration
set_transient( 'myplugin_payment_methods', $methods, 6 * HOUR_IN_SECONDS );

// Config: Longer expiration
set_transient( 'myplugin_supported_countries', $countries, WEEK_IN_SECONDS );
```

## User-Specific Transients

For per-user cached data, include user ID in the key:

```php
/**
 * Gets cached dashboard data for current user.
 *
 * @since 1.0.0
 *
 * @return array Dashboard data.
 */
function myplugin_get_user_dashboard_data() {
    $user_id = get_current_user_id();
    if ( ! $user_id ) {
        return [];
    }

    $key = 'myplugin_dashboard_' . $user_id;
    $data = get_transient( $key );

    if ( false === $data ) {
        $data = myplugin_generate_dashboard_data( $user_id );
        set_transient( $key, $data, 30 * MINUTE_IN_SECONDS );
    }

    return $data;
}

/**
 * Clears user's dashboard cache.
 *
 * @since 1.0.0
 *
 * @param int $user_id User ID.
 */
function myplugin_clear_user_dashboard_cache( $user_id ) {
    delete_transient( 'myplugin_dashboard_' . $user_id );
}
```

## Transients with Object Caching

When an object cache (Redis, Memcached) is available:

- Transients are stored in memory, not the database
- Faster read/write operations
- May have different eviction policies
- `delete_transient()` clears from object cache

**Check for object cache:**

```php
if ( wp_using_ext_object_cache() ) {
    // Object cache is active
    // Transients will be stored in memory
}
```

## Debugging Transients

### Check if Transient Exists

```php
// In development/debugging
$value = get_transient( 'myplugin_payment_methods' );
if ( false === $value ) {
    error_log( 'Transient myplugin_payment_methods not found or expired' );
} else {
    error_log( 'Transient myplugin_payment_methods exists: ' . print_r( $value, true ) );
}
```

### View Transients in Database

```sql
-- View all myplugin transients
SELECT option_name, option_value
FROM wp_options
WHERE option_name LIKE '_transient_myplugin%'
ORDER BY option_name;

-- View expiration times
SELECT
    REPLACE(option_name, '_transient_timeout_', '') AS transient_name,
    FROM_UNIXTIME(option_value) AS expires_at
FROM wp_options
WHERE option_name LIKE '_transient_timeout_myplugin%';
```

### Query Monitor Integration

The Query Monitor plugin shows transient operations. Use it during development to verify caching behavior.

## Common Anti-Patterns

### 1. Not Checking Return Value Properly

```php
// WRONG
$data = get_transient( 'myplugin_data' );
if ( ! $data ) {  // Fails for empty arrays, 0, etc.
    $data = generate_data();
}

// CORRECT
$data = get_transient( 'myplugin_data' );
if ( false === $data ) {
    $data = generate_data();
    set_transient( 'myplugin_data', $data, HOUR_IN_SECONDS );
}
```

### 2. Not Invalidating on Data Change

```php
// WRONG - Cache never cleared when data changes
function myplugin_update_payment_method( $method_id, $data ) {
    // Update in database
    myplugin_db_update_method( $method_id, $data );
    // Oops! Forgot to clear cache
}

// CORRECT
function myplugin_update_payment_method( $method_id, $data ) {
    myplugin_db_update_method( $method_id, $data );
    delete_transient( 'myplugin_payment_methods' );  // Clear stale cache
}
```

### 3. Caching Errors

```php
// WRONG - Caches error responses
$response = myplugin_api_call();
set_transient( 'myplugin_response', $response, HOUR_IN_SECONDS );  // Even if $response is WP_Error!

// CORRECT
$response = myplugin_api_call();
if ( ! is_wp_error( $response ) ) {
    set_transient( 'myplugin_response', $response, HOUR_IN_SECONDS );
}
```

### 4. Using Transients for Critical Data

```php
// WRONG - Critical data that must persist
set_transient( 'myplugin_api_key', $api_key, YEAR_IN_SECONDS );  // Could disappear!

// CORRECT - Use options for permanent data
update_option( 'myplugin_api_key', $api_key );
```

## Filters and Actions

### Modify Transient Before Retrieval

```php
/**
 * Filters a transient value before it's returned.
 *
 * @param mixed  $value     Transient value.
 * @param string $transient Transient name.
 */
add_filter( 'transient_myplugin_payment_methods', function( $value, $transient ) {
    // Modify or validate cached value
    return $value;
}, 10, 2 );
```

### Modify Transient Expiration

```php
/**
 * Filters the expiration time for a transient before it's set.
 *
 * @param int    $expiration Time until expiration in seconds.
 * @param mixed  $value      Transient value.
 * @param string $transient  Transient name.
 */
add_filter( 'expiration_of_transient_myplugin_payment_methods', function( $expiration, $value, $transient ) {
    // Extend expiration in production
    if ( ! defined( 'WP_DEBUG' ) || ! WP_DEBUG ) {
        return $expiration * 2;
    }
    return $expiration;
}, 10, 3 );
```

## Summary Checklist

Before using transients, verify:

- [ ] Data can be safely regenerated if transient disappears
- [ ] Using strict comparison (`false ===`) when checking `get_transient()`
- [ ] Not storing plain boolean `false` values
- [ ] Using prefixed, descriptive transient names
- [ ] Transient name is under 172 characters
- [ ] Appropriate expiration time is set
- [ ] Error responses are not cached
- [ ] Cache is invalidated when underlying data changes
- [ ] Fallback logic exists for when transient doesn't exist