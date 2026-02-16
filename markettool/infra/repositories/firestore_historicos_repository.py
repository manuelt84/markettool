"""Firestore-based historical data repository adapter."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

import pandas as pd

from markettool.core.models.historico import Historico
from markettool.core.ports.historicos_repo import HistoricosRepository
from markettool.core.errors import DataNotFoundError, DataValidationError


class FirestoreHistoricosRepository(HistoricosRepository):
    """
    Repository that fetches/stores historical data from Firestore and local files.
    Acts as adapter between domain and infrastructure layers.
    """
    
    def __init__(
        self,
        firestore_client,
        fmp_client,
        local_cache_dir: str = "./data/historicos",
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize repository.
        
        Args:
            firestore_client: Firestore client instance
            fmp_client: FMP API client for fetching new data
            local_cache_dir: Directory for local cache
            logger: Optional logger
        """
        self.firestore = firestore_client
        self.fmp = fmp_client
        self.cache_dir = local_cache_dir
        self.logger = logger or logging.getLogger(__name__)
    
    async def get_historico(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Historico:
        """Fetch historical data from FMP, Firestore, or local cache."""
        try:
            # Try local cache first
            df = self._load_local(symbol, timeframe)
            if df is not None and not df.empty:
                self.logger.debug(f"Loaded {symbol}/{timeframe} from local cache")
                historico = Historico(
                    symbol=symbol,
                    timeframe=timeframe,
                    df=df,
                    source="local",
                )
                if start_date or end_date:
                    historico = historico.get_range(start_date, end_date)
                return historico
            
            # Fetch from FMP
            self.logger.info(f"Fetching {symbol}/{timeframe} from FMP")
            historico = await self._fetch_from_fmp(symbol, timeframe, start_date, end_date)
            
            # Save to local cache
            self._save_local(historico)
            
            return historico
        
        except Exception as e:
            self.logger.error(f"Failed to get historico: {e}")
            raise DataNotFoundError(f"Could not fetch {symbol}/{timeframe}: {e}")
    
    async def save_historico(self, historico: Historico) -> None:
        """Save historical data locally and to Firestore."""
        try:
            self._save_local(historico)
            
            # Also save metadata to Firestore
            metadata = {
                "symbol": historico.symbol,
                "timeframe": historico.timeframe,
                "length": historico.length,
                "first_timestamp": historico.first_timestamp.isoformat() if historico.first_timestamp else None,
                "last_timestamp": historico.last_timestamp.isoformat() if historico.last_timestamp else None,
                "source": historico.source,
                "updated_at": datetime.utcnow().isoformat(),
            }
            
            key = f"{historico.symbol}_{historico.timeframe}"
            await self.firestore.set_document(
                collection="historicos_metadata",
                doc_id=key,
                data=metadata,
                merge=True,
            )
            
            self.logger.info(f"Saved {historico.symbol}/{historico.timeframe}")
        
        except Exception as e:
            self.logger.error(f"Failed to save historico: {e}")
            raise
    
    async def delete_historico(self, symbol: str, timeframe: str) -> None:
        """Delete historical data."""
        try:
            import os
            path = os.path.join(self.cache_dir, f"{symbol}_{timeframe}.json")
            if os.path.exists(path):
                os.remove(path)
            
            await self.firestore.delete_document("historicos_metadata", f"{symbol}_{timeframe}")
            self.logger.info(f"Deleted {symbol}/{timeframe}")
        
        except Exception as e:
            self.logger.error(f"Failed to delete: {e}")
    
    async def exists(self, symbol: str, timeframe: str) -> bool:
        """Check if data exists."""
        if self._load_local(symbol, timeframe) is not None:
            return True
        
        doc = await self.firestore.get_document("historicos_metadata", f"{symbol}_{timeframe}")
        return doc is not None
    
    async def get_last_update(self, symbol: str, timeframe: str) -> Optional[datetime]:
        """Get last update timestamp from metadata."""
        doc = await self.firestore.get_document("historicos_metadata", f"{symbol}_{timeframe}")
        if doc and "updated_at" in doc:
            return datetime.fromisoformat(doc["updated_at"])
        return None
    
    async def _fetch_from_fmp(
        self,
        symbol: str,
        timeframe: str,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> Historico:
        """Fetch from FMP API."""
        # This would call FMP client methods
        # Placeholder - concrete implementation in MarketTool.py
        raise NotImplementedError("Implement FMP fetch in MarketTool.py context")
    
    def _load_local(self, symbol: str, timeframe: str) -> Optional[pd.DataFrame]:
        """Load from local JSON cache."""
        try:
            import os
            import json
            
            path = os.path.join(self.cache_dir, f"{symbol}_{timeframe}.json")
            if not os.path.exists(path):
                return None
            
            with open(path, "r") as f:
                data = json.load(f)
            
            if not data:
                return None
            
            df = pd.DataFrame(data)
            if "date" in df.columns:
                df["date"] = pd.to_datetime(df["date"], utc=True)
                df = df.set_index("date")
            elif "timestamp" in df.columns:
                df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
                df = df.set_index("timestamp")
            
            return df
        
        except Exception as e:
            self.logger.debug(f"Failed to load local {symbol}/{timeframe}: {e}")
            return None
    
    def _save_local(self, historico: Historico) -> None:
        """Save to local JSON cache."""
        try:
            import os
            import json
            
            os.makedirs(self.cache_dir, exist_ok=True)
            path = os.path.join(self.cache_dir, f"{historico.symbol}_{historico.timeframe}.json")
            
            df = historico.df.reset_index()
            with open(path, "w") as f:
                json.dump(df.to_dict(orient="records"), f)
        
        except Exception as e:
            self.logger.error(f"Failed to save local: {e}")
