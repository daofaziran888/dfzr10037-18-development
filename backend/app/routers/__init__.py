"""业务路由汇总：专用路由先注册，通用资源路由兜底 /api/{module}。"""
from __future__ import annotations

from app.routers import batch_load, resource

ROUTERS = [batch_load, resource]
