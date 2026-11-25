"""AI is opt-in per submitted message; no startup/model calls or CDS dependency."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from backend.app.ai.agent import AgentRequest, ClimateAgent
from backend.app.ai.config import AISettings
from backend.app.ai.db_tools import build_database_tools
from backend.app.ai.responses_client import ResponsesClient
from backend.app.core.user_context import current_user_id
from backend.app.core.user_settings import get_settings_for_user
from backend.app.domain.ui_actions import UIAction
from backend.app.ai.view_service import resolve_action
from backend.app.services.dependencies import get_stored_climate_service
from backend.app.services.collection_service import CollectionService
from backend.app.ai.db_tools import DatabaseTools

router = APIRouter(prefix='/api/v1/agent', tags=['agent'])


def get_agent():
    try:
        settings = get_settings_for_user(current_user_id.get())
        return ClimateAgent(ResponsesClient(AISettings()), build_database_tools(settings))
    except ValidationError:
        raise HTTPException(503,'Cấu hình AI chưa hợp lệ. Kiểm tra AI_* ở backend.') from None


@router.get('/status')
def agent_status():
    try:
        settings = AISettings()
        configured = bool(settings.api_key and settings.api_key.get_secret_value().strip())
        return dict(configured=configured, model=settings.model, reasoning_effort=settings.reasoning_effort,
                    query_mode='db_only',capabilities=['basic_social','related_meteorology','project_knowledge','stored_climate_data'])
    except ValidationError:
        return dict(configured=False, model=None, query_mode='db_only')


@router.post('/chat')
def chat(request: AgentRequest, agent=Depends(get_agent)):
    return agent.run(request)


@router.post('/resolve-action')
def resolve(request: UIAction,stored=Depends(get_stored_climate_service)):
    return resolve_action(DatabaseTools(CollectionService(stored)),request)
