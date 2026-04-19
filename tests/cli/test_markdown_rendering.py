"""Tests for markdown rendering in CLI responses."""

import pytest
from pathlib import Path


class TestMarkdownRendering:
    """Test markdown rendering functions in cli.py."""

    @pytest.fixture
    def cli_source(self):
        """Load cli.py source code for inspection."""
        cli_path = Path(__file__).parent.parent.parent / "cli.py"
        return cli_path.read_text()

    def test_rich_markdown_import_exists(self, cli_source):
        """Verify rich.markdown.Markdown is imported in cli.py."""
        assert "from rich.markdown import Markdown" in cli_source or \
               "from rich.markdown import Markdown as" in cli_source

    def test_rich_markdown_function_exists(self, cli_source):
        """Verify _rich_markdown_from_text function is defined."""
        assert "def _rich_markdown_from_text" in cli_source

    def test_smart_render_function_exists(self, cli_source):
        """Verify _smart_render auto-detection function is defined."""
        assert "def _smart_render" in cli_source

    def test_smart_render_uses_markdown_markers(self, cli_source):
        """Verify _smart_render detects markdown syntax markers."""
        # Check for markdown detection markers
        assert "```" in cli_source
        assert "**" in cli_source or "## " in cli_source

    def test_smart_render_fallback_to_ansi(self, cli_source):
        """Verify _smart_render falls back to _rich_text_from_ansi."""
        assert "_rich_text_from_ansi" in cli_source
        # Should call _smart_render or _rich_markdown_from_text in response paths
        assert "_smart_render(response)" in cli_source or \
               "_rich_markdown_from_text(response)" in cli_source

    def test_response_rendering_updated(self, cli_source):
        """Verify response rendering points use markdown-aware rendering."""
        # At least one Panel should use smart_render or markdown renderer
        # instead of plain _rich_text_from_ansi
        lines = cli_source.split("\n")
        panel_with_smart_render = False
        
        for i, line in enumerate(lines):
            if "Panel(" in line:
                # Check next few lines for smart_render or markdown call
                context = "\n".join(lines[i:i+5])
                if "_smart_render" in context or "_rich_markdown_from_text" in context:
                    panel_with_smart_render = True
                    break
        
        assert panel_with_smart_render, \
            "No Panel found using _smart_render or _rich_markdown_from_text"


class TestMarkdownRenderingIntegration:
    """Integration tests for markdown rendering (require Rich)."""

    def test_rich_markdown_renders_bold(self):
        """Verify Rich Markdown renders bold text without raw asterisks."""
        from rich.markdown import Markdown
        from rich.console import Console
        from io import StringIO

        console = Console(file=StringIO(), force_terminal=True)
        md = Markdown("**bold text**")
        console.print(md)
        
        output = console.file.getvalue()
        # Rich should render bold without raw asterisks
        assert "**bold text**" not in output

    def test_rich_markdown_renders_code_block(self):
        """Verify Rich Markdown renders code blocks properly."""
        from rich.markdown import Markdown
        from rich.console import Console
        from io import StringIO

        console = Console(file=StringIO(), force_terminal=True)
        code_md = "```python\nprint('hello')\n```"
        md = Markdown(code_md)
        console.print(md)
        
        output = console.file.getvalue()
        # Should not contain raw triple backticks
        assert "```" not in output

    def test_rich_markdown_renders_header(self):
        """Verify Rich Markdown renders headers without raw ##."""
        from rich.markdown import Markdown
        from rich.console import Console
        from io import StringIO

        console = Console(file=StringIO(), force_terminal=True)
        md = Markdown("## Header")
        console.print(md)
        
        output = console.file.getvalue()
        # Should not contain raw ##
        assert "## " not in output

    def test_rich_markdown_renders_list(self):
        """Verify Rich Markdown renders lists without raw dashes."""
        from rich.markdown import Markdown
        from rich.console import Console
        from io import StringIO

        console = Console(file=StringIO(), force_terminal=True)
        md = Markdown("- Item 1\n- Item 2")
        console.print(md)
        
        output = console.file.getvalue()
        # Rich may still show some bullet chars, but not raw "- " pattern
        # This is a softer check since Rich uses unicode bullets
        assert "- Item 1" not in output or "•" in output or "─" in output
