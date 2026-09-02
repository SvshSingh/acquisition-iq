"""Discovery sources.

Two sources, one interface, because they are good at opposite things and the
product needs both:

* **Licensing boards** publish *structure* — ownership form, licence issue date,
  trade classification, whether the business employs anyone. Filed facts, on
  every row, about exactly the questions the scorer asks.
* **OpenStreetMap** publishes *presence* — coordinates and, when someone has
  bothered to map it, a website. Sparse for trade contractors, but it is the
  only open source that links a business to a URL, and without a URL the
  crawler has nothing to read.

Neither is sufficient alone. A licence record cannot tell you the company's
website is a 2011 FrontPage page; a map node cannot tell you the owner filed as
a sole proprietor in 1987.
"""

from __future__ import annotations

from app.pipeline.sources.base import DiscoverySource
from app.pipeline.sources.cslb import CslbSource
from app.pipeline.sources.osm import OverpassSource

__all__ = ["CslbSource", "DiscoverySource", "OverpassSource"]
