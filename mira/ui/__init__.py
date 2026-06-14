"""The clean-rebuild UI package (charter §4 step 7).

The reassembled UI lives here. It binds **only** to the gateway
(``mira.gateway``) — never to ``core/`` / ``data/`` journals — and never
imports from the legacy ``ui/`` package (that one stays alive as the oracle /
fallback until the §4-step-8 cutover). Reused legacy widgets are copied in and
rewired, not imported across the boundary.
"""
