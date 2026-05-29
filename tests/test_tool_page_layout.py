from __future__ import annotations

import re


def test_tool_page_omits_notes_aside(open_app_client) -> None:
    response = open_app_client.get("/tools/extract-images")

    assert response.status_code == 200
    assert "<h2>Notes</h2>" not in response.text
    assert "Split` supports explicit groups" not in response.text
    assert "panel-side" not in response.text
    assert 'class="tool-layout tool-layout-single"' in response.text


def test_top_nav_marks_current_tool_as_active_pill(open_app_client) -> None:
    response = open_app_client.get("/tools/extract-images")

    assert response.status_code == 200
    assert '<nav class="topnav" aria-label="Tools">' in response.text
    assert re.search(
        r'<a\s+class="nav-pill is-active"\s+href="/tools/extract-images"\s+aria-current="page"\s*>\s*Extract Images\s*</a>',
        response.text,
    )
    assert re.search(r'<a\s+class="nav-pill"\s+href="/tools/merge"\s*>\s*Merge\s*</a>', response.text)


def test_app_css_uses_flat_background_and_visible_nav_pills(open_app_client) -> None:
    response = open_app_client.get("/static/app.css")
    assert response.status_code == 200

    body_block = re.search(r"body\s*\{(?P<body>.*?)\n\}", response.text, re.DOTALL)

    assert body_block is not None
    assert "background-color: var(--bg);" in body_block.group("body")
    assert "linear-gradient(145deg" not in body_block.group("body")
    assert "background: rgba(255, 251, 244, 0.86);" in response.text
    assert "border: 1px solid rgba(171, 59, 36, 0.16);" in response.text
    assert "box-shadow: 0 6px 18px rgba(73, 49, 24, 0.08);" in response.text
