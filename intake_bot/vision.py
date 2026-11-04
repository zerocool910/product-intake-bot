"""Claude calls: propose fields from photos, and apply plain-language corrections.

Claude only picks values from the closed code lists. Names, SKUs, the
cipher and prices are computed by ordinary code (naming.py / pricing.py).
"""

from __future__ import annotations

import base64
import json
import logging

import anthropic

from .catalog import Catalog
from .item import ItemFields

log = logging.getLogger(__name__)

# Fields Claude must never fill from a photo: business inputs, not visual facts.
NOT_FROM_PHOTO = ("gender", "supplied_price", "selling_price", "cost_price", "quantity", "ref_code", "category")


class VisionError(RuntimeError):
    pass


def _rules(catalog: Catalog) -> str:
    return "\n".join([
        "Allowed values (use the name exactly as written, or null):",
        f"- brand: {', '.join(catalog.brands.names())}",
        f"- strap_type: {', '.join(catalog.strap_types.names())}",
        f"- colors (strap_color, case_color, dial_color, crown_color): {', '.join(catalog.colors.names())}",
        f"- index_type: {', '.join(catalog.index_types.names())}",
        "- type: watch or clock",
    ])


def _system(catalog: Catalog) -> str:
    # Stable across requests so it can be prompt-cached.
    return f"""You help staff at a watch shop log incoming stock. You look at photos of one item \
(the item itself, and sometimes its price tag or box) and fill in its catalog attributes.

{_rules(catalog)}

How to fill the fields:
- Strap vs case: strap_color is the band/bracelet; case_color is the watch body (bezel/case). \
"Stainless St." is a metal bracelet; "Leather", "Rubber", "Kanvas" etc. are straps.
- manufacturer_sku: only if a model/reference code (e.g. SRPD53K1) is clearly printed on a tag, \
caseback, or box in the photo. Copy it exactly.
- Price tags in this shop show a letter cipher (e.g. "EBB"). If you see one, copy the letters \
into notes as "tag cipher: EBB". Do not try to decode it.
- Leave dial_color, index_type and crown_color null. Mention the dial color and index style in notes instead — \
staff add these only when two items would otherwise share a name.
- Never fill gender, prices, quantity, ref_code or category. Those come from staff, not the photo.
- Put every field you filled but are not sure about into `uncertain`. A wrong value that looks right \
ends up printed on a tag, so leaving a field null or marking it uncertain is always better than guessing.
- Keep notes short (one or two lines)."""


def _effort_config(effort: str) -> dict:
    return {"effort": effort}


class Vision:
    def __init__(self, catalog: Catalog, model: str, client: anthropic.Anthropic | None = None):
        self.catalog = catalog
        self.model = model
        self.client = client or anthropic.Anthropic()

    def _parse(self, content: list, effort: str) -> ItemFields:
        try:
            response = self.client.beta.messages.parse(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": _system(self.catalog), "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": content}],
                output_format=ItemFields,
                output_config=_effort_config(effort),
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.RateLimitError as e:
            raise VisionError("Claude is rate-limited right now, try again in a minute.") from e
        except anthropic.APIConnectionError as e:
            raise VisionError("Couldn't reach Claude (network).") from e
        except anthropic.APIStatusError as e:
            log.exception("Claude API error")
            raise VisionError(f"Claude API error {e.status_code}.") from e

        if response.stop_reason == "refusal":
            raise VisionError("Claude declined to read this photo.")
        if response.stop_reason == "max_tokens" or response.parsed_output is None:
            raise VisionError("Claude's answer was incomplete.")
        return response.parsed_output

    def propose(self, images: list[tuple[bytes, str]], caption: str | None) -> ItemFields:
        """images: (bytes, media_type) pairs, all of the same item."""
        content: list = []
        for data, media_type in images:
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": media_type,
                "data": base64.standard_b64encode(data).decode("ascii")}})
        text = "Fill in this item's fields."
        if caption:
            text += f"\nStaff caption (may name the brand): {caption}"
        content.append({"type": "text", "text": text})

        fields = self._parse(content, effort="medium")
        # Belt and braces: drop anything that must not come from a photo.
        data = fields.model_dump()
        for k in NOT_FROM_PHOTO:
            data[k] = None
        data["uncertain"] = [u for u in data["uncertain"] if u not in NOT_FROM_PHOTO]
        return ItemFields(**data)

    def correct(self, current: ItemFields, message: str) -> ItemFields:
        """Apply a staff correction like "it's rubber, not leather, and ladies"."""
        text = (
            "Here are an item's current fields as JSON:\n"
            f"{json.dumps(current.model_dump(), ensure_ascii=False)}\n\n"
            f"Staff wrote this correction: {message!r}\n\n"
            "Return the full updated fields. Change only what the correction asks for and keep "
            "everything else exactly as it is. Staff may set any field here, including gender, "
            "prices (in Rupiah: 950k = 950000, 1,2jt = 1200000), quantity, ref_code, and the "
            "dial/index/crown descriptor fields. Remove a field from `uncertain` once staff confirm "
            "or change it."
        )
        return self._parse([{"type": "text", "text": text}], effort="low")
