from datetime import datetime, timezone
from typing import List, Optional

from openai import OpenAI

from letta.constants import MAX_EMBEDDING_DIM
from letta.embeddings import embedding_model, parse_and_chunk_text
from letta.orm.errors import NoResultFound
from letta.orm.passage import AgentPassage, SourcePassage
from letta.schemas.agent import AgentState
from letta.schemas.passage import Passage as PydanticPassage
from letta.schemas.user import User as PydanticUser
from letta.server.db import db_registry
from letta.tracing import trace_method
from letta.utils import enforce_types


class PassageManager:
    """Manager class to handle business logic related to Passages."""

    @enforce_types
    @trace_method
    def get_passage_by_id(self, passage_id: str, actor: PydanticUser) -> Optional[PydanticPassage]:
        """Fetch a passage by ID."""
        with db_registry.session() as session:
            # Try source passages first
            try:
                passage = SourcePassage.read(db_session=session, identifier=passage_id, actor=actor)
                return passage.to_pydantic()
            except NoResultFound:
                # Try archival passages
                try:
                    passage = AgentPassage.read(db_session=session, identifier=passage_id, actor=actor)
                    return passage.to_pydantic()
                except NoResultFound:
                    raise NoResultFound(f"Passage with id {passage_id} not found in database.")

    @enforce_types
    @trace_method
    def create_passage(self, pydantic_passage: PydanticPassage, actor: PydanticUser) -> PydanticPassage:
        """Create a new passage in the appropriate table based on whether it has agent_id or source_id."""
        # Common fields for both passage types
        data = pydantic_passage.model_dump(to_orm=True)
        common_fields = {
            "id": data.get("id"),
            "text": data["text"],
            "embedding": data["embedding"],
            "embedding_config": data["embedding_config"],
            "organization_id": data["organization_id"],
            "metadata_": data.get("metadata", {}),
            "is_deleted": data.get("is_deleted", False),
            "created_at": data.get("created_at", datetime.now(timezone.utc)),
        }

        if "agent_id" in data and data["agent_id"]:
            assert not data.get("source_id"), "Passage cannot have both agent_id and source_id"
            agent_fields = {
                "agent_id": data["agent_id"],
            }
            passage = AgentPassage(**common_fields, **agent_fields)
        elif "source_id" in data and data["source_id"]:
            assert not data.get("agent_id"), "Passage cannot have both agent_id and source_id"
            source_fields = {
                "source_id": data["source_id"],
                "file_id": data.get("file_id"),
            }
            passage = SourcePassage(**common_fields, **source_fields)
        else:
            raise ValueError("Passage must have either agent_id or source_id")

        with db_registry.session() as session:
            passage.create(session, actor=actor)
            return passage.to_pydantic()

    @enforce_types
    @trace_method
    def create_many_passages(self, passages: List[PydanticPassage], actor: PydanticUser) -> List[PydanticPassage]:
        """Create multiple passages."""
        return [self.create_passage(p, actor) for p in passages]

    @enforce_types
    @trace_method
    def insert_passage(
        self,
        agent_state: AgentState,
        agent_id: str,
        text: str,
        actor: PydanticUser,
    ) -> List[PydanticPassage]:
        """Insert passage(s) into archival memory"""

        embedding_chunk_size = agent_state.embedding_config.embedding_chunk_size

        # TODO eventually migrate off of llama-index for embeddings?
        # Already causing pain for OpenAI proxy endpoints like LM Studio...
        if agent_state.embedding_config.embedding_endpoint_type != "openai":
            embed_model = embedding_model(agent_state.embedding_config)

        passages = []

        try:
            # breakup string into passages
            for text in parse_and_chunk_text(text, embedding_chunk_size):

                if agent_state.embedding_config.embedding_endpoint_type != "openai":
                    embedding = embed_model.get_text_embedding(text)
                else:
                    # TODO should have the settings passed in via the server call
                    from letta.settings import model_settings

                    # Simple OpenAI client code
                    client = OpenAI(
                        api_key=model_settings.openai_api_key, base_url=agent_state.embedding_config.embedding_endpoint, max_retries=0
                    )
                    response = client.embeddings.create(input=text, model=agent_state.embedding_config.embedding_model)
                    embedding = response.data[0].embedding

                if isinstance(embedding, dict):
                    try:
                        embedding = embedding["data"][0]["embedding"]
                    except (KeyError, IndexError):
                        # TODO as a fallback, see if we can find any lists in the payload
                        raise TypeError(
                            f"Got back an unexpected payload from text embedding function, type={type(embedding)}, value={embedding}"
                        )
                passage = self.create_passage(
                    PydanticPassage(
                        organization_id=actor.organization_id,
                        agent_id=agent_id,
                        text=text,
                        embedding=embedding,
                        embedding_config=agent_state.embedding_config,
                    ),
                    actor=actor,
                )
                passages.append(passage)

            return passages

        except Exception as e:
            raise e

    @enforce_types
    @trace_method
    def update_passage_by_id(self, passage_id: str, passage: PydanticPassage, actor: PydanticUser, **kwargs) -> Optional[PydanticPassage]:
        """Update a passage."""
        if not passage_id:
            raise ValueError("Passage ID must be provided.")

        with db_registry.session() as session:
            # Try source passages first
            try:
                curr_passage = SourcePassage.read(
                    db_session=session,
                    identifier=passage_id,
                    actor=actor,
                )
            except NoResultFound:
                # Try agent passages
                try:
                    curr_passage = AgentPassage.read(
                        db_session=session,
                        identifier=passage_id,
                        actor=actor,
                    )
                except NoResultFound:
                    raise ValueError(f"Passage with id {passage_id} does not exist.")

            # Update the database record with values from the provided record
            update_data = passage.model_dump(to_orm=True, exclude_unset=True, exclude_none=True)
            for key, value in update_data.items():
                setattr(curr_passage, key, value)

            # Commit changes
            curr_passage.update(session, actor=actor)
            return curr_passage.to_pydantic()

    @enforce_types
    @trace_method
    def delete_passage_by_id(self, passage_id: str, actor: PydanticUser) -> bool:
        """Delete a passage from either source or archival passages."""
        if not passage_id:
            raise ValueError("Passage ID must be provided.")

        with db_registry.session() as session:
            # Try source passages first
            try:
                passage = SourcePassage.read(db_session=session, identifier=passage_id, actor=actor)
                passage.hard_delete(session, actor=actor)
                return True
            except NoResultFound:
                # Try archival passages
                try:
                    passage = AgentPassage.read(db_session=session, identifier=passage_id, actor=actor)
                    passage.hard_delete(session, actor=actor)
                    return True
                except NoResultFound:
                    raise NoResultFound(f"Passage with id {passage_id} not found.")

    @enforce_types
    @trace_method
    def delete_passages(
        self,
        actor: PydanticUser,
        passages: List[PydanticPassage],
    ) -> bool:
        # TODO: This is very inefficient
        # TODO: We should have a base `delete_all_matching_filters`-esque function
        for passage in passages:
            self.delete_passage_by_id(passage_id=passage.id, actor=actor)
        return True

    @enforce_types
    @trace_method
    def size(
        self,
        actor: PydanticUser,
        agent_id: Optional[str] = None,
    ) -> int:
        """Get the total count of messages with optional filters.

        Args:
            actor: The user requesting the count
            agent_id: The agent ID of the messages
        """
        with db_registry.session() as session:
            return AgentPassage.size(db_session=session, actor=actor, agent_id=agent_id)

    @enforce_types
    @trace_method
    async def size_async(
        self,
        actor: PydanticUser,
        agent_id: Optional[str] = None,
    ) -> int:
        """Get the total count of messages with optional filters.
        Args:
            actor: The user requesting the count
            agent_id: The agent ID of the messages
        """
        async with db_registry.async_session() as session:
            return await AgentPassage.size_async(db_session=session, actor=actor, agent_id=agent_id)

    @enforce_types
    @trace_method
    def estimate_embeddings_size(
        self,
        actor: PydanticUser,
        agent_id: Optional[str] = None,
        storage_unit: str = "GB",
    ) -> float:
        """
        Estimate the size of the embeddings. Defaults to GB.
        """
        BYTES_PER_STORAGE_UNIT = {
            "B": 1,
            "KB": 1024,
            "MB": 1024**2,
            "GB": 1024**3,
            "TB": 1024**4,
        }
        if storage_unit not in BYTES_PER_STORAGE_UNIT:
            raise ValueError(f"Invalid storage unit: {storage_unit}. Must be one of {list(BYTES_PER_STORAGE_UNIT.keys())}.")
        BYTES_PER_EMBEDDING_DIM = 4
        GB_PER_EMBEDDING = BYTES_PER_EMBEDDING_DIM / BYTES_PER_STORAGE_UNIT[storage_unit] * MAX_EMBEDDING_DIM
        return self.size(actor=actor, agent_id=agent_id) * GB_PER_EMBEDDING
