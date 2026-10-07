"""Parsers: turn stored raw pages into rows. They read blobs and never touch the network,
so they can be re-run whenever a parser improves (principle 2)."""
