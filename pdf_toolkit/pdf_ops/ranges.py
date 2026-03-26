from __future__ import annotations


def parse_page_range_spec(spec: str, total_pages: int) -> list[int]:
    if total_pages <= 0:
        raise ValueError("The PDF must contain at least one page.")
    normalized = spec.strip()
    if not normalized:
        raise ValueError("A page range is required.")

    pages: list[int] = []
    seen: set[int] = set()
    for token in normalized.split(","):
        item = token.strip()
        if not item:
            continue
        if "-" in item:
            start_text, end_text = item.split("-", maxsplit=1)
            start = _parse_page_number(start_text, total_pages)
            end = _parse_page_number(end_text, total_pages)
            if start > end:
                raise ValueError(f"Invalid descending page range: {item}")
            for page_number in range(start, end + 1):
                if page_number not in seen:
                    pages.append(page_number)
                    seen.add(page_number)
            continue

        page_number = _parse_page_number(item, total_pages)
        if page_number not in seen:
            pages.append(page_number)
            seen.add(page_number)

    if not pages:
        raise ValueError("The page range did not resolve to any pages.")
    return pages


def parse_split_range_groups(spec: str, total_pages: int) -> list[list[int]]:
    normalized = spec.strip()
    if not normalized:
        raise ValueError("Split ranges are required.")
    groups: list[list[int]] = []
    for token in normalized.replace("\n", ";").split(";"):
        group_spec = token.strip()
        if not group_spec:
            continue
        groups.append(parse_page_range_spec(group_spec, total_pages))
    if not groups:
        raise ValueError("No valid split ranges were supplied.")
    return groups


def build_every_n_groups(total_pages: int, every_n: int) -> list[list[int]]:
    if every_n <= 0:
        raise ValueError("Every-N split size must be greater than zero.")
    return [
        list(range(start, min(start + every_n, total_pages + 1)))
        for start in range(1, total_pages + 1, every_n)
    ]


def _parse_page_number(token: str, total_pages: int) -> int:
    try:
        page_number = int(token)
    except ValueError as exc:
        raise ValueError(f"Invalid page number: {token}") from exc

    if not 1 <= page_number <= total_pages:
        raise ValueError(f"Page {page_number} is outside the valid range 1-{total_pages}.")
    return page_number
