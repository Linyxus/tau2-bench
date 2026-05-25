DOMAIN=airline
MODEL=google/gemini-3.1-flash-lite
uv run tau2 run --domain $DOMAIN --agent-llm openrouter/$MODEL --user-llm openrouter/openai/gpt-5.4 \
  --num-trials 4

