# orchestrator/graph.py

from langgraph.graph import StateGraph, END
from orchestrator.state import AgentState
from orchestrator.nodes import (
    orchestrator_node, adapter_node, query_splitter_node,
    classifier_node, router_node, sales_optimizer_node,
    loyalty_node, narrator_node,
)


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    graph.add_node("orchestrator",    orchestrator_node)
    graph.add_node("adapter",         adapter_node)
    graph.add_node("query_splitter",  query_splitter_node)
    graph.add_node("classifier",      classifier_node)
    graph.add_node("router",          router_node)
    graph.add_node("sales_optimizer", sales_optimizer_node)
    graph.add_node("loyalty",         loyalty_node)
    graph.add_node("narrator",        narrator_node)

    graph.set_entry_point("orchestrator")
    graph.add_edge("orchestrator", "adapter")

    # ── Après adapter ──────────────────────────────────────────────────────
    def after_adapter(state: AgentState) -> str:
        graph_state = state.get("graph_state", {})
        
        if graph_state.get("waiting_for") == "clarification":
            print("[GRAPH] 🔒 waiting_for=clarification → bypass classifier → router")
            return "router"

        if state.get("is_form_response") or state.get("form_data"):
            print("[GRAPH] 📋 form_data → bypass → router")
            return "router"

        if state.get("clarification_pending"):
            return "classifier"

        return "query_splitter"

    graph.add_conditional_edges(
        "adapter",
        after_adapter,
        {
            "router":         "router",
            "classifier":     "classifier",
            "query_splitter": "query_splitter",
        },
    )

    # ── Après query_splitter ───────────────────────────────────────────────
    def after_query_splitter(state: AgentState) -> str:
        if state.get("entities") and state.get("classification"):
            print("[GRAPH] ✅ Mode formulaire → skip classifier → router")
            return "router"
        if state.get("final_response"):
            print("[GRAPH] 🛑 Shield/blocage → end")
            return "end"
        return "classifier"

    graph.add_conditional_edges(
        "query_splitter",
        after_query_splitter,
        {
            "router":     "router",
            "classifier": "classifier",
            "end":        END,
        },
    )

    # ── Après classifier ───────────────────────────────────────────────────
    def after_classifier(state: AgentState) -> str:
        if state.get("final_response"):
            print("[GRAPH] 🛑 Clarification/reject → end")
            return "end"
        return "router"

    graph.add_conditional_edges(
        "classifier",
        after_classifier,
        {
            "router": "router",
            "end":    END,
        },
    )

    # ── Après router ──────────────────────────────────────────────────────
    def after_router(state: AgentState) -> str:
        if state.get("final_response"):
            print("[GRAPH] 🛑 Service désactivé → end")
            return "end"
        return "sales_optimizer"

    graph.add_conditional_edges(
        "router",
        after_router,
        {
            "sales_optimizer": "sales_optimizer",
            "end":             END,
        },
    )
    
    graph.add_edge("sales_optimizer", "loyalty")
    graph.add_edge("loyalty",         "narrator")
    graph.add_edge("narrator",        END)

    return graph.compile()


travel_graph = build_graph()

def build_cross_sell_graph():
    graph = StateGraph(AgentState)

    graph.add_node("router",          router_node)
    graph.add_node("sales_optimizer", sales_optimizer_node)
    graph.add_node("loyalty",         loyalty_node)
    graph.add_node("narrator",        narrator_node)

    graph.set_entry_point("router")

    def after_router(state: AgentState) -> str:
        if state.get("final_response"):
            return "end"
        return "sales_optimizer"

    graph.add_conditional_edges(
        "router", after_router,
        {"sales_optimizer": "sales_optimizer", "end": END}
    )

    graph.add_edge("sales_optimizer", "loyalty")
    graph.add_edge("loyalty",         "narrator")
    graph.add_edge("narrator",        END)

    return graph.compile()


cross_sell_graph = build_cross_sell_graph()