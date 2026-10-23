from django import template
from ..document_content import section_rows
register = template.Library()


@register.simple_tag
def document_sections(snapshot):
    return section_rows(snapshot)
