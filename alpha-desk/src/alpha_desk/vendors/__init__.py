"""Vendor adapters.

Everything under this package is replaceable. No module outside it may import
from it directly: wire adapters through ``alpha_desk.marketdata.registry`` so a
vendor swap is a configuration change.
"""
