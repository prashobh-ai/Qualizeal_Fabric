import os
from knowledge_fabric.app import Platform
from knowledge_fabric.tenants import demo


def seeded(tenants=None, model_mode="mock"):
    os.environ["KF_MODEL_MODE"] = model_mode
    p = Platform(db_path=":memory:", blob_root="./data/test-blobs")
    demo.seed(p, tenants or ["acme-assurance", "northwind-air"])
    return p
