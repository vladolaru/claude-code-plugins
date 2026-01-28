---
name: woocommerce-browser-interaction
description: Use when needing to interact with WooCommerce in a browser - debugging UI issues, verifying changes, testing flows, taking screenshots, or exploring admin and frontend pages (shop, cart, checkout, my account). Requires a browser automation MCP server (chrome-devtools, playwright, or puppeteer).
---

# WooCommerce Browser Interaction

Browser automation for WooCommerce debugging, verification, and testing.

**REQUIRED:** First read and follow the `pirategoat-tools:browser-interaction` skill for MCP detection, tool mapping, RULE 0, error handling, and browser closing. This skill adds WooCommerce-specific patterns.

**Complete the browser task requested. Do not add extra checks or improvements beyond what is explicitly asked.**

---

## Required Setup Before Browser Use

**Get site URL first.** Use `AskUserQuestion`:

```
Question: "What is the WordPress site URL?"
Header: "Site URL"
Options:
  - "http://localhost:8082" (WooPayments wp-env, credentials: admin/admin)
  - "http://localhost:8888" (wp-env default)
  - "http://localhost:8080" (alternate local)
```

Also gather **admin credentials** (username/password) if authentication is needed.

Verify site is running: `curl -s -o /dev/null -w "%{http_code}" {base_url}` → expect 200/301/302.

---

## Login and Navigate Workflow

```
1. Navigate → {base_url}/wp-admin/
2. If wp-login.php → fill #user_login, #user_pass → click #wp-submit
3. Navigate directly to target URL via address bar
4. Take snapshot → Interact → Verify
```

**Use direct URL navigation.** Menu clicking is slow and unreliable. URLs are in the Navigation Reference below.

### Authentication Errors

| Symptom | Cause | Resolution |
|---------|-------|------------|
| Redirects back to wp-login.php | Wrong credentials | Ask user for correct username/password |
| "Invalid username" error | User doesn't exist | Verify username with user |
| "Incorrect password" error | Wrong password | Ask user to verify password |
| "Too many failed attempts" | Rate limited | Wait 15 min or ask user to unlock |
| Captcha/reCAPTCHA appears | Bot protection | Ask user to login manually first |
| 2FA/MFA prompt | Two-factor enabled | Ask user to provide code or login manually |

**Login selector fallbacks:**
```
Primary:    #user_login, #user_pass, #wp-submit
Fallback:   input[name="log"], input[name="pwd"], input[type="submit"]
```

**Session persistence:** After successful login, verify by checking for `#wpadminbar` or `.wp-admin` body class.

---

## URL Reference for Direct Navigation

Navigate via URL. Use snapshot to discover current selectors.

### WC Admin (React SPA)
Wait for `.woocommerce-layout` after navigation.

| Page | URL |
|------|-----|
| Home | `/wp-admin/admin.php?page=wc-admin` |
| Orders | `/wp-admin/admin.php?page=wc-orders` |
| Products | `/wp-admin/edit.php?post_type=product` |
| Customers | `/wp-admin/admin.php?page=wc-admin&path=/customers` |
| Reports | `/wp-admin/admin.php?page=wc-reports` |
| Analytics | `/wp-admin/admin.php?page=wc-admin&path=/analytics/overview` |
| Marketing | `/wp-admin/admin.php?page=wc-admin&path=/marketing` |
| Extensions | `/wp-admin/admin.php?page=wc-admin&path=/extensions` |

### WC Settings (Classic + Hybrid)
Wait for `#mainform` after navigation.

| Tab | URL | Notes |
|-----|-----|-------|
| General | `?page=wc-settings&tab=general` | Classic |
| Products | `?page=wc-settings&tab=products` | Classic |
| Tax | `?page=wc-settings&tab=tax` | Classic |
| Shipping | `?page=wc-settings&tab=shipping` | Classic |
| Payments | `?page=wc-settings&tab=checkout` | Hybrid (React in classic wrapper) |
| Accounts | `?page=wc-settings&tab=account` | Classic |
| Emails | `?page=wc-settings&tab=email` | Classic |
| Advanced | `?page=wc-settings&tab=advanced` | Classic |

**WooPayments Onboarding:**
`?page=wc-settings&tab=checkout&path=%2Fwoopayments%2Fonboarding`

### WC Status
| Page | URL |
|------|-----|
| System Status | `?page=wc-status` |
| Tools | `?page=wc-status&tab=tools` |
| Logs | `?page=wc-status&tab=logs` |

### Dynamic URLs
| Page | Pattern |
|------|---------|
| Edit Product | `/wp-admin/post.php?post={ID}&action=edit` |
| Edit Order (HPOS) | `?page=wc-orders&action=edit&id={ID}` |

### Frontend Pages
**Permalink Fallback:** Try pretty URL first. If 404, switch to query string URLs for the session.

| Page | Pretty | Query String | Body Class |
|------|--------|--------------|------------|
| Shop | `/shop/` | `?post_type=product` | `.post-type-archive-product` |
| Cart | `/cart/` | `?page_id={id}` | `.woocommerce-cart` |
| Checkout | `/checkout/` | `?page_id={id}` | `.woocommerce-checkout` |
| My Account | `/my-account/` | `?page_id={id}` | `.woocommerce-account` |

Get page IDs from `?page=wc-status` → "WooCommerce pages" section.

---

## Identify Page Type Before Interacting

Detect page type from URL or body class:
- **WC Admin (React):** URL contains `page=wc-admin` → wait for `.woocommerce-layout`
- **WC Settings:** URL contains `page=wc-settings` → wait for `#mainform`
- **WC Status:** URL contains `page=wc-status` → wait for `.nav-tab-wrapper`
- **Frontend:** Body has `.woocommerce-cart`, `.woocommerce-checkout`, etc.

**Block vs Classic Cart/Checkout:**
```javascript
// Block-based (modern default)
!!document.querySelector('.wp-block-woocommerce-cart')
!!document.querySelector('.wp-block-woocommerce-checkout')

// Classic shortcode
!!document.querySelector('.woocommerce-cart-form')
!!document.querySelector('form.checkout')
```

---

## WooCommerce-Specific Errors

### Navigation Errors

| Error | Cause | Recovery |
|-------|-------|----------|
| "You need a higher level of permission" | Insufficient user role | Ask user for admin account |
| "Sorry, you are not allowed" | Capability missing | User needs correct WordPress role |

### React SPA Errors (WC Admin)

| Symptom | Cause | Recovery |
|---------|-------|----------|
| Spinner never stops | API request hanging | Check network for failed requests |
| "Something went wrong" | React error boundary | Check console for stack trace |
| Data not loading | REST API error | Look for 4xx/5xx in network tab |
| Partial render | JS bundle failed | Check for 404 on chunk files |

**Wait strategy for React:**
```
1. Wait for .woocommerce-layout to appear
2. Wait for .woocommerce-spinner to disappear
3. Then interact with content
```

---

## Debug, Fix, and Verify Workflow

### Debug → Fix → Verify Loop

```
1. IDENTIFY: Find error via console/network/DOM
2. LOCATE: Map error to source file (stack trace, grep for message)
3. FIX: Edit the source file
4. REBUILD: (localhost only) See build workflow below
5. REFRESH: Navigate to same URL
6. VERIFY: Confirm error gone, no regressions
```

### Build Workflow (Localhost Only)

**Skip this section if URL is not localhost** (e.g., `localhost:*`, `127.0.0.1:*`).

```
1. DETECT REPO: Check if current working directory is a WooCommerce-related repo
   - Run: git rev-parse --show-toplevel
   - Read README.md at repo root and check if it mentions WooCommerce
   - Applies to: core repo, WooPayments, extensions, themes, any WooCommerce-related project
2. CONFIRM: If CWD is not WooCommerce-related, use AskUserQuestion:
   - Question: "What is the path to your local WooCommerce-related repository?"
   - Header: "Repo Path"
   - Do NOT guess or search filesystem
3. READ AI DOCS: Look for build instructions in repo's AI documentation:
   - CLAUDE.md (root or .claude/ directory)
   - AGENTS.md, CONTRIBUTING.md
   - package.json scripts section
4. BUILD: Run the documented build command
5. WAIT: Ensure build completes before refreshing browser
```

**If no build docs found:** Ask user for build command via AskUserQuestion.

---

## WooCommerce-Specific Autonomous Recovery

In addition to general error recovery from `browser-interaction` skill:

| Category | Errors | Recovery Action |
|----------|--------|-----------------|
| **Session** | Redirect to wp-login.php | Re-authenticate, return to previous URL |
| **Frontend** | 404 on pretty URL | Switch to query string URLs for session |

---

## Common Selectors by Page Type

Use snapshot to find current selectors. Common patterns:

**WC Admin (React):**
- App root: `.woocommerce-layout`
- Tables: `.woocommerce-table`
- Loading: `.woocommerce-spinner`
- Notices: `.components-notice`

**WC Settings (Classic):**
- Form: `#mainform`
- Tabs: `.nav-tab-wrapper`, `.nav-tab-active`
- Save: `.woocommerce-save-button`

**WooPayments Onboarding Modal:**
- Overlay: `.components-modal__screen-overlay`
- Frame: `.settings-payments-onboarding-modal`

**Frontend Cart (Block):**
- Container: `.wp-block-woocommerce-cart`
- Empty state: `.wp-block-woocommerce-empty-cart-block`
- Checkout button: `.wc-block-cart__submit-button`

**Frontend Checkout (Block):**
- Container: `.wp-block-woocommerce-checkout`
- Place order: `.wc-block-components-checkout-place-order-button`

**My Account:**
- Navigation: `.woocommerce-MyAccount-navigation`
- Content: `.woocommerce-MyAccount-content`

For complete selector reference, see `SELECTORS.md` in this skill directory.
