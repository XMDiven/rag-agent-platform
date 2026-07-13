# LangChain Docs

Source: https://python.langchain.com/docs/

LangChain is the easy way to start building completely custom agents and applications powered by LLMs. With under 10 lines of code, you can connect to OpenAI, Anthropic, Google, and [more](https://python.langchain.com/oss/python/integrations/providers/overview). LangChain provides a prebuilt agent architecture and model integrations to help you get started quickly and seamlessly incorporate LLMs into your agents and applications.

LangChain [agents](https://python.langchain.com/oss/python/langchain/agents) are built on top of LangGraph in order to provide durable execution, streaming, human-in-the-loop, persistence, and more. You do not need to know LangGraph for basic LangChain agent usage. We recommend you use LangChain if you want to quickly build agents and autonomous applications.

## Create an agent

```
# pip install -qU langchain "langchain[anthropic]"
from langchain.agents import create_agent

def get_weather(city: str) -> str:
    """Get weather for a given city."""
    return f"It's always sunny in {city}!"

agent = create_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[get_weather],
    system_prompt="You are a helpful assistant",
)

# Run the agent
agent.invoke(
    {"messages": [{"role": "user", "content": "what is the weather in sf"}]}
)
```

See the [Installation instructions](https://python.langchain.com/oss/python/langchain/install) and [Quickstart guide](https://python.langchain.com/oss/python/langchain/quickstart) to get started building your own agents and applications with LangChain.

## Core benefits

* * *
