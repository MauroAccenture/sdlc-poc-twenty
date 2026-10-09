import pytest
from worker.pipeline import run_pipeline

class Blob:
    def __init__(self): self.data=None
    async def download_json(self,c,n): return self.data
    async def upload_json(self,c,n,v): self.data=v

@pytest.mark.asyncio
async def test_pipeline_runs_in_order_and_checkpoints():
    from worker.shared.checkpoint import CheckpointStore
    order=[]
    async def activity(name):
        async def run(value): order.append(name); return {"step":name}
        return run
    deps={name: await activity(name) for name in ("download","analyse","price","generate","draft","notify")}
    store=CheckpointStore(Blob())
    msg=type("QueueMessage", (), {"folder_path":"Tradera/Shirt Size M", "run_id":"run1"})()
    await run_pipeline(msg,deps,store)
    assert order == ["download","analyse","price","generate","draft","notify"]
    checkpoint=await store.read("run1",msg.folder_path)
    assert checkpoint.status == "completed"
    assert len(checkpoint.completed_steps) == 6
