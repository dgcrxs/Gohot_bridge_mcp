import asyncio
import websockets
import json

async def test():
    uri = "ws://localhost:8080"
    try:
        async with websockets.connect(uri) as ws:
            print("✅ 连接成功")
            await ws.send(json.dumps({"id": 1, "method": "get_editor_info", "params": {}}))
            response = await ws.recv()
            print("📩 收到:", response)
    except Exception as e:
        print("❌ 失败:", e)

asyncio.run(test())