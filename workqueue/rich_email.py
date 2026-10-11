"""Allowlisted email HTML; never retain executable markup or remote images."""
from html import escape
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit


class EmailHTML(HTMLParser):
    tags = {'p', 'br', 'strong', 'b', 'em', 'i', 'u', 's', 'ul', 'ol', 'li', 'blockquote', 'h2', 'h3', 'span', 'a'}
    blocked = {'script', 'style', 'iframe', 'object', 'svg', 'math', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.html, self.text, self.stack, self.suppressed = [], [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.blocked:
            self.suppressed += 1
            return
        if self.suppressed or tag not in self.tags:
            return
        attrs = dict(attrs)
        safe = ''
        href = attrs.get('href', '')
        if tag == 'a' and urlsplit(href).scheme.lower() in {'https', 'http', 'mailto'} and not re.search(r'[\x00-\x20]', href):
            safe += f' href="{escape(href, quote=True)}" rel="noopener noreferrer"'
        styles = []
        for key, value in re.findall(r'([a-z-]+)\s*:\s*([^;]+)', attrs.get('style', '').lower()):
            if key in {'color', 'background-color'} and re.fullmatch(r'#[0-9a-f]{3,8}|rgb\(\d{1,3},\s*\d{1,3},\s*\d{1,3}\)', value.strip()):
                styles.append(f'{key}:{value.strip()}')
            if key == 'text-align' and value.strip() in {'left', 'center', 'right'}:
                styles.append(f'{key}:{value.strip()}')
        if styles:
            safe += f' style="{escape(";".join(styles), quote=True)}"'
        self.html.append(f'<{tag}{safe}>')
        if tag == 'br':
            self.text.append('\n')
        else:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.blocked:
            self.suppressed = max(0, self.suppressed - 1)
            return
        if self.suppressed or tag not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.html.append(f'</{current}>')
            if current in {'p', 'li', 'blockquote', 'h2', 'h3'}:
                self.text.append('\n')
            if current == tag:
                break

    def handle_data(self, data):
        if not self.suppressed:
            self.html.append(escape(data))
            self.text.append(data)


def clean_email_html(value):
    parser = EmailHTML()
    parser.feed(value or '')
    parser.close()
    while parser.stack:
        parser.html.append(f'</{parser.stack.pop()}>')
    return ''.join(parser.html), ''.join(parser.text).strip()
