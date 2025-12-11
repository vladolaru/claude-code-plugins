# WordPress Users API

Comprehensive guide to working with WordPress users, roles, capabilities, and user metadata.

## Overview

WordPress users are access accounts stored in the `wp_users` database table. Each user has:
- Username (login)
- Password (hashed)
- Email address
- Display name
- Role(s) with capabilities

Additional user data is stored in the `wp_usermeta` table.

## The Principle of Least Privileges

**Security best practice:** Give users only the privileges essential for their work.

- Create roles with minimal required capabilities
- Always verify capabilities before sensitive operations
- Don't grant admin-level access when lower privileges suffice

## Working with Users

### Creating Users

#### wp_create_user() - Simple Creation

Creates a user with basic parameters:

```php
/**
 * Creates a new user with minimal data.
 *
 * @param string $username  User login name.
 * @param string $password  User password.
 * @param string $email     Optional. User email address.
 * @return int|WP_Error User ID on success, WP_Error on failure.
 */
wp_create_user( $username, $password, $email );
```

**Example:**

```php
/**
 * Creates a new customer user if not exists.
 *
 * @since 1.0.0
 *
 * @param string $username User login name.
 * @param string $email    User email address.
 * @return int|WP_Error User ID on success, WP_Error on failure.
 */
function myplugin_create_customer( $username, $email ) {
    // Check if username already exists
    if ( username_exists( $username ) ) {
        return new WP_Error( 'username_exists', __( 'Username already exists.', 'my-plugin' ) );
    }

    // Check if email already exists
    if ( email_exists( $email ) ) {
        return new WP_Error( 'email_exists', __( 'Email already registered.', 'my-plugin' ) );
    }

    // Generate secure random password
    $password = wp_generate_password( 12, true, true );

    // Create user
    $user_id = wp_create_user( $username, $password, $email );

    if ( is_wp_error( $user_id ) ) {
        return $user_id;
    }

    // Set role
    $user = get_user_by( 'ID', $user_id );
    $user->set_role( 'customer' );

    // Send notification email
    wp_new_user_notification( $user_id, null, 'user' );

    return $user_id;
}
```

#### wp_insert_user() - Detailed Creation

Creates or updates a user with full data:

```php
/**
 * Inserts or updates a user in the database.
 *
 * @param array|object $userdata {
 *     User data array or object.
 *
 *     @type int    $ID              User ID (for updates only).
 *     @type string $user_login      Required. User login name.
 *     @type string $user_pass       Required for new users. User password.
 *     @type string $user_nicename   URL-friendly username.
 *     @type string $user_email      User email address.
 *     @type string $user_url        User website URL.
 *     @type string $display_name    Display name.
 *     @type string $nickname        User nickname.
 *     @type string $first_name      User first name.
 *     @type string $last_name       User last name.
 *     @type string $description     User biographical info.
 *     @type string $rich_editing    Enable rich editor. 'true' or 'false'.
 *     @type string $role            User role.
 *     @type string $locale          User locale.
 * }
 * @return int|WP_Error User ID on success, WP_Error on failure.
 */
wp_insert_user( $userdata );
```

**Example:**

```php
/**
 * Creates a merchant user with complete profile.
 *
 * @since 1.0.0
 *
 * @param array $merchant_data Merchant information.
 * @return int|WP_Error User ID on success, WP_Error on failure.
 */
function myplugin_create_merchant( $merchant_data ) {
    $userdata = [
        'user_login'   => sanitize_user( $merchant_data['username'] ),
        'user_pass'    => $merchant_data['password'],
        'user_email'   => sanitize_email( $merchant_data['email'] ),
        'user_url'     => esc_url_raw( $merchant_data['website'] ),
        'first_name'   => sanitize_text_field( $merchant_data['first_name'] ),
        'last_name'    => sanitize_text_field( $merchant_data['last_name'] ),
        'display_name' => sanitize_text_field( $merchant_data['business_name'] ),
        'role'         => 'shop_manager',
    ];

    $user_id = wp_insert_user( $userdata );

    if ( ! is_wp_error( $user_id ) ) {
        // Add custom metadata
        update_user_meta( $user_id, 'myplugin_merchant_id', sanitize_key( $merchant_data['merchant_id'] ) );
        update_user_meta( $user_id, 'myplugin_account_status', 'pending' );

        /**
         * Fires after a merchant user is created.
         *
         * @since 1.0.0
         *
         * @param int   $user_id       The user ID.
         * @param array $merchant_data The merchant data.
         */
        do_action( 'myplugin_merchant_created', $user_id, $merchant_data );
    }

    return $user_id;
}
```

### Retrieving Users

#### get_user_by() - Single User

```php
/**
 * Retrieves user info by a given field.
 *
 * @param string     $field Field to search: 'id', 'ID', 'slug', 'email', 'login'.
 * @param int|string $value Value to search for.
 * @return WP_User|false User object on success, false on failure.
 */
get_user_by( $field, $value );
```

**Examples:**

```php
// Get by ID
$user = get_user_by( 'ID', 123 );

// Get by email
$user = get_user_by( 'email', 'customer@example.com' );

// Get by login/username
$user = get_user_by( 'login', 'johndoe' );

// Get by slug (nicename)
$user = get_user_by( 'slug', 'john-doe' );

// Always check if user was found
if ( $user ) {
    $display_name = $user->display_name;
    $email = $user->user_email;
}
```

#### get_userdata() - By ID

```php
/**
 * Retrieves user data by user ID.
 *
 * @param int $user_id User ID.
 * @return WP_User|false User object on success, false on failure.
 */
get_userdata( $user_id );
```

#### get_current_user_id() - Current User

```php
/**
 * Gets the current user's ID.
 *
 * @return int Current user ID, 0 if not logged in.
 */
get_current_user_id();
```

**Example:**

```php
$user_id = get_current_user_id();

if ( 0 === $user_id ) {
    // User is not logged in
    wp_die( esc_html__( 'You must be logged in.', 'my-plugin' ) );
}

$user = get_userdata( $user_id );
```

#### wp_get_current_user() - Current User Object

```php
/**
 * Retrieves the current user object.
 *
 * @return WP_User Current user object.
 */
wp_get_current_user();
```

#### get_users() - Multiple Users

```php
/**
 * Retrieves users matching given criteria.
 *
 * @param array $args {
 *     Query arguments.
 *
 *     @type int          $blog_id      Blog ID. Default current blog.
 *     @type string|array $role         Role name or array of role names.
 *     @type string|array $role__in     Array of role names to include.
 *     @type string|array $role__not_in Array of role names to exclude.
 *     @type array        $meta_key     Meta key to filter by.
 *     @type array        $meta_value   Meta value to filter by.
 *     @type array        $meta_query   Meta query clauses.
 *     @type array        $include      Array of user IDs to include.
 *     @type array        $exclude      Array of user IDs to exclude.
 *     @type string       $search       Search keyword.
 *     @type int          $number       Number of users to return.
 *     @type int          $offset       Number of users to skip.
 *     @type string       $orderby      Field to order by.
 *     @type string       $order        'ASC' or 'DESC'.
 *     @type string       $fields       Fields to return: 'all', 'ID', 'display_name', etc.
 * }
 * @return array Array of users.
 */
get_users( $args );
```

**Examples:**

```php
// Get all administrators
$admins = get_users( [
    'role' => 'administrator',
] );

// Get shop managers with pagination
$shop_managers = get_users( [
    'role'   => 'shop_manager',
    'number' => 20,
    'offset' => 0,
    'orderby' => 'display_name',
    'order'   => 'ASC',
] );

// Get users by meta value
$myplugin_merchants = get_users( [
    'meta_key'   => 'myplugin_merchant_id',
    'meta_value' => $merchant_id,
] );

// Complex meta query
$active_merchants = get_users( [
    'role'       => 'shop_manager',
    'meta_query' => [
        'relation' => 'AND',
        [
            'key'   => 'myplugin_account_status',
            'value' => 'active',
        ],
        [
            'key'     => 'myplugin_last_login',
            'value'   => strtotime( '-30 days' ),
            'compare' => '>',
            'type'    => 'NUMERIC',
        ],
    ],
] );

// Get only user IDs (more efficient)
$user_ids = get_users( [
    'role'   => 'customer',
    'fields' => 'ID',
] );
```

### Updating Users

```php
/**
 * Updates a user in the database.
 *
 * @param array|object $userdata User data to update.
 * @return int|WP_Error User ID on success, WP_Error on failure.
 */
wp_update_user( $userdata );
```

**Warning:** If the current user's password is being updated, cookies will be cleared!

**Example:**

```php
/**
 * Updates merchant profile information.
 *
 * @since 1.0.0
 *
 * @param int   $user_id User ID.
 * @param array $data    Profile data.
 * @return int|WP_Error User ID on success, WP_Error on failure.
 */
function myplugin_update_merchant_profile( $user_id, $data ) {
    // Verify the user exists
    $user = get_userdata( $user_id );
    if ( ! $user ) {
        return new WP_Error( 'invalid_user', __( 'User not found.', 'my-plugin' ) );
    }

    $userdata = [ 'ID' => $user_id ];

    // Only include fields that are provided
    if ( isset( $data['email'] ) ) {
        $userdata['user_email'] = sanitize_email( $data['email'] );
    }

    if ( isset( $data['website'] ) ) {
        $userdata['user_url'] = esc_url_raw( $data['website'] );
    }

    if ( isset( $data['first_name'] ) ) {
        $userdata['first_name'] = sanitize_text_field( $data['first_name'] );
    }

    if ( isset( $data['last_name'] ) ) {
        $userdata['last_name'] = sanitize_text_field( $data['last_name'] );
    }

    $result = wp_update_user( $userdata );

    if ( ! is_wp_error( $result ) ) {
        /**
         * Fires after a merchant profile is updated.
         *
         * @since 1.0.0
         *
         * @param int   $user_id The user ID.
         * @param array $data    The updated data.
         */
        do_action( 'myplugin_merchant_profile_updated', $user_id, $data );
    }

    return $result;
}
```

### Deleting Users

```php
/**
 * Removes a user from the site.
 *
 * @param int $user_id  ID of the user to delete.
 * @param int $reassign Optional. User ID to reassign posts/links to.
 * @return bool True when finished.
 */
wp_delete_user( $user_id, $reassign );
```

**Critical:** If `$reassign` is not set, all content belonging to the deleted user will be deleted!

**Example:**

```php
/**
 * Deletes a merchant user and reassigns their content.
 *
 * @since 1.0.0
 *
 * @param int $user_id   User ID to delete.
 * @param int $reassign  User ID to reassign content to.
 * @return bool|WP_Error True on success, WP_Error on failure.
 */
function myplugin_delete_merchant( $user_id, $reassign = null ) {
    // Verify current user has permission
    if ( ! current_user_can( 'delete_users' ) ) {
        return new WP_Error( 'permission_denied', __( 'You cannot delete users.', 'my-plugin' ) );
    }

    // Don't allow deleting administrators
    $user = get_userdata( $user_id );
    if ( ! $user ) {
        return new WP_Error( 'invalid_user', __( 'User not found.', 'my-plugin' ) );
    }

    if ( in_array( 'administrator', $user->roles, true ) ) {
        return new WP_Error( 'cannot_delete_admin', __( 'Cannot delete administrators.', 'my-plugin' ) );
    }

    /**
     * Fires before a merchant user is deleted.
     *
     * @since 1.0.0
     *
     * @param int      $user_id The user ID being deleted.
     * @param WP_User  $user    The user object.
     */
    do_action( 'myplugin_before_merchant_deleted', $user_id, $user );

    // Clean up custom data
    delete_user_meta( $user_id, 'myplugin_merchant_id' );
    delete_user_meta( $user_id, 'myplugin_account_status' );

    // Delete user, reassign content to specified user or admin
    if ( null === $reassign ) {
        // Get the first administrator as default reassignment
        $admins = get_users( [ 'role' => 'administrator', 'number' => 1, 'fields' => 'ID' ] );
        $reassign = ! empty( $admins ) ? $admins[0] : 0;
    }

    require_once ABSPATH . 'wp-admin/includes/user.php';
    return wp_delete_user( $user_id, $reassign );
}
```

### Checking User Existence

```php
// Check if username exists
$user_id = username_exists( 'johndoe' );
if ( $user_id ) {
    // Username is taken
}

// Check if email exists
$user_id = email_exists( 'john@example.com' );
if ( false !== $user_id ) {
    // Email is registered
}

// Validate username format
$sanitized = sanitize_user( $username );
if ( $sanitized !== $username || empty( $sanitized ) ) {
    // Invalid username
}

// Check if valid user ID
$user = get_userdata( $user_id );
if ( false === $user ) {
    // User doesn't exist
}
```

## User Metadata

User metadata stores additional information in the `wp_usermeta` table.

### Adding Metadata

```php
/**
 * Adds metadata for a user.
 *
 * @param int    $user_id    User ID.
 * @param string $meta_key   Metadata key.
 * @param mixed  $meta_value Metadata value (auto-serialized if non-scalar).
 * @param bool   $unique     Optional. Whether key should be unique. Default false.
 * @return int|false Meta ID on success, false on failure.
 */
add_user_meta( $user_id, $meta_key, $meta_value, $unique );
```

**Examples:**

```php
// Add single unique value
add_user_meta( $user_id, 'myplugin_customer_id', 'cus_abc123', true );

// Add multiple values for same key (unique = false)
add_user_meta( $user_id, 'myplugin_payment_method', 'pm_card_visa', false );
add_user_meta( $user_id, 'myplugin_payment_method', 'pm_card_mastercard', false );

// Add array/object (automatically serialized)
add_user_meta( $user_id, 'myplugin_preferences', [
    'currency'      => 'USD',
    'notifications' => true,
], true );
```

### Updating Metadata

```php
/**
 * Updates user metadata.
 *
 * @param int    $user_id    User ID.
 * @param string $meta_key   Metadata key.
 * @param mixed  $meta_value New metadata value.
 * @param mixed  $prev_value Optional. Previous value to update. Default ''.
 * @return int|bool Meta ID if new, true on update, false on failure.
 */
update_user_meta( $user_id, $meta_key, $meta_value, $prev_value );
```

**Note:** `update_user_meta()` creates the meta if it doesn't exist.

**Examples:**

```php
// Update (or create) single value
update_user_meta( $user_id, 'myplugin_account_status', 'active' );

// Update specific previous value
update_user_meta( $user_id, 'myplugin_payment_method', 'pm_new_card', 'pm_old_card' );

// Update complex data
$preferences = get_user_meta( $user_id, 'myplugin_preferences', true );
$preferences['currency'] = 'EUR';
update_user_meta( $user_id, 'myplugin_preferences', $preferences );
```

### Retrieving Metadata

```php
/**
 * Retrieves user metadata.
 *
 * @param int    $user_id User ID.
 * @param string $key     Optional. Metadata key. Default '' (all meta).
 * @param bool   $single  Optional. Return single value. Default false.
 * @return mixed Single value, array of values, or all meta.
 */
get_user_meta( $user_id, $key, $single );
```

**Examples:**

```php
// Get single value (most common)
$customer_id = get_user_meta( $user_id, 'myplugin_customer_id', true );

// Get all values for key (array)
$payment_methods = get_user_meta( $user_id, 'myplugin_payment_method', false );
// Returns: ['pm_card_visa', 'pm_card_mastercard']

// Get all metadata for user
$all_meta = get_user_meta( $user_id );
// Returns associative array of all meta keys and values

// Handle missing meta
$status = get_user_meta( $user_id, 'myplugin_status', true );
if ( empty( $status ) ) {
    $status = 'pending'; // Default value
}
```

### Deleting Metadata

```php
/**
 * Removes metadata for a user.
 *
 * @param int    $user_id    User ID.
 * @param string $meta_key   Metadata key.
 * @param mixed  $meta_value Optional. Value to delete. Default '' (all).
 * @return bool True on success, false on failure.
 */
delete_user_meta( $user_id, $meta_key, $meta_value );
```

**Examples:**

```php
// Delete all values for key
delete_user_meta( $user_id, 'myplugin_temp_data' );

// Delete specific value only (when multiple exist)
delete_user_meta( $user_id, 'myplugin_payment_method', 'pm_expired_card' );
```

### Admin Profile Fields

Add custom fields to user profile in admin:

```php
/**
 * Displays My_Plugin fields on user profile.
 *
 * @since 1.0.0
 *
 * @param WP_User $user The user object.
 */
function myplugin_user_profile_fields( $user ) {
    // Check capability
    if ( ! current_user_can( 'manage_woocommerce' ) ) {
        return;
    }

    $merchant_id = get_user_meta( $user->ID, 'myplugin_merchant_id', true );
    $status = get_user_meta( $user->ID, 'myplugin_account_status', true );
    ?>
    <h3><?php esc_html_e( 'My Plugin', 'my-plugin' ); ?></h3>
    <table class="form-table">
        <tr>
            <th><label for="myplugin_merchant_id"><?php esc_html_e( 'Merchant ID', 'my-plugin' ); ?></label></th>
            <td>
                <input type="text" name="myplugin_merchant_id" id="myplugin_merchant_id"
                       value="<?php echo esc_attr( $merchant_id ); ?>" class="regular-text">
            </td>
        </tr>
        <tr>
            <th><label for="myplugin_account_status"><?php esc_html_e( 'Account Status', 'my-plugin' ); ?></label></th>
            <td>
                <select name="myplugin_account_status" id="myplugin_account_status">
                    <option value="pending" <?php selected( $status, 'pending' ); ?>>
                        <?php esc_html_e( 'Pending', 'my-plugin' ); ?>
                    </option>
                    <option value="active" <?php selected( $status, 'active' ); ?>>
                        <?php esc_html_e( 'Active', 'my-plugin' ); ?>
                    </option>
                    <option value="suspended" <?php selected( $status, 'suspended' ); ?>>
                        <?php esc_html_e( 'Suspended', 'my-plugin' ); ?>
                    </option>
                </select>
            </td>
        </tr>
    </table>
    <?php
}
add_action( 'show_user_profile', 'myplugin_user_profile_fields' );
add_action( 'edit_user_profile', 'myplugin_user_profile_fields' );

/**
 * Saves My_Plugin fields from user profile.
 *
 * @since 1.0.0
 *
 * @param int $user_id The user ID.
 */
function myplugin_save_user_profile_fields( $user_id ) {
    // Verify capability
    if ( ! current_user_can( 'manage_woocommerce' ) ) {
        return;
    }

    // Verify nonce (WordPress adds one automatically for profile forms)
    if ( ! isset( $_POST['_wpnonce'] ) || ! wp_verify_nonce( sanitize_key( $_POST['_wpnonce'] ), 'update-user_' . $user_id ) ) {
        return;
    }

    // Save merchant ID
    if ( isset( $_POST['myplugin_merchant_id'] ) ) {
        update_user_meta( $user_id, 'myplugin_merchant_id', sanitize_key( $_POST['myplugin_merchant_id'] ) );
    }

    // Save account status
    if ( isset( $_POST['myplugin_account_status'] ) ) {
        $allowed_statuses = [ 'pending', 'active', 'suspended' ];
        $status = sanitize_key( $_POST['myplugin_account_status'] );

        if ( in_array( $status, $allowed_statuses, true ) ) {
            update_user_meta( $user_id, 'myplugin_account_status', $status );
        }
    }
}
add_action( 'personal_options_update', 'myplugin_save_user_profile_fields' );
add_action( 'edit_user_profile_update', 'myplugin_save_user_profile_fields' );
```

## Roles and Capabilities

### Default Roles

| Role | Description | Key Capabilities |
|------|-------------|------------------|
| Super Admin | Multisite network admin | All capabilities |
| Administrator | Full site control | `manage_options`, `edit_users`, `install_plugins` |
| Editor | Manage all content | `edit_others_posts`, `publish_posts`, `manage_categories` |
| Author | Publish own content | `publish_posts`, `edit_published_posts`, `upload_files` |
| Contributor | Write, can't publish | `edit_posts`, `delete_posts` |
| Subscriber | Read only | `read` |

### Checking Capabilities

#### current_user_can()

```php
/**
 * Checks if current user has capability.
 *
 * @param string $capability Capability name.
 * @param mixed  ...$args    Optional. Object ID for meta capabilities.
 * @return bool True if has capability.
 */
current_user_can( $capability, ...$args );
```

**Examples:**

```php
// Check general capability
if ( current_user_can( 'manage_woocommerce' ) ) {
    // User can manage WooCommerce
}

// Check meta capability (object-specific)
if ( current_user_can( 'edit_post', $post_id ) ) {
    // User can edit this specific post
}

if ( current_user_can( 'edit_user', $user_id ) ) {
    // User can edit this specific user
}

// Common pattern for admin pages
if ( ! current_user_can( 'manage_options' ) ) {
    wp_die( esc_html__( 'You do not have permission to access this page.', 'my-plugin' ) );
}
```

#### user_can()

```php
/**
 * Checks if a specific user has capability.
 *
 * @param int|WP_User $user       User ID or object.
 * @param string      $capability Capability name.
 * @param mixed       ...$args    Optional. Object ID for meta capabilities.
 * @return bool True if has capability.
 */
user_can( $user, $capability, ...$args );
```

**Example:**

```php
// Check if specific user can manage WooCommerce
$user_id = 123;
if ( user_can( $user_id, 'manage_woocommerce' ) ) {
    // User 123 can manage WooCommerce
}

// Check if user can edit a specific post
if ( user_can( $user_id, 'edit_post', $post_id ) ) {
    // User can edit this post
}
```

#### current_user_can_for_blog() (Multisite)

```php
/**
 * Checks if current user has capability on specific blog.
 *
 * @param int    $blog_id    Blog ID.
 * @param string $capability Capability name.
 * @param mixed  ...$args    Optional. Additional arguments.
 * @return bool True if has capability on blog.
 */
current_user_can_for_blog( $blog_id, $capability, ...$args );
```

### Common Capabilities

| Capability | Description |
|------------|-------------|
| `read` | Access dashboard, read posts |
| `edit_posts` | Create/edit own posts |
| `publish_posts` | Publish own posts |
| `edit_others_posts` | Edit posts by other users |
| `delete_posts` | Delete own posts |
| `upload_files` | Upload media files |
| `manage_categories` | Create/edit categories |
| `moderate_comments` | Moderate comments |
| `manage_options` | Access site settings |
| `edit_users` | Edit user profiles |
| `delete_users` | Delete users |
| `create_users` | Create new users |
| `install_plugins` | Install plugins |
| `manage_woocommerce` | WooCommerce admin access |

### Adding Custom Roles

```php
/**
 * Adds a custom role.
 *
 * @param string $role         Role identifier.
 * @param string $display_name Role display name.
 * @param array  $capabilities Array of capabilities.
 * @return WP_Role|void Role object on success.
 */
add_role( $role, $display_name, $capabilities );
```

**Important:** Roles are stored in the database. Only call `add_role()` once (e.g., on plugin activation).

**Example:**

```php
/**
 * Registers custom My_Plugin roles on plugin activation.
 *
 * @since 1.0.0
 */
function myplugin_register_roles() {
    // Add Merchant role
    add_role(
        'myplugin_merchant',
        __( 'My_Plugin Merchant', 'my-plugin' ),
        [
            'read'                   => true,
            'edit_posts'             => false,
            'delete_posts'           => false,
            'manage_woocommerce'     => true,
            'view_woocommerce_reports' => true,
            'myplugin_manage_payments'  => true,
            'myplugin_view_transactions' => true,
            'myplugin_issue_refunds'    => true,
        ]
    );

    // Add Support Agent role
    add_role(
        'myplugin_support',
        __( 'My_Plugin Support', 'my-plugin' ),
        [
            'read'                    => true,
            'manage_woocommerce'      => true,
            'myplugin_view_transactions' => true,
            'myplugin_view_customers'    => true,
        ]
    );
}
register_activation_hook( __FILE__, 'myplugin_register_roles' );

/**
 * Removes custom My_Plugin roles on plugin deactivation.
 *
 * @since 1.0.0
 */
function myplugin_remove_roles() {
    remove_role( 'myplugin_merchant' );
    remove_role( 'myplugin_support' );
}
register_deactivation_hook( __FILE__, 'myplugin_remove_roles' );
```

### Adding Capabilities to Existing Roles

```php
/**
 * Adds custom capabilities to roles.
 *
 * @since 1.0.0
 */
function myplugin_add_capabilities() {
    // Add to Administrator
    $admin = get_role( 'administrator' );
    if ( $admin ) {
        $admin->add_cap( 'myplugin_manage_settings' );
        $admin->add_cap( 'myplugin_manage_payments' );
        $admin->add_cap( 'myplugin_view_transactions' );
        $admin->add_cap( 'myplugin_issue_refunds' );
    }

    // Add to Shop Manager
    $shop_manager = get_role( 'shop_manager' );
    if ( $shop_manager ) {
        $shop_manager->add_cap( 'myplugin_view_transactions' );
        $shop_manager->add_cap( 'myplugin_issue_refunds' );
    }
}
register_activation_hook( __FILE__, 'myplugin_add_capabilities' );

/**
 * Removes custom capabilities on deactivation.
 *
 * @since 1.0.0
 */
function myplugin_remove_capabilities() {
    $caps_to_remove = [
        'myplugin_manage_settings',
        'myplugin_manage_payments',
        'myplugin_view_transactions',
        'myplugin_issue_refunds',
    ];

    foreach ( wp_roles()->roles as $role_name => $role_info ) {
        $role = get_role( $role_name );
        if ( $role ) {
            foreach ( $caps_to_remove as $cap ) {
                $role->remove_cap( $cap );
            }
        }
    }
}
register_deactivation_hook( __FILE__, 'myplugin_remove_capabilities' );
```

### Checking Custom Capabilities

```php
// Check custom capability
if ( current_user_can( 'myplugin_manage_payments' ) ) {
    // Show payment management interface
}

// Gate admin menu
add_menu_page(
    __( 'Payments', 'my-plugin' ),
    __( 'Payments', 'my-plugin' ),
    'myplugin_view_transactions',  // Required capability
    'wcpay-payments',
    'myplugin_render_payments_page'
);

// Gate AJAX handler
add_action( 'wp_ajax_myplugin_refund', function() {
    if ( ! current_user_can( 'myplugin_issue_refunds' ) ) {
        wp_send_json_error( [ 'message' => 'Permission denied' ], 403 );
    }
    // Process refund...
} );
```

## User Sessions

### Get User Sessions

```php
$sessions = WP_Session_Tokens::get_instance( $user_id );
$all_sessions = $sessions->get_all();
```

### Destroy Sessions

```php
// Destroy all sessions for user (force logout everywhere)
$sessions = WP_Session_Tokens::get_instance( $user_id );
$sessions->destroy_all();

// Destroy all sessions except current
$sessions->destroy_others( wp_get_session_token() );
```

## User Authentication Hooks

### Login/Logout Actions

```php
// After successful login
add_action( 'wp_login', function( $user_login, $user ) {
    update_user_meta( $user->ID, 'myplugin_last_login', time() );
}, 10, 2 );

// Before logout
add_action( 'wp_logout', function( $user_id ) {
    // Clean up temporary data
    delete_user_meta( $user_id, 'myplugin_session_data' );
} );

// Failed login attempt
add_action( 'wp_login_failed', function( $username ) {
    // Log failed attempt, implement rate limiting, etc.
} );
```

### User Registration

```php
// After user registration
add_action( 'user_register', function( $user_id ) {
    // Set default My_Plugin preferences
    update_user_meta( $user_id, 'myplugin_preferences', [
        'currency' => get_option( 'woocommerce_currency', 'USD' ),
        'notifications' => true,
    ] );
} );
```

## Security Best Practices

### Always Verify Capabilities

```php
// Before any user operation
if ( ! current_user_can( 'edit_user', $user_id ) ) {
    wp_die( esc_html__( 'Permission denied.', 'my-plugin' ) );
}
```

### Sanitize All Input

```php
$username = sanitize_user( wp_unslash( $_POST['username'] ) );
$email = sanitize_email( $_POST['email'] );
$first_name = sanitize_text_field( wp_unslash( $_POST['first_name'] ) );
```

### Validate Before Operations

```php
// Validate user exists before operations
$user = get_userdata( $user_id );
if ( ! $user ) {
    return new WP_Error( 'invalid_user', __( 'User not found.', 'my-plugin' ) );
}

// Validate email uniqueness
$existing = email_exists( $new_email );
if ( $existing && $existing !== $user_id ) {
    return new WP_Error( 'email_exists', __( 'Email already in use.', 'my-plugin' ) );
}
```

### Never Trust User IDs from Requests

```php
// WRONG - User can manipulate to access other users
$user_id = absint( $_POST['user_id'] );
update_user_meta( $user_id, 'data', $value );

// CORRECT - Verify permission first
$user_id = absint( $_POST['user_id'] );
if ( ! current_user_can( 'edit_user', $user_id ) ) {
    wp_die( 'Permission denied' );
}
update_user_meta( $user_id, 'data', $value );

// BETTER - For user's own data, use current user ID
$user_id = get_current_user_id();
if ( ! $user_id ) {
    wp_die( 'Not logged in' );
}
update_user_meta( $user_id, 'data', $value );
```

## Summary Checklist

When working with users, verify:

- [ ] Capabilities checked before sensitive operations
- [ ] User existence validated before operations
- [ ] Input sanitized (usernames, emails, meta values)
- [ ] Nonces verified for form submissions
- [ ] Custom roles only added once (on activation)
- [ ] User meta keys prefixed to avoid conflicts
- [ ] Content reassigned when deleting users
- [ ] Password changes handled carefully (cookie clearing)
- [ ] User IDs from requests validated for permission