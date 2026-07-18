#!/usr/bin/env python3
"""iNaturalist provider."""
from __future__ import annotations
from .common import BaseProvider, Batch, Taxon, normalize_space, safe_int, now


class Provider(BaseProvider):
    PROVIDER_NAME = "inaturalist"

    def fetch(self) -> Batch:
        base = self.definition.get(
            "base_url",
            "https://api.inaturalist.org/v1",
        ).rstrip("/")
        page = safe_int(self.cursor, 1)
        limit = min(self.batch_size, 200)
        payload = self.http.get_json(
            f"{base}/taxa",
            {
                "page": page,
                "per_page": limit,
                "order": "asc",
                "order_by": "id",
                "is_active": "true",
            },
        )
        rows = (
            payload.get("results", [])
            if isinstance(payload, dict)
            else []
        )
        records: list[Taxon] = []

        for item in rows:
            if not isinstance(item, dict):
                continue
            provider_id = item.get("id")
            name = normalize_space(item.get("name"))
            if provider_id is None or not name:
                continue
            records.append(
                Taxon(
                    provider=self.name,
                    provider_id=str(provider_id),
                    scientific_name=name,
                    canonical_name=name,
                    rank=normalize_space(
                        item.get("rank")
                    ).lower() or "unknown",
                    status=(
                        "accepted"
                        if item.get("is_active")
                        else "inactive"
                    ),
                    source_url=(
                        "https://www.inaturalist.org/taxa/"
                        f"{provider_id}"
                    ),
                    retrieved_at=now(),
                    extra={
                        "rank_level": item.get("rank_level"),
                        "ancestry": item.get("ancestry"),
                        "iconic_taxon_name": item.get(
                            "iconic_taxon_name"
                        ),
                        "preferred_common_name": item.get(
                            "preferred_common_name"
                        ),
                    },
                )
            )

        exhausted = len(rows) < limit
        return Batch(
            records=records,
            next_cursor=None if exhausted else str(page + 1),
            exhausted=exhausted,
            requests=1,
            raw=len(rows),
        )
