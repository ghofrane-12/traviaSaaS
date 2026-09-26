from core.database import get_pool
from orchestrator.state import AgentState
import uuid

async def adapter_node(state: AgentState) -> AgentState:
    """
    Charge la configuration tenant : tone, currency, margin et API keys actives.
    """
    pool = await get_pool()

    tenant = await pool.fetchrow(
        "SELECT tone, currency, margin_percentage FROM tenants WHERE tenant_id = $1",
        uuid.UUID(state["tenant_id"])
    )
    if not tenant:
        return {**state, "tenant_config": {}}

    keys = await pool.fetch(
    "SELECT label, api_key, api_url, http_method, timeout_ms, "
    "agent_type, param_mapping, headers_template, payload_template, result_path "
    "FROM tenant_api_keys WHERE tenant_id = $1 AND is_active = TRUE",
    uuid.UUID(state["tenant_id"])
    )

    tenant_config = dict(tenant)
    tenant_config["api_keys"] = {row["label"]: dict(row) for row in keys}

    return {**state, "tenant_config": tenant_config}