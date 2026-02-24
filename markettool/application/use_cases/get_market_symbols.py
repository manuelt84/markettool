"""
Use case for retrieving active market symbols.

This use case retrieves the list of symbols that are currently active
in the market and available for trading analysis.
"""

from __future__ import annotations

import logging
from typing import List, Optional


class GetMarketSymbolsUseCase:
    """
    Use case for retrieving active market symbols.
    
    This abstracts the logic for determining which symbols are:
    - Currently tradable
    - Available in the market
    - Configured for analysis
    
    In production, this could query:
    - Firestore configuration
    - FMP API for available symbols
    - Local configuration files
    """
    
    def __init__(
        self,
        firestore_client=None,
        default_symbols: Optional[List[str]] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Initialize use case.
        
        Args:
            firestore_client: Firestore client to query symbols config
            default_symbols: Fallback list if Firestore unavailable
            logger: Optional logger
        """
        self.firestore = firestore_client
        self.default_symbols = default_symbols or [
            # Forex majors
            "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF",
            # Forex crosses
            "EURGBP", "EURJPY", "GBPJPY",
            # US Indices
            "SPX", "DJI", "IXIC",
            # Popular stocks (if FMP plan allows)
            "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA",
        ]
        self.logger = logger or logging.getLogger(__name__)
    
    async def execute(self) -> List[str]:
        """
        Get list of active market symbols.
        
        Returns:
            List of symbol strings (e.g., ["EURUSD", "GBPUSD", ...])
            
        Raises:
            UseCaseError: If retrieval fails completely
        """
        # Try Firestore first
        if self.firestore:
            try:
                symbols = await self._get_from_firestore()
                if symbols:
                    self.logger.info(f"Loaded {len(symbols)} symbols from Firestore")
                    return symbols
            except Exception as exc:
                self.logger.warning(f"Failed to load symbols from Firestore: {exc}")
        
        # Fallback to defaults
        self.logger.info(f"Using default symbols ({len(self.default_symbols)} symbols)")
        return self.default_symbols
    
    async def _get_from_firestore(self) -> Optional[List[str]]:
        """
        Retrieve symbols from Firestore configuration.
        
        Expected Firestore structure:
            config/symbols (document)
                - active: List[str]
                - updated_at: datetime
        """
        try:
            doc_ref = self.firestore.collection("config").document("symbols")
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                symbols = data.get("active", [])
                if symbols:
                    return symbols
            
            return None
        
        except Exception as exc:
            self.logger.error(f"Firestore query failed: {exc}")
            return None
    
    async def add_symbol(self, symbol: str) -> bool:
        """
        Add a symbol to the active list.
        
        Args:
            symbol: Symbol to add (e.g., "BTCUSD")
            
        Returns:
            True if successfully added
        """
        if not self.firestore:
            self.logger.warning("Cannot add symbol: Firestore not configured")
            return False
        
        try:
            doc_ref = self.firestore.collection("config").document("symbols")
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                symbols = data.get("active", [])
            else:
                symbols = []
            
            if symbol not in symbols:
                symbols.append(symbol)
                doc_ref.set({"active": symbols}, merge=True)
                self.logger.info(f"Added symbol {symbol}")
                return True
            
            return False
        
        except Exception as exc:
            self.logger.error(f"Failed to add symbol: {exc}")
            return False
    
    async def remove_symbol(self, symbol: str) -> bool:
        """
        Remove a symbol from the active list.
        
        Args:
            symbol: Symbol to remove
            
        Returns:
            True if successfully removed
        """
        if not self.firestore:
            self.logger.warning("Cannot remove symbol: Firestore not configured")
            return False
        
        try:
            doc_ref = self.firestore.collection("config").document("symbols")
            doc = doc_ref.get()
            
            if doc.exists:
                data = doc.to_dict()
                symbols = data.get("active", [])
                
                if symbol in symbols:
                    symbols.remove(symbol)
                    doc_ref.set({"active": symbols}, merge=True)
                    self.logger.info(f"Removed symbol {symbol}")
                    return True
            
            return False
        
        except Exception as exc:
            self.logger.error(f"Failed to remove symbol: {exc}")
            return False
