import asyncio
from typing import Dict, Any

class BaseAgent:
    """
    Classe de base pour tous les agents métiers.
    Chaque agent métier doit hériter de cette classe
    et implémenter la méthode `run`.
    """
    def __init__(self, entities: Dict[str, Any], tenant_config: Dict[str, Any]):
        self.entities = entities
        self.tenant_config = tenant_config

    async def run(self) -> Dict[str, Any]:
        """
        Méthode principale à surcharger par chaque agent métier
        """
        raise NotImplementedError("Chaque agent doit implémenter la méthode run()")