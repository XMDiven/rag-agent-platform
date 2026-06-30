from fastapi import FastAPI

from fastapi.middleware.cors import CORSMiddleware

from agent_app.app.routers.health import router as health_router
from agent_app.app.routers.run import router as run_router
from agent_app.app.routers.tools import router as tools_router

app = FastAPI()

app.add_middleware(
      CORSMiddleware,
      allow_origins=["http://localhost:3000"],
      allow_methods=["*"],
      allow_headers=["*"],
  )

app.include_router(health_router)
app.include_router(run_router)
app.include_router(tools_router)
