"""
Neo4j repository for VIP graph operations.

This layer only contains Cypher and does not touch Postgres or business logic.
"""

from typing import Any

from neo4j import AsyncSession

from app.db.neo4j import get_neo4j_session


class Neo4jRepository:
    """Low-level Neo4j operations for VIP graph syncing."""

    @staticmethod
    async def create_or_update_user(
        user_id: str,
        email_encrypted: bytes | None,
        subscription_status: str | None,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        query = """
            MERGE (u:User {user_id: $user_id})
            SET u.email_encrypted = $email_encrypted,
                u.subscription_status = $subscription_status,
                u.updated_at = datetime()
            ON CREATE SET u.created_at = datetime()
        """
        params = {
            "user_id": user_id,
            "email_encrypted": email_encrypted,
            "subscription_status": subscription_status,
        }
        await Neo4jRepository._run_write(query, params, session=session)

    @staticmethod
    async def create_or_update_person(
        user_id: str,
        contact_hash: str,
        email_encrypted: bytes | None,
        display_name_encrypted: bytes | None,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        query = """
            MERGE (p:Person {user_id: $user_id, contact_hash: $contact_hash})
            SET p.email_encrypted = $email_encrypted,
                p.display_name_encrypted = $display_name_encrypted,
                p.updated_at = datetime()
            ON CREATE SET p.created_at = datetime()
        """
        params = {
            "user_id": user_id,
            "contact_hash": contact_hash,
            "email_encrypted": email_encrypted,
            "display_name_encrypted": display_name_encrypted,
        }
        await Neo4jRepository._run_write(query, params, session=session)

    @staticmethod
    async def create_vip_relationship(
        user_id: str,
        contact_hash: str,
        rank: int,
        source: str,
        *,
        session: AsyncSession | None = None,
    ) -> None:
        query = """
            MATCH (u:User {user_id: $user_id})
            MATCH (p:Person {user_id: $user_id, contact_hash: $contact_hash})
            MERGE (u)-[r:SELECTED_VIP]->(p)
            SET r.rank = $rank,
                r.source = $source,
                r.active = true,
                r.deactivated_at = null,
                r.updated_at = datetime()
            ON CREATE SET r.selected_at = datetime()
        """
        params = {
            "user_id": user_id,
            "contact_hash": contact_hash,
            "rank": rank,
            "source": source,
        }
        await Neo4jRepository._run_write(query, params, session=session)

    @staticmethod
    async def deactivate_all_vips(user_id: str, *, session: AsyncSession | None = None) -> None:
        query = """
            MATCH (u:User {user_id: $user_id})-[r:SELECTED_VIP]->(:Person)
            SET r.active = false,
                r.deactivated_at = datetime(),
                r.updated_at = datetime()
        """
        await Neo4jRepository._run_write(query, {"user_id": user_id}, session=session)

    @staticmethod
    async def deactivate_vip(
        user_id: str, contact_hash: str, *, session: AsyncSession | None = None
    ) -> None:
        query = """
            MATCH (u:User {user_id: $user_id})-[r:SELECTED_VIP]->(p:Person {contact_hash: $contact_hash})
            SET r.active = false,
                r.deactivated_at = datetime(),
                r.updated_at = datetime()
        """
        await Neo4jRepository._run_write(
            query,
            {"user_id": user_id, "contact_hash": contact_hash},
            session=session,
        )

    @staticmethod
    async def reactivate_vip(
        user_id: str, contact_hash: str, *, session: AsyncSession | None = None
    ) -> None:
        query = """
            MATCH (u:User {user_id: $user_id})-[r:SELECTED_VIP]->(p:Person {contact_hash: $contact_hash})
            SET r.active = true,
                r.deactivated_at = null,
                r.updated_at = datetime()
        """
        await Neo4jRepository._run_write(
            query,
            {"user_id": user_id, "contact_hash": contact_hash},
            session=session,
        )

    @staticmethod
    async def get_user_vips(user_id: str, *, session: AsyncSession | None = None) -> list[dict]:
        query = """
            MATCH (u:User {user_id: $user_id})-[r:SELECTED_VIP {active: true}]->(p:Person)
            RETURN
                p.contact_hash AS contact_hash,
                p.email_encrypted AS email_encrypted,
                p.display_name_encrypted AS display_name_encrypted,
                r.rank AS rank,
                r.source AS source
            ORDER BY r.rank ASC
        """
        params = {"user_id": user_id}
        if session:
            result = await session.run(query, params)
            return await result.data()

        async with get_neo4j_session() as new_session:
            result = await new_session.run(query, params)
            return await result.data()

    @staticmethod
    async def delete_user_graph(user_id: str, *, session: AsyncSession | None = None) -> None:
        query = """
            MATCH (u:User {user_id: $user_id})
            DETACH DELETE u
        """
        await Neo4jRepository._run_write(query, {"user_id": user_id}, session=session)

    @staticmethod
    async def _run_write(
        query: str, params: dict[str, Any], *, session: AsyncSession | None
    ) -> None:
        if session:
            result = await session.run(query, params)
            await result.consume()
            return

        async with get_neo4j_session() as new_session:
            result = await new_session.run(query, params)
            await result.consume()
