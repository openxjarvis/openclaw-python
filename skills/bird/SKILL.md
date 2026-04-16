---
name: bird
description: "Search and read Twitter/X posts, profiles, and trending topics via web search and fetch. Use when user asks to find tweets, check a Twitter/X profile, search hashtags, or look up trending discussions on Twitter/X."
metadata: { "openclaw": { "emoji": "🐦", "requires": {} } }
---

# Twitter/X (Bird)

Search and read Twitter/X content using web search and fetch tools. This skill focuses on reading public content — it does not post or interact with the Twitter/X API directly.

## Workflow

1. **Identify the request**: Determine if the user wants to search tweets, read a profile, check trends, or find a specific post.
2. **Search**: Use `web_search` with Twitter/X-specific queries.
3. **Fetch**: Use `web_fetch` on result URLs to get full content when needed.
4. **Summarize**: Present findings in a clear, readable format.

## Search Patterns

Twitter/X content is indexed by search engines. Use site-scoped queries for best results:

```
"site:x.com from:elonmusk AI announcement"
"site:x.com #python trending"
"site:x.com @openai latest"
"twitter.com/username/status"
```

For general topic searches:

```
"twitter reactions to [event]"
"[topic] twitter discussion 2024"
"[person] tweet about [subject]"
```

## Reading Profiles

To look up a user's recent activity:

1. Search: `site:x.com [username]`
2. Fetch their profile page or recent tweets from search results
3. Summarize bio, follower count, and recent posts

## Reading Specific Tweets

If the user provides a tweet URL (e.g. `x.com/user/status/123456`):

1. Fetch the URL directly with `web_fetch`
2. Extract the tweet text, author, date, engagement metrics
3. Include any thread context if it's part of a longer thread

## Presenting Results

Format tweet summaries clearly:

```
**@username** · 2h ago
Tweet text here...
❤️ 1.2K  🔁 340  💬 89
```

## Tips

- Twitter/X may block some fetches — fall back to search engine cached versions or alternative frontends like nitter instances when direct fetch fails.
- For trending topics, search `"trending on twitter [topic/region]"` via web_search.
- When summarizing threads, number the tweets and indicate the thread structure.
- Always note when content might be outdated or when you can't verify tweet authenticity from search results alone.
