"""Compatibility facade; vendor implementation belongs to integrations/graphify."""
from .integrations.graphify.adapter import GraphifyError, build_graph, normalize_graph, _offline_environment
