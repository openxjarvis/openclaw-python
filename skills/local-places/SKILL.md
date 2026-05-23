---
name: local-places
description: "Search for local businesses, restaurants, and places of interest using web search and fetch tools. Use when user asks to find nearby places, local businesses, compare ratings, check hours, or get location-based recommendations."
metadata: { "openclaw": { "emoji": "📍", "requires": {} } }
---

# Local Places

Find local businesses, restaurants, services, and points of interest using web search and fetch.

## Workflow

1. **Extract location and intent**: Identify the user's location (city, neighborhood, or address) and what they're looking for (cuisine type, service, category).
2. **Search**: Use `web_search` with a location-qualified query.
3. **Fetch details**: Use `web_fetch` on promising results to get hours, ratings, reviews, menus, or contact info.
4. **Present results**: Format findings with name, address, rating, hours, and a brief note on why each is relevant.

## Search Patterns

Effective queries combine category + location + qualifiers:

```
"best Italian restaurant in downtown Portland"
"24-hour pharmacy near Union Square San Francisco"
"pet-friendly cafes in Austin TX open now"
"highly rated dentist in Brooklyn NY accepting new patients"
```

Add qualifiers like `open now`, `best rated`, `cheapest`, `near [landmark]` to narrow results.

## Presenting Results

Format each result clearly:

```
**Joe's Pizza** ⭐ 4.7 (1,200 reviews)
📍 123 Main St, Portland, OR
🕐 Open until 10 PM
💰 $$ | 🍕 Neapolitan pizza
📞 (503) 555-0123
```

Include:
- Name and rating when available
- Address
- Hours (mention if currently open/closed when known)
- Price range and category
- Phone or website link

## Tips

- If the user doesn't specify a location, ask before searching — results are much better with a specific area.
- Cross-reference multiple sources for hours and ratings, as they can be outdated.
- For restaurants, check if menus are available online and summarize relevant options.
- Mention distance or travel time when comparing multiple options.
- Note if a place requires reservations or has long wait times when that info is available.
