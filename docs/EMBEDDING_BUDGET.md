# Google embedding budget: item 13

Customer OpenRouter keys cover OpenRouter model calls. The app's Google key supplies `gemini-embedding-001` embeddings, including new uncached search queries. These share the operator project's Google limits and billing.

1. In Google AI Studio, find the project associated with the server's `GOOGLE_API_KEY` or `GEMINI_API_KEY`. Keep the key private. Check whether its plan is Free or Paid.
2. If Paid and the control is available, open **Spend**, select that project, then **Monthly spend cap > Edit spend cap**. Choose your monthly allowance; **$10 is an example**, not an applied setting. Project caps can overshoot during Google's approximately ten-minute billing delay and are unavailable for invoiced/offline accounts. See [Google's billing instructions](https://ai.google.dev/gemini-api/docs/billing#project-spend-caps).
3. In Cloud Billing, create a project-scoped budget with email thresholds at **50%, 80% and 100%**. Alerts-only budgets notify you; they do not stop spending. See [Google Cloud budget setup](https://docs.cloud.google.com/billing/docs/how-to/budgets).
4. Check **Usage** and **Rate limits** in AI Studio during beta. Quotas apply per project. The app already paces embedding inputs through `GEMINI_EMBEDDING_TEXTS_PER_MINUTE` (default 80 per process); this is a throughput setting, not a money cap. See [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits).

Reuse ready indexes and cached queries. Keep the existing model and 768 dimensions. Review usage after the first uploads. No Google billing, quota, key, environment or model setting was changed by preparing these steps.
