# WooCommerce Selectors Reference

Complete selector reference for WooCommerce browser interaction. Use `take_snapshot` first to verify selectors exist on current page.

---

## WC Admin (React Pages)

| Element | Selector |
|---------|----------|
| App root | `.woocommerce-layout` |
| Main content | `.woocommerce-layout__main` |
| Header | `.woocommerce-layout__header` |
| Loading spinner | `.woocommerce-spinner` |
| Data table | `.woocommerce-table` |
| Table row | `.woocommerce-table__item` |
| Pagination | `.woocommerce-pagination` |
| Search box | `.woocommerce-search` |
| Filter dropdown | `.woocommerce-filters` |
| Empty state | `.woocommerce-empty-content` |
| Card component | `.woocommerce-card` |
| Notice/alert | `.components-notice` |

---

## WC Settings (Classic PHP)

| Element | Selector |
|---------|----------|
| Settings form | `#mainform` |
| Tab navigation | `.nav-tab-wrapper` |
| Active tab | `.nav-tab-active` |
| Section nav (sub-tabs) | `.subsubsub` |
| Save button | `.woocommerce-save-button` |
| Input field | `input[type="text"]` within `#mainform` |
| Toggle switch | `.woocommerce-input-toggle` |
| Color picker | `.wp-picker-container` |
| Notice (success) | `.woocommerce-message` or `.updated` |
| Notice (error) | `.woocommerce-error` or `.error` |

---

## WooPayments Onboarding Modal

| Element | Selector |
|---------|----------|
| Modal overlay | `.components-modal__screen-overlay` |
| Modal frame | `.settings-payments-onboarding-modal` |
| Modal wrapper | `.settings-payments-onboarding-modal__wrapper` |
| Modal sidebar | `.settings-payments-onboarding-modal__sidebar` |
| Modal content | `.components-modal__content` |
| Close button | `.components-modal__header button` |

---

## WC Status Pages

| Element | Selector |
|---------|----------|
| Status table | `table.wc_status_table` |
| Tab navigation | `.nav-tab-wrapper` |
| Active tab | `.nav-tab-active` |

---

## Product Editor

| Element | Selector |
|---------|----------|
| Block editor | `.editor-post-title` |
| Classic editor title | `#title` |
| Publish button | `.editor-post-publish-button` |
| Update button | `.editor-post-publish-button` |
| Product data tabs | `.product_data_tabs` |
| General tab | `#general_product_data` |
| Inventory tab | `#inventory_product_data` |
| Shipping tab | `#shipping_product_data` |
| Price field (regular) | `#_regular_price` |
| Price field (sale) | `#_sale_price` |
| SKU field | `#_sku` |
| Stock qty | `#_stock` |
| Product gallery | `#product_images_container` |

---

## Order Editor

| Element | Selector |
|---------|----------|
| Order status | `#order_status` |
| Order items | `#order_line_items` |
| Add item button | `.add-line-item` |
| Customer dropdown | `#customer_user` |
| Billing address | `.order_data_column:nth-child(1)` |
| Shipping address | `.order_data_column:nth-child(2)` |
| Order actions | `#woocommerce-order-actions` |
| Order notes | `#woocommerce-order-notes` |
| Save order | `button[name="save"]` |

---

## Frontend - Cart (Classic Shortcode)

| Element | Selector |
|---------|----------|
| Cart table | `.woocommerce-cart-form` |
| Cart item | `.woocommerce-cart-form__cart-item` |
| Quantity input | `.qty` |
| Remove item | `.remove` |
| Update cart | `button[name="update_cart"]` |
| Coupon field | `#coupon_code` |
| Apply coupon | `button[name="apply_coupon"]` |
| Cart totals | `.cart_totals` |
| Proceed to checkout | `.checkout-button` |

---

## Frontend - Cart (Block-based)

| Element | Selector |
|---------|----------|
| Cart block container | `.wp-block-woocommerce-cart` |
| Empty cart block | `.wp-block-woocommerce-empty-cart-block` |
| Cart items | `.wc-block-cart-items` |
| Cart item row | `.wc-block-cart-items__row` |
| Quantity selector | `.wc-block-components-quantity-selector` |
| Remove button | `.wc-block-cart-item__remove-link` |
| Coupon form | `.wc-block-components-totals-coupon` |
| Order summary | `.wc-block-components-order-summary` |
| Proceed to checkout | `.wc-block-cart__submit-button` |

---

## Frontend - Checkout (Classic Shortcode)

| Element | Selector |
|---------|----------|
| Checkout form | `form.checkout` |
| Billing fields | `.woocommerce-billing-fields` |
| Shipping fields | `.woocommerce-shipping-fields` |
| First name | `#billing_first_name` |
| Last name | `#billing_last_name` |
| Email | `#billing_email` |
| Payment methods | `.wc_payment_methods` |
| Place order button | `#place_order` |
| Order review | `.woocommerce-checkout-review-order` |
| Coupon toggle | `.woocommerce-form-coupon-toggle` |

---

## Frontend - Checkout (Block-based)

| Element | Selector |
|---------|----------|
| Checkout block container | `.wp-block-woocommerce-checkout` |
| Contact info | `.wc-block-checkout__contact-fields` |
| Shipping address | `.wc-block-checkout__shipping-fields` |
| Billing address | `.wc-block-checkout__billing-fields` |
| Shipping options | `.wc-block-checkout__shipping-option` |
| Payment methods | `.wc-block-checkout__payment-method` |
| Place order button | `.wc-block-components-checkout-place-order-button` |
| Order summary | `.wc-block-components-order-summary` |

---

## Frontend - My Account

| Element | Selector |
|---------|----------|
| Navigation | `.woocommerce-MyAccount-navigation` |
| Content area | `.woocommerce-MyAccount-content` |
| Orders table | `.woocommerce-orders-table` |
| Login form | `.woocommerce-form-login` |
| Register form | `.woocommerce-form-register` |
| Edit account form | `.woocommerce-EditAccountForm` |

---

## Page Body Classes

| Body Class | Page Type |
|------------|-----------|
| `woocommerce-page` | Any WooCommerce page |
| `single-product` | Single product view |
| `woocommerce-cart` | Cart page |
| `woocommerce-checkout` | Checkout page |
| `woocommerce-account` | My Account section |
| `post-type-archive-product` | Shop/product archive |
| `tax-product_cat` | Product category |
| `woocommerce-order-received` | Thank you page |
