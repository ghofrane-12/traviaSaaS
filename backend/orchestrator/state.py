from typing import Any, Optional
from typing_extensions import TypedDict

class AgentState(TypedDict):
    #  Contexte multi-tenant 
    session_id:    str
    tenant_id:     str
    user_id:       str
    raw_text:      str

    #  Pipeline 
    language:       Optional[str]           
    segments:       list[str]               
    classification: list[dict]              
    entities:       dict[str, list]         

    #  Config tenant 
    tenant_config:  dict                    

    #  Contexte conversationnel 
    context:        dict                  
      # ── Clarification (zone 0.5 – 0.8) ───────────────────────
    clarification_pending:  bool        
    clarification_subcats:  list[dict]  
    clarification_segment:  str         
    clarification_choice:   Optional[dict]  
    clarification_accepted:  list[dict]   
    clarification_entities:  dict       
    ambiguous_queue: list[dict] 
    #  Résultats agents 
    results:        dict                    
    suggestions:    list      
    loyalty_summary: dict               

    #  Formulaire en attente (need_more_info)
    waiting_for_form:  bool  
    is_form_response:  bool       
    pending_segments:  dict   
    cross_sell_proposals: list
    is_cross_sell: bool          
    cross_sell_id: Optional[str] 
    active_subcategories: list

    #  Réponse finale 
    final_response: Optional[Any]