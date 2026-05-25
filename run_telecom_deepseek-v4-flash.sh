DOMAIN=telecom
MODEL=deepseek/deepseek-v4-flash
uv run tau2 run --domain $DOMAIN --agent-llm openrouter/$MODEL --agent-llm-args '{"temperature": 0.0, "reasoning": {"enabled": false}}' --user-llm openrouter/openai/gpt-5.4 \
  --num-trials 4
