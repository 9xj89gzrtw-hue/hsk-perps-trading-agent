"""dreamDEX Delta-Neutral Funding Harvester — read-only analytics + guarded execution.

The package never hides a number it cannot source. Every market row comes from
the official Hideki indexer, every contract read comes from the pool that will
execute the order, and every write goes through the official SDK bridge.
"""

__version__ = "0.1.0"
