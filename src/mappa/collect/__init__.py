"""Collectors: discovery, store metadata, privacy policies, Data Safety pages, APKs.

Every network request goes through one fetch layer (``fetching``, ``polite``,
``attempts``), so the ethics rules are enforced in one place: the research User-Agent,
at most one request per second per site, at most four sites at once, backing off when
blocked, and a ``fetch_log`` row plus the raw response for every attempt.
"""
