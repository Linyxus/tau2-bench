DOMAIN=retail
MODEL=deepseek/deepseek-v4-flash
uv run tau2 run --domain $DOMAIN --agent-llm openrouter/$MODEL --user-llm openrouter/openai/gpt-5.4 \
  --num-trials 4

