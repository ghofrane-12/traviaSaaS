import httpx
import asyncio
from openai import AsyncOpenAI

async def test():
    async with httpx.AsyncClient(
        verify=False,
        timeout=httpx.Timeout(30.0, connect=10.0)
    ) as h:
        c = AsyncOpenAI(
            api_key="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",
            http_client=h
        )
        r = await c.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=10,
            messages=[{"role": "user", "content": "Hi"}]
        )
        print("SUCCESS:", r.choices[0].message.content)

asyncio.run(test())