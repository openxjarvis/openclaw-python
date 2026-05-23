---
name: food-order
description: "Help users find restaurants, browse menus, and prepare food delivery orders via web search and fetch. Use when user asks to order food, find food delivery options, browse restaurant menus, or compare delivery services."
metadata: { "openclaw": { "emoji": "🍔", "requires": {} } }
---

# Food Ordering

Help users find restaurants, browse menus, and prepare food delivery orders using web search and fetch tools.

## Workflow

1. **Gather preferences**: Ask for location, cuisine type, dietary restrictions, budget, and preferred delivery service (DoorDash, Uber Eats, Grubhub, etc.) if not specified.
2. **Search restaurants**: Use `web_search` to find options matching the user's criteria.
3. **Browse menus**: Use `web_fetch` on restaurant pages or delivery platform listings to get menu items and prices.
4. **Present options**: Show restaurant name, rating, delivery time, fee, and relevant menu items.
5. **Confirm order**: Summarize the user's selections with items, prices, and estimated total before they place the order themselves.

## Search Patterns

Combine delivery platform + location + cuisine for targeted results:

```
"DoorDash pizza delivery near 94105"
"Uber Eats Thai food downtown Seattle"
"Grubhub vegan restaurants open now Chicago"
"best rated Chinese delivery [neighborhood]"
```

For menu browsing:

```
"[restaurant name] menu [city]"
"[restaurant name] doordash menu"
```

## Presenting Options

Format restaurant results clearly:

```
**Sakura Sushi** ⭐ 4.6 (820 reviews)
🚗 25-35 min · $2.99 delivery fee
💰 $$ | 🍣 Japanese, Sushi

Popular items:
- Dragon Roll — $14.99
- Salmon Bento Box — $16.99
- Miso Soup — $3.99
```

## Order Summary Format

Before the user places an order, present a clear summary:

```
📋 Order Summary — Sakura Sushi (via DoorDash)
- 1x Dragon Roll: $14.99
- 2x Miso Soup: $7.98
- Subtotal: $22.97
- Delivery fee: $2.99
- Estimated tax: ~$2.07
- Estimated total: ~$28.03
- ETA: 25-35 min
```

## Tips

- Always confirm the delivery address — results vary significantly by location.
- Note minimum order amounts and delivery fees, which differ across platforms.
- Mention promo codes or deals when visible on the platform page.
- Flag allergen info when the user has dietary restrictions and the restaurant lists ingredients.
- If a restaurant is on multiple platforms, compare delivery fees and ETAs.
