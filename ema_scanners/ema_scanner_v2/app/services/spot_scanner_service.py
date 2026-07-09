"""
Spot market scanner — the SAME ScannerService logic as futures (unchanged),
just instantiated for market="spot". See scanner_service.py for the actual
implementation; this module only provides the spot singleton.
"""

from app.services.scanner_service import ScannerService

spot_scanner_service = ScannerService(market="spot")
