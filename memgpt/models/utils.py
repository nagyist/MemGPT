from datetime import datetime

from memgpt.data_types import AgentState, EmbeddingConfig, LLMConfig
from memgpt.models.pydantic_models import (
    AgentStateModel,
    EmbeddingConfigModel,
    LLMConfigModel,
)
from memgpt.utils import datetime_to_unix_int


def embeddingconfig_to_embeddingconfigmodel(embedding_config: EmbeddingConfig):
    return EmbeddingConfigModel(
        embedding_endpoint_type=embedding_config.embedding_endpoint_type,
        embedding_endpoint=embedding_config.embedding_endpoint,
        embedding_model=embedding_config.embedding_model,
        embedding_dim=embedding_config.embedding_dim,
        embedding_chunk_size=embedding_config.embedding_chunk_size,
    )


def llmconfig_to_llmconfigmodel(llm_config: LLMConfig):
    return LLMConfigModel(
        model=llm_config.model,
        model_endpoint_type=llm_config.model_endpoint_type,
        model_endpoint=llm_config.model_endpoint,
        model_wrapper=llm_config.model_wrapper,
        context_window=llm_config.context_window,
    )


def agentstate_to_agentstatemodel(agent_state: AgentState):
    model = AgentStateModel(
        id=agent_state.id,
        name=agent_state.name,
        description=None,
        user_id=agent_state.user_id,
        created_at=datetime_to_unix_int(agent_state.created_at),
        tools=agent_state.tools,
        system=agent_state.system,
        llm_config=llmconfig_to_llmconfigmodel(agent_state.llm_config),
        embedding_config=embeddingconfig_to_embeddingconfigmodel(agent_state.embedding_config),
        state=agent_state.state,
        metadata=agent_state._metadata,
    )

    # TODO this really needs to be done via a pydantic model auto-conversion
    if "last_run" in model.metadata and isinstance(model.metadata["last_run"], datetime):
        model.metadata["last_run"] = datetime_to_unix_int(model.metadata["last_run"])

    return model