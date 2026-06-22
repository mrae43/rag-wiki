"""Central rulebook for all LLM system instructions.

Import prompt strings from .constants and render templates via render_template().
Do not import jinja2 directly outside this module -- use render_template() instead.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_jinja_env = Environment(loader=FileSystemLoader(str(_TEMPLATE_DIR)))


def render_template(name: str, **context: object) -> str:
    return _jinja_env.get_template(name).render(**context)
