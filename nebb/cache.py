import json
import threading
from pathlib import Path
from typing import Dict, Any, Optional

class EntidadeCache:
    _lock = threading.Lock()
    
    def __init__(self, cache_file: str = "entity_cache.json") -> None:
        self.cache_file = Path(cache_file)
        self.cache: Dict[str, Dict[str, Any]] = self._load()

    def _load(self) -> Dict[str, Dict[str, Any]]:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        if self.cache_file.exists():
            with EntidadeCache._lock:
                with open(self.cache_file, "r") as f:
                    try:
                        from typing import cast
                        return cast(Dict[str, Dict[str, Any]], json.load(f))
                    except json.JSONDecodeError:
                        return {}
        return {}

    def _save(self) -> None:
        with EntidadeCache._lock:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache, f, indent=4)

    def get(self, entity: str) -> Optional[Dict[str, Any]]:
        with EntidadeCache._lock:
            return self.cache.get(entity)

    def set(self, entity: str, data: Dict[str, Any]) -> None:
        with EntidadeCache._lock:
            self.cache[entity] = data
        self._save()
